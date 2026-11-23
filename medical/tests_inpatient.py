"""M8 (HG-088): inpatient — beds, admission, charges, discharge and the stay's bill.

What these pin, with the money written out: the bed-day rule at every boundary; one open admission per patient
and per bed; a move closing the old bed's days; charges billed once and frozen; discharge making a DRAFT invoice
with exact lines, tax and totals and moving nothing (no stock, cashbox, customer-ledger or journal row); the
ordinary posting being what moves the stock of a supply; the advance as an S5 deposit (taken, applied, refunded);
who may do what; entity scoping; the audit trail.

"Today" is always the Cairo local date and every past moment is built from it, so the suite passes at any hour
of the night and on the first or last day of a month.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from itertools import product

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from approvals.models import ApprovalRequest, ApprovalStatus
from approvals.services import approve, set_held_actions
from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from deposits import services as deposits
from deposits.models import Deposit, DepositStatus
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.warehouses import on_hand
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from reports.tests_shell import sidebar_links
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settlements.models import InvoiceSettlement
from staff.services import save_employee
from taxes.models import SalesLineTax

from . import inpatient
from .models import Admission, AdmissionCharge, AdmissionState, ChargeKind

MODULES = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians,inventory"


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def at(day, hour, minute=0):
    """A moment on a Cairo local day."""

    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def customer_balance(customer):
    agg = customer.ledger_entries.aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
    return customer.opening_balance + (agg["i"] or D("0")) - (agg["d"] or D("0"))


def cash_balance(cashbox):
    moves = CashboxMovement.objects.filter(cashbox=cashbox)
    return (moves.filter(direction="in").aggregate(t=Sum("amount"))["t"] or D("0")) - (moves.filter(direction="out").aggregate(t=Sum("amount"))["t"] or D("0"))


def bal(code):
    """Debit-positive balance of a ledger account (a liability shows negative)."""

    Projector().rebuild()
    agg = JournalLine.objects.filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


def footprint():
    """Every table money or stock is written to. A discharge must leave all of them exactly as they were."""

    Projector().rebuild()
    return {"stock": StockMovement.objects.count(), "cash": CashboxMovement.objects.count(), "customer_ledger": CustomerLedgerEntry.objects.count(),
            "payments": CustomerPayment.objects.count(), "journal": JournalEntry.objects.count(), "journal_lines": JournalLine.objects.count(),
            "posted": SalesInvoice.objects.filter(status="posted").count()}


# ---- the rule, on its own ----------------------------------------------------------


class BedDayRuleTests(SimpleTestCase):
    """``bed_days(admitted_on, since_on, until_on, closing)``: the admission day is billed, the discharge day is not,
    the shortest stay is one day, and the day of a move goes to the new bed."""

    D0 = date(2026, 3, 10)

    def day(self, n):
        return self.D0 + timedelta(days=n)

    def test_a_same_day_stay_is_one_day(self):
        self.assertEqual(inpatient.bed_days(self.D0, self.D0, self.D0, closing=True), [self.D0])

    def test_one_night_is_one_day_and_the_discharge_day_is_not_billed(self):
        self.assertEqual(inpatient.bed_days(self.D0, self.D0, self.day(1), closing=True), [self.D0])

    def test_three_nights_are_three_days(self):
        self.assertEqual(inpatient.bed_days(self.D0, self.D0, self.day(3), closing=True), [self.D0, self.day(1), self.day(2)])

    def test_a_stay_across_the_end_of_a_month_and_of_a_year(self):
        self.assertEqual(inpatient.bed_days(date(2026, 1, 30), date(2026, 1, 30), date(2026, 2, 2), closing=True),
                         [date(2026, 1, 30), date(2026, 1, 31), date(2026, 2, 1)])
        self.assertEqual(inpatient.bed_days(date(2026, 12, 31), date(2026, 12, 31), date(2027, 1, 1), closing=True), [date(2026, 12, 31)])
        self.assertEqual(inpatient.bed_days(date(2028, 2, 28), date(2028, 2, 28), date(2028, 3, 1), closing=True), [date(2028, 2, 28), date(2028, 2, 29)])

    def test_a_move_gives_the_old_bed_the_days_before_it_and_the_new_bed_the_day_of_the_move(self):
        old = inpatient.bed_days(self.D0, self.D0, self.day(2), closing=False)
        new = inpatient.bed_days(self.D0, self.day(2), self.day(5), closing=True)
        self.assertEqual(old, [self.D0, self.day(1)])
        self.assertEqual(new, [self.day(2), self.day(3), self.day(4)])
        self.assertEqual(len(set(old + new)), 5)  # five nights, five days, none twice

    def test_a_move_on_the_admission_day_bills_the_old_bed_nothing(self):
        self.assertEqual(inpatient.bed_days(self.D0, self.D0, self.D0, closing=False), [])

    def test_admitted_moved_and_discharged_on_one_day_is_one_day_on_the_last_bed(self):
        self.assertEqual(inpatient.bed_days(self.D0, self.D0, self.D0, closing=False), [])
        self.assertEqual(inpatient.bed_days(self.D0, self.D0, self.D0, closing=True), [self.D0])

    def test_a_move_and_a_discharge_on_the_same_later_day_bill_the_new_bed_nothing(self):
        self.assertEqual(inpatient.bed_days(self.D0, self.D0, self.day(2), closing=False), [self.D0, self.day(1)])
        self.assertEqual(inpatient.bed_days(self.D0, self.day(2), self.day(2), closing=True), [])

    def test_every_day_of_any_stay_is_billed_exactly_once(self):
        """Every stay of up to six days with up to two moves on any days: the days billed are exactly the nights
        (one for a same-day stay), each once."""

        for length, first_move, second_move in product(range(0, 7), range(0, 7), range(0, 7)):
            if not first_move <= second_move <= length:
                continue
            marks = [0, first_move, second_move, length]  # admission, two moves, discharge
            billed = []
            for index in range(3):
                billed += inpatient.bed_days(self.D0, self.day(marks[index]), self.day(marks[index + 1]), closing=index == 2)
            with self.subTest(length=length, moves=(first_move, second_move)):
                self.assertEqual(sorted(billed), [self.day(n) for n in range(max(length, 1))])


# ---- the fixture -------------------------------------------------------------------


class Hospital(TestCase):
    """A medical centre (the capability is suggested there) with two wards, three beds, staff of every kind."""

    def setUp(self):
        self.profile = prepared_client("medical", "medical_center", MODULES)
        self.today = timezone.localdate()
        self.location = make_location(location_code="CENTER", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="IP-CASH", is_default=True)
        self.owner = person(RoleCode.OWNER, "ip_owner")
        self.manager = person(RoleCode.MANAGER, "ip_manager")
        self.doctor = person(RoleCode.DOCTOR, "ip_doctor")
        self.sara = save_employee({"name": "د. سارة", "title": "جرّاحة"}, self.owner)
        self.sara.user = self.doctor
        self.sara.save()
        self.desk = self.linked(person(RoleCode.CASHIER, "ip_desk"), "منى الاستقبال")
        self.keeper = self.linked(person(RoleCode.STOCK_KEEPER, "ip_keeper"), "عم سيد")
        self.nobody = self.linked(make_user(username="ip_nobody"), "عامل نظافة")  # on the payroll, no role at all
        self.room_day = make_item(item_code="BED-STD", item_name="إقامة يوم — غرفة عادية", is_stock_tracked=False, default_sale_price="500.00")
        self.icu_day = make_item(item_code="BED-ICU", item_name="إقامة يوم — عناية", is_stock_tracked=False, default_sale_price="1200.00")
        self.sonar = make_item(item_code="SRV-SONAR", item_name="سونار", is_stock_tracked=False, default_sale_price="600.00")
        self.appendix = make_item(item_code="OP-APP", item_name="استئصال الزايدة", is_stock_tracked=False, default_sale_price="8000.00")
        self.saline = make_item(item_code="SUP-SAL", item_name="محلول ملح", is_stock_tracked=True, default_sale_price="45.00")
        stock_in(self.saline, self.location, 10, "20.00")
        self.ward = inpatient.save_ward({"name_ar": "الدور الأول", "name_en": "First floor"}, self.owner)
        self.icu = inpatient.save_ward({"name_ar": "العناية"}, self.owner)
        self.bed1 = inpatient.save_bed(self.ward, {"code": "101", "daily_service": self.room_day}, self.owner)
        self.bed2 = inpatient.save_bed(self.ward, {"code": "102", "daily_service": self.room_day}, self.owner)
        self.icu1 = inpatient.save_bed(self.icu, {"code": "ICU-1", "daily_service": self.icu_day}, self.owner)
        self.patient = make_customer(customer_code="P-1", name="منى علي", phone="01001234567")
        self.other = make_customer(customer_code="P-2", name="حسن فؤاد", phone="01009998877")

    def linked(self, user, name):
        employee = save_employee({"name": name}, self.owner)
        employee.user = user
        employee.save()
        return user

    def admit(self, days_ago=0, hour=0, patient=None, bed=None, user=None, **extra):
        """Admitted ``days_ago`` days ago at ``hour``. Hour 0 of today is always in the past, whatever the clock says."""

        return inpatient.admit(patient or self.patient, bed or self.bed1, user or self.owner, when=at(self.today - timedelta(days=days_ago), hour), **extra)

    def bed_day_rows(self, admission):
        return [(c.day, c.bed.code, c.price) for c in admission.charges.filter(kind=ChargeKind.BED_DAY).order_by("day")]

    def stay_url(self, admission, lang="ar", **extra):
        query = "".join(f"&{key}={value}" for key, value in extra.items())
        return f"{reverse('medical:stay', args=[admission.pk])}?lang={lang}{query}"

    def lines(self, invoice):
        return [(line.item.item_code, line.quantity, line.unit_sale_price, line.line_total_amount) for line in invoice.lines.order_by("line_number")]


# ---- bed-days through the services, with their amounts ------------------------------


class BedDayServiceTests(Hospital):
    def test_a_same_day_stay_bills_one_day(self):
        admission = self.admit(days_ago=0, hour=0)
        inpatient.discharge(admission, self.owner)
        self.assertEqual(self.bed_day_rows(admission), [(self.today, "101", D("500.00"))])

    def test_a_stay_across_midnight_bills_the_night_it_began(self):
        yesterday = self.today - timedelta(days=1)
        admission = inpatient.admit(self.patient, self.bed1, self.owner, when=at(yesterday, 23, 59))
        inpatient.discharge(admission, self.owner, when=at(self.today, 0, 0))  # one minute later, the next day
        self.assertEqual(self.bed_day_rows(admission), [(yesterday, "101", D("500.00"))])

    def test_three_nights_bill_three_days_and_not_the_discharge_day(self):
        admission = self.admit(days_ago=3, hour=10)
        inpatient.discharge(admission, self.owner)
        days = [self.today - timedelta(days=n) for n in (3, 2, 1)]
        self.assertEqual(self.bed_day_rows(admission), [(day, "101", D("500.00")) for day in days])
        self.assertEqual(sum(c.price for c in admission.charges.all()), D("1500.00"))

    def test_a_move_mid_stay_closes_the_old_bed_at_its_price_and_never_bills_a_day_twice(self):
        admission = self.admit(days_ago=4, hour=9)
        moved_on = self.today - timedelta(days=2)
        inpatient.move(admission, self.icu1, self.owner, when=at(moved_on, 15))
        admission.refresh_from_db()
        self.assertEqual((admission.bed, admission.bed_since), (self.icu1, at(moved_on, 15)))
        self.assertEqual(self.bed_day_rows(admission), [(self.today - timedelta(days=4), "101", D("500.00")), (self.today - timedelta(days=3), "101", D("500.00"))])
        inpatient.discharge(admission, self.owner)
        rows = self.bed_day_rows(admission)
        self.assertEqual(rows, [(self.today - timedelta(days=4), "101", D("500.00")), (self.today - timedelta(days=3), "101", D("500.00")),
                                (moved_on, "ICU-1", D("1200.00")), (self.today - timedelta(days=1), "ICU-1", D("1200.00"))])
        self.assertEqual(len({day for day, _, _ in rows}), 4)  # four nights, four days
        self.assertEqual(sum(price for _, _, price in rows), D("3400.00"))  # 2 × 500 + 2 × 1200

    def test_moving_there_and_back_on_one_day_bills_that_day_once(self):
        admission = self.admit(days_ago=2, hour=8)
        moved_on = self.today - timedelta(days=1)
        inpatient.move(admission, self.icu1, self.owner, when=at(moved_on, 10))
        inpatient.move(admission, self.bed2, self.owner, when=at(moved_on, 18))
        inpatient.discharge(admission, self.owner)
        self.assertEqual(self.bed_day_rows(admission), [(self.today - timedelta(days=2), "101", D("500.00")), (moved_on, "102", D("500.00"))])

    def test_a_move_and_a_discharge_on_the_admission_day_bill_one_day_on_the_last_bed(self):
        admission = self.admit(days_ago=0, hour=0)
        inpatient.move(admission, self.icu1, self.owner)
        self.assertEqual(self.bed_day_rows(admission), [])
        inpatient.discharge(admission, self.owner)
        self.assertEqual(self.bed_day_rows(admission), [(self.today, "ICU-1", D("1200.00"))])

    def test_the_table_refuses_two_bed_days_on_one_day_of_one_stay(self):
        admission = self.admit(days_ago=1, hour=9)
        inpatient.discharge(admission, self.owner)
        day = self.today - timedelta(days=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdmissionCharge.objects.create(admission=admission, kind=ChargeKind.BED_DAY, item=self.room_day, quantity=1, price=D("500.00"), day=day,
                                           bed=self.bed1, created_by=self.owner)

    def test_the_running_view_shows_what_a_discharge_now_would_write_and_saves_nothing(self):
        admission = self.admit(days_ago=2, hour=9)
        pending = inpatient.pending_bed_days(admission)
        self.assertEqual(pending, [(self.today - timedelta(days=2), D("500.00")), (self.today - timedelta(days=1), D("500.00"))])
        self.assertEqual(admission.charges.count(), 0)
        self.assertEqual(inpatient.stay_day(admission), 3)  # «اليوم 3 من الإقامة»: the admission day is day 1

    def test_times_that_cannot_be_are_refused(self):
        with self.assertRaises(ValidationError):
            inpatient.admit(self.patient, self.bed1, self.owner, when=timezone.now() + timedelta(hours=2))
        admission = self.admit(days_ago=1, hour=12)
        with self.assertRaises(ValidationError):
            inpatient.discharge(admission, self.owner, when=at(self.today - timedelta(days=1), 11))  # before the patient took the bed
        with self.assertRaises(ValidationError):
            inpatient.move(admission, self.bed2, self.owner, when=at(self.today - timedelta(days=2), 9))
        admission.refresh_from_db()
        self.assertEqual((admission.state, admission.bed), (AdmissionState.ADMITTED, self.bed1))


# ---- one patient, one bed ----------------------------------------------------------


class OccupancyTests(Hospital):
    def test_one_open_admission_per_patient_and_per_bed(self):
        admission = self.admit()
        self.assertEqual((admission.number, admission.state, admission.entity), ("ADM-00001", AdmissionState.ADMITTED, self.ward.entity))
        with self.assertRaisesMessage(ValidationError, "مقيم بالفعل"):
            self.admit(bed=self.bed2)  # the same patient, another bed
        with self.assertRaisesMessage(ValidationError, "عليه مريض"):
            self.admit(patient=self.other)  # another patient, the same bed
        self.assertEqual(Admission.objects.count(), 1)
        second = self.admit(patient=self.other, bed=self.bed2)
        self.assertEqual(second.number, "ADM-00002")

    def test_the_table_itself_refuses_a_second_open_admission(self):
        admission = self.admit()
        values = {"bed_since": admission.admitted_at, "admitted_at": admission.admitted_at, "created_by": self.owner}
        with self.assertRaises(IntegrityError), transaction.atomic():
            Admission.objects.create(number="ADM-X1", patient=self.patient, bed=self.bed2, **values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Admission.objects.create(number="ADM-X2", patient=self.other, bed=self.bed1, **values)

    def test_a_bed_out_of_service_takes_nobody_and_an_occupied_one_cannot_go_out(self):
        inpatient.save_bed(self.ward, {"active": False}, self.owner, bed=self.bed2)
        with self.assertRaisesMessage(ValidationError, "خارج الخدمة"):
            self.admit(bed=self.bed2)
        self.admit()
        with self.assertRaisesMessage(ValidationError, "عليه مريض"):
            inpatient.save_bed(self.ward, {"active": False}, self.owner, bed=self.bed1)

    def test_who_cannot_be_admitted(self):
        from .models import InsuranceContract

        insurer = make_customer(customer_code="INS-1", name="شركة تأمين")
        InsuranceContract.objects.create(insurer=insurer, coverage_percent=D("80"), created_by=self.owner)
        walk_in = make_customer(customer_code="WALK-IN", name="عميل نقدي")
        for nobody in (None, insurer, walk_in):
            with self.assertRaises(ValidationError):
                inpatient.admit(nobody, self.bed1, self.owner)
        self.assertEqual(Admission.objects.count(), 0)

    def test_a_move_frees_the_old_bed_and_refuses_a_taken_or_the_same_one(self):
        admission = self.admit()
        other = self.admit(patient=self.other, bed=self.bed2)
        with self.assertRaisesMessage(ValidationError, "عليه مريض"):
            inpatient.move(admission, self.bed2, self.owner)
        with self.assertRaisesMessage(ValidationError, "بالفعل"):
            inpatient.move(admission, self.bed1, self.owner)
        inpatient.move(admission, self.icu1, self.owner)
        inpatient.move(other, self.bed1, self.owner)  # the bed the first patient left is free
        board = {cell["bed"].code: cell["state"] for row in inpatient.board() for cell in row["beds"]}
        self.assertEqual(board, {"101": "occupied", "102": "free", "ICU-1": "occupied"})

    def test_a_discharged_patient_can_be_admitted_again_and_their_bed_is_free(self):
        admission = self.admit(days_ago=1, hour=9)
        inpatient.discharge(admission, self.owner)
        again = self.admit(bed=self.bed1)
        self.assertEqual((again.state, again.bed), (AdmissionState.ADMITTED, self.bed1))

    def test_a_cancelled_admission_frees_the_bed_and_needs_no_charges(self):
        admission = self.admit()
        charge = inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar}, self.owner)
        with self.assertRaisesMessage(ValidationError, "عليها بنود"):
            inpatient.cancel(admission, self.owner)
        inpatient.remove_charge(charge, self.owner)
        result = inpatient.cancel(admission, self.owner)
        self.assertEqual((result["admission"].state, result["outcome"]), (AdmissionState.CANCELLED, None))
        self.admit(patient=self.other)  # the bed is free again
        for change in (lambda: inpatient.discharge(admission, self.owner), lambda: inpatient.move(admission, self.bed2, self.owner),
                       lambda: inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar}, self.owner),
                       lambda: inpatient.bill(admission, self.owner)):
            with self.assertRaises(ValidationError):
                change()


# ---- charges -----------------------------------------------------------------------


class ChargeTests(Hospital):
    def test_a_service_an_operation_and_a_supply(self):
        admission = self.admit(days_ago=1, hour=9)
        yesterday = self.today - timedelta(days=1)
        sonar = inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar, "day": yesterday}, self.doctor)
        operation = inpatient.add_charge(admission, {"kind": "operation", "item": self.appendix, "surgeon": self.sara, "note": "تحت مخدر كلي", "price": "7500"}, self.doctor)
        supply = inpatient.add_charge(admission, {"kind": "supply", "item": self.saline, "quantity": "3"}, self.doctor)
        self.assertEqual((sonar.quantity, sonar.price, sonar.day, sonar.surgeon), (D("1"), D("600.00"), yesterday, None))
        self.assertEqual((operation.price, operation.surgeon, operation.note, operation.day), (D("7500.00"), self.sara, "تحت مخدر كلي", self.today))
        self.assertEqual((supply.quantity, supply.price), (D("3"), D("45.00")))
        self.assertEqual(on_hand([self.location])[(self.location.pk, self.saline.pk)], D("10"))  # a charge takes nothing off the shelf
        self.assertEqual(StockMovement.objects.count(), 1)  # the opening stock only

    def test_what_a_charge_refuses(self):
        admission = self.admit(days_ago=1, hour=9)
        bad = (
            {"kind": "bed_day", "item": self.room_day},  # a bed-day is the system's
            {"kind": "", "item": self.sonar}, {"kind": "procedure", "item": None},
            {"kind": "procedure", "item": self.saline},  # a service is not a stock item
            {"kind": "operation", "item": self.saline}, {"kind": "supply", "item": self.sonar},  # a supply is one
            {"kind": "procedure", "item": self.sonar, "quantity": "0"}, {"kind": "procedure", "item": self.sonar, "quantity": "-1"},
            {"kind": "procedure", "item": self.sonar, "quantity": "abc"}, {"kind": "procedure", "item": self.sonar, "price": "-5"},
            {"kind": "procedure", "item": self.sonar, "price": "nan"},
            {"kind": "procedure", "item": self.sonar, "day": self.today + timedelta(days=1)},  # not yet
            {"kind": "procedure", "item": self.sonar, "day": self.today - timedelta(days=2)},  # before the admission
        )
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                inpatient.add_charge(admission, data, self.owner)
        self.assertEqual(admission.charges.count(), 0)

    def test_a_surgeon_is_kept_on_an_operation_only(self):
        admission = self.admit()
        charge = inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar, "surgeon": self.sara}, self.owner)
        self.assertIsNone(charge.surgeon)


# ---- the bill ----------------------------------------------------------------------


class BillTests(Hospital):
    def stay(self):
        """Two nights in bed 101, a sonar the first day, an operation and three bottles of saline the second."""

        admission = self.admit(days_ago=2, hour=10)
        first, second = self.today - timedelta(days=2), self.today - timedelta(days=1)
        inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar, "day": first}, self.owner)
        inpatient.add_charge(admission, {"kind": "operation", "item": self.appendix, "surgeon": self.sara, "day": second, "note": "ZZNOTE"}, self.owner)
        inpatient.add_charge(admission, {"kind": "supply", "item": self.saline, "quantity": "3", "day": second}, self.owner)
        return admission

    def test_the_review_shows_exactly_what_the_discharge_will_bill(self):
        admission = self.stay()
        review = inpatient.review(admission)
        self.assertEqual([(row["item"].item_code, row["quantity"], row["unit_sale_price"], row["total"]) for row in review["rows"]],
                         [("BED-STD", D("2"), D("500.00"), D("1000.00")), ("SRV-SONAR", D("1"), D("600.00"), D("600.00")),
                          ("OP-APP", D("1"), D("8000.00"), D("8000.00")), ("SUP-SAL", D("3"), D("45.00"), D("135.00"))])
        self.assertEqual((review["bed_days"], review["subtotal"], review["tax"], review["total"], review["advance"]),
                         (2, D("9735.00"), D("0"), D("9735.00"), D("0")))
        self.assertEqual(admission.charges.filter(kind=ChargeKind.BED_DAY).count(), 0)  # a review writes nothing

    def test_discharge_makes_one_draft_invoice_with_exact_lines_and_moves_nothing(self):
        admission = self.stay()
        before = footprint()
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        self.assertEqual((admission.state, invoice.status), (AdmissionState.DISCHARGED, "draft"))
        self.assertEqual(self.lines(invoice), [("BED-STD", D("2"), D("500.00"), D("1000.00")), ("SRV-SONAR", D("1"), D("600.00"), D("600.00")),
                                               ("OP-APP", D("1"), D("8000.00"), D("8000.00")), ("SUP-SAL", D("3"), D("45.00"), D("135.00"))])
        self.assertEqual((invoice.subtotal, invoice.discount_amount, invoice.tax_amount, invoice.total_amount, invoice.paid_now, invoice.remaining_due),
                         (D("9735.00"), D("0"), D("0"), D("9735.00"), D("0"), D("9735.00")))
        self.assertEqual((invoice.customer, invoice.selling_location, invoice.cashbox, invoice.invoice_date), (self.patient, self.location, None, self.today))
        self.assertTrue(invoice.invoice_number.startswith("SI-"))
        descriptions = list(invoice.lines.order_by("line_number").values_list("description", flat=True))
        self.assertIn("101", descriptions[0])
        self.assertIn("د. سارة", descriptions[2])  # the surgeon is on the invoice
        self.assertFalse(any("ZZNOTE" in text for text in descriptions))  # a clinical note never is
        self.assertNotIn("ZZNOTE", invoice.notes)
        # Nothing was posted: no stock movement, no cashbox movement, no customer-ledger row, no payment, no journal entry.
        self.assertEqual(footprint(), before)
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(customer_balance(self.patient), D("0"))
        self.assertEqual(on_hand([self.location])[(self.location.pk, self.saline.pk)], D("10"))
        self.assertEqual(set(admission.charges.values_list("invoice_id", flat=True)), {invoice.pk})  # every charge is on it, bed-days included
        self.assertEqual(list(inpatient.invoices(admission)), [invoice])

    def test_posting_the_draft_through_the_ordinary_service_is_what_moves_the_supply_and_the_account(self):
        admission, invoice = inpatient.discharge_and_bill(self.stay(), self.owner)
        stock_before, moves_before = on_hand([self.location])[(self.location.pk, self.saline.pk)], StockMovement.objects.count()
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")
        self.assertEqual((stock_before, on_hand([self.location])[(self.location.pk, self.saline.pk)]), (D("10"), D("7")))  # three bottles left the shelf
        self.assertEqual(StockMovement.objects.count(), moves_before + 1)  # one movement: the supply; services move no stock
        movement = StockMovement.objects.order_by("-pk").first()
        self.assertEqual((movement.item, movement.quantity, movement.location), (self.saline, D("3"), self.location))
        self.assertEqual(customer_balance(self.patient), D("9735.00"))  # the patient owes the stay
        self.assertEqual(CashboxMovement.objects.count(), 0)  # nothing was paid

    def test_tax_is_what_the_draft_service_computes(self):
        set_capability_enabled(self.profile, "vat", True, user=self.owner)  # every item at the default 14%
        admission = self.stay()
        review = inpatient.review(admission)
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        taxes = [(row.taxable_amount, row.rate, row.tax_amount) for row in SalesLineTax.objects.filter(line__invoice=invoice).order_by("line__line_number")]
        self.assertEqual(taxes, [(D("1000.00"), D("14.00"), D("140.00")), (D("600.00"), D("14.00"), D("84.00")),
                                 (D("8000.00"), D("14.00"), D("1120.00")), (D("135.00"), D("14.00"), D("18.90"))])
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.remaining_due),
                         (D("9735.00"), D("1362.90"), D("11097.90"), D("11097.90")))
        self.assertEqual((review["subtotal"], review["tax"], review["total"]), (invoice.subtotal, invoice.tax_amount, invoice.total_amount))  # the review said so

    def test_a_charge_is_billed_once_and_frozen_after_billing(self):
        admission, invoice = inpatient.discharge_and_bill(self.stay(), self.owner)
        with self.assertRaisesMessage(ValidationError, "مفيش حاجة جديدة"):
            inpatient.bill(admission, self.owner)  # again: nothing new
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(invoice.lines.count(), 4)
        for charge in admission.charges.exclude(kind=ChargeKind.BED_DAY):
            with self.assertRaisesMessage(ValidationError, "اتفوتر"):
                inpatient.remove_charge(charge, self.owner)
        with self.assertRaisesMessage(ValidationError, "بتتحسب لوحدها"):
            inpatient.remove_charge(admission.charges.filter(kind=ChargeKind.BED_DAY).first(), self.owner)
        with self.assertRaises(ValidationError):
            inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar}, self.owner)  # the stay is closed
        with self.assertRaises(ValidationError):
            inpatient.discharge(admission, self.owner)  # a second discharge writes no more bed-days
        self.assertEqual(admission.charges.count(), 5)  # two bed-days and three charges, as billed

    def test_a_stay_is_billed_after_discharge_only(self):
        admission = self.stay()
        with self.assertRaisesMessage(ValidationError, "بعد الخروج"):
            inpatient.bill(admission, self.owner)
        self.assertEqual(SalesInvoice.objects.count(), 0)

    def test_a_cancelled_invoice_lets_the_stay_be_billed_again_once(self):
        admission, invoice = inpatient.discharge_and_bill(self.stay(), self.owner)
        SalesInvoice.objects.filter(pk=invoice.pk).update(status="cancelled")
        again = inpatient.bill(admission, self.owner)
        self.assertNotEqual(again.pk, invoice.pk)
        self.assertEqual((again.status, again.total_amount, self.lines(again)), ("draft", D("9735.00"), self.lines(invoice)))
        with self.assertRaises(ValidationError):
            inpatient.bill(admission, self.owner)
        self.assertEqual(list(inpatient.invoices(admission)), [again])  # the stay points at its live bill

    def test_a_failed_bill_leaves_the_patient_admitted(self):
        admission = self.stay()
        self.location.is_selling_location = False
        self.location.save()
        with self.assertRaisesMessage(ValidationError, "مخزن بيع"):
            inpatient.discharge_and_bill(admission, self.owner)
        admission.refresh_from_db()
        self.assertEqual((admission.state, admission.charges.filter(kind=ChargeKind.BED_DAY).count()), (AdmissionState.ADMITTED, 0))

    def test_an_insured_patient_is_billed_in_full_and_the_page_says_so(self):
        from . import insurance
        from .models import InsuredBill

        insurer = make_customer(customer_code="INS-1", name="مصر للتأمين")
        contract = insurance.save_contract({"insurer": insurer, "coverage_percent": "80", "claim_day": "1"}, self.owner)
        insurance.set_cover(self.patient, {"contract": contract, "member_number": "M-77"}, self.owner)
        admission = self.stay()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.stay_url(admission)), "data-insured-note")
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        self.assertEqual((invoice.customer, invoice.total_amount), (self.patient, D("9735.00")))
        self.assertEqual((SalesInvoice.objects.count(), InsuredBill.objects.count()), (1, 0))  # no insurer invoice: the split is an open question


# ---- the advance -------------------------------------------------------------------


class AdvanceTests(Hospital):
    def setUp(self):
        super().setUp()
        set_capability_enabled(self.profile, "deposits", True, user=self.owner)  # «العربون» is off by default

    def stay(self, advance):
        admission = self.admit(days_ago=2, hour=10, advance=advance, cashbox=self.cashbox)
        inpatient.add_charge(admission, {"kind": "operation", "item": self.appendix, "surgeon": self.sara}, self.owner)
        return admission

    def test_the_advance_is_a_deposit_on_the_admission(self):
        admission = self.stay("2000")
        deposit = Deposit.objects.get()
        self.assertEqual((deposit.status, deposit.target, deposit.amount, deposit.invoice), (DepositStatus.HELD, admission, D("2000.00"), None))
        self.assertEqual((deposit.payment.customer, deposit.payment.cashbox, deposit.payment.payment_date, deposit.payment.status),
                         (self.patient, self.cashbox, self.today, "posted"))
        self.assertTrue(deposit.payment.payment_number.startswith(f"DEP-{self.today:%Y%m%d}-"))
        self.assertIn(admission.number, deposit.payment.notes)
        self.assertEqual((customer_balance(self.patient), cash_balance(self.cashbox)), (D("-2000.00"), D("2000.00")))
        self.assertEqual(bal("2105"), D("-2000.00"))  # the patient's money, held as an advance: a liability, not revenue

    def test_an_advance_has_no_lateness_and_is_never_forfeited(self):
        admission = self.stay("2000")
        far = timezone.now() + timedelta(days=400)
        self.assertEqual((deposits.is_late(admission), deposits.is_late(admission, far), deposits.policy(admission), deposits.policy(admission, far)),
                         (False, False, "refund", "refund"))
        inpatient.discharge(admission, self.owner)
        self.assertEqual(deposits.policy(Admission.objects.get(pk=admission.pk)), "refund")
        self.assertEqual(SalesInvoice.objects.count(), 0)  # no «رسوم إلغاء» invoice, ever

    def test_the_advance_is_applied_when_the_stay_invoice_posts_with_exact_remaining_amounts(self):
        admission = self.stay("2000")
        review = inpatient.review(admission)
        self.assertEqual((review["total"], review["advance"], review["after_advance"], review["credit_left"]), (D("9000.00"), D("2000.00"), D("7000.00"), D("0")))
        before = footprint()
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        deposit = Deposit.objects.get()
        self.assertEqual((deposit.status, deposit.invoice), (DepositStatus.HELD, invoice))  # pointed at the draft, not applied yet
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("9000.00"), D("0"), D("9000.00")))  # 2 × 500 + 8000
        self.assertEqual(footprint(), before)
        self.assertEqual(InvoiceSettlement.objects.count(), 0)
        post_sales_invoice(invoice.pk, self.owner)
        deposit.refresh_from_db()
        settlement = InvoiceSettlement.objects.get()
        self.assertEqual((deposit.status, settlement.invoice, settlement.amount), (DepositStatus.APPLIED, invoice, D("2000.00")))
        self.assertEqual(customer_balance(self.patient), D("7000.00"))  # 9000 − the 2000 already paid
        self.assertEqual(cash_balance(self.cashbox), D("2000.00"))  # no second cash movement
        self.assertEqual(bal("2105"), D("0.00"))  # the advance left the liability for the invoice

    def test_an_advance_larger_than_the_bill_leaves_the_rest_as_the_patients_credit(self):
        admission = self.stay("12000")
        review = inpatient.review(admission)
        self.assertEqual((review["after_advance"], review["credit_left"]), (D("0"), D("3000.00")))
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(InvoiceSettlement.objects.get().amount, D("9000.00"))
        self.assertEqual(customer_balance(self.patient), D("-3000.00"))  # 3000 stays the patient's

    def test_cancelling_the_posted_bill_holds_the_advance_again_and_the_stay_is_billed_again(self):
        admission, invoice = inpatient.discharge_and_bill(self.stay("2000"), self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "فاتورة غلط")  # the ordinary cancel; the existing hook releases the advance
        deposit = Deposit.objects.get()
        self.assertEqual((deposit.status, deposit.invoice, InvoiceSettlement.objects.count()), (DepositStatus.HELD, None, 0))
        self.assertEqual((customer_balance(self.patient), cash_balance(self.cashbox), bal("2105")), (D("-2000.00"), D("2000.00"), D("-2000.00")))
        again = inpatient.bill(admission, self.owner)  # the cancelled bill freed its charges: one new draft, the same lines
        deposit.refresh_from_db()
        self.assertEqual((again.status, again.total_amount, self.lines(again), deposit.invoice), ("draft", D("9000.00"), self.lines(invoice), again))
        with self.assertRaises(ValidationError):
            inpatient.bill(admission, self.owner)
        post_sales_invoice(again.pk, self.owner)
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, customer_balance(self.patient), cash_balance(self.cashbox), bal("2105")),
                         (DepositStatus.APPLIED, D("7000.00"), D("2000.00"), D("0.00")))

    def test_one_advance_per_stay_while_it_is_held_and_none_after_discharge(self):
        admission = self.stay("2000")
        with self.assertRaisesMessage(ValidationError, "عربون بالفعل"):
            inpatient.take_advance(admission, self.cashbox, "500", self.owner)
        inpatient.discharge(admission, self.owner)
        Deposit.objects.all().delete()
        with self.assertRaisesMessage(ValidationError, "لسه مقيم"):
            inpatient.take_advance(admission, self.cashbox, "500", self.owner)
        self.assertEqual(CustomerPayment.objects.count(), 1)

    def test_a_refused_advance_admits_nobody(self):
        for advance, cashbox in (("abc", self.cashbox), ("-5", self.cashbox), ("500", None)):
            with self.subTest(advance=advance), self.assertRaises(ValidationError):
                self.admit(advance=advance, cashbox=cashbox)
        with self.assertRaises(PermissionDenied):
            self.admit(user=self.doctor, advance="500", cashbox=self.cashbox)  # a doctor does not collect money
        set_capability_enabled(self.profile, "deposits", False, user=self.owner)
        with self.assertRaisesMessage(ValidationError, "العربون مقفول"):
            self.admit(advance="500", cashbox=self.cashbox)
        self.assertEqual((Admission.objects.count(), Deposit.objects.count(), CustomerPayment.objects.count(), CashboxMovement.objects.count()), (0, 0, 0, 0))

    def test_a_cancelled_admission_refunds_the_advance_through_the_existing_path(self):
        admission = self.admit(advance="500", cashbox=self.cashbox)
        self.assertEqual((customer_balance(self.patient), cash_balance(self.cashbox)), (D("-500.00"), D("500.00")))
        result = inpatient.cancel(admission, self.owner)
        deposit = Deposit.objects.get()
        self.assertEqual((result["outcome"], deposit.status, deposit.payment.status), ("refunded", DepositStatus.REFUNDED, "cancelled"))
        self.assertEqual((customer_balance(self.patient), cash_balance(self.cashbox), bal("2105")), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(SalesInvoice.objects.count(), 0)  # refunded, not turned into a fee

    def test_a_refund_follows_the_collection_permission_and_the_second_person_rule(self):
        admission = self.admit(advance="500", cashbox=self.cashbox)
        result = inpatient.cancel(admission, self.doctor)  # a doctor may cancel the admission, not hand money back
        deposit = Deposit.objects.get()
        self.assertEqual((result["outcome"], deposit.status, cash_balance(self.cashbox)), ("refund_not_allowed", DepositStatus.HELD, D("500.00")))
        set_held_actions(["sales.cancel_payment"], self.owner)  # HG-068: cancelling a collection waits for a second person
        result = inpatient.refund_advance(admission, self.desk)
        deposit.refresh_from_db()
        self.assertEqual((result["outcome"], deposit.status, deposit.payment.status), ("refund_pending", DepositStatus.HELD, "posted"))
        request = ApprovalRequest.objects.get(action="sales.cancel_payment", object_id=deposit.payment_id, status=ApprovalStatus.PENDING)
        approve(request.pk, self.owner)
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.payment.status), (DepositStatus.REFUNDED, "cancelled"))
        self.assertEqual((customer_balance(self.patient), cash_balance(self.cashbox)), (D("0.00"), D("0.00")))

    def test_the_admission_sheet_takes_the_advance_and_the_stay_page_shows_it(self):
        self.client.force_login(self.owner)
        sheet = self.client.get(reverse("medical:admit") + f"?patient={self.patient.pk}&bed={self.bed1.pk}")
        self.assertContains(sheet, "data-advance-fields")
        response = self.client.post(reverse("medical:admit"), {"lang": "ar", "patient": self.patient.pk, "bed": self.bed1.pk, "doctor": self.sara.pk,
                                                               "advance": "1500", "cashbox": self.cashbox.pk})
        admission = Admission.objects.get()
        self.assertRedirects(response, self.stay_url(admission), fetch_redirect_response=False)
        self.assertEqual((Deposit.objects.get().amount, cash_balance(self.cashbox)), (D("1500.00"), D("1500.00")))
        page = self.client.get(self.stay_url(admission, review=1))
        self.assertContains(page, "data-advance-amount")
        self.assertContains(page, 'data-review-advance>1,500.00')
        self.assertNotContains(page, "data-advance-form")  # one advance while it is held


# ---- who may do what ---------------------------------------------------------------


class AccessTests(Hospital):
    def setUp(self):
        super().setUp()
        self.admission = self.admit(days_ago=1, hour=9, diagnosis="ZZDIAGNOSIS التهاب الزايدة", doctor=self.sara)
        inpatient.add_charge(self.admission, {"kind": "operation", "item": self.appendix, "surgeon": self.sara, "note": "ZZNOTE"}, self.owner)
        self.people = {"owner": self.owner, "manager": self.manager, "doctor": self.doctor, "desk": self.desk, "keeper": self.keeper, "nobody": self.nobody}

    def status(self, who, url, method="get", data=None):
        self.client.force_login(self.people[who])
        return getattr(self.client, method)(url, data or {}).status_code

    def test_the_board_and_the_stay_open_for_clinical_staff_and_reception_only(self):
        for url in (reverse("medical:beds"), self.stay_url(self.admission), reverse("medical:admit")):
            for who in ("owner", "manager", "doctor", "desk"):
                with self.subTest(url=url, who=who):
                    self.assertEqual(self.status(who, url), 200)
            for who in ("keeper", "nobody"):  # an employee link alone opens nothing
                with self.subTest(url=url, who=who):
                    self.assertEqual(self.status(who, url), 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("medical:beds")).status_code, 302)  # signed out: the login page

    def test_the_diagnosis_and_a_charge_note_show_to_those_who_read_records_only(self):
        for who in ("owner", "manager", "doctor"):
            self.client.force_login(self.people[who])
            page = self.client.get(self.stay_url(self.admission))
            self.assertContains(page, "ZZDIAGNOSIS")
            self.assertContains(page, "ZZNOTE")
        self.client.force_login(self.desk)
        for url in (self.stay_url(self.admission), self.stay_url(self.admission, review=1), self.stay_url(self.admission, add="operation"),
                    reverse("medical:beds"), reverse("medical:admit")):
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, "ZZDIAGNOSIS")
            self.assertNotContains(page, "ZZNOTE")
            self.assertNotContains(page, "data-diagnosis")
        self.assertContains(self.client.get(reverse("medical:beds")), "منى علي")  # reception sees who is in which bed, as on the queue

    def test_reception_admits_without_a_diagnosis_and_a_linked_employee_cannot(self):
        admitted = inpatient.admit(self.other, self.bed2, self.desk, diagnosis="ZZDESK")
        self.assertEqual(admitted.diagnosis, "")  # clinical text is not reception's to write
        self.client.force_login(self.desk)
        self.assertNotContains(self.client.get(reverse("medical:admit") + f"?patient={self.patient.pk}"), 'name="diagnosis"')
        for who in (self.keeper, self.nobody):
            with self.assertRaises(PermissionDenied):
                inpatient.admit(self.other, self.icu1, who)
            with self.assertRaises(PermissionDenied):
                inpatient.move(self.admission, self.icu1, who)
            with self.assertRaises(PermissionDenied):
                inpatient.cancel(admitted, who)
        self.assertEqual(self.status("keeper", reverse("medical:admit"), "post", {"patient": self.other.pk, "bed": self.icu1.pk}), 403)

    def test_charges_and_discharge_are_clinical(self):
        for who in (self.desk, self.keeper, self.nobody):
            with self.assertRaises(PermissionDenied):
                inpatient.add_charge(self.admission, {"kind": "procedure", "item": self.sonar}, who)
            with self.assertRaises(PermissionDenied):
                inpatient.remove_charge(self.admission.charges.first(), who)
            with self.assertRaises(PermissionDenied):
                inpatient.discharge(self.admission, who)
            with self.assertRaises(PermissionDenied):
                inpatient.set_diagnosis(self.admission, "x", who)
        self.client.force_login(self.desk)
        page = self.client.post(self.stay_url(self.admission), {"lang": "ar", "action": "charge", "kind": "procedure", "item": self.sonar.pk})
        self.assertContains(page, "data-stay-error")
        page = self.client.post(self.stay_url(self.admission), {"lang": "ar", "action": "discharge"})
        self.assertContains(page, "data-stay-error")
        page = self.client.get(self.stay_url(self.admission))
        for marker in ("data-discharge-open", 'data-add="procedure"', "data-remove-charge"):
            self.assertNotContains(page, marker)
        self.assertContains(page, "data-move-open")  # moving a patient is the desk's too
        self.admission.refresh_from_db()
        self.assertEqual((self.admission.state, self.admission.charges.count()), (AdmissionState.ADMITTED, 1))

    def test_a_doctor_discharges_and_reception_makes_the_invoice(self):
        self.client.force_login(self.doctor)
        review = self.client.get(self.stay_url(self.admission, review=1))
        self.assertContains(review, "data-needs-bill")
        self.client.post(self.stay_url(self.admission), {"lang": "ar", "action": "discharge"})
        self.admission.refresh_from_db()
        self.assertEqual((self.admission.state, SalesInvoice.objects.count()), (AdmissionState.DISCHARGED, 0))  # a doctor does not make invoices
        with self.assertRaises(PermissionDenied):
            inpatient.bill(self.admission, self.doctor)
        self.client.force_login(self.desk)
        page = self.client.get(self.stay_url(self.admission))
        self.assertContains(page, "data-needs-billing")
        response = self.client.post(self.stay_url(self.admission), {"lang": "ar", "action": "bill"}, follow=True)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.total_amount), ("draft", D("8500.00")))  # one night at 500 + the operation
        self.assertContains(response, f'data-stay-invoice="{invoice.pk}"')
        self.assertContains(response, reverse("sales:detail", args=[invoice.pk]))
        self.assertNotContains(response, "data-needs-billing")

    def test_wards_and_beds_are_set_up_by_owner_and_manager(self):
        url = reverse("medical:wards")
        self.assertEqual([self.status(who, url) for who in ("owner", "manager", "doctor", "desk", "keeper")], [200, 200, 403, 403, 403])
        for who in (self.doctor, self.desk):
            with self.assertRaises(PermissionDenied):
                inpatient.save_ward({"name_ar": "جديد"}, who)
            with self.assertRaises(PermissionDenied):
                inpatient.save_bed(self.ward, {"code": "X", "daily_service": self.room_day}, who)
        self.client.force_login(self.manager)
        self.client.post(url, {"lang": "ar", "action": "ward", "name_ar": "الدور التاني"})
        from .models import Bed, Ward

        ward = Ward.objects.get(name_ar="الدور التاني")
        self.client.post(url, {"lang": "ar", "action": "bed", "ward": ward.pk, "code": "201", "daily_service": self.room_day.pk})
        bed = Bed.objects.get(code="201")
        self.assertEqual((bed.ward, bed.daily_service, bed.active), (ward, self.room_day, True))
        page = self.client.post(url, {"lang": "ar", "action": "bed", "ward": ward.pk, "code": "201", "daily_service": self.room_day.pk})
        self.assertContains(page, "الرقم ده موجود")  # one number per ward
        page = self.client.post(url, {"lang": "ar", "action": "bed", "ward": ward.pk, "code": "202", "daily_service": self.saline.pk})
        self.assertContains(page, "خدمة الإقامة اليومية")  # a stock item is not a daily service
        self.client.post(url, {"lang": "ar", "action": "bed_active", "bed": bed.pk, "active": "0"})
        bed.refresh_from_db()
        self.assertFalse(bed.active)

    def test_a_small_clinic_never_sees_beds_until_it_switches_them_on(self):
        from settings_core.models import ClientProfile

        self.client.force_login(self.owner)
        self.assertIn(reverse("medical:beds"), sidebar_links(self.client.get(reverse("dashboard_snapshot"))))
        set_capability_enabled(self.profile, "inpatient", False, user=self.owner)
        self.assertNotIn(reverse("medical:beds"), sidebar_links(self.client.get(reverse("dashboard_snapshot"))))
        for url in (reverse("medical:beds"), reverse("medical:admit"), reverse("medical:wards"), self.stay_url(self.admission)):
            closed = self.client.get(url)
            self.assertEqual(closed.status_code, 403)  # the capability gate closes the whole path with its «مقفول» page
            self.assertNotContains(closed, "منى علي", status_code=403)
        self.assertFalse(inpatient.has_inpatient())
        set_capability_enabled(self.profile, "inpatient", True, user=self.owner)
        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.assertFalse(inpatient.has_inpatient())  # never outside medical

    def test_the_capability_is_suggested_for_a_medical_centre_and_only_offered_where_patients_stay(self):
        from settings_core import capabilities as caps

        self.assertEqual(caps.preset_state("medical", "medical_center", "inpatient"), caps.SUGGESTED)
        self.assertEqual([caps.fits("medical", "inpatient", sub) for sub in ("medical_center", "clinic", "other", "dental", "lab", "physio", "vet")],
                         [True, True, True, False, False, False, False])
        self.assertFalse(caps.fits("commercial", "inpatient", "pharmacy"))
        self.assertNotIn("inpatient", caps.default_selection("medical", "clinic"))  # a clinic starts without beds

    def test_settings_offers_open_on_the_feature_only_to_whom_the_board_opens_for(self):
        from settings_core.operational_views import _features

        def link(user):
            return next(row["url"] for row in _features("ar", user) if row["slug"] == "inpatient")

        self.assertEqual(link(self.owner), reverse("medical:beds"))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(link(self.owner) + "?lang=ar").status_code, 200)
        self.assertEqual(link(self.desk), reverse("medical:beds"))
        self.assertEqual(link(self.keeper), "")  # a stock keeper has no way into the board: no dead link for them

    def test_the_setup_interview_asks_about_beds_only_where_patients_stay(self):
        from settings_core import setup_interview as interview

        def asked(sub, lang="ar"):
            return {row["key"]: row for row in interview.questions("medical", sub, lang)}

        centre, clinic = asked("medical_center")["beds"], asked("clinic")["beds"]
        self.assertEqual((centre["default"], clinic["default"], centre["features"]), (True, False, "inpatient"))  # suggested for a centre only
        self.assertIn("أسرّة", clinic["ask"])
        self.assertIn("beds", asked("clinic", "en")["beds"]["ask"])
        for sub in ("dental", "lab", "physio", "vet"):
            self.assertNotIn("beds", asked(sub))
        self.assertNotIn("beds", {row["key"] for row in interview.questions("commercial", "retail", "ar")})
        modules, features = interview.resolve("medical", "clinic", {"beds"})
        self.assertIn("inpatient", features)
        self.assertNotIn("inpatient", interview.resolve("medical", "clinic", set())[1])


# ---- the screens -------------------------------------------------------------------


class ScreenTests(Hospital):
    def test_admit_charge_move_discharge_and_open_the_draft(self):
        self.client.force_login(self.owner)
        board = self.client.get(reverse("medical:beds"))
        self.assertContains(board, "inpatient.css?v=m8-2")
        self.assertContains(board, f'data-bed="{self.bed1.pk}" data-state="free"')
        self.assertContains(board, "الدور الأول")
        sheet = self.client.get(reverse("medical:admit") + f"?bed={self.bed1.pk}&q=منى")
        self.assertContains(sheet, f'data-found-patient="{self.patient.pk}"')
        self.assertNotContains(sheet, f'data-found-patient="{self.other.pk}"')
        self.assertContains(self.client.get(reverse("medical:admit") + "?q=zzzz"), "data-no-match")
        response = self.client.post(reverse("medical:admit"), {"lang": "ar", "patient": self.patient.pk, "bed": self.bed1.pk, "doctor": self.sara.pk,
                                                               "diagnosis": "اشتباه زايدة"})
        admission = Admission.objects.get()
        self.assertRedirects(response, self.stay_url(admission), fetch_redirect_response=False)
        self.assertEqual((admission.patient, admission.bed, admission.doctor, admission.diagnosis), (self.patient, self.bed1, self.sara, "اشتباه زايدة"))
        board = self.client.get(reverse("medical:beds"))
        self.assertContains(board, f'data-bed="{self.bed1.pk}" data-state="occupied"')
        self.assertContains(board, "اليوم 1")

        form = self.client.get(self.stay_url(admission, add="supply"))
        self.assertContains(form, 'data-add-form="supply"')
        self.assertContains(form, "محلول ملح")
        self.assertNotContains(form, f'<option value="{self.sonar.pk}"')  # a supply is picked from stock items
        self.client.post(self.stay_url(admission), {"lang": "ar", "action": "charge", "kind": "supply", "item": self.saline.pk, "quantity": "2"})
        self.client.post(self.stay_url(admission), {"lang": "ar", "action": "charge", "kind": "operation", "item": self.appendix.pk, "surgeon": self.sara.pk,
                                                     "price": "", "day": self.today.isoformat()})
        self.assertEqual(sorted(admission.charges.values_list("kind", "price", "quantity")), [("operation", D("8000.00"), D("1")), ("supply", D("45.00"), D("2"))])
        bad = self.client.post(self.stay_url(admission), {"lang": "ar", "action": "charge", "kind": "procedure", "item": self.sonar.pk, "quantity": "0"})
        self.assertContains(bad, "data-stay-error")
        self.assertContains(bad, 'data-add-form="procedure"')  # the form stays open with what was typed

        moving = self.client.get(self.stay_url(admission, move=1))
        self.assertContains(moving, f'<option value="{self.icu1.pk}">')
        self.assertNotContains(moving, f'<option value="{self.bed1.pk}">')
        self.client.post(self.stay_url(admission), {"lang": "ar", "action": "move", "bed": self.icu1.pk})
        admission.refresh_from_db()
        self.assertEqual(admission.bed, self.icu1)

        review = self.client.get(self.stay_url(admission, review=1))
        self.assertContains(review, "data-review")
        self.assertContains(review, "data-review-total>9,290.00")  # one day in ICU 1200 + 8000 + 2 × 45
        self.assertContains(review, "data-pending-bed-day")
        self.assertEqual(SalesInvoice.objects.count(), 0)  # looking at the review bills nothing
        response = self.client.post(self.stay_url(admission), {"lang": "ar", "action": "discharge"}, follow=True)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.total_amount), ("draft", D("9290.00")))
        self.assertContains(response, 'data-stay-state="discharged"')
        self.assertContains(response, f'href="{reverse("sales:detail", args=[invoice.pk])}?lang=ar"')
        self.assertNotContains(response, "data-remove-charge")
        self.assertNotContains(response, "data-discharge-open")
        opened = self.client.get(reverse("sales:detail", args=[invoice.pk]))
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, invoice.invoice_number)
        board = self.client.get(reverse("medical:beds"))
        self.assertContains(board, f'data-bed="{self.icu1.pk}" data-state="free"')

    def test_the_screens_in_english(self):
        admission = self.admit(days_ago=1, hour=9, doctor=self.sara)
        inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar}, self.owner)
        self.client.force_login(self.owner)
        board = self.client.get(reverse("medical:beds") + "?lang=en")
        for text in ("Beds", "First floor", "Day 2", "Free"):
            self.assertContains(board, text)
        page = self.client.get(self.stay_url(admission, lang="en", review=1))
        for text in ("Discharge &amp; bill", "Review before discharge", "Stay charges", "Bed day", "Invoice total"):
            self.assertContains(page, text)
        self.assertContains(self.client.get(reverse("medical:admit") + f"?lang=en&patient={self.patient.pk}"), "Treating doctor")
        self.assertContains(self.client.get(reverse("medical:wards") + "?lang=en"), "Daily stay service")

    def test_an_empty_board_points_the_owner_to_the_set_up(self):
        from .models import Bed, Ward

        Bed.objects.all().delete()
        Ward.objects.all().delete()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("medical:beds")), "data-no-wards")

    def test_the_templates_keep_the_house_rules(self):
        from pathlib import Path

        from django.conf import settings

        for name in ("beds", "admit", "stay", "wards"):
            source = Path(settings.BASE_DIR, "templates/medical", f"{name}.html").read_text(encoding="utf-8")
            self.assertNotIn("data-label", source)  # the table-cards script writes them; a template never does
            self.assertNotIn("{#", source)
            self.assertIn("inpatient.css' %}?v=m8-2", source)
        css = Path(settings.BASE_DIR, "static/hesba/css/inpatient.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")  # tokens only
        self.assertNotRegex(css, r"rgba?\(")


# ---- entities ----------------------------------------------------------------------


class EntityScopeTests(Hospital):
    def setUp(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        super().setUp()
        self.main = Entity.objects.get(is_main=True)
        self.branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        self.branch_store = make_location(location_code="BR-STORE", name_ar="مخزن الفرع", entity=self.branch, is_selling_location=True)
        self.branch_cash = make_cashbox(cashbox_code="BR-CASH", name_ar="خزنة الفرع", entity=self.branch)
        with working_in(self.branch):
            self.branch_ward = inpatient.save_ward({"name_ar": "عنبر الفرع"}, self.owner)
            self.branch_bed = inpatient.save_bed(self.branch_ward, {"code": "B-1", "daily_service": self.room_day}, self.owner)

    def test_a_ward_and_its_admissions_belong_to_the_entity_they_were_made_in(self):
        from entities.current import working_in

        self.assertEqual(self.branch_ward.entity, self.branch)
        admission = self.admit(days_ago=1, hour=9, bed=self.branch_bed)
        self.assertEqual(admission.entity, self.branch)
        with working_in(self.main):
            self.assertEqual([row["ward"] for row in inpatient.board()], [self.ward, self.icu])
            self.assertFalse(inpatient.scoped_admissions().exists())
            self.assertNotIn(self.branch_bed, list(inpatient.free_beds()))
        with working_in(self.branch):
            self.assertEqual([row["ward"] for row in inpatient.board()], [self.branch_ward])
            self.assertEqual(list(inpatient.scoped_admissions()), [admission])
        self.assertEqual(len(inpatient.board()), 3)  # the whole group sees every ward

    def test_the_boards_lists_of_ended_stays_keep_to_the_entity(self):
        from entities.current import working_in

        admission = self.admit(days_ago=1, hour=9, bed=self.branch_bed)
        inpatient.discharge(admission, self.owner)
        with working_in(self.main):
            self.assertEqual((inpatient.needing_action(), inpatient.recent_closed()), ([], []))
        with working_in(self.branch):
            self.assertEqual([row["admission"] for row in inpatient.needing_action()], [admission])
            self.assertEqual(inpatient.recent_closed(), [admission])

    def test_the_bill_lands_in_the_admissions_entity_whoever_makes_it(self):
        from entities.current import working_in

        admission = self.admit(days_ago=1, hour=9, bed=self.branch_bed)
        with working_in(self.main):  # billed by someone working in the main entity
            admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        self.assertEqual((invoice.selling_location, invoice.total_amount), (self.branch_store, D("500.00")))

    def test_a_patient_is_not_moved_to_another_entitys_bed_and_the_advance_stays_in_its_books(self):
        set_capability_enabled(self.profile, "deposits", True, user=self.owner)
        admission = self.admit(bed=self.branch_bed)
        with self.assertRaisesMessage(ValidationError, "فرع تاني"):
            inpatient.move(admission, self.bed2, self.owner)
        with self.assertRaisesMessage(ValidationError, "اختار الخزنة"):
            inpatient.take_advance(admission, self.cashbox, "300", self.owner)  # the main entity's cashbox
        deposit = inpatient.take_advance(admission, self.branch_cash, "300", self.owner)
        self.assertEqual((deposit.payment.cashbox, cash_balance(self.branch_cash), cash_balance(self.cashbox)), (self.branch_cash, D("300.00"), D("0")))

    def test_a_ward_made_before_entities_is_the_main_entitys(self):
        from entities.current import working_in

        from .models import Ward

        self.assertIsNone(self.ward.entity)  # made in the whole-group view: no entity, so the main one's (HG-034)
        with working_in(self.main):
            main_ward = inpatient.save_ward({"name_ar": "جناح جديد"}, self.owner)
            main_bed = inpatient.save_bed(main_ward, {"code": "M-1", "daily_service": self.room_day}, self.owner)
            self.assertEqual(set(inpatient.scoped_wards()), {self.ward, self.icu, main_ward})
        self.assertEqual(Ward.objects.get(pk=main_ward.pk).entity, self.main)
        admission = self.admit()  # in bed 101, whose ward has no entity
        inpatient.move(admission, main_bed, self.owner)  # both are the main entity: the move goes through
        admission.refresh_from_db()
        self.assertEqual(admission.bed, main_bed)

    def test_another_entitys_stay_answers_404(self):
        from entities.current import SESSION_KEY
        from entities.models import EntityMembership

        admission = self.admit(bed=self.branch_bed)
        EntityMembership.objects.create(user=self.manager, entity=self.main, is_default=True)  # the manager works in the main entity only
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.stay_url(admission)).status_code, 404)
        self.assertEqual(self.client.post(self.stay_url(admission), {"lang": "ar", "action": "cancel"}).status_code, 404)
        board = self.client.get(reverse("medical:beds"))
        self.assertNotContains(board, "عنبر الفرع")
        self.assertContains(board, "الدور الأول")
        admission.refresh_from_db()
        self.assertEqual(admission.state, AdmissionState.ADMITTED)
        self.client.force_login(self.owner)
        session = self.client.session
        session[SESSION_KEY] = self.branch.pk
        session.save()
        self.assertEqual(self.client.get(self.stay_url(admission)).status_code, 200)
        self.assertContains(self.client.get(reverse("medical:beds")), "عنبر الفرع")


# ---- the audit trail ---------------------------------------------------------------


# ---- what the independent review found (PR #370) ------------------------------------


class AfterTheBedTests(Hospital):
    """A stay that left its bed must still be reachable, its nights are not erased by a click, and an advance
    that nothing will use can be handed back."""

    def setUp(self):
        super().setUp()
        set_capability_enabled(self.profile, "deposits", True, user=self.owner)

    def board(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("medical:beds")).content.decode()

    def test_a_discharged_stay_nobody_billed_is_on_the_board_until_its_bill_posts(self):
        admission = self.admit(days_ago=2, hour=10, doctor=self.sara)
        inpatient.add_charge(admission, {"kind": "operation", "item": self.appendix}, self.doctor)
        self.client.force_login(self.doctor)
        self.client.post(self.stay_url(admission), {"lang": "ar", "action": "discharge"})  # a doctor makes no invoice
        link = f'data-attention-stay="{admission.pk}"'
        board = self.board(self.desk)
        self.assertIn(link, board)
        self.assertIn(reverse("medical:stay", args=[admission.pk]), board)
        self.assertIn("محتاجة فاتورة", board)
        self.assertEqual([(row["admission"].pk, row["reasons"]) for row in inpatient.needing_action()], [(admission.pk, ["bill"])])
        invoice = inpatient.bill(admission, self.desk)
        self.assertEqual([row["reasons"] for row in inpatient.needing_action()], [["draft"]])  # still there: nobody posted it
        self.assertIn("فاتورتها مسودة", self.board(self.desk))
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(inpatient.needing_action(), [])
        board = self.board(self.desk)
        self.assertNotIn(link, board)
        self.assertIn(f'data-recent-stay="{admission.pk}"', board)  # and it stays reachable among the latest that ended
        cancel_posted_sales_invoice(invoice.pk, self.owner, "اتعملت غلط")
        self.assertEqual([row["reasons"] for row in inpatient.needing_action()], [["bill"]])  # cancelled: to be billed again

    def test_a_cancelled_admission_whose_advance_is_still_held_is_on_the_board(self):
        admission = self.admit(advance="500", cashbox=self.cashbox)
        result = inpatient.cancel(admission, self.doctor)  # a doctor cannot hand money back
        self.assertEqual(result["outcome"], "refund_not_allowed")
        self.assertEqual([(row["admission"].pk, row["reasons"]) for row in inpatient.needing_action()], [(admission.pk, ["advance"])])
        board = self.board(self.desk)
        self.assertIn(f'data-attention-stay="{admission.pk}"', board)
        self.assertIn("مقدم لسه محجوز", board)
        inpatient.refund_advance(admission, self.desk)
        self.assertEqual(inpatient.needing_action(), [])
        self.assertEqual((customer_balance(self.patient), cash_balance(self.cashbox)), (D("0.00"), D("0.00")))

    def test_cancelling_a_stay_with_nights_behind_it_is_the_owners_or_a_managers(self):
        admission = self.admit(days_ago=5, hour=10, advance="2000", cashbox=self.cashbox)
        self.assertEqual(sum(price for _, price in inpatient.pending_bed_days(admission)), D("2500.00"))  # five nights at 500
        for user in (self.desk, self.doctor):
            with self.assertRaisesMessage(PermissionDenied, "عدّى عليها 5 ليلة"):
                inpatient.cancel(admission, user)
        self.client.force_login(self.desk)
        page = self.client.get(self.stay_url(admission))
        self.assertNotContains(page, "data-cancel")
        response = self.client.post(self.stay_url(admission), {"lang": "ar", "action": "cancel"})
        self.assertContains(response, "data-stay-error")
        admission.refresh_from_db()
        self.assertEqual((admission.state, Deposit.objects.get().status, cash_balance(self.cashbox)), (AdmissionState.ADMITTED, DepositStatus.HELD, D("2000.00")))
        self.client.force_login(self.owner)
        page = self.client.get(self.stay_url(admission, cancel=1))
        self.assertContains(page, "data-cancel-late")
        self.assertContains(page, "2,500.00")  # what the cancel waives is said before the click
        result = inpatient.cancel(admission, self.owner)
        self.assertEqual((result["admission"].state, result["outcome"]), (AdmissionState.CANCELLED, "refunded"))
        row = AuditLog.objects.filter(action="cancel_admission").get()
        self.assertEqual((row.after_data["nights_behind"], row.after_data["bed_days_waived"]), (5, "2500.00"))

    def test_a_same_day_admission_is_still_cancelled_by_the_desk(self):
        admission = self.admit(user=self.desk)
        self.client.force_login(self.desk)
        self.assertContains(self.client.get(self.stay_url(admission)), "data-cancel")
        self.assertEqual(inpatient.cancel(admission, self.desk)["admission"].state, AdmissionState.CANCELLED)
        self.assertEqual(AuditLog.objects.filter(action="cancel_admission").get().after_data["bed_days_waived"], "0")

    def test_an_advance_the_bill_did_not_use_is_handed_back_after_discharge(self):
        free_day = make_item(item_code="BED-FREE", item_name="إقامة ملاحظة", is_stock_tracked=False, default_sale_price="0.00")
        bed = inpatient.save_bed(self.ward, {"code": "OBS", "daily_service": free_day}, self.owner)
        admission = self.admit(bed=bed, advance="500", cashbox=self.cashbox)
        with self.assertRaisesMessage(ValidationError, "المقدم بيترد"):
            inpatient.refund_advance(admission, self.owner)  # still admitted
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        self.assertEqual(invoice.total_amount, D("0.00"))
        self.assertFalse(inpatient.advance_refundable(admission))  # the draft has not posted yet
        post_sales_invoice(invoice.pk, self.owner)
        deposit = Deposit.objects.get()
        self.assertEqual((deposit.status, InvoiceSettlement.objects.count(), bal("2105")), (DepositStatus.HELD, 0, D("-500.00")))  # nothing to settle
        self.assertEqual([row["reasons"] for row in inpatient.needing_action()], [["advance"]])
        self.client.force_login(self.desk)
        self.assertContains(self.client.get(self.stay_url(admission)), "data-refund-form")
        self.client.post(self.stay_url(admission), {"lang": "ar", "action": "refund"})
        deposit.refresh_from_db()
        self.assertEqual((deposit.status, deposit.payment.status), (DepositStatus.REFUNDED, "cancelled"))
        self.assertEqual((customer_balance(self.patient), cash_balance(self.cashbox), bal("2105")), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(inpatient.needing_action(), [])

    def test_an_advance_is_not_handed_back_while_the_stay_still_has_something_to_bill(self):
        admission = self.admit(days_ago=1, hour=9, advance="300", cashbox=self.cashbox)
        inpatient.discharge(admission, self.owner)  # discharged, one night of 500 not billed
        with self.assertRaisesMessage(ValidationError, "المقدم بيترد"):
            inpatient.refund_advance(admission, self.owner)
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.stay_url(admission)), "data-refund-form")
        self.assertEqual((Deposit.objects.get().status, cash_balance(self.cashbox)), (DepositStatus.HELD, D("300.00")))

    def test_an_advance_larger_than_the_bill_says_what_it_settled_and_what_is_left(self):
        admission = self.admit(days_ago=2, hour=10, advance="12000", cashbox=self.cashbox)
        inpatient.add_charge(admission, {"kind": "operation", "item": self.appendix}, self.owner)
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)  # two nights at 500 + 8000 = 9000
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual((InvoiceSettlement.objects.get().amount, customer_balance(self.patient), bal("2105")), (D("9000.00"), D("-3000.00"), D("-3000.00")))
        self.client.force_login(self.owner)
        page = self.client.get(self.stay_url(admission))
        self.assertContains(page, "data-advance-split")
        self.assertContains(page, "اتخصم من الفاتورة 9,000.00، والباقي 3,000.00 رصيد للمريض")
        english = self.client.get(self.stay_url(admission, lang="en"))
        self.assertContains(english, "9,000.00 came off the invoice; the remaining 3,000.00 is the patient")

    def test_the_running_total_rounds_each_line_as_the_invoice_does(self):
        gauze = make_item(item_code="SUP-GZ", item_name="شاش", is_stock_tracked=True, default_sale_price="33.33")
        stock_in(gauze, self.location, 10, "10.00")
        admission = self.admit()
        for _ in range(2):
            inpatient.add_charge(admission, {"kind": "supply", "item": gauze, "quantity": "1.5"}, self.owner)  # 49.995 each: 50.00 on the invoice
        self.client.force_login(self.owner)
        page = self.client.get(self.stay_url(admission)).content.decode()
        self.assertIn("data-running-total>600.00<", page)  # one day at 500 + 50.00 + 50.00, not 599.99
        self.assertEqual(inpatient.review(admission)["subtotal"], D("600.00"))
        admission, invoice = inpatient.discharge_and_bill(admission, self.owner)
        self.assertEqual(invoice.subtotal, D("600.00"))


class AuditTests(Hospital):
    def test_every_step_leaves_a_row_and_none_carries_clinical_text(self):
        admission = self.admit(days_ago=1, hour=9, diagnosis="ZZDIAGNOSIS", doctor=self.sara)
        kept = inpatient.add_charge(admission, {"kind": "operation", "item": self.appendix, "surgeon": self.sara, "note": "ZZNOTE"}, self.doctor)
        dropped = inpatient.add_charge(admission, {"kind": "procedure", "item": self.sonar}, self.doctor)
        inpatient.remove_charge(dropped, self.doctor)
        inpatient.set_diagnosis(admission, "ZZDIAGNOSIS 2", self.doctor)
        inpatient.move(admission, self.icu1, self.desk)
        inpatient.discharge(admission, self.doctor)
        invoice = inpatient.bill(admission, self.desk)
        mistaken = self.admit(patient=self.other, bed=self.bed2, user=self.desk)
        inpatient.cancel(mistaken, self.desk)

        def rows(action, obj=admission):
            return list(AuditLog.objects.filter(module="medical", action=action, object_type="medical.Admission", object_id=str(obj.pk)).order_by("pk"))

        admitted = rows("admit_patient")[0]
        self.assertEqual((admitted.actor, admitted.after_data["bed_code"], admitted.after_data["patient"], admitted.after_data["has_diagnosis"]),
                         (self.owner, "101", self.patient.pk, True))
        added = rows("add_admission_charge")
        self.assertEqual([(row.actor, row.after_data["kind"], row.after_data["price"]) for row in added],
                         [(self.doctor, "operation", "8000.00"), (self.doctor, "procedure", "600.00")])
        removed = rows("remove_admission_charge")[0]
        self.assertEqual((removed.before_data["item"], removed.before_data["price"]), ("SRV-SONAR", "600.00"))
        moved = rows("move_bed")[0]
        self.assertEqual((moved.actor, moved.before_data["bed_code"], moved.after_data["bed_code"], moved.after_data["bed_days_written"]),
                         (self.desk, "101", "ICU-1", [(self.today - timedelta(days=1)).isoformat()]))
        discharged = rows("discharge_patient")[0]
        self.assertEqual((discharged.actor, discharged.after_data["state"], discharged.after_data["bed_days"]), (self.doctor, "discharged", 1))
        billed = rows("bill_admission")[0]
        self.assertEqual((billed.actor, billed.after_data["invoice"], billed.after_data["total"], sorted(billed.after_data["charges"])),
                         (self.desk, invoice.invoice_number, "8500.00", sorted(admission.charges.values_list("pk", flat=True))))
        self.assertEqual(kept.pk in billed.after_data["charges"], True)
        cancelled = rows("cancel_admission", mistaken)[0]
        self.assertEqual((cancelled.actor, cancelled.after_data["state"]), (self.desk, "cancelled"))
        self.assertEqual(len(rows("set_admission_diagnosis")), 1)
        self.assertEqual(AuditLog.objects.filter(module="medical", action__in=("save_ward", "save_bed")).count(), 5)
        for row in AuditLog.objects.all():  # the diagnosis and the note stay out of the trail
            text = f"{row.before_data} {row.after_data} {row.reason}"
            self.assertNotIn("ZZDIAGNOSIS", text)
            self.assertNotIn("ZZNOTE", text)
