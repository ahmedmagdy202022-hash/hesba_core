"""E3 (HG-101): the guardian portal: a public page that shows minors' data, behind a signed link, a revocable row, a second factor and throttles.

The page is ``medical.public`` (HG-085) defence for defence, so these tests follow ``medical.tests_lab_link``: nothing is shown before the
factor, every failure looks the same, the factor is throttled and never reveals what is on file, a pass belongs to one link, and only a
manager can make or revoke one.

Hygiene (learned the hard way): the clock is frozen at a fixed Wednesday noon and every group meets every day, so no test depends on the
time of day or the weekday; the signing clock is moved separately where a signature or a cookie must expire; markers are long Arabic
phrases and dotted numbers a base64 token or a CSRF value can never contain, and every body is normalised (token, CSRF value) before an
"is not in" assertion; lists are asserted in an explicit order.
"""

import re
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from unittest import mock
from urllib.parse import unquote

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from entities.current import working_in
from entities.models import Entity
from inventory.models import StockMovement
from ledger.models import JournalEntry
from messaging.models import OutboxMessage
from payments import services as pay
from payments.models import LinkStatus, PaymentLink
from permissions.models import RoleCode
from sales.models import CustomerPayment, SalesInvoice
from sales.services import record_customer_payment
from settlements.services import settle

from . import attendance, billing, exams, paylinks, portal, services, views_portal
from .models import Attendance, DayRecord, GuardianLink, PickupPerson, Student
from .tests_paylinks import PayFeesSetup, person

FIXED = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.get_current_timezone())  # a Wednesday, at noon
EVERY_DAY = "sun,mon,tue,wed,thu,fri,sat"
PRIVATE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "same-origin"}

# Markers: long Arabic phrases and dotted numbers. None of them may ever be on a guardian's page.
NOTES = "ملاحظة سرية للمركز عن يوسف فقط"
SCHOOL = "مدرسة الأمل الدولية للغات"
STAGE = "الصف الثالث الإعدادي المتميز"
PAYER_PHONE = "01001234567"
PAYER_NAME = "نبيل الحداد الكبير"
RELATION = "قرابة سرية للمركز"
ESCORT = "مرافق سري صباحي"
PICKUP_ID = "29803150101234"
PICKUP_PHONE = "01112223334"
TEACHER_RATE = "987654.32"
ATT_NOTE = "سبب غياب داخلي سري رقم تسعمية"
SESSION_TOPIC = "موضوع حصة داخلي سري"
EXAM_NOTES = "ملاحظة امتحان داخلية سرية"
MARK_NOTE = "ملاحظة درجة داخلية سرية"
DAY_NOTE = "ملاحظة يوم الحضانة الداخلية"
ENROLL_NOTE = "ملاحظة تسجيل داخلية سرية"
HIDDEN_EXAM = "امتحان غير منشور سري جدًا"
STRANGER, STRANGER_PAYER = "ليلى الغريبة عن الأسرة", "ولي أمر الأسرة الغريبة"
BRANCH_CHILD = "ابن الفرع التاني للأسرة"
YOUSEF, MARIAM = "يوسف السيد الشاذلي", "مريم السيد الشاذلي"
GROUP_A, GROUP_B = "مجموعة الرياضيات المسائية", "مجموعة العلوم الصباحية"
TEACHER_A, TEACHER_B = "أ. منى الشاذلي الكبرى", "أ. هاني درويش الصغير"
PICKER = "الجد عبد الرحمن الفقي"
BORN_YOUSEF, BORN_MARIAM, BORN_STRANGER = date(2011, 3, 17), date(2014, 6, 2), date(2009, 9, 9)


def day(n):
    return FIXED.date() - timedelta(days=n)


def token_of(url):
    return unquote(url.split("/p/edu/", 1)[1].split("/", 1)[0])


def normalized(response, *tokens):
    """The body with what differs between two links by construction (the token, the CSRF value) taken out."""

    body = response.content.decode()
    for token in tokens:
        body = body.replace(token, "TOKEN")
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken" value="X"', body)


class PortalSetup(PayFeesSetup):
    """A tutoring centre on a frozen Wednesday: one family with two children, another family's child, fees of 600.00 and 450.00."""

    def setUp(self):
        cache.clear()
        clock = mock.patch("django.utils.timezone.now", return_value=FIXED)
        clock.start()
        self.addCleanup(clock.stop)
        self.real0 = time.time()
        super().setUp()
        from settings_core.models import SystemSetting
        from staff.services import save_employee

        SystemSetting.objects.update_or_create(key="company.phone", defaults={"value": "0225551234", "active": True})

        self.month = FIXED.date().replace(day=1)
        self.cashier = person(RoleCode.CASHIER, "e3_cashier")
        self.manager = person(RoleCode.MANAGER, "e3_manager")
        self.teacher_a = save_employee({"name": TEACHER_A}, self.owner)
        self.teacher_b = save_employee({"name": TEACHER_B}, self.owner)
        self.course_a = services.save_course({"name": "رياضيات الإعدادي", "fee": "600", "basis": "monthly"}, self.owner)
        self.course_b = services.save_course({"name": "علوم الإعدادي", "fee": "450", "basis": "monthly"}, self.owner)
        self.group_a = self.make_group(GROUP_A, self.course_a, self.teacher_a, EVERY_DAY, "monthly", TEACHER_RATE, "17:00", "قاعة سرية 9")
        self.group_b = self.make_group(GROUP_B, self.course_b, self.teacher_b, EVERY_DAY, "none", "0", "09:00", "قاعة العلوم")
        self.yousef = services.save_student({"name": YOUSEF, "payer_name": PAYER_NAME, "payer_phone": PAYER_PHONE, "date_of_birth": BORN_YOUSEF.isoformat(),
                                             "school": SCHOOL, "notes": NOTES, "stage": STAGE, "relation": RELATION}, self.owner)
        self.payer = self.yousef.payer
        self.mariam = services.save_student({"name": MARIAM, "payer": self.payer, "date_of_birth": BORN_MARIAM.isoformat(), "school": SCHOOL}, self.owner)
        self.stranger = services.save_student({"name": STRANGER, "payer_name": STRANGER_PAYER, "payer_phone": "01555666777", "date_of_birth": BORN_STRANGER.isoformat(),
                                               "school": SCHOOL, "notes": NOTES}, self.owner)
        services.enroll(self.yousef, self.group_a, self.owner, start_date=day(45), notes=ENROLL_NOTE)
        (self.inv600,) = billing.bill_month(self.month, self.owner, post=True)
        services.enroll(self.mariam, self.group_b, self.owner, start_date=day(30))
        (self.inv450,) = billing.bill_month(self.month, self.owner, post=True)
        services.enroll(self.stranger, self.group_a, self.owner, start_date=day(30))
        (self.inv_stranger,) = billing.bill_month(self.month, self.owner, post=True)
        for invoice in (self.inv600, self.inv450, self.inv_stranger):
            invoice.refresh_from_db()
        self.client.logout()

    # ---- builders ----

    def make_group(self, name, course, teacher, days, pay, rate, start, room):
        return services.save_group({"course": course, "name": name, "teacher": teacher, "days": days, "start_time": start, "duration_minutes": "90", "capacity": "0",
                                    "starts_on": day(60), "room": room, "teacher_pay": pay, "teacher_rate": rate}, self.owner)

    def hold_classes(self):
        """Yousef: classes on days 1 to 10 ago of group A; absent on 3 and 7 (the second with a reason), late on 5; a cancelled class 12 days
        ago and an absence 40 days ago, both outside what the page counts. The other family's child is absent every day."""

        for n in range(10, 0, -1):
            mark = "absent" if n in (3, 7) else "late" if n == 5 else "present"
            extra = {f"note_{self.yousef.pk}": ATT_NOTE} if n == 7 else {}
            attendance.take(self.group_a, self.owner, day(n), {str(self.yousef.pk): mark, str(self.stranger.pk): "absent", **extra}, topic=SESSION_TOPIC)
        attendance.take(self.group_a, self.owner, day(12), {}, cancelled=True)
        attendance.take(self.group_a, self.owner, day(40), {str(self.yousef.pk): "absent"})

    def hold_mariam(self, count=4):
        for n in range(count, 0, -1):
            attendance.take(self.group_b, self.owner, day(n), {})

    def exam(self, group, title, kind, ago, out_of, marks, published=True, notes=""):
        exam = exams.save_exam(group, {"title": title, "kind": kind, "held_on": day(ago), "out_of": out_of, "notes": notes}, self.owner)
        exams.record_marks(exam, marks, self.owner)
        if published:
            exams.set_published(exam, True, self.owner)
        return exam

    def marks_for_yousef(self):
        self.hold_classes()
        self.final = self.exam(self.group_a, "الامتحان النهائي للرياضيات", "final", 5, "20",
                               {self.yousef.pk: {"score": "17", "note": MARK_NOTE}, self.stranger.pk: {"score": "5.5"}}, notes=EXAM_NOTES)
        self.monthly = self.exam(self.group_a, "امتحان الشهر للرياضيات", "monthly", 9, "10", {self.yousef.pk: {"score": "7.5"}})
        self.quiz = self.exam(self.group_a, "اختبار قصير للرياضيات", "quiz", 8, "10", {self.yousef.pk: {"absent": True}})
        self.hidden = self.exam(self.group_a, HIDDEN_EXAM, "midterm", 3, "20", {self.yousef.pk: {"score": "11.25"}}, published=False)
        self.pending = self.exam(self.group_a, "اختبار لسه ماتصححش", "quiz", 2, "10", {self.stranger.pk: {"score": "4"}})  # published, but nothing entered for Yousef

    def nursery_day(self):
        person_ = PickupPerson.objects.create(student=self.mariam, name=PICKER, relation="جد", phone=PICKUP_PHONE, id_number=PICKUP_ID, created_by=self.owner)
        DayRecord.objects.create(student=self.mariam, day=day(1), arrived_at=timezone.make_aware(datetime.combine(day(1), datetime.min.time().replace(hour=7, minute=50))),
                                 left_at=timezone.make_aware(datetime.combine(day(1), datetime.min.time().replace(hour=13, minute=10))), picked_by=person_, note=DAY_NOTE,
                                 arrived_with=ESCORT, checked_in_by=self.owner, checked_out_by=self.owner)
        return person_

    def pay_desk(self, invoice, amount, settled=False):
        """Money received at the desk on the payer's account, dated on the frozen day (a plain payment lowers the account; ``settled`` ties it to the invoice)."""

        self.count += 1
        payment = record_customer_payment(f"CP-E3-{self.count}", FIXED.date(), invoice.customer, self.cash, D(amount), self.owner)
        if settled:
            settle(invoice, payment, D(amount), "deposit")
        return payment

    def make(self, student=None, lang="ar", user=None):
        return portal.make_link(student or self.yousef, user or self.owner, lang)

    def token(self, student=None):
        return token_of(self.make(student)["url"])

    def url(self, token, lang="ar"):
        return f"{reverse('guardian_portal', args=[token])}?lang={lang}"

    def anon(self, **extra):
        return Client(**extra)

    def post(self, client, token, answer, lang="ar"):
        return client.post(self.url(token, lang), {"answer": answer, "lang": lang})

    def open_portal(self, student=None, answer="2011", lang="ar", client=None):
        """(client, token, the portal page) of a fresh link, the factor answered."""

        client = client or self.anon()
        token = self.token(student)
        self.assertEqual(self.post(client, token, answer, lang).status_code, 303)
        return client, token, client.get(self.url(token, lang))

    @contextmanager
    def clock(self, **delta):
        """Move the page's clock and the signature's clock together."""

        shift = timedelta(**delta)
        with mock.patch("django.utils.timezone.now", return_value=FIXED + shift), mock.patch("django.core.signing.time.time", return_value=self.real0 + shift.total_seconds()):
            yield

    def assertPrivate(self, response):
        for header, value in PRIVATE.items():
            self.assertEqual(response[header], value, header)

    def assertDead(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "data-portal-dead", status_code=404)
        self.assertNotContains(response, "data-portal-ask", status_code=404)
        self.assertPrivate(response)

    def counts(self):
        return {"invoices": SalesInvoice.objects.count(), "payments": CustomerPayment.objects.count(), "cashbox": CashboxMovement.objects.count(),
                "journal": JournalEntry.objects.count(), "stock": StockMovement.objects.count(), "outbox": OutboxMessage.objects.count(),
                "links": list(PaymentLink.objects.order_by("pk").values_list("pk", "status", "amount")), "attendance": Attendance.objects.count(),
                "students": Student.objects.count(), "exams": exams.Exam.objects.count(), "marks": exams.ExamMark.objects.count()}


# ---- the key: the table, the token and the factor ----

class TokenTests(PortalSetup):
    def test_a_made_link_is_a_row_and_a_signed_address_from_the_public_base_url(self):
        made = self.make()
        link = made["link"]
        self.assertTrue(made["url"].startswith("https://pay.example.test/p/edu/"))
        self.assertTrue(made["url"].endswith("?lang=ar"))
        self.assertEqual((link.payer, link.entity.is_main, link.created_by, link.revoked_at, link.last_opened_at), (self.payer, True, self.owner, None, None))
        self.assertGreaterEqual(len(link.nonce), 32)
        data = signing.loads(token_of(made["url"]), salt=portal.SALT)
        self.assertEqual((data["l"], data["n"]), (link.pk, link.nonce))
        self.assertEqual(portal.days(), 30)
        self.assertEqual(portal.expires_at(link), FIXED + timedelta(days=30))

    def test_a_valid_link_shows_only_the_centre_and_the_question(self):
        self.marks_for_yousef()
        self.nursery_day()
        token = self.token()
        response = self.anon().get(self.url(token))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-guardian-portal="ask"')
        self.assertContains(response, 'data-portal-factor="year"')
        self.assertContains(response, "Demo Store")
        self.assertContains(response, "سنة الميلاد")
        self.assertNotContains(response, "data-section")
        body = normalized(response, token)
        for secret in (YOUSEF, MARIAM, STRANGER, GROUP_A, GROUP_B, TEACHER_A, "2011", "2014", "2009", PAYER_PHONE, PAYER_NAME, "رياضيات", "17"):
            self.assertNotIn(secret, body)
        self.assertPrivate(response)
        self.assertFalse(AuditLog.objects.filter(action="open_guardian_portal").exists())

    def test_every_dead_link_shows_the_same_page_with_the_same_status(self):
        made = self.make()
        token, link = token_of(made["url"]), made["link"]
        other = self.make(self.stranger)
        other_token, other_link = token_of(other["url"]), other["link"]
        from medical import public as lab
        from printing.share import make_token as share_token

        from . import certificates

        signed = lambda **data: signing.dumps({"l": link.pk, "n": link.nonce, "f": portal.fingerprint(self.payer), **data}, salt=portal.SALT, compress=True)  # noqa: E731
        failures = {
            "forged": token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb"),
            "garbage": "not-a-token",
            "the lab's salt": signing.dumps({"l": link.pk, "n": link.nonce, "f": portal.fingerprint(self.payer)}, salt=lab.SALT),
            "the certificate's salt": signing.dumps({"l": link.pk, "n": link.nonce, "f": portal.fingerprint(self.payer)}, salt=certificates.SALT),
            "the share salt": share_token("sales_invoice", self.inv600.pk),
            "another payer's nonce": signed(n=other_link.nonce),
            "another payer's id": signed(l=other_link.pk),
            "a wrong fingerprint": signed(f="0" * 32),
            "no such link": signed(l=999999),
            "not numbers": signed(l="1"),
            "a list": signing.dumps(["x"], salt=portal.SALT),
        }
        reference = None
        for label, bad in failures.items():
            with self.subTest(label):
                for method in ("get", "post"):
                    client = self.anon()
                    response = client.get(self.url(bad)) if method == "get" else self.post(client, bad, "2011")
                    self.assertDead(response)
                    body = normalized(response, bad)
                    reference = reference or body
                    self.assertEqual(body, reference)
        self.assertEqual(self.anon().get(self.url(other_token)).status_code, 200)  # the other payer's own link is fine
        self.assertFalse(AuditLog.objects.filter(action="open_guardian_portal").exists())

    def test_revoked_and_replaced_links_fail_like_a_forged_one(self):
        first = self.make()
        old = token_of(first["url"])
        client = self.anon()
        self.assertEqual(self.post(client, old, "2011").status_code, 303)
        self.assertContains(client.get(self.url(old)), 'data-guardian-portal="sheet"')
        second = self.make()  # «ابعت تاني»: the old one stops, even for a browser that had passed
        new = token_of(second["url"])
        self.assertNotEqual(old, new)
        forged = self.anon().get(self.url("forged-token-xyz"))
        self.assertDead(client.get(self.url(old)))
        self.assertDead(self.post(self.anon(), old, "2011"))
        self.assertEqual(normalized(client.get(self.url(old)), old), normalized(forged, "forged-token-xyz"))
        self.assertEqual(self.post(self.anon(), new, "2011").status_code, 303)
        first["link"].refresh_from_db()
        self.assertIsNotNone(first["link"].revoked_at)
        portal.revoke(second["link"], self.owner)
        self.assertDead(self.anon().get(self.url(new)))
        self.assertEqual(normalized(self.anon().get(self.url(new)), new), normalized(forged, "forged-token-xyz"))
        self.assertEqual(GuardianLink.objects.filter(payer=self.payer).count(), 2)  # rows are kept, so the audit still names them

    def test_the_database_allows_one_live_link_per_payer_and_a_new_one_revokes_the_old(self):
        first = self.make()["link"]
        with self.assertRaises(IntegrityError), transaction.atomic():
            GuardianLink.objects.create(payer=self.payer, entity=first.entity, created_by=self.owner, nonce="another-nonce-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        second = self.make()["link"]
        self.assertEqual(list(GuardianLink.objects.filter(payer=self.payer, revoked_at__isnull=True)), [second])
        self.make(self.stranger)  # another payer's link is untouched
        self.assertEqual(GuardianLink.objects.filter(revoked_at__isnull=True).count(), 2)
        self.make(self.mariam)  # a sibling's student file makes the same payer's link
        self.assertEqual(GuardianLink.objects.filter(payer=self.payer, revoked_at__isnull=True).count(), 1)

    def test_an_expired_link_fails_by_the_signature_and_by_the_row(self):
        token = self.token()
        with self.clock(days=29):
            self.assertEqual(self.anon().get(self.url(token)).status_code, 200)
        with self.clock(days=31):  # both clocks moved
            self.assertDead(self.anon().get(self.url(token)))
        with mock.patch("django.core.signing.time.time", return_value=self.real0 + 31 * 86400):  # only the signature says so
            self.assertDead(self.anon().get(self.url(token)))
        with mock.patch("django.utils.timezone.now", return_value=FIXED + timedelta(days=31)):  # only the row says so (a re-signed token would not extend it)
            self.assertDead(self.anon().get(self.url(token)))
        with override_settings(EDU_PORTAL_DAYS=1), self.clock(days=2):
            self.assertDead(self.anon().get(self.url(token)))
        client = self.anon()
        self.post(client, token, "2011")
        with self.clock(days=31):
            self.assertDead(client.get(self.url(token)))  # a passed browser does not outlive the link either

    def test_a_changed_payer_phone_or_an_archived_payer_kills_the_link(self):
        for label, change in (("phone", lambda: type(self.payer).objects.filter(pk=self.payer.pk).update(phone="01009998887")),
                              ("whatsapp", lambda: type(self.payer).objects.filter(pk=self.payer.pk).update(whatsapp="01009998887")),
                              ("archived", lambda: type(self.payer).objects.filter(pk=self.payer.pk).update(active=False))):
            with self.subTest(label):
                type(self.payer).objects.filter(pk=self.payer.pk).update(phone=PAYER_PHONE, whatsapp="", active=True)
                self.payer.refresh_from_db()
                token = self.token()
                client = self.anon()
                self.post(client, token, "2011")
                self.assertContains(client.get(self.url(token)), 'data-guardian-portal="sheet"')
                change()
                self.assertDead(client.get(self.url(token)))
                self.assertDead(self.post(self.anon(), token, "2011"))

    def test_a_child_moved_to_another_payer_disappears_and_an_empty_portal_is_dead(self):
        token = self.token()
        client = self.anon()
        self.post(client, token, "2011")
        Student.objects.filter(pk=self.mariam.pk).update(payer=self.stranger.payer)  # custody changed
        page = client.get(self.url(token))
        self.assertContains(page, YOUSEF)
        self.assertNotContains(page, MARIAM)
        self.assertEqual(self.post(self.anon(), token, "2014").status_code, 400)  # her birth year no longer opens it
        Student.objects.filter(pk=self.yousef.pk).update(active=False)  # nobody left to show
        self.assertDead(client.get(self.url(token)))
        self.assertDead(self.post(self.anon(), token, "2011"))

    def test_the_links_of_another_entity_show_that_entitys_children_only(self):
        branch = Entity.objects.create(code="E-E3", name_ar="فرع الاختبار", activity_slug="education", sub_activity_slug="tutoring_center")
        with working_in(branch):
            course = services.save_course({"name": "كورس الفرع", "fee": "300", "basis": "monthly"}, self.owner)
            group = services.save_group({"course": course, "name": "مجموعة الفرع", "days": EVERY_DAY, "starts_on": day(60)}, self.owner)
            child = services.save_student({"name": BRANCH_CHILD, "payer": self.payer, "date_of_birth": "2016-01-05"}, self.owner)
            services.enroll(child, group, self.owner, start_date=day(30))
        self.assertEqual(child.entity, branch)
        main = self.make(self.yousef)
        main_link = main["link"]
        self.assertTrue(main_link.entity.is_main)
        client = self.anon()
        token = token_of(main["url"])
        self.assertEqual(self.post(client, token, "2016").status_code, 400)  # the branch child's year is no factor on the main link
        self.assertEqual(self.post(client, token, "2011").status_code, 303)
        page = client.get(self.url(token))
        self.assertContains(page, YOUSEF)
        self.assertNotContains(page, BRANCH_CHILD)
        branch_made = self.make(child)  # a new link for the payer, in the branch: the main one is revoked
        main_link.refresh_from_db()
        self.assertIsNotNone(main_link.revoked_at)
        self.assertEqual(branch_made["link"].entity, branch)
        branch_token = token_of(branch_made["url"])
        other = self.anon()
        self.assertEqual(self.post(other, branch_token, "2011").status_code, 400)  # the main children's years are no factor on the branch link
        self.assertEqual(self.post(other, branch_token, "2016").status_code, 303)
        shown = other.get(self.url(branch_token))
        self.assertContains(shown, BRANCH_CHILD)
        for name in (YOUSEF, MARIAM, GROUP_A, "كورس الفرع x"):
            self.assertNotContains(shown, name)
        self.assertContains(shown, "مجموعة الفرع")
        # a link whose entity holds no child of the payer is the one plain page
        GuardianLink.objects.filter(pk=branch_made["link"].pk).update(entity=Entity.objects.get(is_main=True))
        self.assertContains(self.anon().get(self.url(branch_token)), 'data-guardian-portal="ask"')  # main entity: Yousef and Mariam are there
        Student.objects.filter(payer=self.payer, entity__isnull=True).update(active=False)
        Student.objects.filter(payer=self.payer, entity=Entity.objects.get(is_main=True)).update(active=False)
        self.assertDead(self.anon().get(self.url(branch_token)))


class FactorTests(PortalSetup):
    def test_any_childs_birth_year_opens_it_and_nothing_else_does(self):
        token = self.token()
        ask = self.anon().get(self.url(token))
        self.assertContains(ask, 'data-portal-factor="year"')
        self.assertNotContains(ask, "آخر 4 أرقام")  # one question, no «or»
        for answer in ("2011", "2014", "٢٠١٤"):  # either child's year, Arabic digits read
            client = self.anon()
            response = self.post(client, token, answer)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response["Location"], self.url(token))
            self.assertPrivate(response)
            self.assertNotIn("sessionid", response.cookies)
        for answer in ("2009", "2012", "4567", "0000", "1234"):  # another family's child's year, a stranger's guess, the phone's last 4
            client = self.anon()
            response = self.post(client, token, answer)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "data-portal-message", status_code=400)
            self.assertNotIn(portal.cookie_name(portal.read_token(token)), response.cookies)
            self.assertContains(client.get(self.url(token)), 'data-guardian-portal="ask"')

    def test_wrong_answers_look_the_same_whoever_the_family(self):
        bodies = []
        for student, secret in ((self.yousef, MARIAM), (self.stranger, YOUSEF)):
            client = self.anon()
            token = self.token(student)
            response = self.post(client, token, "1000")
            self.assertEqual(response.status_code, 400)
            body = normalized(response, token)
            for hidden in (YOUSEF, MARIAM, STRANGER, "2011", "2014", "2009", secret):
                self.assertNotIn(hidden, body)
            bodies.append(body)
        self.assertEqual(bodies[0], bodies[1])

    def test_with_no_birth_date_on_any_child_the_last_four_digits_of_the_phone_are_the_factor(self):
        self.stranger.date_of_birth = None
        self.stranger.save()
        token = self.token(self.stranger)
        ask = self.anon().get(self.url(token))
        self.assertContains(ask, 'data-portal-factor="phone"')
        self.assertContains(ask, "آخر 4 أرقام")
        self.assertNotContains(ask, "سنة الميلاد")
        self.assertEqual(self.post(self.anon(), token, "2009").status_code, 400)  # a birth year is no factor now
        self.assertEqual(self.post(self.anon(), token, "6777").status_code, 303)
        self.assertEqual(self.post(self.anon(), token, "٦٧٧٧").status_code, 303)
        self.assertEqual(portal.factor(self.stranger.payer, [self.stranger]), ("phone", ["6777"]))

    def test_a_child_with_a_birth_date_among_children_without_one_makes_the_years_the_only_factor(self):
        self.mariam.date_of_birth = None
        self.mariam.save()
        token = self.token()
        self.assertEqual(self.post(self.anon(), token, "4567").status_code, 400)  # the phone is the weaker factor: not offered while a year exists
        self.assertEqual(self.post(self.anon(), token, "2014").status_code, 400)
        self.assertEqual(self.post(self.anon(), token, "2011").status_code, 303)

    def test_with_neither_factor_no_link_is_made(self):
        self.stranger.date_of_birth = None
        self.stranger.save()
        type(self.stranger.payer).objects.filter(pk=self.stranger.payer.pk).update(phone="123")  # too short to be a number
        with self.assertRaisesMessage(ValidationError, "مفيش للأبناء تاريخ ميلاد"):
            self.make(self.stranger)
        self.assertFalse(GuardianLink.objects.filter(payer=self.stranger.payer).exists())
        self.assertFalse(AuditLog.objects.filter(action="create_guardian_link").exists())
        self.assertEqual(portal.preview(Student.objects.get(pk=self.stranger.pk))["kind"], None)

    def test_a_badly_typed_answer_is_not_counted(self):
        client = self.anon()
        token = self.token()
        for answer in ("", "12", "12345", "abcd"):
            response = self.post(client, token, answer)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "4 أرقام", status_code=400)
        for _ in range(portal.TOKEN_SHORT[1]):  # all five real tries are still there
            self.assertEqual(self.post(client, token, "0000").status_code, 400)
        self.assertEqual(self.post(client, token, "2011").status_code, 429)

    def test_the_comparison_is_constant_time_and_checks_every_year(self):
        found = portal.read_token(self.token())
        with mock.patch.object(portal.hmac, "compare_digest", wraps=portal.hmac.compare_digest) as spy:
            self.assertTrue(portal._matches(found, "2011"))  # the first year matches, the second is still compared
        self.assertEqual(spy.call_count, 2)


# ---- throttles ----

class ThrottleTests(PortalSetup):
    def test_the_limits_are_the_plans(self):
        self.assertEqual((portal.TOKEN_LIFE[1], portal.TOKEN_SHORT[1:], portal.ADDRESS_HOUR[1:], portal.PAGE_MINUTE[1:]), (8, (5, 900), (50, 3600), (60, 60)))
        self.assertEqual(portal.PASS_SECONDS, 30 * 60)

    def test_five_wrong_answers_in_fifteen_minutes_lock_the_link_even_for_the_right_one(self):
        client = self.anon()
        token = self.token()
        for _ in range(portal.TOKEN_SHORT[1]):
            self.assertEqual(self.post(client, token, "0000").status_code, 400)
        locked = self.post(client, token, "2011")
        self.assertEqual(locked.status_code, 429)
        self.assertContains(locked, "محاولات كتير", status_code=429)
        self.assertPrivate(locked)
        self.assertEqual(self.post(self.anon(REMOTE_ADDR="10.9.9.9"), token, "2011").status_code, 429)  # per link, whatever the address
        self.assertEqual(self.post(client, self.token(self.stranger), "2009").status_code, 303)  # another link from the same address still works
        with self.clock(seconds=portal.TOKEN_SHORT[2] + 1):  # the window passed
            self.assertEqual(self.post(client, token, "2011").status_code, 303)

    def _spend(self, token, wrong, start_seconds=0):
        """``wrong`` wrong answers spread over fifteen-minute windows so the short bucket never refuses them; the clock the last one ran at."""

        done, window = 0, 0
        while done < wrong:
            with self.clock(seconds=start_seconds + window * portal.TOKEN_SHORT[2]):
                for _ in range(min(portal.TOKEN_SHORT[1], wrong - done)):
                    self.assertEqual(self.post(self.anon(), token, "0000").status_code, 400)
                    done += 1
            window += 1
        return start_seconds + window * portal.TOKEN_SHORT[2]

    def test_a_link_has_eight_wrong_answers_in_its_whole_life_then_it_is_dead(self):
        token = self.token()
        passed = self.anon()
        self.assertEqual(self.post(passed, token, "2011").status_code, 303)  # a right answer is not charged
        later = self._spend(token, portal.TOKEN_LIFE[1])
        with self.clock(seconds=later):
            self.assertDead(self.post(self.anon(), token, "2011"))  # even the right answer, from anywhere
            self.assertDead(self.post(self.anon(REMOTE_ADDR="10.7.7.7"), token, "2011"))
            self.assertDead(self.anon().get(self.url(token)))  # no question left to ask
            self.assertContains(passed.get(self.url(token)), 'data-guardian-portal="sheet"')  # a browser that passed keeps its 30 minutes
            self.assertDead(self.post(passed, token, "2011"))  # but makes no new answer
            self.assertEqual(normalized(self.anon().get(self.url(token)), token), normalized(self.anon().get(self.url("forged-xyz")), "forged-xyz"))
            self.assertEqual(self.post(self.anon(), self.token(), "2011").status_code, 303)  # «ابعت تاني»: a new link, clean counters

    def test_a_re_signed_token_of_the_same_link_shares_its_counters_and_its_pass(self):
        made = self.make()
        link = made["link"]
        with mock.patch("django.core.signing.time.time", return_value=self.real0 + 5):  # signed again a few seconds later: another text, the same link
            again = signing.dumps({"l": link.pk, "n": link.nonce, "f": portal.fingerprint(self.payer)}, salt=portal.SALT, compress=True)
        first = token_of(made["url"])
        self.assertNotEqual(first, again)
        for _ in range(portal.TOKEN_SHORT[1]):
            self.assertEqual(self.post(self.anon(), first, "0000").status_code, 400)
        self.assertEqual(self.post(self.anon(), again, "2011").status_code, 429)  # the counters belong to the link, not to the token's text

    def test_the_address_limit_counts_wrong_answers_from_one_address(self):
        with mock.patch.object(portal, "ADDRESS_HOUR", ("ip", 3, 3600)):
            client = self.anon(REMOTE_ADDR="10.0.0.7")
            for student in (self.yousef, self.stranger):
                self.assertEqual(self.post(client, self.token(student), "0000").status_code, 400)
            self.assertEqual(self.post(client, self.token(self.yousef), "0000").status_code, 400)
            fresh = self.token(self.yousef)
            self.assertEqual(self.post(client, fresh, "2011").status_code, 429)
            self.assertEqual(self.post(self.anon(REMOTE_ADDR="10.0.0.8"), fresh, "2011").status_code, 303)

    def test_sixty_page_loads_a_minute_then_the_plain_page_with_429(self):
        token = self.token()
        client = self.anon(REMOTE_ADDR="10.1.1.1")
        for _ in range(portal.PAGE_MINUTE[1]):
            self.assertEqual(client.get(self.url(token)).status_code, 200)
        response = client.get(self.url(token))
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "data-portal-dead", status_code=429)
        self.assertPrivate(response)
        self.assertEqual(self.anon(REMOTE_ADDR="10.1.1.2").get(self.url(token)).status_code, 200)  # another address is not affected
        with self.clock(seconds=61):
            self.assertEqual(client.get(self.url(token)).status_code, 200)

    def test_answers_take_their_slot_before_they_are_compared(self):
        request = RequestFactory().post("/", REMOTE_ADDR="10.3.3.3")
        found = portal.read_token(self.token())
        outcomes = [portal.take(found, request) for _ in range(12)]  # twelve answers that all reach the counting step before any compares
        self.assertEqual(outcomes.count("ok"), portal.TOKEN_SHORT[1])
        self.assertEqual(outcomes.count("busy"), 12 - portal.TOKEN_SHORT[1])
        self.assertEqual(cache.get(portal._key(portal.TOKEN_LIFE, portal.ident(found.link))), portal.TOKEN_SHORT[1])  # refused ones gave theirs back
        cache.clear()
        results, barrier = [], threading.Barrier(20, timeout=20)

        def answer():
            barrier.wait()
            results.append(portal.take(found, request))

        with mock.patch.object(portal, "TOKEN_SHORT", ("token15", 1000, 900)):
            threads = [threading.Thread(target=answer) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(results.count("ok"), portal.TOKEN_LIFE[1])  # twenty at once: eight compared, no more
        self.assertEqual(results.count("dead"), 20 - portal.TOKEN_LIFE[1])
        self.assertTrue(portal.used_up(found))
        cache.clear()
        seen, real = [], portal._matches

        def spy(found_, answer_):
            seen.append(cache.get(portal._key(portal.TOKEN_LIFE, portal.ident(found.link))))
            return real(found_, answer_)

        fresh = self.token()  # made now: its slot counters are the ones the spy reads
        found = portal.read_token(fresh)
        with mock.patch.object(portal, "_matches", side_effect=spy):
            self.assertEqual(self.post(self.anon(), fresh, "0000").status_code, 400)
        self.assertEqual(seen, [1])  # the view compares only after its slot is taken
        cache.clear()
        self.assertEqual(portal.take(found, request), "ok")
        portal.give_back(found, request)
        self.assertEqual(cache.get(portal._key(portal.TOKEN_LIFE, portal.ident(found.link))), 0)  # a right answer gives its slot back


# ---- the pass cookie ----

class CookieTests(PortalSetup):
    def test_without_the_pass_the_content_is_never_drawn_and_a_pass_has_the_right_attributes(self):
        client = self.anon()
        token = self.token()
        self.assertContains(client.get(self.url(token)), 'data-guardian-portal="ask"')
        response = self.post(client, token, "2011")
        found = portal.read_token(token)
        cookie = response.cookies[portal.cookie_name(found)]
        self.assertEqual(cookie["path"], reverse("guardian_portal", args=[token]))
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(int(cookie["max-age"]), 30 * 60)
        page = client.get(self.url(token))
        self.assertContains(page, 'data-guardian-portal="sheet"')
        self.assertPrivate(page)

    def test_the_pass_expires_after_thirty_minutes(self):
        client, token, page = self.open_portal()
        self.assertContains(page, 'data-guardian-portal="sheet"')
        with self.clock(minutes=29):
            self.assertContains(client.get(self.url(token)), 'data-guardian-portal="sheet"')
        with self.clock(minutes=31):
            self.assertContains(client.get(self.url(token)), 'data-guardian-portal="ask"')

    def test_the_pass_of_one_link_opens_no_other_link(self):
        client = self.anon()
        first = self.token(self.yousef)
        second = self.token(self.stranger)
        self.post(client, first, "2011")
        self.assertContains(client.get(self.url(first)), 'data-guardian-portal="sheet"')
        response = client.get(self.url(second))
        self.assertContains(response, 'data-guardian-portal="ask"')
        self.assertNotContains(response, STRANGER)
        one, two = portal.read_token(first), portal.read_token(second)
        value = client.cookies[portal.cookie_name(one)].value
        client.cookies[portal.cookie_name(two)] = value  # link one's signed value under link two's cookie name
        self.assertContains(client.get(self.url(second)), 'data-guardian-portal="ask"')
        name = portal.cookie_name(two)  # properly signed under link two's own name, but holding link one's identity
        client.cookies[name] = signing.get_cookie_signer(salt=name + portal.PASS_SALT).sign(portal.ident(one.link))
        self.assertContains(client.get(self.url(second)), 'data-guardian-portal="ask"')
        client.cookies[name] = signing.get_cookie_signer(salt=name + portal.PASS_SALT).sign(portal.ident(two.link))
        self.assertContains(client.get(self.url(second)), 'data-guardian-portal="sheet"')  # the control: the right identity opens it
        fresh = self.anon()
        fresh.cookies[portal.cookie_name(one)] = portal.ident(one.link)  # unsigned: opens nothing
        self.assertContains(fresh.get(self.url(first)), 'data-guardian-portal="ask"')

    def test_a_revoked_link_opens_nothing_even_with_a_valid_pass(self):
        client, token, _ = self.open_portal()
        portal.revoke(portal.read_token(token).link, self.owner)
        self.assertDead(client.get(self.url(token)))

    def test_a_staff_login_neither_opens_the_page_nor_is_touched_by_it(self):
        token = self.token()
        staff = self.client
        staff.force_login(self.owner)
        before = (staff.session.session_key, staff.session.get("_auth_user_id"))
        self.assertContains(staff.get(self.url(token)), 'data-guardian-portal="ask"')
        self.post(staff, token, "2011")
        self.assertContains(staff.get(self.url(token)), 'data-guardian-portal="sheet"')
        self.assertEqual((staff.session.session_key, staff.session.get("_auth_user_id")), before)
        self.assertEqual(staff.get(reverse("education:student", args=[self.yousef.pk])).status_code, 200)  # still signed in
        self.assertIsNone(AuditLog.objects.filter(action="open_guardian_portal").latest("pk").actor)  # not filed under the staff member


# ---- headers, methods, CSRF ----

class HeaderTests(PortalSetup):
    def test_every_response_carries_the_private_headers_and_failures_share_a_status(self):
        token = self.token()
        client = self.anon()
        responses = [client.get(self.url(token)), self.post(client, token, "0000"), self.post(client, token, "bad"), client.put(self.url(token)),
                     client.get(self.url("forged")), self.post(client, "forged", "2011"), client.delete(self.url("forged")), client.get(self.url(token, "en")),
                     self.post(self.anon(), token, "2011"), client.get(self.url(token))]
        for response in responses:
            self.assertPrivate(response)
        statuses = [response.status_code for response in responses]
        self.assertEqual(statuses, [200, 400, 400, 405, 404, 404, 405, 200, 303, 200])
        with mock.patch.object(portal, "PAGE_MINUTE", ("page", 1, 60)):
            client.get(self.url(token))
            self.assertPrivate(client.get(self.url(token)))  # even the flood page
        self.assertPrivate(self.post(self.anon(), token, "2011"))
        for _ in range(portal.TOKEN_SHORT[1]):
            self.assertPrivate(self.post(client, token, "0000"))
        self.assertPrivate(self.post(client, token, "0000"))  # a locked link

    def test_the_pages_embed_nothing_from_another_site_and_set_no_cookie_but_the_pass(self):
        client, token, page = self.open_portal()
        for response in (page, client.get(self.url("forged")), self.anon().get(self.url(token))):
            body = response.content.decode()
            self.assertEqual(re.findall(r'<(?:script|link|img|iframe|source|video|audio)\b[^>]*(?:src|href)="(?:https?:)?//', body), [])
        token = self.token(self.stranger)
        response = self.post(self.anon(), token, "2009")
        self.assertEqual(sorted(response.cookies.keys()), [portal.cookie_name(portal.read_token(token))])  # the pass, and nothing else (no session)
        self.assertNotIn(portal.cookie_name(portal.read_token(token)), self.post(self.anon(), token, "1000").cookies)  # a wrong answer sets no pass

    def test_csrf_is_enforced_on_the_answer(self):
        token = self.token()
        strict = self.anon(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self.url(token), {"answer": "2011"}).status_code, 403)
        page = strict.get(self.url(token))
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        self.assertEqual(strict.post(self.url(token), {"answer": "2011", "csrfmiddlewaretoken": csrf}).status_code, 303)

    def test_the_route_is_public_and_a_bad_token_is_the_plain_page_not_the_login(self):
        response = self.anon().get("/p/edu/x/")
        self.assertDead(response)
        self.assertNotIn("login", response.get("Location", ""))


# ---- what the guardian reads ----

class ContentTests(PortalSetup):
    def setUp(self):
        super().setUp()
        self.marks_for_yousef()
        self.hold_mariam()
        self.nursery_day()
        self.exam(self.group_b, "امتحان العلوم الشهري", "monthly", 3, "20", {self.mariam.pk: {"score": "14"}})

    def test_the_portal_shows_each_active_child_with_groups_times_teacher_and_the_contact_buttons(self):
        client, token, page = self.open_portal()
        body = page.content.decode()
        self.assertEqual(re.findall(r"<bdi>([^<]*)</bdi></h2>", body)[:2], [MARIAM, YOUSEF])  # name order, not id order
        for text in (YOUSEF, MARIAM, "رياضيات الإعدادي", GROUP_A, GROUP_B, TEACHER_A, TEACHER_B, "الأحد", "17:00", "Demo Store", "المدرس"):
            self.assertContains(page, text)
        self.assertContains(page, "data-contact-call")
        self.assertContains(page, "data-contact-whatsapp")
        self.assertContains(page, 'dir="rtl"')
        self.assertContains(page, "تواصل مع المركز")
        self.assertEqual(len(re.findall(r"data-child[ >]", body)), 2)
        self.assertEqual(len(re.findall(r"data-group>", body)), 2)
        audit = AuditLog.objects.get(action="open_guardian_portal")
        self.assertEqual((audit.actor, audit.ip_address, audit.user_agent), (None, None, ""))

    def test_nothing_internal_and_nothing_of_another_family_is_ever_drawn(self):
        client, token, page = self.open_portal()
        body = normalized(page, token)
        for marker in (NOTES, SCHOOL, STAGE, PAYER_PHONE, "1234567", "+201001234567", "2011", "2014", "2009", "2011-03-17", "17 مارس", "March 17", "2014-06-02", "2 يونيو",
                       PICKUP_ID, PICKUP_PHONE, ESCORT, TEACHER_RATE, "987,654", "قاعة سرية", ATT_NOTE, SESSION_TOPIC, EXAM_NOTES, MARK_NOTE, DAY_NOTE, ENROLL_NOTE,
                       HIDDEN_EXAM, "11.25", STRANGER, STRANGER_PAYER, "01555666777", self.inv_stranger.invoice_number, "ST-0", PAYER_NAME, RELATION,
                       "student", "/education/", "/admin/", "teacher_pay", "ledger", "cost"):
            self.assertNotIn(marker, body, marker)
        english = normalized(client.get(self.url(token, "en")), token)
        for marker in (NOTES, SCHOOL, PAYER_PHONE, PICKUP_ID, TEACHER_RATE, ATT_NOTE, HIDDEN_EXAM, STRANGER, "2011-03-17", "March 17"):
            self.assertNotIn(marker, english, marker)
        self.assertNotIn(PICKUP_ID, body)

    def test_attendance_is_the_last_thirty_days_and_equals_what_the_staff_screens_say(self):
        client, token, page = self.open_portal()
        body = page.content.decode()
        yousef_block = body.split(YOUSEF, 1)[1].split("data-section=\"marks\"", 1)[0]
        self.assertIn("<bdi dir=\"ltr\">80%</bdi>", yousef_block)  # 10 held, 2 absent: late counts as attended
        self.assertEqual(attendance.student_summary(self.yousef, 30, FIXED.date())["rate"], 80)
        absent = re.search(r"data-absent-days>(.*?)</ul>", yousef_block, re.S).group(1)
        late = re.search(r"data-late-days>(.*?)</ul>", yousef_block, re.S).group(1)
        self.assertEqual(re.findall(r"<bdi>([^<]+)</bdi>", absent), ["4 نوفمبر 2026", "8 نوفمبر 2026"])  # days 7 and 3 ago, oldest first
        self.assertEqual(re.findall(r"<bdi>([^<]+)</bdi>", late), ["6 نوفمبر 2026"])
        self.assertNotIn("2 أكتوبر 2026", yousef_block)  # 40 days ago: outside
        self.assertNotIn(ATT_NOTE, body)
        mariam_block = body.split(MARIAM, 1)[1].split(YOUSEF, 1)[0]
        self.assertIn('<bdi dir="ltr">100%</bdi>', mariam_block)
        self.assertIn("مفيش غياب ولا تأخير", mariam_block)

    def test_a_child_with_no_recorded_class_says_so(self):
        Attendance.objects.filter(student=self.mariam).delete()
        client, token, page = self.open_portal()
        self.assertContains(page, "لسه مفيش حصص متسجّلة")

    def test_only_published_marks_show_and_the_average_is_the_report_cards_arithmetic(self):
        client, token, page = self.open_portal()
        body = page.content.decode()
        block = body.split(YOUSEF, 1)[1].split('data-section="nursery"', 1)[0].split('data-section="marks"', 1)[1]
        titles = re.findall(r"data-exam><div class=\"gp-main-text\"><strong><bdi>([^<]+)</bdi>", block)
        self.assertEqual(titles, ["الامتحان النهائي للرياضيات", "اختبار قصير للرياضيات", "امتحان الشهر للرياضيات"])  # newest first; hidden and not-yet-entered left out
        self.assertIn('<bdi dir="ltr">17 / 20</bdi>', block)
        self.assertIn('<bdi dir="ltr">85.0%</bdi>', block)
        self.assertIn('<bdi dir="ltr">7.5 / 10</bdi>', block)
        self.assertIn('<bdi dir="ltr">75.0%</bdi>', block)
        self.assertIn("غياب عن الامتحان", block)
        self.assertIn('data-marks-average><bdi dir="ltr">80.0%</bdi>', block)  # (85.0 + 75.0) / 2; the absent exam is left out
        self.assertNotIn(HIDDEN_EXAM, body)
        self.assertNotIn("اختبار لسه ماتصححش", body)
        exams.set_published(self.hidden, True, self.owner)  # publishing makes it appear, and moves the average
        later = client.get(self.url(token)).content.decode()
        self.assertIn(HIDDEN_EXAM, later)
        self.assertIn('data-marks-average><bdi dir="ltr">72.1%</bdi>', later)  # 11.25 / 20 = 56.25; (85 + 75 + 56.25) / 3 = 72.083, half up
        exams.set_published(self.hidden, False, self.owner)  # taking it back hides it again
        self.assertNotIn(HIDDEN_EXAM, client.get(self.url(token)).content.decode())

    def test_the_average_equals_report_card_when_every_exam_is_published(self):
        exams.set_published(self.hidden, True, self.owner)
        card = exams.report_card(self.yousef, day(365), FIXED.date(), self.owner)
        client, token, page = self.open_portal()
        average = re.search(r"data-marks-average><bdi dir=\"ltr\">([\d.]+)%</bdi>", page.content.decode().split(YOUSEF, 1)[1]).group(1)
        self.assertEqual(D(average), card["average"])
        self.assertEqual(D(average), D("72.1"))  # (85 + 75 + 56.25) / 3 = 72.083, half up

    def test_an_exam_the_child_was_not_on_the_roster_for_is_not_shown(self):
        late = services.save_student({"name": "طالب انضم بعد الامتحان", "payer": self.payer, "date_of_birth": "2012-02-02"}, self.owner)
        services.enroll(late, self.group_a, self.owner, start_date=day(1))
        client, token, page = self.open_portal()
        block = page.content.decode().split("طالب انضم بعد الامتحان", 1)[1].split('data-section="nursery"', 1)[0]
        self.assertIn("لسه مفيش درجات منشورة", block)

    def test_nursery_days_show_the_arrival_the_leaving_and_the_name_of_who_picked_up(self):
        client, token, page = self.open_portal()
        body = page.content.decode()
        block = body.split(MARIAM, 1)[1].split(YOUSEF, 1)[0]
        self.assertIn('data-section="nursery"', block)
        self.assertIn("وقت الوصول", block)
        self.assertIn('<bdi dir="ltr">07:50</bdi>', block)
        self.assertIn('<bdi dir="ltr">13:10</bdi>', block)
        self.assertIn(PICKER, block)
        self.assertNotIn(PICKUP_ID, body)
        self.assertNotIn(PICKUP_PHONE, body)
        self.assertNotIn('data-section="nursery"', body.split(YOUSEF, 1)[1])  # Yousef has no nursery days

    def test_a_stopped_enrolment_and_a_closed_group_are_not_listed(self):
        from .models import Enrollment

        Enrollment.objects.filter(student=self.mariam).update(status="stopped")
        client, token, page = self.open_portal()
        self.assertNotContains(page, GROUP_B)
        self.assertContains(page, "مفيش مجموعات شغالة دلوقتي")
        self.group_a.active = False
        self.group_a.save()
        self.assertNotContains(client.get(self.url(token)), GROUP_A)

    def test_english(self):
        client, token, page = self.open_portal(lang="en")
        self.assertContains(page, 'dir="ltr"')
        for text in ("Welcome", "Groups and times", "Attendance in the last 30 days", "Marks and progress report", "Fees", "Call the centre", "Teacher", "Sun"):
            self.assertContains(page, text)
        self.assertContains(page, "Only the marks the centre has published are shown here.")
        self.assertContains(page, "4 November 2026")
        self.assertContains(page, f'href="{reverse("guardian_portal", args=[token])}?lang=ar"')  # the switch stays on this link
        self.assertNotContains(page, "المجموعات والمواعيد")
        self.assertContains(client.get(self.url(token, "ar")), "المجموعات والمواعيد")  # the pass survives the language switch
        fresh = self.token()  # a new link replaces the one above
        ask = self.anon().get(self.url(fresh, "en"))
        self.assertContains(ask, "Birth year")
        self.assertContains(ask, "To make sure you are the guardian")
        wrong = self.post(self.anon(), fresh, "0000", "en")
        self.assertContains(wrong, "does not match", status_code=400)
        dead = self.anon().get(self.url("x", "en"))
        self.assertContains(dead, "This link no longer works", status_code=404)


# ---- fees ----

class FeesTests(PortalSetup):
    def owed(self, page):
        found = re.search(r"data-fees-owed><bdi dir=\"ltr\">([\d,.]+)</bdi>", page.content.decode())
        return D(found.group(1).replace(",", "")) if found else None

    def dues_row(self):
        return next(row["total"] for row in billing.dues() if row["party"].pk == self.payer.pk)

    def lines(self, page):
        block = page.content.decode().split('data-section="fees"', 1)[1]
        return [(match[0], D(match[1].replace(",", ""))) for match in re.findall(r"data-invoice>.*?<bdi dir=\"ltr\">([^<]+)</bdi></strong>.*?gp-label\">[^<]+</span><strong><bdi dir=\"ltr\">([\d,.]+)</bdi>", block, re.S)]

    def test_600_and_450_with_200_paid_at_the_desk_owe_850_exactly_as_the_dues_screen_says(self):
        self.pay_desk(self.inv600, "200")
        self.assertEqual(self.dues_row(), D("850.00"))
        client, token, page = self.open_portal()
        self.assertEqual(self.owed(page), D("850.00"))
        self.assertEqual(self.owed(page), self.dues_row())
        lines = self.lines(page)
        self.assertEqual(sum(amount for _, amount in lines), D("850.00"))
        self.assertEqual([number for number, _ in lines], [self.inv600.invoice_number, self.inv450.invoice_number])  # the aging's order: oldest first
        board = paylinks.Board(self.owner, "ar").load()  # the pay-link board asks the same per invoice
        self.assertEqual([amount for _, amount in lines], [board.states[self.inv600.pk].ask, board.states[self.inv450.pk].ask])
        self.assertEqual([amount for _, amount in lines], [D("400.00"), D("450.00")])

    def test_the_same_850_when_the_200_is_settled_on_one_invoice(self):
        self.pay_desk(self.inv600, "200", settled=True)
        self.assertEqual(self.dues_row(), D("850.00"))
        client, token, page = self.open_portal()
        self.assertEqual(self.owed(page), D("850.00"))
        self.assertEqual([amount for _, amount in self.lines(page)], [D("400.00"), D("450.00")])

    def test_fully_paid_says_nothing_is_due(self):
        self.pay_desk(self.inv600, "600")
        self.pay_desk(self.inv450, "450")
        client, token, page = self.open_portal()
        self.assertContains(page, "data-fees-none")
        self.assertNotContains(page, "data-invoice")
        self.assertNotContains(page, "data-pay-go")
        self.assertIsNone(self.owed(page))

    def test_other_amounts_on_the_account_are_shown_in_the_total_and_explained(self):
        type(self.payer).objects.filter(pk=self.payer.pk).update(opening_balance=D("100.00"))
        client, token, page = self.open_portal()
        self.assertEqual(self.owed(page), D("1150.00"))
        self.assertEqual(self.owed(page), self.dues_row())
        self.assertContains(page, "data-other-dues")
        self.assertEqual(sum(amount for _, amount in self.lines(page)), D("1050.00"))

    def test_the_pay_button_is_where_a_live_link_already_exists_and_nowhere_else(self):
        client, token, page = self.open_portal()
        self.assertNotContains(page, "data-pay-go")
        link, _ = paylinks.make_or_show(self.inv450, self.owner, "ar")
        self.assertEqual(link.amount, D("450.00"))
        page = client.get(self.url(token))
        body = page.content.decode()
        self.assertEqual(body.count("data-pay-go"), 1)
        card = body.split(self.inv450.invoice_number, 1)[1]
        self.assertIn("data-pay-go", card.split("data-invoice", 1)[0])  # on the 450 invoice, not the 600 one
        href = re.search(r'href="([^"]+)" rel="nofollow noreferrer" data-pay-go', body).group(1)
        self.assertEqual(href, f"{reverse('pay_page', args=[pay.sign_token(link)])}?lang=ar")
        self.assertIn("ادفع", body)
        self.assertEqual(client.get(href).status_code, 200)  # the pay page opens
        self.assertTrue(self.owed(page) == D("1050.00"))
        # a link past its time, or paid, or one whose gateway is switched off, shows no button, and nothing is swept: the portal wrote nothing
        before_logs = AuditLog.objects.exclude(action="open_guardian_portal").count()
        with self.clock(hours=73):
            gone = self.anon()
            self.post(gone, token, "2011")
            self.assertNotContains(gone.get(self.url(token)), "data-pay-go")
        link.refresh_from_db()
        self.assertEqual(link.status, LinkStatus.NEW)  # no expiry sweep from a public page
        self.assertEqual(AuditLog.objects.exclude(action="open_guardian_portal").count(), before_logs)
        from settings_core.capabilities import set_capability_enabled

        set_capability_enabled(self.profile, "payment_links", False, user=self.owner)
        self.assertNotContains(client.get(self.url(token)), "data-pay-go")

    def test_a_multi_entity_install_reads_the_links_entity_alone(self):
        Entity.objects.create(code="E-FEES", name_ar="فرع المصروفات", activity_slug="education", sub_activity_slug="tutoring_center")
        self.pay_desk(self.inv600, "200")
        client, token, page = self.open_portal()
        with working_in(portal.read_token(token).link.entity):
            expected = next(row["total"] for row in billing.dues() if row["party"].pk == self.payer.pk)
        self.assertEqual(self.owed(page), expected)
        self.assertEqual(self.owed(page), D("850.00"))


# ---- nothing is written, nothing is posted ----

class NothingMovesTests(PortalSetup):
    def test_making_opening_answering_and_revoking_move_no_money_stock_message_or_data(self):
        self.marks_for_yousef()
        self.nursery_day()
        paylinks.make_or_show(self.inv450, self.owner, "ar")
        before, logs = self.counts(), AuditLog.objects.exclude(module="education").count()
        education_logs = AuditLog.objects.filter(module="education").count()
        made = self.make()
        token = token_of(made["url"])
        client = self.anon()
        self.post(client, token, "0000")
        self.post(client, token, "2011")
        for _ in range(3):
            client.get(self.url(token))
        client.get(self.url(token, "en"))
        portal.revoke(made["link"], self.owner)
        self.assertEqual(self.counts(), before)
        self.assertEqual(AuditLog.objects.exclude(module="education").count(), logs)  # not a payments, ledger or messaging audit row
        actions = sorted(AuditLog.objects.filter(module="education").order_by("pk").values_list("action", flat=True)[education_logs:])
        self.assertEqual(actions, ["create_guardian_link"] + ["open_guardian_portal"] * 4 + ["revoke_guardian_link"])
        link = GuardianLink.objects.get(pk=made["link"].pk)
        self.assertEqual(link.last_opened_at, FIXED)

    def test_the_page_reads_inside_a_fixed_number_of_queries_whatever_the_wrong_answers(self):
        client, token, page = self.open_portal()
        client.get(self.url(token))
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            client.get(self.url(token))
        writes = [q["sql"] for q in queries if q["sql"].split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]
        self.assertEqual(len(writes), 2, writes)  # last_opened_at and the audit row; nothing else
        self.assertTrue(any("guardianlink" in sql for sql in writes))
        self.assertTrue(any("audit" in sql for sql in writes))


# ---- the centre's side ----

class StaffTests(PortalSetup):
    def student_url(self):
        return reverse("education:student", args=[self.yousef.pk])

    def make_url(self, student=None):
        return reverse("education:portal_make", args=[(student or self.yousef).pk])

    def revoke_url(self, student=None):
        return reverse("education:portal_revoke", args=[(student or self.yousef).pk])

    def as_(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_the_owner_makes_a_link_from_the_student_file_and_sees_it_once(self):
        owner = self.as_(self.owner)
        page = owner.get(self.student_url())
        self.assertContains(page, "data-portal-card")
        self.assertContains(page, "data-portal-make")
        self.assertContains(page, "ابعت بوابة ولي الأمر")
        self.assertContains(page, 'data-portal-kind="year"')
        self.assertNotContains(page, "data-portal-live")
        self.assertNotContains(page, "data-portal-revoke")
        self.assertNotContains(page, "data-portal-phone-only")
        response = owner.post(self.make_url(), {"lang": "ar"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertContains(response, "data-portal-made")
        url = re.search(r'data-portal-url[^>]*value="([^"]+)"|value="([^"]+)"[^>]*data-portal-url', response.content.decode())
        shown = next(group for group in url.groups() if group)
        self.assertTrue(shown.startswith("https://pay.example.test/p/edu/"))
        self.assertContains(response, f'data-copy-link="{shown}"')
        self.assertContains(response, "https://wa.me/20100")
        self.assertEqual(self.anon().get(self.url(token_of(shown))).status_code, 200)
        after = owner.get(self.student_url())
        self.assertContains(after, "data-portal-live")
        self.assertContains(after, "data-portal-revoke")
        self.assertContains(after, "ابعت رابط جديد")
        self.assertNotContains(after, token_of(shown))  # the address is not kept, so it is not shown again
        self.assertEqual(owner.get(self.make_url()).status_code, 405)

    def test_the_whatsapp_text_holds_only_a_greeting_and_the_link(self):
        for lang, greeting in (("ar", "أهلاً بحضرتك"), ("en", "Hello")):
            made = self.make(lang=lang)
            text = unquote(made["whatsapp"].split("?text=", 1)[1])
            self.assertTrue(made["whatsapp"].startswith("https://wa.me/20100"))
            self.assertIn(greeting, text)
            self.assertIn("Demo Store", text)
            self.assertTrue(text.endswith(made["url"]))
            self.assertIn("\n" + made["url"], text)  # the link on its own line
            words = text.replace(made["url"], "")  # the address holds a base64 token and its timestamp: digits and letters by chance, so it is taken out first
            for secret in (YOUSEF, MARIAM, PAYER_NAME, "2011", "2014", GROUP_A, "درجة", "17", "850", "Mark", "owe"):
                self.assertNotIn(secret, words)
        stranger = self.make(self.stranger)
        year = unquote(stranger["whatsapp"].split("?text=", 1)[1]).replace(stranger["url"], "")
        self.assertIn("سنة ميلاد أحد الأبناء", year)
        self.assertNotIn("آخر 4 أرقام", year)
        self.stranger.date_of_birth = None
        self.stranger.save()
        again = self.make(self.stranger)
        phone = unquote(again["whatsapp"].split("?text=", 1)[1]).replace(again["url"], "")  # no birth date: the question is the phone's last 4 digits, still no name or number
        self.assertIn("آخر 4 أرقام", phone)
        self.assertNotIn("سنة ميلاد", phone)
        for secret in (STRANGER, STRANGER_PAYER, "01555666777", "6777"):
            self.assertNotIn(secret, phone)

    def test_a_manager_may_and_a_cashier_a_teacher_and_a_signed_out_visitor_may_not(self):
        self.assertEqual(self.as_(self.manager).post(self.make_url(), {"lang": "ar"}).status_code, 200)
        link = GuardianLink.objects.get(revoked_at__isnull=True)
        for user in (self.cashier, person(RoleCode.STOCK_KEEPER, "e3_stock")):
            client = self.as_(user)
            card = client.get(self.student_url())
            self.assertNotContains(card, "data-portal-card")
            self.assertEqual(client.post(self.make_url(), {"lang": "ar"}).status_code, 403)
            self.assertEqual(client.post(self.revoke_url(), {"link": link.pk}).status_code, 403)
            with self.assertRaises(PermissionDenied):
                portal.make_link(self.yousef, user)
            with self.assertRaises(PermissionDenied):
                portal.revoke(link, user)
        self.assertEqual(Client().post(self.make_url(), {}).status_code, 302)  # to the login
        link.refresh_from_db()
        self.assertIsNone(link.revoked_at)
        self.assertEqual(GuardianLink.objects.count(), 1)

    def test_revoking_ends_the_link_at_once_and_is_audited(self):
        owner = self.as_(self.owner)
        made = self.make()
        token = token_of(made["url"])
        client = self.anon()
        self.post(client, token, "2011")
        response = owner.post(self.revoke_url(), {"link": made["link"].pk, "lang": "ar"}, follow=True)
        self.assertContains(response, "الرابط اتلغى")
        self.assertDead(client.get(self.url(token)))
        log = AuditLog.objects.get(action="revoke_guardian_link")
        self.assertEqual((log.actor, log.object_id, log.after_data["why"]), (self.owner, str(made["link"].pk), "revoked"))
        again = owner.post(self.revoke_url(), {"link": made["link"].pk, "lang": "ar"}, follow=True)
        self.assertContains(again, "الرابط ده اتلغى بالفعل")
        self.assertEqual(AuditLog.objects.filter(action="revoke_guardian_link").count(), 1)
        self.assertNotContains(owner.get(self.student_url()), "data-portal-live")

    def test_the_audit_names_the_link_but_never_the_token_or_the_nonce_or_an_address(self):
        made = self.make()
        token = token_of(made["url"])
        client = self.anon(REMOTE_ADDR="10.2.3.4", HTTP_USER_AGENT="PhoneBrowser/1.0")
        self.post(client, token, "0000")
        self.assertFalse(AuditLog.objects.filter(action="open_guardian_portal").exists())  # failed tries are throttled, not audited
        self.post(client, token, "2011")
        client.get(self.url(token))
        again = self.make()  # replaces the first
        for row in AuditLog.objects.filter(module="education", action__contains="guardian"):
            text = f"{row.before_data}{row.after_data}{row.reason}{row.object_id}"
            for secret in (token, made["link"].nonce, again["link"].nonce, made["url"], "p/edu"):
                self.assertNotIn(secret, text)
        created = AuditLog.objects.get(action="create_guardian_link", object_id=str(made["link"].pk))
        self.assertEqual((created.actor, created.after_data), (self.owner, {"payer": self.payer.pk, "entity": made["link"].entity_id, "children": 2, "factor": "year", "days": 30}))
        replaced = AuditLog.objects.get(action="revoke_guardian_link", object_id=str(made["link"].pk))
        self.assertEqual(replaced.after_data["why"], "replaced")
        opened = AuditLog.objects.get(action="open_guardian_portal")
        self.assertEqual((opened.actor, opened.ip_address, opened.user_agent, opened.object_id), (None, None, "", str(made["link"].pk)))
        self.assertEqual(opened.after_data, {"link": made["link"].pk, "payer": self.payer.pk})

    def test_the_centre_is_warned_when_only_the_phone_protects_the_link(self):
        self.stranger.date_of_birth = None
        self.stranger.save()
        owner = self.as_(self.owner)
        url = reverse("education:student", args=[self.stranger.pk])
        page = owner.get(url)
        self.assertContains(page, 'data-portal-kind="phone"')
        self.assertContains(page, "data-portal-phone-only")
        self.assertContains(page, "مش بتحمي لو الرقم نفسه غلط")
        made = owner.post(reverse("education:portal_make", args=[self.stranger.pk]), {"lang": "ar"})
        self.assertContains(made, "data-portal-warning")
        self.assertContains(made, "مش بتحمي لو الرقم نفسه غلط")
        english = owner.post(reverse("education:portal_make", args=[self.stranger.pk]), {"lang": "en"})
        self.assertContains(english, "does not help if the number itself is wrong")
        self.assertNotContains(owner.get(self.student_url()), "data-portal-phone-only")  # a birth date on file: no such note

    def test_a_student_with_neither_factor_has_no_send_button_and_a_post_is_refused_calmly(self):
        self.stranger.date_of_birth = None
        self.stranger.save()
        type(self.stranger.payer).objects.filter(pk=self.stranger.payer.pk).update(phone="")
        owner = self.as_(self.owner)
        page = owner.get(reverse("education:student", args=[self.stranger.pk]))
        self.assertContains(page, "data-portal-blocked")
        self.assertNotContains(page, "data-portal-make")
        response = owner.post(reverse("education:portal_make", args=[self.stranger.pk]), {"lang": "ar"}, follow=True)
        self.assertContains(response, "مفيش للأبناء تاريخ ميلاد")
        self.assertFalse(GuardianLink.objects.filter(payer=self.stranger.payer).exists())

    def test_nothing_is_made_or_revoked_without_the_public_address_or_for_an_inactive_student(self):
        first = self.make()["link"]
        with override_settings(PUBLIC_BASE_URL=""):
            with self.assertRaisesMessage(ValidationError, "PUBLIC_BASE_URL"):
                self.make()
        first.refresh_from_db()
        self.assertIsNone(first.revoked_at)  # a refused make leaves the old link alone
        self.assertEqual(GuardianLink.objects.count(), 1)
        Student.objects.filter(pk=self.yousef.pk).update(active=False)
        self.yousef.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "موقوف"):
            self.make()
        self.assertEqual(AuditLog.objects.filter(action="create_guardian_link").count(), 1)

    def test_another_entitys_students_and_links_are_not_found_and_other_installs_have_no_route(self):
        branch = Entity.objects.create(code="E-STAFF", name_ar="فرع آخر", activity_slug="education", sub_activity_slug="tutoring_center")
        made = self.make()
        owner = self.as_(self.owner)
        session = owner.session
        session["hesba_entity"] = branch.pk
        session.save()
        self.assertEqual(owner.post(self.make_url(), {"lang": "ar"}).status_code, 404)  # the student belongs to the main entity
        self.assertEqual(owner.post(self.revoke_url(), {"link": made["link"].pk}).status_code, 404)
        made["link"].refresh_from_db()
        self.assertIsNone(made["link"].revoked_at)
        session["hesba_entity"] = Entity.objects.get(is_main=True).pk
        session.save()
        self.assertEqual(owner.post(self.revoke_url(), {"link": "abc"}).status_code, 404)
        other = self.make(self.stranger)["link"]
        self.assertEqual(owner.post(self.revoke_url(), {"link": other.pk}).status_code, 404)  # another payer's link, through this student's file
        with mock.patch.object(services, "is_education_install", return_value=False):
            self.assertEqual(owner.post(self.make_url(), {"lang": "ar"}).status_code, 404)
            self.assertEqual(owner.post(self.revoke_url(), {"link": made["link"].pk}).status_code, 404)

    def test_the_staff_pages_are_in_both_languages_and_use_the_staff_wording(self):
        owner = self.as_(self.owner)
        english = owner.get(self.student_url() + "?lang=en")
        self.assertContains(english, "Guardian portal")
        self.assertContains(english, "Send the guardian portal")
        made = owner.post(self.make_url(), {"lang": "en"})
        self.assertContains(made, "This link is shown here once only")
        for text in list(views_portal.STAFF_WORDS["ar"].values()) + list(portal.MESSAGES["ar"].values()):
            self.assertNotIn("دوس", text)  # «اضغط», never «دوس», on a staff screen


# ---- the words ----

class WordsTests(PortalSetup):
    def flatten(self, value):
        if isinstance(value, dict):
            return [text for item in value.values() for text in self.flatten(item)]
        return [value]

    def test_everything_a_guardian_reads_follows_the_customer_voice_rules(self):
        from reports.tests_customer_voice import GENDERED, _problems

        texts = self.flatten(portal.WORDS["ar"])
        self.assertGreater(len(texts), 60)
        for text in texts:
            self.assertEqual(_problems(text), [], text)
            for gendered in GENDERED:
                self.assertNotIn(gendered, text)
        for path in ("templates/education/portal.html", "templates/education/portal_dead.html"):
            source = re.sub(r"\{#.*?#\}|\{% comment %\}.*?\{% endcomment %\}", "", open(f"{self.base_dir()}/{path}", encoding="utf-8").read(), flags=re.S)
            self.assertEqual(_problems(source), [], path)

    def base_dir(self):
        from django.conf import settings

        return str(settings.BASE_DIR)

    def test_no_arabic_line_starts_or_ends_with_an_english_word_and_the_voice_is_warm(self):
        for key, text in portal.WORDS["ar"].items():
            for part in self.flatten(text):
                for line in part.split("\n"):
                    stripped = re.sub(r"[\s.،:؛!؟?()«»\-–—]+", " ", line).strip()
                    if not stripped or re.search(r"\{url\}|\{company\}", stripped) and key.startswith("wa_text"):
                        stripped = re.sub(r"\{url\}", "", stripped).strip()
                    if re.search(r"[؀-ۿ]", stripped):
                        self.assertNotRegex(stripped[:1], r"[A-Za-z]", f"{key}: {line}")
                        self.assertNotRegex(stripped[-1:], r"[A-Za-z]", f"{key}: {line}")
        self.assertIn("حضرتك", portal.WORDS["ar"]["dead"] + portal.WORDS["ar"]["private"] + portal.WORDS["ar"]["hello"] + portal.WORDS["ar"]["ask_year"])
        self.assertIn("رابط", portal.WORDS["ar"]["dead_h"])
        self.assertIn("تواصل مع المركز", portal.WORDS["ar"]["wrong"])
        for text in self.flatten(portal.WORDS["ar"]):
            self.assertNotIn("لينك", text)
            self.assertNotIn("المحل", text)
            self.assertNotIn("بتاع", text)

    def test_the_english_has_every_key_the_arabic_has_and_the_staff_words_too(self):
        self.assertEqual(set(portal.WORDS["ar"]), set(portal.WORDS["en"]))
        self.assertEqual(set(portal.WORDS["ar"]["exam_kinds"]), set(portal.WORDS["en"]["exam_kinds"]))
        self.assertEqual(set(portal.MESSAGES["ar"]), set(portal.MESSAGES["en"]))
        self.assertEqual(set(views_portal.STAFF_WORDS["ar"]), set(views_portal.STAFF_WORDS["en"]))


# ---- the migration, and the schema ----

class MigrationTests(TransactionTestCase):
    """0010 only creates the one table, after 0009; the payers, students and groups that already exist are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0009_creates_one_table_and_leaves_existing_data_alone(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("education", "0010_guardian_links")]
        self.assertIn(("education", "0009_certificates"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["CreateModel"])
        executor.migrate([("education", "0009_certificates")])
        leaves = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "education"] + [("education", "0009_certificates")]
        old = executor.loader.project_state(leaves).apps
        user = old.get_model("auth", "User").objects.create(username="mig_e3")
        payer = old.get_model("master_data", "Customer").objects.create(customer_code="C-E3", name="ولي أمر قديم", phone="01001234567")
        student = old.get_model("education", "Student").objects.create(code="ST-OLD", name="طالب قديم", payer=payer, created_by=user, date_of_birth=date(2012, 1, 1))
        self.assertNotIn("education_guardianlink", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("education", "0010_guardian_links")])
        self.assertIn("education_guardianlink", connection.introspection.table_names())
        self.assertEqual(Student.objects.get(pk=student.pk).name, "طالب قديم")
        self.assertEqual(Student.objects.get(pk=student.pk).payer_id, payer.pk)
        self.assertEqual(GuardianLink.objects.count(), 0)
        from entities.services import main_entity

        link = GuardianLink.objects.create(payer_id=payer.pk, entity=main_entity(), created_by_id=user.pk, nonce="n" * 40)
        self.assertEqual(link.payer.name, "ولي أمر قديم")
        with self.assertRaises(IntegrityError), transaction.atomic():
            GuardianLink.objects.create(payer_id=payer.pk, entity=main_entity(), created_by_id=user.pk, nonce="m" * 40)


class StylesheetTests(PortalSetup):
    def test_the_portal_sheet_draws_from_the_tokens_only(self):
        from django.contrib.staticfiles import finders

        def read(path):
            with open(finders.find(path), encoding="utf-8") as handle:
                return handle.read()

        css, tokens = read("hesba/css/education_portal.css"), read("hesba/css/tokens.css")
        self.assertEqual([c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css) if c.lower() not in {"#fff", "#ffffff"}], [])
        self.assertNotRegex(css, r"rgba?\(")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", tokens))
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])
        self.assertNotRegex(css, r"font-weight:\s*[89]00")
        self.assertIn("var(--hs-focus-ring)", css)
        self.assertIn("min-block-size:var(--hs-control-height-lg)", css)  # the answer box and every button are at least 44px (48px here)
        for template in ("portal.html", "portal_dead.html", "portal_made.html", "_portal_card.html"):  # no colour typed into a template either
            with open(f"{self.base_dir()}/templates/education/{template}", encoding="utf-8") as handle:
                self.assertNotRegex(handle.read(), r"style=\"[^\"]*#")

    def base_dir(self):
        from django.conf import settings

        return str(settings.BASE_DIR)


class SchemaTests(PortalSetup):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_makemigrations_has_nothing_to_add(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)

    def test_the_table_has_exactly_the_planned_columns(self):
        self.assertEqual(sorted(field.name for field in GuardianLink._meta.get_fields()),
                         sorted(["id", "payer", "nonce", "entity", "created_by", "created_at", "revoked_at", "last_opened_at"]))
        self.assertTrue(GuardianLink._meta.get_field("nonce").unique)
        self.assertEqual(GuardianLink._meta.get_field("payer").remote_field.on_delete.__name__, "PROTECT")
