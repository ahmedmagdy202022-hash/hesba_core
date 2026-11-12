"""E5 (HG-093): the automatic messages to a guardian, an absence and fees still due.

"Today" comes from ``timezone.localdate()`` and every other day is built from it; no clock is patched. What these pin: both kinds are off until
the owner switches them on and exist only for a school or a centre; an absence is messaged once per student per class, for today and
yesterday only, never for a cancelled class or a late mark, never again once it was sent or skipped (even after the sheet is saved again,
which rewrites every mark), and withdrawn when the mark is corrected before it goes out; the text carries the student, the group, the
day and the centre and nothing else; the fees message is one per guardian with the balance the fees screen shows, three days after the
invoice, every seven days at most, nothing for a draft or a paid invoice, and the general amount-due reminder does not double it while a
business that is not a school is untouched; and nothing here moves money.
"""

import importlib
import os
import re
from datetime import time, timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import migrations
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from education import attendance, billing
from education import messages as education_messages
from education import services as education
from education.models import ClassSession, Student
from education.tests import EducationSetup
from entities.current import working_in
from entities.models import Entity
from entities.services import main_entity
from hesba_testing.factories import make_cashbox, make_customer, make_location
from medical.tests_inpatient import footprint
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, CustomerLedgerEntryType, SalesInvoice
from sales.services import cancel_posted_sales_invoice, record_customer_payment
from settings_core.templatetags.hesba_format import day_text

from . import services, whatsapp
from .models import MessageKind, OutboxMessage, OutboxStatus
from .tests_m3 import ENV, Graph, at, person

EVERY_DAY = "sun,mon,tue,wed,thu,fri,sat"
TODAY = timezone.localdate()
SECRET = "ZZNOTE حالة خاصة جدًا"


def days_ago(count):
    return TODAY - timedelta(days=count)


class Centre(EducationSetup):
    """A tutoring centre with one class meeting every day, and three children of three guardians."""

    def setUp(self):
        super().setUp()
        make_location(location_code="CENTER", is_default=True)  # where the fee invoices are recorded, as every install has
        self.cashbox = make_cashbox()
        self.daily = self.group("مجموعة يومية", days=EVERY_DAY, fee="0")  # a free group: only the fee groups bill
        self.a, self.b, self.c = (self.child("آدم", "01010000001"), self.child("بسمة", "01010000002"), self.child("جاد", "01010000003"))
        self.sheets = {}  # the marks of each saved class, so a second student on the same day is added to the sheet, not instead of it

    def child(self, name, phone, group=None):
        student = education.save_student({"name": name, "payer_name": f"ولي أمر {name}", "payer_phone": phone}, self.owner)
        education.enroll(student, group or self.daily, self.owner, start_date=days_ago(90))
        return student

    def turn_on(self, kind, **extra):
        return services.save_template(kind, {"text": services.template(kind).text, "active": "1", "every_days": str(services.template(kind).every_days), **extra}, self.owner)

    def absent(self, student, day=None, presence="absent", group=None, note=""):
        """What a teacher does: save the class of ``day`` with this student marked."""

        group, day = group or self.daily, day or TODAY
        sheet = self.sheets.setdefault((group.pk, day), {})
        sheet.update({str(student.pk): presence, f"note_{student.pk}": note})
        return attendance.take(group, self.owner, day, dict(sheet))

    def messages(self, kind):
        return OutboxMessage.objects.filter(kind=kind)


# ---- the absence message ----

class AbsenceTests(Centre):
    def setUp(self):
        super().setUp()
        self.turn_on(MessageKind.ABSENCE)

    def test_it_is_off_until_the_owner_switches_it_on(self):
        services.save_template(MessageKind.ABSENCE, {"text": services.template(MessageKind.ABSENCE).text, "every_days": "1"}, self.owner)  # unticked
        self.absent(self.a)
        self.assertFalse(services.template(MessageKind.ABSENCE).active)
        self.assertEqual(services.build(), 0)
        self.turn_on(MessageKind.ABSENCE)
        self.assertEqual(services.build(), 1)
        self.assertTrue(MessageKind.ABSENCE in services.DEFAULT_OFF and MessageKind.FEES in services.DEFAULT_OFF)
        self.assertFalse(services.template(MessageKind.FEES).active)

    def test_last_weeks_absence_is_not_queued_and_todays_and_yesterdays_are_once_each(self):
        self.absent(self.a, days_ago(7))
        self.absent(self.b, days_ago(2))
        self.absent(self.a, days_ago(1))
        self.absent(self.c, TODAY)
        self.assertEqual(services.build(), 2 + 0)  # yesterday's آدم and today's جاد; the week-old and the two-day-old ones are out
        self.assertEqual(sorted(self.messages(MessageKind.ABSENCE).values_list("name", flat=True)), ["ولي أمر آدم", "ولي أمر جاد"])
        self.assertEqual(services.build(), 0)  # the same day again adds nothing
        self.assertEqual(self.messages(MessageKind.ABSENCE).count(), 2)

    def test_the_key_holds_the_class_and_the_student(self):
        session = self.absent(self.a)
        services.build()
        self.assertEqual(self.messages(MessageKind.ABSENCE).get().key, f"absence:{session.pk}:{self.a.pk}")

    def test_a_cancelled_class_is_not_messaged_even_when_a_mark_is_left_on_it(self):
        session = self.absent(self.a)
        ClassSession.objects.filter(pk=session.pk).update(status="cancelled")
        self.assertEqual(session.marks.filter(presence="absent").count(), 1)
        self.assertEqual(services.build(), 0)

    def test_late_excused_and_present_are_not_messaged(self):
        for student, presence in ((self.a, "late"), (self.b, "excused"), (self.c, "present")):
            self.absent(student, presence=presence)
        self.assertEqual(services.build(), 0)

    def test_a_correction_to_present_before_it_goes_out_withdraws_the_waiting_message(self):
        self.absent(self.a)
        services.build()
        self.assertEqual(self.messages(MessageKind.ABSENCE).filter(status=OutboxStatus.PENDING).count(), 1)
        self.absent(self.a, presence="present")  # the teacher fixes the mark
        services.build()
        self.assertEqual(self.messages(MessageKind.ABSENCE).count(), 0)  # gone before anyone could send it
        self.absent(self.a)  # and marked absent again: it comes back
        self.assertEqual(services.build(), 1)

    def test_one_already_sent_stays_in_the_record_when_the_mark_is_corrected(self):
        self.absent(self.a)
        services.build()
        services.mark(self.messages(MessageKind.ABSENCE).get(), OutboxStatus.SENT, self.owner)
        self.absent(self.a, presence="present")
        services.build()
        self.assertEqual(self.messages(MessageKind.ABSENCE).filter(status=OutboxStatus.SENT).count(), 1)  # cannot be taken back; history is kept

    def test_saving_the_sheet_again_never_sends_a_second_message(self):
        """``take`` deletes and rewrites every mark, so a mark's id changes; the key must not."""

        first = self.absent(self.a)
        ids = set(first.marks.values_list("pk", flat=True))
        services.build()
        services.mark(self.messages(MessageKind.ABSENCE).get(), OutboxStatus.SENT, self.owner)
        again = self.absent(self.a, note="ملاحظة جديدة")  # the teacher saves the sheet once more, to fix someone else
        self.assertTrue(set(again.marks.values_list("pk", flat=True)).isdisjoint(ids))
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(services.build(), 0)
        self.assertEqual(self.messages(MessageKind.ABSENCE).count(), 1)

    def test_a_sent_or_skipped_one_is_never_queued_again_the_next_day(self):
        self.absent(self.a, days_ago(1))
        self.absent(self.b, TODAY)
        services.build()
        services.mark(self.messages(MessageKind.ABSENCE).get(name="ولي أمر آدم"), OutboxStatus.SENT, self.owner)
        services.mark(self.messages(MessageKind.ABSENCE).get(name="ولي أمر بسمة"), OutboxStatus.SKIPPED, self.owner)
        # Tomorrow, yesterday's and today's absences are both still inside the two-day window; neither is queued twice (every_days is 1).
        self.assertEqual(services.build(TODAY + timedelta(days=1)), 0)
        self.assertEqual(self.messages(MessageKind.ABSENCE).count(), 2)

    def test_the_text_carries_the_student_the_group_the_day_and_the_centre_and_nothing_else(self):
        self.absent(self.a, note=SECRET)
        education.save_student({"name": "آدم", "payer_name": "ولي أمر آدم", "payer_phone": "01010000001", "notes": SECRET, "stage": "تالتة إعدادي"}, self.owner, self.a)
        services.build()
        message = self.messages(MessageKind.ABSENCE).get()
        self.assertEqual((message.phone, message.status, message.name, message.channel), ("201010000001", OutboxStatus.PENDING, "ولي أمر آدم", ""))
        self.assertEqual(message.text, f"أهلاً ولي أمر آدم، آدم كان غايب يوم {day_text(TODAY, 'ar')} عن مجموعة يومية في Demo Store.")
        for forbidden in ("ZZNOTE", "خاصة", "تالتة", "http", "/education", "%", "درجة", "{"):
            self.assertNotIn(forbidden, message.text)
        self.assertFalse(message.text.startswith(("Demo", "ST-")))  # an Arabic sentence never starts with a Latin word
        self.assertEqual(message.link, f"/education/students/{self.a.pk}/")  # inside Hesba, never in the text
        self.assertEqual(message.entity, main_entity())

    def test_it_goes_to_the_guardians_whatsapp_then_phone_never_the_students_own(self):
        payer = self.a.payer
        payer.whatsapp = "01112223334"
        payer.save()
        Student.objects.filter(pk=self.a.pk).update(phone="01299999999")
        self.absent(self.a)
        services.build()
        self.assertEqual(self.messages(MessageKind.ABSENCE).get().phone, "201112223334")

    def test_a_guardian_with_no_number_or_switched_off_gets_nothing_and_so_does_an_inactive_student(self):
        for student, fields in ((self.a, {"phone": "", "whatsapp": ""}), (self.b, {"active": False})):
            for name, value in fields.items():
                setattr(student.payer, name, value)
            student.payer.save()
        Student.objects.filter(pk=self.c.pk).update(active=False)
        for student in (self.a, self.b, self.c):
            self.absent(student)
        self.assertEqual(services.build(), 0)

    def test_another_entitys_student_is_not_gathered_while_working_in_this_one(self):
        branch = Entity.objects.create(code="E-BR", name_ar="فرع المعادي", activity_slug="education", sub_activity_slug="tutoring_center")
        Student.objects.filter(pk=self.b.pk).update(entity=branch)
        self.absent(self.a)
        self.absent(self.b)
        with working_in(main_entity()):
            keys = [row["key"] for row in education_messages.absences(TODAY, "", "")]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].endswith(f":{self.a.pk}"))
        with working_in(branch):
            keys = [row["key"] for row in education_messages.absences(TODAY, "", "")]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].endswith(f":{self.b.pk}"))
        self.assertEqual(services.build(), 2)
        rows = {row.name: row for row in self.messages(MessageKind.ABSENCE)}
        self.assertEqual(rows["ولي أمر آدم"].entity, main_entity())
        self.assertEqual(rows["ولي أمر بسمة"].entity, branch)
        self.assertIn("فرع المعادي", rows["ولي أمر بسمة"].text)  # a group names each centre in its own message

    def test_the_english_business_gets_the_english_wording(self):
        from settings_core.models import ClientProfile

        profile = ClientProfile.get_active()
        profile.default_language = "en"
        profile.save()
        self.absent(self.a)
        services.build()
        self.assertEqual(self.messages(MessageKind.ABSENCE).get().text, f"Hello ولي أمر آدم, آدم was absent on {day_text(TODAY, 'en')} from مجموعة يومية at Demo Store.")

    def test_the_date_is_in_words_not_iso(self):
        self.absent(self.a)
        services.build()
        text = self.messages(MessageKind.ABSENCE).get().text
        self.assertIn(day_text(TODAY, "ar"), text)
        self.assertNotIn(TODAY.isoformat(), text)


# ---- the fees message ----

class Fees(Centre):
    def setUp(self):
        super().setUp()
        self.turn_on(MessageKind.FEES)
        self.rooms = iter(range(1, 20))

    def fee_group(self, fee):
        n = next(self.rooms)
        return self.group(f"غ{fee}-{n}", days="sat", start=time(8 + n), room=f"ر{n}", teacher=False, fee=str(fee))

    def bill(self, day, post=True):
        return billing.bill_month(billing.month_of(day), self.owner, post=post, invoice_date=day)

    def pay(self, payer, amount, number="CP-1"):
        return record_customer_payment(number, TODAY, payer, self.cashbox, D(amount), self.owner)

    def fees(self):
        return self.messages(MessageKind.FEES)


class FeesAmountTests(Fees):
    def setUp(self):
        super().setUp()
        # The daily class of the base setup is a free group (no fee), so only the fee groups bill.
        self.parent = self.child("يوسف", "01001234501", self.fee_group(900))

    def test_an_invoice_of_900_posted_four_days_ago_with_300_paid_says_600(self):
        invoice = self.bill(days_ago(4))[0]
        self.assertEqual((invoice.total_amount, invoice.status), (D("900.00"), "posted"))
        self.pay(invoice.customer, "300")
        self.assertEqual(services.build(), 1)
        message = self.fees().get()
        self.assertEqual((message.key, message.name, message.phone, message.status), (f"fees:{invoice.customer_id}", "ولي أمر يوسف", "201001234501", OutboxStatus.PENDING))
        self.assertEqual(message.text, "أهلاً ولي أمر يوسف، تذكير ودّي من Demo Store: المتبقي من مصروفات الدراسة على حضرتك 600.00 EGP. لو سدّدت خلاص، متاخدش بالك من الرسالة. شكرًا لحضرتك.")
        for forbidden in ("غ900", "900", "300", "http", "{"):
            self.assertNotIn(forbidden, message.text)  # the guardian's balance, never the group, the invoice total or a link

    def test_two_children_of_one_guardian_are_one_message_with_the_total(self):
        # Replace the 900 child by two: a 600 and a 450, on the same guardian (the same phone finds the same parent).
        education.stop(self.parent.enrollments.get(), self.owner, end_date=days_ago(60))
        first = self.child("سلمى", "01001234501", self.fee_group(600))
        second = self.child("مريم", "01001234501", self.fee_group(450))
        self.assertEqual(first.payer, second.payer)
        invoices = self.bill(days_ago(4))
        self.assertEqual([invoice.total_amount for invoice in invoices], [D("1050.00")])
        self.assertEqual(services.build(), 1)
        self.assertEqual(self.fees().count(), 1)  # one message per guardian, not per child or per invoice
        self.assertIn("1,050.00", self.fees().get().text)

    def test_two_invoices_of_one_guardian_are_one_message_with_the_total_of_both(self):
        self.bill(days_ago(35))
        self.bill(days_ago(4))
        self.assertEqual(sorted(self.parent.payer.sales_invoices.values_list("total_amount", flat=True)), [D("900.00"), D("900.00")])
        self.assertEqual(services.build(), 1)
        self.assertIn("1,800.00", self.fees().get().text)

    def test_an_invoice_two_days_old_is_not_chased_yet_and_three_days_is(self):
        self.bill(days_ago(2))
        self.assertEqual(services.build(), 0)
        self.assertEqual(services.build(TODAY + timedelta(days=1)), 1)  # the invoice is three days old tomorrow
        self.assertEqual(education_messages.FEES_AFTER_DAYS, 3)

    def test_a_draft_is_never_chased(self):
        draft = self.bill(days_ago(10), post=False)[0]
        self.assertEqual(draft.status, "draft")
        self.assertEqual(services.build(), 0)

    def test_fully_paid_is_not_chased_and_a_waiting_message_is_withdrawn(self):
        invoice = self.bill(days_ago(4))[0]
        services.build()
        self.assertEqual(self.fees().filter(status=OutboxStatus.PENDING).count(), 1)
        self.pay(invoice.customer, "900")
        services.build()
        self.assertEqual(self.fees().count(), 0)  # gone before anyone could send it
        self.assertEqual(education_messages.fee_payers(TODAY), [])

    def test_a_cancelled_invoice_is_not_chased(self):
        invoice = self.bill(days_ago(4))[0]
        cancel_posted_sales_invoice(invoice.pk, self.owner, reason="test")
        self.assertEqual(services.build(), 0)

    def test_a_guardian_who_owes_something_that_is_not_a_fee_invoice_is_not_chased_for_it(self):
        other = make_customer(customer_code="C-OPEN", name="عميل عادي", phone="01055555555")
        CustomerLedgerEntry.objects.create(customer=other, entry_date=days_ago(40), entry_type=CustomerLedgerEntryType.SALES_DUE, due_increase=D("500"), created_by=self.owner)
        services.build()  # a customer who pays for no student is not on the education dues screen and has no fee invoice: no fees message (the general one stays)
        self.assertEqual(self.fees().count(), 0)
        self.assertEqual(self.messages(MessageKind.DUE).count(), 1)

    def test_it_is_not_repeated_within_seven_days_of_being_sent(self):
        self.bill(days_ago(4))
        services.build()
        services.mark(self.fees().get(), OutboxStatus.SENT, self.owner)
        for ahead in (1, 3, 6):
            self.assertEqual(services.build(TODAY + timedelta(days=ahead)), 0, ahead)
        self.assertEqual(services.build(TODAY + timedelta(days=7)), 1)  # still due after a week: reminded again
        self.assertEqual(self.fees().count(), 2)

    def test_a_skipped_one_also_waits_the_seven_days(self):
        self.bill(days_ago(4))
        services.build()
        services.mark(self.fees().get(), OutboxStatus.SKIPPED, self.owner)
        self.assertEqual(services.build(), 0)
        self.assertEqual(services.build(TODAY + timedelta(days=1)), 0)
        self.assertEqual(services.build(TODAY + timedelta(days=7)), 1)

    def test_a_waiting_message_moves_to_today_with_todays_balance(self):
        invoice = self.bill(days_ago(4))[0]
        services.build()
        self.pay(invoice.customer, "300")
        services.build(TODAY + timedelta(days=1))
        message = self.fees().get()
        self.assertEqual(message.day, TODAY + timedelta(days=1))
        self.assertIn("600.00", message.text)
        self.assertNotIn("900.00", message.text)

    def test_a_guardian_with_no_number_gets_nothing(self):
        invoice = self.bill(days_ago(4))[0]
        invoice.customer.phone = ""
        invoice.customer.save()
        self.assertEqual(services.build(), 0)


class DueNotDoubledTests(Fees):
    def setUp(self):
        super().setUp()
        self.child("يوسف", "01001234501", self.fee_group(900))
        self.invoice = self.bill(days_ago(40))[0]  # past the 30 credit days: the general reminder would message this guardian

    def due(self):
        return self.messages(MessageKind.DUE)

    def test_without_the_fees_kind_the_general_reminder_messages_the_guardian_as_before(self):
        services.save_template(MessageKind.FEES, {"text": services.template(MessageKind.FEES).text, "every_days": "7"}, self.owner)  # off
        self.assertEqual(services.build(), 1)
        message = self.due().get()
        self.assertEqual((message.key, message.name), (f"due:customer:{self.invoice.customer_id}", "ولي أمر يوسف"))
        self.assertIn("900.00", message.text)
        self.assertEqual(self.fees().count(), 0)

    def test_with_the_fees_kind_on_only_the_fees_message_goes_and_a_waiting_due_one_is_withdrawn(self):
        services.save_template(MessageKind.FEES, {"text": services.template(MessageKind.FEES).text, "every_days": "7"}, self.owner)
        services.build()
        self.assertEqual(self.due().filter(status=OutboxStatus.PENDING).count(), 1)
        self.turn_on(MessageKind.FEES)
        services.build()
        self.assertEqual(self.due().filter(status=OutboxStatus.PENDING).count(), 0)  # the same money is not asked for twice
        self.assertEqual(self.fees().filter(status=OutboxStatus.PENDING).count(), 1)

    def test_a_guardian_the_fees_source_does_not_cover_still_gets_the_general_reminder(self):
        other = make_customer(customer_code="C-OPEN", name="عميل عادي", phone="01055555555")
        CustomerLedgerEntry.objects.create(customer=other, entry_date=days_ago(40), entry_type=CustomerLedgerEntryType.SALES_DUE, due_increase=D("500"), created_by=self.owner)
        services.build()
        self.assertEqual(sorted(self.due().values_list("name", flat=True)), ["عميل عادي"])
        self.assertEqual(self.fees().count(), 1)


class CommercialUntouchedTests(TestCase):
    """A business that is not a school: neither kind is listed or gathered, and its amount-due reminder is exactly what it was."""

    def setUp(self):
        prepared_client("commercial", "retail", "customers,items_services,sales_operations,cashboxes,reports")
        self.owner = person(RoleCode.OWNER, "e5_owner")
        self.customer = make_customer(customer_code="C-1", name="أحمد", phone="01001234567")
        CustomerLedgerEntry.objects.create(customer=self.customer, entry_date=days_ago(40), entry_type=CustomerLedgerEntryType.SALES_DUE, due_increase=D("750"), created_by=self.owner)

    def test_the_kinds_are_not_listed_and_nothing_is_queued_even_when_switched_on(self):
        for kind in (MessageKind.ABSENCE, MessageKind.FEES):
            services.save_template(kind, {"text": services.template(kind).text, "active": "1", "every_days": "1"}, self.owner)
        self.assertEqual(education_messages.fee_payers(TODAY), [])
        self.assertEqual(list(education_messages.absences(TODAY, "", "")), [])
        services.build()
        self.assertFalse(OutboxMessage.objects.filter(kind__in=(MessageKind.ABSENCE, MessageKind.FEES)).exists())
        kinds = [row.kind for row in services.templates()]
        self.assertNotIn(MessageKind.ABSENCE, kinds)
        self.assertNotIn(MessageKind.FEES, kinds)

    def test_the_general_reminder_is_unchanged_with_the_fees_kind_on(self):
        services.build()
        before = OutboxMessage.objects.get(kind=MessageKind.DUE)
        self.assertEqual((before.key, before.name, before.phone, before.link), (f"due:customer:{self.customer.pk}", "أحمد", "201001234567", f"/parties/customer/{self.customer.pk}/"))
        self.assertEqual(before.text, "أهلاً أحمد، ده تذكير ودّي من Demo Store بالمبلغ المستحق على حضرتك: 750.00 EGP. شكرًا لحضرتك.")
        services.save_template(MessageKind.FEES, {"text": services.template(MessageKind.FEES).text, "active": "1", "every_days": "7"}, self.owner)
        OutboxMessage.objects.all().delete()
        self.assertEqual(services.build(), 1)
        after = OutboxMessage.objects.get(kind=MessageKind.DUE)
        self.assertEqual((after.key, after.name, after.phone, after.link, after.text), (before.key, before.name, before.phone, before.link, before.text))

    def test_the_two_kinds_have_no_effect_on_a_clinic_either(self):
        OutboxMessage.objects.all().delete()
        Entity.objects.filter(pk=main_entity().pk).update(activity_slug="medical", sub_activity_slug="clinic")
        kinds = [row.kind for row in services.templates()]
        self.assertNotIn(MessageKind.ABSENCE, kinds)
        self.assertNotIn(MessageKind.FEES, kinds)


# ---- the wording, the screen and the schedule ----

class TemplateTests(Centre):
    def test_the_two_new_words_are_accepted_and_filled(self):
        row = services.save_template(MessageKind.ABSENCE, {"text": "أهلاً {name}، {student} غاب عن {group} يوم {date} — {company}", "active": "1", "every_days": "1"}, self.owner)
        self.assertIn("{student}", row.text)
        self.absent(self.a)
        services.build()
        text = self.messages(MessageKind.ABSENCE).get().text
        self.assertTrue(text.startswith("أهلاً ولي أمر آدم، آدم غاب عن مجموعة يومية يوم "))
        self.assertTrue(text.endswith("— Demo Store"))
        self.assertNotIn("{", text)
        self.assertIn("{student}", services.PLACEHOLDERS)
        self.assertIn("{group}", services.PLACEHOLDERS)

    def test_an_unknown_word_is_still_refused(self):
        for word in ("{grade}", "{note}", "{rate}", "{reason}", "{mark}", "{stage}"):
            with self.subTest(word=word), self.assertRaises(ValidationError):
                services.save_template(MessageKind.ABSENCE, {"text": f"أهلاً {word}", "active": "1", "every_days": "1"}, self.owner)

    def test_the_defaults_use_only_allowed_words_carry_no_link_and_are_labelled_in_both_languages(self):
        for kind in (MessageKind.ABSENCE, MessageKind.FEES):
            for text in (services.DEFAULT_TEXTS[kind], services.DEFAULT_TEXTS_EN[kind]):
                self.assertEqual([word for word in re.findall(r"\{[^}]*\}", text) if word not in services.PLACEHOLDERS], [])
                self.assertNotIn("{link}", text)
            self.assertNotIn(kind, services.LINK_KINDS)
            self.assertFalse(services.DEFAULT_TEXTS[kind].startswith("{"))
            self.assertFalse(re.match(r"[A-Za-z]", services.DEFAULT_TEXTS[kind]))  # an Arabic sentence never starts with a Latin word
        self.assertEqual(services.DEFAULT_TEXTS[MessageKind.ABSENCE], "أهلاً {name}، {student} كان غايب يوم {date} عن {group} في {company}.")
        self.assertEqual((services.DEFAULT_EVERY[MessageKind.ABSENCE], services.DEFAULT_EVERY[MessageKind.FEES]), (1, 7))
        self.assertEqual(set(services.KIND_LABELS["ar"]), set(MessageKind.values))
        self.assertEqual(set(services.KIND_LABELS["en"]), set(MessageKind.values))
        self.assertEqual(set(services.KIND_HINTS["ar"]), {"absence", "fees"})
        self.assertEqual(set(services.KIND_HINTS["en"]), {"absence", "fees"})
        self.assertTrue(len(MessageKind.ABSENCE.value) <= 12 and len(MessageKind.FEES.value) <= 12)

    def test_the_existing_kinds_keep_their_wording_and_switches(self):
        for kind in (MessageKind.DUE, MessageKind.INSTALMENT, MessageKind.APPOINTMENT, MessageKind.REFILL, MessageKind.WORKORDER):
            self.assertTrue(services.template(kind).active, kind)
        for kind in (MessageKind.FOLLOWUP, MessageKind.VACCINE, MessageKind.ABSENCE, MessageKind.FEES):
            self.assertFalse(services.template(kind).active, kind)

    def test_the_centre_sees_both_kinds_to_edit(self):
        kinds = [row.kind for row in services.templates()]
        self.assertIn(MessageKind.ABSENCE, kinds)
        self.assertIn(MessageKind.FEES, kinds)
        self.assertNotIn(MessageKind.CAMPAIGN, kinds)
        self.assertNotIn(MessageKind.VACCINE, kinds)
        self.assertNotIn(MessageKind.FOLLOWUP, kinds)


class ScreenTests(Centre):
    def test_the_outbox_lists_the_rows_and_the_settings_in_both_languages_with_the_cannot_take_back_note(self):
        self.turn_on(MessageKind.ABSENCE)
        self.absent(self.a)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("messaging:outbox") + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-kind="absence"')
        self.assertContains(page, 'data-template="absence"')
        self.assertContains(page, 'data-template="fees"')
        self.assertContains(page, "غياب طالب")
        self.assertContains(page, "مصروفات مستحقة")
        self.assertContains(page, "{student}")
        self.assertContains(page, "{group}")
        self.assertContains(page, 'data-kind-hint="absence"')
        self.assertContains(page, "مينفعش تترجّع")
        english = self.client.get(reverse("messaging:outbox") + "?lang=en")
        self.assertContains(english, "Student absence")
        self.assertContains(english, "Fees due")
        self.assertContains(english, "cannot be taken back")
        self.assertContains(english, "{student} the student")

    def test_the_outbox_edits_the_new_kinds(self):
        self.client.force_login(self.owner)
        posted = self.client.post(reverse("messaging:outbox") + "?lang=ar", {"lang": "ar", "action": "template", "kind": "absence", "text": "أهلاً {name}، {student} غاب عن {group}",
                                                                           "every_days": "1", "active": "1"})
        self.assertIn(posted.status_code, (200, 302))
        self.assertTrue(services.template(MessageKind.ABSENCE).active)
        refused = self.client.post(reverse("messaging:outbox") + "?lang=ar", {"lang": "ar", "action": "template", "kind": "fees", "text": "أهلاً {grade}", "every_days": "7", "active": "1"})
        self.assertEqual(refused.status_code, 200)
        self.assertFalse(services.template(MessageKind.FEES).active)

    def test_the_outbox_of_a_shop_has_no_education_wording(self):
        Entity.objects.filter(pk=main_entity().pk).update(activity_slug="commercial", sub_activity_slug="retail")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("messaging:outbox") + "?lang=ar")
        self.assertNotContains(page, 'data-template="absence"')
        self.assertNotContains(page, 'data-template="fees"')


class ScheduledRunTests(Centre):
    def setUp(self):
        super().setUp()
        self.turn_on(MessageKind.ABSENCE, wa_template="hesba_absence")
        self.absent(self.a)

    def test_without_keys_the_messages_only_wait_in_the_outbox(self):
        with mock.patch.dict(os.environ, {"WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": ""}), mock.patch.object(whatsapp, "_http") as http:
            result = services.run_scheduled(at(TODAY, 12))
        http.assert_not_called()
        self.assertEqual(result, {"status": "gathered", "added": 1, "sent": 0, "failed": 0, "left": 0})
        self.assertEqual(self.messages(MessageKind.ABSENCE).get().status, OutboxStatus.PENDING)  # ready for one tap

    def test_with_keys_it_sends_inside_the_hours_and_leaves_what_has_no_approved_template_waiting(self):
        self.turn_on(MessageKind.FEES)  # no wa_template: this kind is sent by a tap only
        self.child("يوسف", "01001234501", self.group("غ900", days="sat", start=time(9), room="ر9", teacher=False, fee="900"))
        billing.bill_month(billing.month_of(days_ago(4)), self.owner, post=True, invoice_date=days_ago(4))
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            result = services.run_scheduled(at(TODAY, 12))
        self.assertEqual((result["status"], result["added"], result["sent"], result["failed"]), ("sent", 2, 1, 0))
        self.assertEqual(self.messages(MessageKind.ABSENCE).get().status, OutboxStatus.SENT)
        self.assertEqual(self.messages(MessageKind.ABSENCE).get().channel, "api")
        self.assertEqual(self.messages(MessageKind.FEES).get().status, OutboxStatus.PENDING)  # waits for its tap
        self.assertEqual(len(graph.calls), 1)
        self.assertEqual(graph.calls[0]["to"], "201010000001")
        self.assertEqual(graph.calls[0]["template"]["name"], "hesba_absence")

    def test_it_never_sends_at_night_but_still_gathers(self):
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            for hour in (0, 3, services.SEND_FROM_HOUR - 1, services.SEND_UNTIL_HOUR, 23):
                result = services.run_scheduled(at(TODAY, hour))
                self.assertEqual((result["status"], result["sent"]), ("outside_hours", 0), hour)
        self.assertEqual(graph.calls, [])
        self.assertEqual(self.messages(MessageKind.ABSENCE).get().status, OutboxStatus.PENDING)
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            self.assertEqual(services.run_scheduled(at(TODAY, services.SEND_FROM_HOUR))["sent"], 1)
            self.assertEqual(services.run_scheduled(at(TODAY, services.SEND_FROM_HOUR + 1))["sent"], 0)  # never twice
        self.assertEqual(len(graph.calls), 1)


class NothingMovesTests(Fees):
    def test_gathering_and_sending_change_no_sale_payment_cashbox_ledger_or_journal_row(self):
        self.turn_on(MessageKind.ABSENCE, wa_template="hesba_absence")
        self.child("يوسف", "01001234501", self.fee_group(900))
        invoice = self.bill(days_ago(4))[0]
        self.pay(invoice.customer, "300")
        self.absent(self.a)
        before = footprint()
        invoices = SalesInvoice.objects.count()
        balance = [(row.customer_id, row.due_increase, row.due_decrease) for row in CustomerLedgerEntry.objects.order_by("pk")]
        self.assertEqual(services.build(), 2)
        services.build()
        for message in list(OutboxMessage.objects.filter(status=OutboxStatus.PENDING)):
            services.mark(message, OutboxStatus.SKIPPED if message.kind == MessageKind.FEES else OutboxStatus.SENT, self.owner)
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            services.run_scheduled(at(TODAY, 12))
        self.client.force_login(self.owner)
        self.client.get(reverse("messaging:outbox"))
        self.assertEqual(footprint(), before)
        self.assertEqual(SalesInvoice.objects.count(), invoices)
        self.assertEqual([(row.customer_id, row.due_increase, row.due_decrease) for row in CustomerLedgerEntry.objects.order_by("pk")], balance)

    def test_the_module_never_calls_posting_or_money_code(self):
        import inspect

        source = inspect.getsource(education_messages)
        for forbidden in ("post_sales_invoice", "record_customer_payment", "CashboxMovement", "JournalEntry", ".save(", ".delete(", ".create(", "bulk_create"):
            self.assertNotIn(forbidden, source)


# ---- the migration ----

class MigrationTests(SimpleTestCase):
    """0007 changes the choices of the two ``kind`` columns and nothing else (the same shape as 0006, HG-090)."""

    def setUp(self):
        self.module = importlib.import_module("messaging.migrations.0007_absence_fees_kinds")
        self.previous = importlib.import_module("messaging.migrations.0006_vaccine_kind")

    def test_it_follows_0006_and_has_only_two_alter_fields_on_kind(self):
        migration = self.module.Migration
        self.assertEqual(migration.dependencies, [("messaging", "0006_vaccine_kind")])
        self.assertEqual(len(migration.operations), 2)
        for operation in migration.operations:
            self.assertIsInstance(operation, migrations.AlterField)
            self.assertEqual(operation.name, "kind")
        self.assertEqual([op.model_name for op in migration.operations], ["messagetemplate", "outboxmessage"])
        self.assertFalse(any(isinstance(op, (migrations.CreateModel, migrations.AddField, migrations.RemoveField, migrations.RunPython, migrations.RunSQL))
                             for op in migration.operations))

    def test_the_only_change_is_two_more_choices_with_the_same_length_and_options(self):
        for before, after in zip(self.previous.Migration.operations, self.module.Migration.operations):
            old, new = before.field, after.field
            self.assertEqual((old.max_length, new.max_length), (12, 12))
            self.assertEqual((old.unique, old.db_index), (new.unique, new.db_index))
            self.assertEqual(len(new.choices), len(old.choices) + 2)
            self.assertEqual([c for c in new.choices if c[0] not in ("absence", "fees")], list(old.choices))
            self.assertIn(("absence", "Student absence"), new.choices)
            self.assertIn(("fees", "Fees due"), new.choices)
            self.assertEqual(list(new.choices), [(value, label) for value, label in MessageKind.choices])

    def test_the_kinds_fit_the_column(self):
        self.assertLessEqual(len(MessageKind.ABSENCE.value), 12)
        self.assertLessEqual(len(MessageKind.FEES.value), 12)


class MakeMigrationsCleanTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_the_models_and_the_migrations_agree(self):
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
