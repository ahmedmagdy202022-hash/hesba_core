"""M7 (HG-090): the vet's vaccines and doses — due dates, the due list, the optional draft bill, access and entities.

What these pin, with the money written out: the next-due rule at its boundaries (a year, a leap day, a one-off, a typed
date); a later dose closing the earlier one without storing anything; the due list's windows, its entity and its
permission; a dose billed as one ordinary DRAFT invoice (250.00, plus VAT as the draft service computes it), once,
again after a cancelled invoice, moving no stock, cashbox, ledger or journal row; stock leaving only when the draft is
posted through the ordinary service; who may do what; the audit trail.

"Today" is always the Cairo local date and every other date is built from it, so the suite passes at any hour and on any
day of the year, a leap year's included.
"""

from calendar import isleap
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, stock_in
from inventory.models import StockMovement
from inventory.warehouses import on_hand
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from staff.services import save_employee
from taxes.models import SalesLineTax

from . import vaccines, vet
from .models import ClinicalVisit, Vaccination, Vaccine
from .tests_inpatient import customer_balance, footprint, person

MODULES = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians,inventory"


def non_leap_span_year(start):
    """The first year at or after ``start`` whose 10 January to 10 January the next year holds no 29 February."""

    year = start
    while isleap(year):
        year += 1
    return year


def leap_year(start):
    year = start
    while not isleap(year):
        year += 1
    return year


class DueAfterTests(SimpleTestCase):
    def test_a_year_lands_on_the_same_calendar_day(self):
        year = non_leap_span_year(timezone.localdate().year)
        self.assertEqual(vaccines.due_after(date(year, 1, 10), 365), date(year + 1, 1, 10))

    def test_a_year_from_a_leap_year_still_lands_on_the_same_calendar_day(self):
        year = leap_year(timezone.localdate().year)
        self.assertEqual(vaccines.due_after(date(year, 1, 10), 365), date(year + 1, 1, 10))  # not the 9th: 365 days across a leap day would be
        self.assertEqual(date(year, 1, 10) + timedelta(days=365), date(year + 1, 1, 9))

    def test_the_leap_day_clamps_to_28_february(self):
        year = leap_year(timezone.localdate().year)
        self.assertEqual(vaccines.due_after(date(year, 2, 29), 365), date(year + 1, 2, 28))
        self.assertEqual(vaccines.due_after(date(year, 2, 29), 365 * 4), date(year + 4, 2, 29))  # four years on is a leap year again

    def test_whole_years_and_other_intervals(self):
        self.assertEqual(vaccines.due_after(date(2026, 1, 10), 730), date(2028, 1, 10))
        self.assertEqual(vaccines.due_after(date(2026, 1, 10), 21), date(2026, 1, 31))
        self.assertEqual(vaccines.due_after(date(2026, 1, 10), 28), date(2026, 2, 7))
        self.assertEqual(vaccines.due_after(date(2026, 1, 10), 180), date(2026, 7, 9))

    def test_a_one_off_has_no_due_date(self):
        self.assertIsNone(vaccines.due_after(date(2026, 1, 10), None))
        self.assertIsNone(vaccines.due_after(date(2026, 1, 10), 0))


class Clinic(TestCase):
    """A vet clinic: an owner with a dog and a cat, a vaccine with a billable item, staff of every kind."""

    def setUp(self):
        self.profile = prepared_client("medical", "vet", MODULES)
        self.today = timezone.localdate()
        self.location = make_location(location_code="VET", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="VET-CASH", is_default=True)
        self.owner = person(RoleCode.OWNER, "vx_owner")
        self.manager = person(RoleCode.MANAGER, "vx_manager")
        self.doctor = person(RoleCode.DOCTOR, "vx_doctor")
        self.karim = save_employee({"name": "د. كريم", "title": "بيطري"}, self.owner)
        self.karim.user = self.doctor
        self.karim.save()
        self.desk = person(RoleCode.CASHIER, "vx_desk")
        self.keeper = person(RoleCode.STOCK_KEEPER, "vx_keeper")
        self.rabies_item = make_item(item_code="VAC-RAB", item_name="تطعيم سعار", is_stock_tracked=True, default_sale_price="250.00")
        self.service_item = make_item(item_code="VAC-SRV", item_name="حقن تطعيم", is_stock_tracked=False, default_sale_price="100.00")
        stock_in(self.rabies_item, self.location, 10, "120.00")
        self.rabies = vaccines.save_vaccine({"name_ar": "سعار", "name_en": "Rabies", "species": "dog", "interval_days": "365", "item": self.rabies_item}, self.owner)
        self.fvrcp = vaccines.save_vaccine({"name_ar": "ثلاثي القطط", "species": "cat", "interval_days": "21"}, self.owner)
        self.puppy = vaccines.save_vaccine({"name_ar": "أول جرعة جراوي", "species": "dog"}, self.owner)  # a one-off
        self.deworm = vaccines.save_vaccine({"name_ar": "طارد ديدان", "interval_days": "90", "item": self.service_item}, self.owner)  # any species
        self.mona = make_customer(customer_code="V-1", name="منى علي", phone="01001234567")
        self.rocky = vet.save_animal(self.mona, {"name": "روكي", "species": "dog", "sex": "male"}, self.owner)
        self.mishmish = vet.save_animal(self.mona, {"name": "مشمش", "species": "cat"}, self.owner)

    def dose(self, animal=None, vaccine=None, days_ago=0, user=None, **extra):
        return vaccines.record(animal or self.rocky, {"vaccine": vaccine or self.rabies, "given_on": self.today - timedelta(days=days_ago), **extra}, user or self.owner)

    def due_rows(self, user=None, **kwargs):
        return vaccines.due_list(user or self.owner, today=self.today, **kwargs)["rows"]

    def lines(self, invoice):
        return [(line.item.item_code, line.quantity, line.unit_sale_price, line.line_total_amount) for line in invoice.lines.order_by("line_number")]


# ---- the vaccines -----------------------------------------------------------------


class VaccineTests(Clinic):
    def test_a_vaccine_carries_its_names_species_interval_and_item(self):
        self.assertEqual((self.rabies.name_ar, self.rabies.name_en, self.rabies.species, self.rabies.interval_days, self.rabies.item), ("سعار", "Rabies", "dog", 365, self.rabies_item))
        self.assertEqual((self.puppy.interval_days, self.puppy.item, self.deworm.species, self.puppy.active), (None, None, "", True))
        self.assertEqual((self.rabies.display("ar"), self.rabies.display("en"), self.fvrcp.display("en")), ("سعار", "Rabies", "ثلاثي القطط"))

    def test_it_is_validated(self):
        for data, text in (({"name_ar": " "}, "اسم التطعيم"), ({"name_ar": "x", "species": "dragon"}, "نوع الحيوان"),
                           ({"name_ar": "x", "interval_days": "0"}, "المدة"), ({"name_ar": "x", "interval_days": "abc"}, "المدة"),
                           ({"name_ar": "x", "interval_days": str(vaccines.MAX_INTERVAL + 1)}, "المدة")):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, text):
                vaccines.save_vaccine(data, self.owner)
        off = make_item(item_code="VAC-OFF", item_name="موقوف", is_stock_tracked=False, active=False)
        with self.assertRaisesMessage(ValidationError, "صنف أو خدمة فعّالة"):
            vaccines.save_vaccine({"name_ar": "x", "item": off}, self.owner)
        self.assertEqual(Vaccine.objects.count(), 4)
        with self.assertRaisesMessage(ValidationError, "Enter the vaccine's name"):
            vaccines.save_vaccine({"name_ar": ""}, self.owner, lang="en")

    def test_editing_and_switching_off(self):
        saved = vaccines.save_vaccine({"name_ar": "سعار سنوي", "species": "dog", "interval_days": "365", "active": False}, self.manager, self.rabies)
        self.assertEqual((saved.pk, saved.name_ar, saved.active, saved.item), (self.rabies.pk, "سعار سنوي", False, None))
        with self.assertRaisesMessage(ValidationError, "موقوف"):
            self.dose()  # a switched-off vaccine is not given

    def test_only_owner_and_manager_set_vaccines_up(self):
        for user in (self.doctor, self.desk, self.keeper):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                vaccines.save_vaccine({"name_ar": "x"}, user)
        self.assertEqual(Vaccine.objects.count(), 4)

    def test_a_vaccine_belongs_to_the_entity_it_was_made_in(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        main = Entity.objects.get(is_main=True)
        with working_in(branch):
            made = vaccines.save_vaccine({"name_ar": "فرع بس"}, self.owner)
            self.assertEqual((made.entity, set(vaccines.scoped_vaccines())), (branch, {made}))
        with working_in(main):
            self.assertNotIn(made, vaccines.scoped_vaccines())
            self.assertEqual(set(vaccines.scoped_vaccines()), {self.rabies, self.fvrcp, self.puppy, self.deworm})
        self.assertEqual(self.rabies.entity, None)  # made in the whole-group view: the main entity's (HG-034)
        self.assertEqual(len(vaccines.scoped_vaccines()), 5)

    def test_the_animals_choice_is_its_species_and_the_any_species_ones(self):
        self.assertEqual(set(vaccines.vaccines_for(self.rocky)), {self.rabies, self.puppy, self.deworm})
        self.assertEqual(set(vaccines.vaccines_for(self.mishmish)), {self.fvrcp, self.deworm})


# ---- recording a dose and its due date --------------------------------------------------


class RecordTests(Clinic):
    def test_next_due_is_worked_out_from_the_vaccines_interval(self):
        year = non_leap_span_year(self.today.year)
        dose = vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": date(year, 1, 10)}, self.owner)
        self.assertEqual((dose.given_on, dose.next_due), (date(year, 1, 10), date(year + 1, 1, 10)))
        short = vaccines.record(self.mishmish, {"vaccine": self.fvrcp, "given_on": date(year, 1, 10)}, self.owner)
        self.assertEqual(short.next_due, date(year, 1, 31))

    def test_a_leap_day_dose_is_due_on_28_february(self):
        year = leap_year(self.today.year - 1)  # a leap day that is never in the future
        if date(year, 2, 29) > self.today:
            year -= 4
        dose = vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": date(year, 2, 29)}, self.owner)
        self.assertEqual(dose.next_due, date(year + 1, 2, 28))

    def test_a_one_off_has_no_due_date(self):
        dose = self.dose(vaccine=self.puppy)
        self.assertIsNone(dose.next_due)

    def test_a_typed_date_is_kept_as_typed(self):
        typed = self.today + timedelta(days=45)
        dose = self.dose(next_due=typed.isoformat())
        self.assertEqual(dose.next_due, typed)  # the interval says a year; the vet said otherwise
        dose = vaccines.record(self.mishmish, {"vaccine": self.fvrcp, "given_on": self.today, "next_due": typed}, self.owner)
        self.assertEqual(dose.next_due, typed)

    def test_the_vet_may_say_there_is_no_next_dose(self):
        dose = self.dose(no_next=True)
        self.assertIsNone(dose.next_due)

    def test_a_later_dose_closes_the_earlier_ones_due_date(self):
        first = self.dose(days_ago=350)  # a year from then is within the month ahead
        self.assertEqual([row["dose"] for row in self.due_rows()], [first])
        second = self.dose(days_ago=0)
        self.assertEqual(list(vaccines.open_doses(Vaccination.objects.all())), [second])
        self.assertTrue(vaccines.superseded(first))
        self.assertFalse(vaccines.superseded(second))
        first.refresh_from_db()
        self.assertIsNotNone(first.next_due)  # nothing is rewritten: the history keeps what was expected then
        self.assertNotIn(first, [row["dose"] for row in self.due_rows()])

    def test_removing_the_later_dose_brings_the_earlier_due_date_back(self):
        first = self.dose(days_ago=400)
        second = self.dose(days_ago=0)
        self.assertEqual(self.due_rows(), [])
        vaccines.remove(second, self.owner)
        self.assertEqual([row["dose"] for row in self.due_rows()], [first])
        self.assertFalse(vaccines.superseded(first))

    def test_the_same_vaccine_for_another_animal_or_another_vaccine_does_not_close_it(self):
        mine = self.dose(days_ago=400)
        vaccines.record(self.mishmish, {"vaccine": self.deworm, "given_on": self.today}, self.owner)
        other_dog = vet.save_animal(self.mona, {"name": "ميمو", "species": "dog"}, self.owner)
        vaccines.record(other_dog, {"vaccine": self.rabies, "given_on": self.today}, self.owner)
        self.assertFalse(vaccines.superseded(mine))
        vaccines.record(self.rocky, {"vaccine": self.deworm, "given_on": self.today}, self.owner)
        self.assertFalse(vaccines.superseded(mine))

    def test_a_dose_given_earlier_than_one_already_recorded_is_closed_from_the_start(self):
        recent = self.dose(days_ago=10)
        older = self.dose(days_ago=380)  # entered late
        self.assertTrue(vaccines.superseded(older))
        self.assertFalse(vaccines.superseded(recent))

    def test_the_validation(self):
        other = ClinicalVisit.objects.create(patient=self.mona, animal=self.mishmish, visit_date=self.today, complaint="x", created_by=self.owner)
        for data, text in (({"vaccine": None}, "اختار التطعيم"), ({"vaccine": self.fvrcp}, "لـ قطة"),
                           ({"vaccine": self.rabies, "given_on": self.today + timedelta(days=1)}, "لسه مجاش"),
                           ({"vaccine": self.rabies, "given_on": "2026-13-45"}, "مش مظبوط"),
                           ({"vaccine": self.rabies, "given_on": date(1950, 1, 1)}, "قديم"),
                           ({"vaccine": self.rabies, "next_due": "soon"}, "ميعاد الجرعة"),
                           ({"vaccine": self.rabies, "next_due": self.today.isoformat()}, "بعد يوم التطعيم"),
                           ({"vaccine": self.rabies, "visit": other}, "مش بتاع الحيوان ده"),
                           ({"vaccine": self.rabies, "given_by": save_employee({"name": "د. سابق"}, self.owner)}, None)):
            with self.subTest(data=data):
                if text is None:
                    save = data["given_by"]
                    save.active = False
                    save.save()
                    text = "الدكتور ده مش فعّال"
                with self.assertRaisesMessage(ValidationError, text):
                    vaccines.record(self.rocky, data, self.owner)
        self.assertEqual(Vaccination.objects.count(), 0)
        with self.assertRaisesMessage(ValidationError, "This vaccine is for Cat; this animal is Dog"):
            vaccines.record(self.rocky, {"vaccine": self.fvrcp}, self.owner, lang="en")

    def test_the_same_dose_is_not_recorded_twice_on_one_day(self):
        self.dose()
        with self.assertRaisesMessage(ValidationError, "نفس اليوم"):
            self.dose()
        self.assertEqual(Vaccination.objects.count(), 1)

    def test_batch_visit_giver_and_note_are_kept(self):
        visit = ClinicalVisit.objects.create(patient=self.mona, animal=self.rocky, visit_date=self.today, complaint="x", created_by=self.owner)
        dose = self.dose(batch="LOT-77 ", visit=visit, given_by=self.karim, note="ZZNOTE خمول بعد الجرعة")
        dose.refresh_from_db()
        self.assertEqual((dose.batch, dose.visit, dose.given_by, dose.created_by, dose.entity), ("LOT-77", visit, self.karim, self.owner, None))
        self.assertEqual(dose.note, "ZZNOTE خمول بعد الجرعة")
        self.assertEqual(list(visit.vaccinations.all()), [dose])

    def test_a_dose_belongs_to_the_entity_it_was_given_in(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        with working_in(branch):
            local = vaccines.save_vaccine({"name_ar": "سعار الفرع", "species": "dog", "interval_days": "365"}, self.owner)
            dose = vaccines.record(self.rocky, {"vaccine": local, "given_on": self.today}, self.owner)
            self.assertEqual(dose.entity, branch)
            with self.assertRaisesMessage(ValidationError, "اختار التطعيم"):
                vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": self.today - timedelta(days=1)}, self.owner)  # the main entity's vaccine
        with working_in(Entity.objects.get(is_main=True)):
            self.assertFalse(vaccines.scoped_vaccinations().filter(pk=dose.pk).exists())

    def test_a_non_vet_entity_records_nothing(self):
        from entities.current import working_in
        from entities.services import save_entity

        shop = save_entity({"code": "SH1", "name_ar": "محل", "activity_slug": "commercial", "sub_activity_slug": "retail"}, self.owner)
        with working_in(shop):
            with self.assertRaisesMessage(ValidationError, "للعيادات البيطرية"):
                self.dose()
            with self.assertRaisesMessage(ValidationError, "للعيادات البيطرية"):
                vaccines.save_vaccine({"name_ar": "x"}, self.owner)

    def test_the_audit_row_names_the_dose_and_never_the_note(self):
        dose = self.dose(note="ZZNOTE", batch="LOT-1")
        row = AuditLog.objects.get(action="record_vaccination")
        self.assertEqual((row.object_type, row.object_id, row.actor), ("medical.Vaccination", str(dose.pk), self.owner))
        self.assertEqual((row.after_data["animal"], row.after_data["vaccine"], row.after_data["given_on"], row.after_data["has_note"]),
                         (self.rocky.pk, self.rabies.pk, self.today.isoformat(), True))
        self.assertNotIn("ZZNOTE", str(row.after_data))


# ---- removing one recorded by mistake ----------------------------------------------------


class RemoveTests(Clinic):
    def test_a_dose_on_no_invoice_can_be_removed(self):
        dose = self.dose()
        vaccines.remove(dose, self.owner)
        self.assertFalse(Vaccination.objects.exists())
        row = AuditLog.objects.get(action="remove_vaccination")
        self.assertEqual((row.event_type, row.before_data["vaccine"], row.object_id), (AuditEventType.DELETE, self.rabies.pk, str(dose.pk)))

    def test_a_dose_on_a_live_invoice_cannot(self):
        dose = self.dose()
        invoice = vaccines.bill(dose, self.owner)
        with self.assertRaisesMessage(ValidationError, "على فاتورة"):
            vaccines.remove(dose, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        with self.assertRaisesMessage(ValidationError, "على فاتورة"):
            vaccines.remove(dose, self.owner)
        self.assertEqual(Vaccination.objects.count(), 1)

    def test_a_dose_on_a_cancelled_invoice_can(self):
        dose = self.dose()
        invoice = vaccines.bill(dose, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "خطأ")
        vaccines.remove(dose, self.owner)
        self.assertFalse(Vaccination.objects.exists())

    def test_a_doctor_removes_their_own_and_the_owner_anyones(self):
        mine = self.dose(user=self.doctor)
        vaccines.remove(mine, self.doctor)
        theirs = self.dose(user=self.owner)
        with self.assertRaisesMessage(PermissionDenied, "دكتور تاني"):
            vaccines.remove(theirs, self.doctor)
        vaccines.remove(theirs, self.manager)
        self.assertFalse(Vaccination.objects.exists())

    def test_reception_and_the_stock_keeper_remove_nothing(self):
        dose = self.dose()
        for user in (self.desk, self.keeper):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                vaccines.remove(dose, user)
        self.assertEqual(Vaccination.objects.count(), 1)


# ---- the due list ------------------------------------------------------------------------


class DueListTests(Clinic):
    def given(self, vaccine, animal, due_in):
        """A dose due ``due_in`` days from today (negative: overdue)."""

        given_on = self.today - timedelta(days=30)
        return vaccines.record(animal, {"vaccine": vaccine, "given_on": given_on, "next_due": (self.today + timedelta(days=due_in)).isoformat()}, self.owner)

    def test_overdue_and_due_soon_are_listed_and_far_ones_are_not(self):
        late = self.given(self.rabies, self.rocky, -12)
        today = self.given(self.fvrcp, self.mishmish, 0)
        week = self.given(self.deworm, self.rocky, 6)
        month = self.given(self.deworm, self.mishmish, 29)
        self.given(self.puppy, self.rocky, vaccines.WINDOW_DAYS + 1)  # too far ahead
        self.assertEqual([row["dose"] for row in self.due_rows()], [late, today, week, month])
        first = self.due_rows()[0]
        self.assertEqual((first["days"], first["late"], first["overdue"]), (-12, 12, True))
        self.assertEqual((self.due_rows()[1]["days"], self.due_rows()[1]["overdue"]), (0, False))

    def test_the_filters_and_their_counts(self):
        late = self.given(self.rabies, self.rocky, -3)
        today = self.given(self.fvrcp, self.mishmish, 0)
        week = self.given(self.deworm, self.rocky, vaccines.WEEK_DAYS)
        month = self.given(self.deworm, self.mishmish, 20)
        result = vaccines.due_list(self.owner, today=self.today, window="overdue")
        self.assertEqual([row["dose"] for row in result["rows"]], [late])
        self.assertEqual([row["dose"] for row in self.due_rows(window="week")], [today, week])
        self.assertEqual([row["dose"] for row in self.due_rows(window="month")], [today, week, month])
        self.assertEqual([row["dose"] for row in self.due_rows(window="nonsense")], [late, today, week, month])  # an unknown filter is "all"
        self.assertEqual(result["counts"], {"overdue": 1, "week": 2, "month": 3})  # the chips do not change with the filter

    def test_a_superseded_dose_is_absent_and_so_is_a_one_off(self):
        old = self.given(self.rabies, self.rocky, -5)
        vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": self.today}, self.owner)  # a year ahead now
        self.dose(vaccine=self.puppy, days_ago=2)  # no next dose
        self.assertNotIn(old, [row["dose"] for row in self.due_rows()])
        self.assertEqual(self.due_rows(), [])

    def test_search_by_animal_owner_phone_and_vaccine(self):
        self.given(self.rabies, self.rocky, 2)
        other_owner = make_customer(customer_code="V-2", name="حسن فؤاد", phone="01009998877")
        bella = vet.save_animal(other_owner, {"name": "بيلا", "species": "cat"}, self.owner)
        self.given(self.fvrcp, bella, 3)
        self.assertEqual([r["animal"].name for r in self.due_rows(q="روكي")], ["روكي"])
        self.assertEqual([r["animal"].name for r in self.due_rows(q="حسن")], ["بيلا"])
        self.assertEqual([r["animal"].name for r in self.due_rows(q="0100999")], ["بيلا"])
        self.assertEqual([r["animal"].name for r in self.due_rows(q="Rabies")], ["روكي"])
        self.assertEqual([r["animal"].name for r in self.due_rows(q="ثلاثي")], ["بيلا"])
        self.assertEqual(self.due_rows(q="مفيش"), [])

    def test_a_row_carries_the_owners_name_and_phone_for_those_allowed(self):
        self.given(self.rabies, self.rocky, 2)
        row = self.due_rows()[0]
        self.assertEqual((row["owner"], row["phone"], row["call"], row["whatsapp"]), (self.mona, "01001234567", "01001234567", "201001234567"))
        self.mona.whatsapp = "01112223334"
        self.mona.save()
        row = self.due_rows()[0]
        self.assertEqual((row["phone"], row["call"], row["whatsapp"]), ("01112223334", "01001234567", "201112223334"))

    def test_an_animal_that_no_longer_comes_and_an_owner_switched_off_are_left_out(self):
        self.given(self.rabies, self.rocky, 2)
        vet.save_animal(self.mona, {"name": "روكي", "species": "dog", "active": False}, self.owner, self.rocky)
        self.assertEqual(self.due_rows(), [])
        vet.save_animal(self.mona, {"name": "روكي", "species": "dog", "active": True}, self.owner, self.rocky)
        self.assertEqual(len(self.due_rows()), 1)
        self.mona.active = False
        self.mona.save()
        self.assertEqual(self.due_rows(), [])

    def test_another_entitys_doses_are_absent(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        with working_in(branch):
            local = vaccines.save_vaccine({"name_ar": "سعار الفرع", "species": "dog", "interval_days": "365"}, self.owner)
            far = vaccines.record(self.rocky, {"vaccine": local, "given_on": self.today - timedelta(days=30), "next_due": (self.today + timedelta(days=4)).isoformat()}, self.owner)
            self.assertEqual([row["dose"] for row in vaccines.due_list(self.owner, today=self.today)["rows"]], [far])
        mine = self.given(self.rabies, self.rocky, 2)  # recorded in the whole-group view: the main entity's
        with working_in(Entity.objects.get(is_main=True)):
            self.assertEqual([row["dose"] for row in vaccines.due_list(self.owner, today=self.today)["rows"]], [mine])
        with working_in(branch):
            self.assertEqual([row["dose"] for row in vaccines.due_list(self.owner, today=self.today)["rows"]], [far])
        self.assertEqual(len(self.due_rows()), 2)  # the whole group sees both

    def test_a_later_dose_in_another_entity_still_closes_it(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        old = self.given(self.rabies, self.rocky, 2)
        Vaccination.objects.create(animal=self.rocky, vaccine=self.rabies, given_on=self.today, next_due=self.today + timedelta(days=365), entity=branch,
                                   created_by=self.owner)  # the animal got its booster at the other branch
        self.assertTrue(vaccines.superseded(old))  # the animal belongs to the group, not to a branch
        with working_in(Entity.objects.get(is_main=True)):
            self.assertEqual(vaccines.due_list(self.owner, today=self.today)["rows"], [])

    def test_who_may_read_it(self):
        self.given(self.rabies, self.rocky, 2)
        for user in (self.owner, self.manager, self.doctor):
            with self.subTest(user=user.username):
                self.assertEqual(len(vaccines.due_list(user, today=self.today)["rows"]), 1)
        for user in (self.desk, self.keeper, person(RoleCode.ACCOUNTANT, "vx_acc")):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                vaccines.due_list(user, today=self.today)

    def test_it_is_for_vets_only(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(sub_activity_slug="clinic")
        with self.assertRaisesMessage(ValidationError, "للعيادات البيطرية"):
            vaccines.due_list(self.owner, today=self.today)

    def test_the_animals_card_lists_every_dose_with_its_state(self):
        old = self.dose(days_ago=500)
        new = self.dose(days_ago=1)
        rows = vaccines.card(self.rocky)
        self.assertEqual([(row["dose"], row["closed"], row["overdue"]) for row in rows], [(new, False, False), (old, True, False)])
        self.assertEqual([row["billable"] for row in rows], [True, True])
        stale = self.given(self.deworm, self.rocky, -4)
        self.assertEqual([(row["dose"], row["overdue"], row["days"]) for row in vaccines.card(self.rocky) if row["dose"] == stale], [(stale, True, -4)])


# ---- the optional draft bill -------------------------------------------------------------


class BillTests(Clinic):
    def test_billing_makes_one_draft_of_exactly_the_items_price_and_moves_nothing(self):
        dose = self.dose(note="ZZNOTE", batch="LOT-9")
        before = footprint()
        invoice = vaccines.bill(dose, self.owner)
        dose.refresh_from_db()
        self.assertEqual((invoice.status, dose.invoice), ("draft", invoice))
        self.assertEqual(self.lines(invoice), [("VAC-RAB", D("1"), D("250.00"), D("250.00"))])
        self.assertEqual((invoice.subtotal, invoice.discount_amount, invoice.tax_amount, invoice.total_amount, invoice.paid_now, invoice.remaining_due),
                         (D("250.00"), D("0"), D("0"), D("250.00"), D("0"), D("250.00")))
        self.assertEqual((invoice.customer, invoice.selling_location, invoice.cashbox, invoice.invoice_date), (self.mona, self.location, None, self.today))
        self.assertTrue(invoice.invoice_number.startswith("SI-"))
        text = invoice.lines.get().description + invoice.notes
        self.assertIn("روكي", text)
        self.assertIn("سعار", text)
        self.assertNotIn("ZZNOTE", text)  # clinical text never reaches an invoice
        self.assertNotIn("LOT-9", text)
        # Nothing was posted: no stock movement, no cashbox movement, no customer-ledger row, no payment, no journal entry.
        self.assertEqual(footprint(), before)
        self.assertEqual(customer_balance(self.mona), D("0"))
        self.assertEqual(on_hand([self.location])[(self.location.pk, self.rabies_item.pk)], D("10"))

    def test_recording_a_dose_moves_nothing_either(self):
        before = footprint()
        self.dose()
        self.dose(animal=self.mishmish, vaccine=self.fvrcp)
        self.assertEqual(footprint(), before)
        self.assertEqual(SalesInvoice.objects.count(), 0)

    def test_posting_the_draft_through_the_ordinary_service_is_what_moves_the_stock_and_the_account(self):
        dose = self.dose()
        invoice = vaccines.bill(dose, self.owner)
        moves_before = StockMovement.objects.count()
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(on_hand([self.location])[(self.location.pk, self.rabies_item.pk)], D("9"))  # one dose left the shelf, now
        self.assertEqual(StockMovement.objects.count(), moves_before + 1)
        movement = StockMovement.objects.order_by("-pk").first()
        self.assertEqual((movement.item, movement.quantity, movement.location), (self.rabies_item, D("1"), self.location))
        self.assertEqual(customer_balance(self.mona), D("250.00"))
        self.assertEqual(vaccines.card(self.rocky)[0]["live"], True)

    def test_a_service_item_moves_no_stock_even_when_posted(self):
        dose = self.dose(vaccine=self.deworm)
        invoice = vaccines.bill(dose, self.owner)
        self.assertEqual(self.lines(invoice), [("VAC-SRV", D("1"), D("100.00"), D("100.00"))])
        moves_before = StockMovement.objects.count()
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(StockMovement.objects.count(), moves_before)

    def test_tax_is_what_the_draft_service_computes(self):
        set_capability_enabled(self.profile, "vat", True, user=self.owner)  # every item at the default 14%
        invoice = vaccines.bill(self.dose(), self.owner)
        taxes = [(row.taxable_amount, row.rate, row.tax_amount) for row in SalesLineTax.objects.filter(line__invoice=invoice)]
        self.assertEqual(taxes, [(D("250.00"), D("14.00"), D("35.00"))])
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.remaining_due), (D("250.00"), D("35.00"), D("285.00"), D("285.00")))

    def test_a_dose_is_billed_once(self):
        dose = self.dose()
        invoice = vaccines.bill(dose, self.owner)
        with self.assertRaisesMessage(ValidationError, invoice.invoice_number):
            vaccines.bill(dose, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        with self.assertRaisesMessage(ValidationError, "اتفوتر خلاص"):
            vaccines.bill(dose, self.owner)
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertFalse(vaccines.card(self.rocky)[0]["billable"])

    def test_after_the_invoice_is_cancelled_it_can_be_billed_again(self):
        dose = self.dose()
        draft = vaccines.bill(dose, self.owner)
        post_sales_invoice(draft.pk, self.owner)
        cancel_posted_sales_invoice(draft.pk, self.owner, "خطأ")
        draft.refresh_from_db()
        self.assertEqual(draft.status, "cancelled")
        self.assertTrue(vaccines.card(self.rocky)[0]["billable"])
        again = vaccines.bill(dose, self.owner)
        dose.refresh_from_db()
        self.assertEqual((dose.invoice, again.status, again.total_amount), (again, "draft", D("250.00")))
        self.assertNotEqual(again.pk, draft.pk)
        self.assertEqual(SalesInvoice.objects.count(), 2)

    def test_the_price_is_the_items_price_on_the_day_it_is_billed(self):
        dose = self.dose()
        self.rabies_item.default_sale_price = D("300.00")
        self.rabies_item.save()
        self.assertEqual(vaccines.bill(dose, self.owner).total_amount, D("300.00"))

    def test_a_vaccine_without_an_item_or_with_a_switched_off_one_is_not_billed(self):
        free = self.dose(vaccine=self.puppy)
        with self.assertRaisesMessage(ValidationError, "مالوش صنف"):
            vaccines.bill(free, self.owner)
        self.assertFalse(vaccines.card(self.rocky)[0]["billable"])
        dose = self.dose(days_ago=1)
        self.rabies_item.active = False
        self.rabies_item.save()
        with self.assertRaisesMessage(ValidationError, "موقوف"):
            vaccines.bill(dose, self.owner)
        self.assertEqual(SalesInvoice.objects.count(), 0)

    def test_it_needs_the_create_invoice_permission(self):
        dose = self.dose()
        for user in (self.keeper,):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                vaccines.bill(dose, user)
        self.assertEqual(SalesInvoice.objects.count(), 0)
        self.assertEqual(vaccines.bill(dose, self.desk).status, "draft")  # whoever may make an invoice

    def test_the_invoice_lands_in_the_doses_entity_whoever_makes_it(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        branch_store = make_location(location_code="BR-STORE", name_ar="مخزن الفرع", entity=branch, is_selling_location=True)
        with working_in(branch):
            local = vaccines.save_vaccine({"name_ar": "سعار الفرع", "species": "dog", "interval_days": "365", "item": self.service_item}, self.owner)
            dose = vaccines.record(self.rocky, {"vaccine": local, "given_on": self.today}, self.owner)
        with working_in(Entity.objects.get(is_main=True)):  # billed by someone working in the main entity
            invoice = vaccines.bill(dose, self.owner)
        self.assertEqual((invoice.selling_location, invoice.total_amount), (branch_store, D("100.00")))

    def test_the_audit_row_has_the_invoice_and_its_totals(self):
        invoice = vaccines.bill(self.dose(), self.owner)
        row = AuditLog.objects.get(action="bill_vaccination")
        self.assertEqual((row.after_data["invoice"], row.after_data["total"]), (invoice.invoice_number, "250.00"))


# ---- who may record ----------------------------------------------------------------------


class AccessTests(Clinic):
    def test_the_clinical_roles_record_and_the_others_cannot(self):
        for user in (self.owner, self.manager, self.doctor):
            with self.subTest(user=user.username):
                dose = vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": self.today - timedelta(days=len(Vaccination.objects.all()))}, user)
                self.assertEqual(dose.created_by, user)
        for user in (self.desk, self.keeper, person(RoleCode.ACCOUNTANT, "vx_acc2")):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                self.dose(user=user, animal=self.mishmish, vaccine=self.fvrcp)
        self.assertEqual(Vaccination.objects.count(), 3)
