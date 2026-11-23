"""DASH-003: the clinic's and the office's own tiles on the dashboard panel."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.utils import timezone

from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.activity_panel import build_activity_panel
from reports.tests_dashboard import prepared_client

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def tiles(panel):
    return {tile["key"]: tile for tile in panel["tiles"]}


class ClinicTilesTests(TestCase):
    def setUp(self):
        from appointments.models import Appointment
        from medical import insurance, services
        from sales.services import post_sales_invoice
        from staff.services import save_employee

        prepared_client("medical", "clinic", "customers,items_services,sales_operations,cashboxes,reports,appointments_visits")
        self.owner = person(RoleCode.OWNER, "tiles_owner")
        make_location(location_code="CL", is_default=True, is_selling_location=True)
        doctor = save_employee({"name": "د. سارة"}, self.owner)
        checkup = make_item(item_code="SRV-1", item_name="كشف", is_stock_tracked=False, default_sale_price="300")
        mona = make_customer(customer_code="P-1", name="منى")
        services.save_visit(mona, {"visit_date": (TODAY - timedelta(days=20)).isoformat(), "diagnosis": "x", "doctor": doctor,
                                   "follow_up_date": (TODAY - timedelta(days=5)).isoformat()}, self.owner)  # missed
        ali = make_customer(customer_code="P-2", name="علي")
        services.save_visit(ali, {"visit_date": (TODAY - timedelta(days=2)).isoformat(), "diagnosis": "x", "doctor": doctor,
                                  "follow_up_date": (TODAY + timedelta(days=3)).isoformat()}, self.owner)  # coming
        from medical import queue

        queue.check_in(mona, self.owner)
        contract = insurance.save_contract({"name": "الأمان", "coverage_percent": "80"}, self.owner)
        insurance.set_cover(ali, {"contract": contract}, self.owner)
        appointment = Appointment.objects.create(number="AP-1", customer=ali, employee=doctor, service=checkup, price="300",
                                                 starts_at=timezone.now(), status="done", created_by=self.owner)
        bill = insurance.bill_insured(appointment, self.owner)
        post_sales_invoice(bill.insurer_invoice.pk, self.owner)

    def test_the_clinic_sees_its_queue_follow_ups_and_insurer_dues(self):
        got = tiles(build_activity_panel("medical", {"appointments"}, "ar", TODAY, self.owner))
        self.assertEqual(got["queue_waiting"]["value"], 1)
        self.assertEqual((got["follow_missed"]["value"], got["follow_coming"]["value"]), (1, 1))
        self.assertEqual((got["insurers_owe"]["value"], got["insurers_owe"]["kind"]), (D("240.00"), "money"))
        self.assertIn("today_total", got)  # the bookings tiles stay

    def test_without_the_permissions_the_extra_tiles_stay_hidden(self):
        keeper = person(RoleCode.STOCK_KEEPER, "tiles_keeper")
        got = tiles(build_activity_panel("medical", {"appointments"}, "ar", TODAY, keeper))
        self.assertFalse({"queue_waiting", "follow_missed", "follow_coming", "insurers_owe"} & set(got))


class OfficeTilesTests(TestCase):
    def setUp(self):
        from agreements import hours

        prepared_client("services", "professional", "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians")
        self.owner = person(RoleCode.OWNER, "office_owner")
        make_location(location_code="OF", is_default=True, is_selling_location=True)
        self.service = make_item(item_code="SRV-H", item_name="استشارة", is_stock_tracked=False, default_sale_price="500")
        self.client_ = make_customer(customer_code="C-1", name="شركة النور")
        hours.log_time({"customer": self.client_, "service": self.service, "hours": "2.5", "worked_on": TODAY.isoformat()}, self.owner)

    def test_the_office_sees_its_unbilled_hours_and_contracts_due(self):
        from agreements import services as agreements

        agreements.save_contract({"customer": self.client_, "service": self.service, "amount": "1000", "every": "month",
                                  "starts_on": (TODAY.replace(day=1) - timedelta(days=40)).isoformat()}, self.owner)
        got = tiles(build_activity_panel("services", {"appointments"}, "ar", TODAY, self.owner))
        self.assertEqual(got["hours_unbilled"]["value"], "2.5")
        self.assertIn("1,250.00", got["hours_unbilled"]["sub"])
        self.assertGreaterEqual(got["contracts_due"]["value"], 1)

    def test_a_beauty_salon_has_no_hours_tile(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(sub_activity_slug="beauty")
        got = tiles(build_activity_panel("services", {"appointments"}, "ar", TODAY, self.owner))
        self.assertNotIn("hours_unbilled", got)
