"""SVC-004: a service contract's visits booked for the period in one click, spread over its days."""

from datetime import date, datetime, time
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from audit.models import AuditLog
from hesba_testing.factories import make_customer
from permissions.models import RoleCode

from . import visits
from .tests import ContractSetup, person

TODAY = timezone.localdate()
FIRST = TODAY.replace(day=1)


def local_days(appointments):
    return [timezone.localtime(appointment.starts_at).date() for appointment in appointments]


class VisitSetup(ContractSetup):
    def setUp(self):
        super().setUp()
        self.customer.address = "١٢ شارع التحرير، الدقي"
        self.customer.save(update_fields=["address"])

    def visits_contract(self, count=4, **extra):
        return self.contract(starts_on=FIRST, visits_per_period=count, **extra)


class PlanTests(VisitSetup):
    def test_the_days_are_spread_over_the_period_and_never_on_a_friday(self):
        days = visits.plan_days(date(2026, 10, 1), date(2026, 10, 31), 4)
        self.assertEqual(days, [date(2026, 10, 1), date(2026, 10, 8), date(2026, 10, 17), date(2026, 10, 24)])  # the 16th is a Friday
        self.assertEqual(visits.plan_days(date(2026, 10, 29), date(2026, 10, 30), 2), [date(2026, 10, 29), date(2026, 10, 29)])  # the last day a Friday: the day before
        self.assertEqual(visits.plan_days(date(2026, 10, 30), date(2026, 10, 30), 1), [date(2026, 10, 30)])  # never outside the period
        self.assertEqual(len(visits.plan_days(date(2026, 10, 28), date(2026, 10, 29), 3)), 3)  # more visits than days: some share a day


class ScheduleTests(VisitSetup):
    def test_the_missing_visits_are_booked_once_and_a_cancelled_one_is_booked_again(self):
        from appointments.services import set_status

        contract = self.visits_contract(4)
        booked = visits.schedule(contract, self.owner, today=FIRST)
        self.assertEqual(len(booked), 4)
        self.assertEqual({(a.kind, a.customer_id, a.service_id, a.price, a.status) for a in booked},
                         {("visit", self.customer.pk, self.service.pk, D("0.00"), "booked")})
        days = local_days(booked)
        self.assertEqual(days, sorted(days))
        self.assertEqual({(day.year, day.month) for day in days}, {(FIRST.year, FIRST.month)})
        self.assertNotIn(4, [day.weekday() for day in days])
        self.assertEqual(booked[0].address, "١٢ شارع التحرير، الدقي")
        self.assertIn(contract.number, booked[0].notes)
        self.assertEqual(visits.schedule(contract, self.owner, today=FIRST), [])  # a second click books nothing
        self.assertEqual(Appointment.objects.count(), 4)
        set_status(booked[1], "cancelled", self.owner)
        self.assertEqual(visits.missing(contract, FIRST), 1)
        again = visits.schedule(contract, self.owner, today=FIRST)
        self.assertEqual((len(again), Appointment.objects.exclude(status="cancelled").count()), (1, 4))
        self.assertIn("(4 من 4)", again[0].notes)
        log = AuditLog.objects.filter(action="schedule_contract_visits").latest("pk")
        self.assertEqual(log.after_data["appointments"], [again[0].number])

    def test_a_busy_technician_gets_the_next_free_hour(self):
        from appointments.services import book
        from staff.services import save_employee

        tech = save_employee({"name": "م. كريم"}, self.owner)
        contract = self.visits_contract(1)
        book({"customer": make_customer(customer_code="C-9", name="عميل تاني"), "employee": tech, "service": self.service,
              "starts_at": timezone.make_aware(datetime.combine(FIRST, time(10))), "duration_minutes": 60}, self.owner)
        visit, = visits.schedule(contract, self.owner, employee=tech, today=FIRST)
        self.assertEqual((visit.employee, timezone.localtime(visit.starts_at).hour), (tech, 11))

    def test_no_address_no_visits_and_not_active_refused(self):
        contract = self.visits_contract(2)
        self.customer.address = ""
        self.customer.save(update_fields=["address"])
        with self.assertRaisesMessage(ValidationError, "مالوش عنوان"):
            visits.schedule(contract, self.owner, today=FIRST)
        self.assertFalse(Appointment.objects.exists())
        with self.assertRaises(ValidationError):
            visits.schedule(self.contract(starts_on=FIRST), self.owner, today=FIRST)  # no visits in the contract
        paused = self.visits_contract(2, status="paused")
        self.assertEqual(visits.missing(paused, FIRST), 0)
        with self.assertRaises(ValidationError):
            visits.schedule(paused, self.owner, today=FIRST)

    def test_booking_needs_the_invoice_permission(self):
        contract = self.visits_contract(2)
        keeper = person(RoleCode.STOCK_KEEPER, "sc_keeper")
        with self.assertRaises(PermissionDenied):
            visits.schedule(contract, keeper, today=FIRST)
        with self.assertRaises(PermissionDenied):
            visits.schedule_all(keeper, today=FIRST)
        self.assertFalse(Appointment.objects.exists())

    def test_all_contracts_at_once_skip_the_one_that_cannot_be_booked(self):
        self.visits_contract(2)
        other = make_customer(customer_code="C-2", name="مصنع بلا عنوان")
        stuck = self.visits_contract(3, customer=other)
        self.assertEqual(visits.missing_count(FIRST), 5)
        booked, skipped = visits.schedule_all(self.owner, today=FIRST)
        self.assertEqual(len(booked), 2)
        self.assertEqual([(contract.pk, "عنوان" in why) for contract, why in skipped], [(stuck.pk, True)])
        self.assertEqual(visits.missing_count(FIRST), 3)


class ScreenTests(VisitSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_list_books_every_contracts_visits(self):
        self.visits_contract(2)
        url = reverse("agreements:list")
        self.assertContains(self.client.get(url), "احجز زيارات الفترة (2 زيارة)")
        response = self.client.post(url, {"action": "schedule_visits"}, follow=True)
        self.assertContains(response, "اتحجزت 2 زيارة")
        self.assertEqual(Appointment.objects.filter(kind="visit").count(), 2)
        self.assertNotContains(self.client.get(url), "data-schedule-visits")  # nothing left to book

    def test_the_contract_page_books_with_a_technician_and_lists_the_visits_in_arabic(self):
        from staff.services import save_employee

        tech = save_employee({"name": "م. كريم"}, self.owner)
        contract = self.visits_contract(2)
        url = reverse("agreements:detail", args=[contract.pk])
        self.assertContains(self.client.get(url), "data-schedule-visits")
        self.client.post(url, {"action": "schedule_visits", "employee": tech.pk})
        page = self.client.get(url)
        self.assertEqual(Appointment.objects.filter(employee=tech).count(), 2)
        self.assertContains(page, "data-period-visits")
        self.assertContains(page, "م. كريم")
        self.assertNotContains(page, ">Booked<")
