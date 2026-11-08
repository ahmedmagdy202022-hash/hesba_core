"""APPT-001: appointments & visits, billed through the ordinary sales engine."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from urllib.parse import unquote

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, SalesInvoice
from sales.services import create_sales_return, post_sales_invoice
from staff.services import save_employee

from . import services
from .models import Appointment, AppointmentStatus


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def at(hour, minute=0, day=None):
    return timezone.make_aware(datetime.combine(day or TODAY, time(hour, minute)))


class AppointmentSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="services", sub_activity="maintenance",
                                       modules="customers,items_services,sales_operations,cashboxes,reports,employees_technicians,appointments_visits")
        self.owner = person(RoleCode.OWNER, "appt_owner")
        make_location(location_code="SHOP", is_default=True)
        self.customer = make_customer(customer_code="C-1", name="مدام سعاد", phone="01001234567")
        self.service = make_item(item_code="SRV-AC", item_name="صيانة تكييف", default_sale_price="300.00", is_stock_tracked=False)
        self.tech = save_employee({"name": "أحمد الفني", "commission_percent": "10"}, self.owner)

    def book(self, hour=10, minutes=60, employee="tech", **extra):
        data = {"customer": self.customer, "employee": self.tech if employee == "tech" else employee, "service": self.service,
                "starts_at": at(hour), "duration_minutes": minutes}
        data.update(extra)
        return services.book(data, self.owner)


class BookingTests(AppointmentSetup):
    def test_booking_takes_the_service_price_and_numbers_by_day(self):
        first = self.book()
        self.assertEqual((first.number, first.price, first.status), (f"AP-{TODAY:%Y%m%d}-001", D("300.00"), AppointmentStatus.BOOKED))
        self.assertEqual(self.book(hour=12, price="250").price, D("250.00"))
        self.assertTrue(AuditLog.objects.filter(action="book_appointment").exists())

    def test_an_employee_cannot_be_in_two_places(self):
        self.book(hour=10, minutes=60)
        for hour, minute in ((10, 30), (9, 30)):
            with self.subTest(start=f"{hour}:{minute}"), self.assertRaisesMessage(ValidationError, "عنده ميعاد تاني من 10:00 لـ 11:00"):
                services.book({"customer": self.customer, "employee": self.tech, "service": self.service, "starts_at": at(hour, minute), "duration_minutes": 60}, self.owner)
        self.book(hour=11)  # back to back is fine
        self.book(hour=10, employee=None)  # no employee, no clash
        cancelled = Appointment.objects.get(starts_at=at(11))
        services.set_status(cancelled, AppointmentStatus.CANCELLED, self.owner)
        self.book(hour=11, minutes=30)  # a cancelled slot is free again

    def test_refusals(self):
        for extra, text in (({"customer": None}, "اختار العميل"), ({"duration_minutes": 2}, "المدة لازم"), ({"price": "-1"}, "السعر"),
                            ({"kind": "visit", "address": ""}, "الزيارة محتاجة عنوان")):
            with self.subTest(extra=extra), self.assertRaisesMessage(ValidationError, text):
                self.book(**extra)
        self.customer.address = "12 ش التحرير"
        self.customer.save()
        self.assertEqual(self.book(kind="visit").address, "12 ش التحرير")  # the customer's own address by default

    def test_statuses_move_forward_only(self):
        appointment = self.book()
        services.set_status(appointment, AppointmentStatus.CONFIRMED, self.owner)
        services.set_status(appointment, AppointmentStatus.ARRIVED, self.owner)
        with self.assertRaises(ValidationError):
            services.set_status(appointment, AppointmentStatus.BOOKED, self.owner)
        with self.assertRaises(ValidationError):
            services.set_status(appointment, AppointmentStatus.NO_SHOW, self.owner)
        services.set_status(appointment, AppointmentStatus.DONE, self.owner)
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            services.reschedule(appointment, {"customer": self.customer, "starts_at": at(15), "duration_minutes": 30}, self.owner)


class BillingTests(AppointmentSetup):
    def test_bill_makes_one_draft_through_sales_and_posting_charges_the_customer(self):
        appointment = self.book()
        with self.assertRaisesMessage(ValidationError, "لازم يكون خلص أو جاري"):
            services.bill(appointment, self.owner)
        services.set_status(appointment, AppointmentStatus.ARRIVED, self.owner)
        invoice = services.bill(appointment, self.owner)
        appointment.refresh_from_db()
        self.assertEqual((invoice.status, invoice.total_amount, invoice.customer, appointment.status, appointment.invoice), ("draft", D("300.00"), self.customer, AppointmentStatus.DONE, invoice))
        self.assertEqual(services.bill(appointment, self.owner), invoice)  # once only
        self.assertEqual(invoice.notes, f"ميعاد {appointment.number}")  # TRACK-ACTS W18: no English note on an Arabic invoice
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertFalse(CustomerLedgerEntry.objects.exists())  # nothing moves until the invoice is posted
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(CustomerLedgerEntry.objects.get().due_increase, D("300.00"))

    def test_performance_counts_posted_sales_net_of_tax_and_returns(self):
        first, second, third = self.book(hour=9), self.book(hour=11), self.book(hour=13)
        for appointment in (first, second):
            services.set_status(appointment, AppointmentStatus.DONE, self.owner)
            post_sales_invoice(services.bill(appointment, self.owner).pk, self.owner)
        services.set_status(third, AppointmentStatus.DONE, self.owner)
        services.bill(third, self.owner)  # a draft earns nothing
        invoice = Appointment.objects.get(pk=second.pk).invoice
        create_sales_return("SR-1", TODAY, invoice.pk, [{"source_line": invoice.lines.get(), "quantity": 1}], "refund", self.owner)
        row = services.performance(TODAY, TODAY)[0]
        self.assertEqual((row["done"], row["sales"], row["commission"]), (3, D("300.00"), D("30.00")))

    def test_billing_needs_a_service(self):
        appointment = self.book(service=None, price="100")
        services.set_status(appointment, AppointmentStatus.DONE, self.owner)
        with self.assertRaisesMessage(ValidationError, "اختار الخدمة"):
            services.bill(appointment, self.owner)


class ScreenTests(AppointmentSetup):
    def test_book_from_the_agenda_see_it_remind_and_bill(self):
        self.client.force_login(self.owner)
        page = self.client.post(reverse("appointments:agenda"), {"day": TODAY.isoformat(), "time": "14:30", "duration_minutes": "45", "customer": str(self.customer.pk),
                                                                 "service": str(self.service.pk), "employee": str(self.tech.pk), "kind": "appointment"})
        appointment = Appointment.objects.get()
        self.assertRedirects(page, f"{reverse('appointments:agenda')}?lang=ar&day={TODAY.isoformat()}", fetch_redirect_response=False)
        agenda = self.client.get(reverse("appointments:agenda"), {"day": TODAY.isoformat()})
        self.assertContains(agenda, f'data-appointment-row="{appointment.number}"')
        self.assertContains(agenda, "محجوز")  # Arabic status label, not "Booked"
        detail = self.client.get(reverse("appointments:detail", args=[appointment.pk]))
        link = unquote(detail.context["reminder"])
        self.assertTrue(link.startswith("https://wa.me/201001234567?text="))
        self.assertIn("14:30", link)
        self.client.post(reverse("appointments:detail", args=[appointment.pk]), {"action": "status", "status": "arrived"})
        billed = self.client.post(reverse("appointments:detail", args=[appointment.pk]), {"action": "bill"})
        appointment.refresh_from_db()
        self.assertRedirects(billed, f"{reverse('sales:detail', args=[appointment.invoice.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("appointments:performance")), 'data-performance-row="E-0001"')

    def test_overlap_shows_on_the_form(self):
        self.book(hour=14)
        self.client.force_login(self.owner)
        page = self.client.post(reverse("appointments:agenda"), {"day": TODAY.isoformat(), "time": "14:15", "customer": str(self.customer.pk), "employee": str(self.tech.pk)})
        self.assertContains(page, "data-appointment-error")
        self.assertEqual(Appointment.objects.count(), 1)

    def test_permissions_and_module_gate(self):
        keeper = person(RoleCode.STOCK_KEEPER, "appt_keeper")
        self.client.force_login(keeper)
        self.assertIn(self.client.post(reverse("appointments:agenda"), {"day": TODAY.isoformat(), "time": "10:00", "customer": str(self.customer.pk)}).status_code, (302, 403))
        self.assertFalse(Appointment.objects.exists())
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), reverse("appointments:agenda"))
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(self.profile, "appointments_visits", False, self.owner)
        self.assertNotEqual(self.client.get(reverse("appointments:agenda")).status_code, 200)


class MoveTests(AppointmentSetup):
    """S10: the JSON backend of drag-to-reschedule."""

    def setUp(self):
        super().setUp()
        self.other_tech = save_employee({"name": "منى الفنية"}, self.owner)
        self.client.force_login(self.owner)

    def move(self, appointment, start, staff=None, client=None, **extra):
        data = {"start": start.isoformat() if hasattr(start, "isoformat") else start, **extra}
        if staff is not None:
            data["staff"] = str(staff.pk)
        return (client or self.client).post(reverse("appointments:move", args=[appointment.pk]), data)

    def test_move_keeps_the_duration_and_answers_with_the_new_times(self):
        appointment = self.book(hour=10, minutes=45)
        response = self.move(appointment, at(14))
        body = response.json()
        appointment.refresh_from_db()
        self.assertEqual((response.status_code, body["ok"], body["id"], body["staff"]), (200, True, appointment.pk, self.tech.pk))
        self.assertEqual((appointment.starts_at, appointment.duration_minutes, appointment.employee), (at(14), 45, self.tech))
        self.assertEqual((body["start"], body["end"]), (at(14).isoformat(), at(14, 45).isoformat()))
        self.assertNotIn("price", body)

    def test_overlap_is_409_in_arabic_and_english_and_changes_nothing(self):
        self.book(hour=10, minutes=60)
        appointment = self.book(hour=13)
        for lang, text in (("ar", "عنده ميعاد تاني من 10:00 لـ 11:00"), ("en", "already has an appointment from 10:00 to 11:00")):
            with self.subTest(lang=lang):
                response = self.move(appointment, at(10, 30), lang=lang)
                self.assertEqual(response.status_code, 409)
                self.assertFalse(response.json()["ok"])
                self.assertIn(text, response.json()["error"])
        appointment.refresh_from_db()
        self.assertEqual((appointment.starts_at, appointment.employee), (at(13), self.tech))
        self.assertFalse(AuditLog.objects.filter(action="move_appointment").exists())

    def test_it_never_clashes_with_itself(self):
        appointment = self.book(hour=10, minutes=60)
        self.assertEqual(self.move(appointment, at(10, 30)).status_code, 200)

    def test_changing_the_employee(self):
        appointment = self.book(hour=10)
        response = self.move(appointment, at(10), staff=self.other_tech)
        appointment.refresh_from_db()
        self.assertEqual((response.status_code, response.json()["staff"], appointment.employee), (200, self.other_tech.pk, self.other_tech))

    def test_changing_the_employee_into_an_overlap_is_refused(self):
        services.book({"customer": self.customer, "employee": self.other_tech, "service": self.service, "starts_at": at(10), "duration_minutes": 60}, self.owner)
        appointment = self.book(hour=10)
        response = self.move(appointment, at(10, 15), staff=self.other_tech)
        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 409)
        self.assertIn("منى الفنية", response.json()["error"])
        self.assertEqual((appointment.starts_at, appointment.employee), (at(10), self.tech))

    def test_a_past_time_is_accepted_exactly_as_booking_accepts_it(self):
        yesterday = at(10, day=TODAY - timedelta(days=1))
        self.assertEqual(self.book(hour=10, starts_at=yesterday).starts_at, yesterday)  # booking has no past-time rule
        appointment = self.book(hour=12)
        self.assertEqual(self.move(appointment, yesterday.replace(hour=12)).status_code, 200)

    def test_done_cancelled_and_no_show_cannot_move(self):
        for status in (AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
            with self.subTest(status=status):
                appointment = self.book(hour=8, employee=None)
                Appointment.objects.filter(pk=appointment.pk).update(status=status)
                response = self.move(appointment, at(16), lang="en")
                self.assertEqual(response.status_code, 400)
                self.assertIn("done or cancelled", response.json()["error"])
                appointment.refresh_from_db()
                self.assertEqual(appointment.starts_at, at(8))
                appointment.delete()

    def test_bad_input_is_400_and_bilingual(self):
        appointment = self.book(hour=10)
        for start, lang, text in (("", "ar", "اكتب ميعاد جديد صحيح"), ("not-a-date", "en", "Enter a valid new date and time"),
                                  ("2026-13-45T10:00", "en", "Enter a valid new date and time")):
            with self.subTest(start=start):
                response = self.move(appointment, start, lang=lang)
                self.assertEqual(response.status_code, 400)
                self.assertIn(text, response.json()["error"])
        url = reverse("appointments:move", args=[appointment.pk])
        response = self.client.post(url, {"start": at(11).isoformat(), "staff": "9999", "lang": "en"})
        self.assertEqual((response.status_code, response.json()["error"]), (400, "That employee does not exist or is not active."))
        self.tech.active = False
        self.tech.save()
        self.assertEqual(self.client.post(url, {"start": at(11).isoformat(), "staff": str(self.tech.pk)}).status_code, 400)
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))
        self.assertEqual(self.client.post(reverse("appointments:move", args=[99999]), {"start": at(11).isoformat()}).status_code, 404)

    def test_only_post_is_allowed(self):
        appointment = self.book()
        self.assertEqual(self.client.get(reverse("appointments:move", args=[appointment.pk])).status_code, 405)

    def test_without_the_permission_it_is_403_and_nothing_moves(self):
        appointment = self.book(hour=10)
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "move_keeper"))
        response = self.move(appointment, at(15))
        self.assertEqual(response.status_code, 403)
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))

    def test_csrf_is_enforced(self):
        from django.test import Client

        appointment = self.book(hour=10)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        self.assertEqual(self.move(appointment, at(15), client=strict).status_code, 403)
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))
        token = strict.get(reverse("appointments:agenda")).cookies["csrftoken"].value
        response = strict.post(reverse("appointments:move", args=[appointment.pk]), {"start": at(15).isoformat()}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_an_audit_entry_records_before_and_after(self):
        appointment = self.book(hour=10)
        self.move(appointment, at(15), staff=self.other_tech)
        entry = AuditLog.objects.get(action="move_appointment")
        self.assertEqual((entry.actor, entry.module, entry.object_id), (self.owner, "appointments", str(appointment.pk)))
        self.assertEqual((parse_datetime(entry.before_data["starts_at"]), entry.before_data["employee"]), (at(10), str(self.tech.pk)))
        self.assertEqual((parse_datetime(entry.after_data["starts_at"]), entry.after_data["employee"]), (at(15), str(self.other_tech.pk)))

    def test_moving_touches_no_money(self):
        appointment = self.book(hour=10)
        self.move(appointment, at(15))
        appointment.refresh_from_db()
        self.assertEqual((appointment.price, appointment.invoice_id), (D("300.00"), None))
        self.assertFalse(SalesInvoice.objects.exists())


class WeekJsonTests(AppointmentSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.monday = TODAY - timedelta(days=TODAY.weekday())

    def get(self, **params):
        return self.client.get(reverse("appointments:week_json"), {"start": self.monday.isoformat(), **params})

    def test_groups_the_week_per_employee_without_money(self):
        other = save_employee({"name": "منى الفنية"}, self.owner)
        inside = self.book(hour=10, starts_at=at(10, day=self.monday + timedelta(days=2)))
        self.book(hour=9, employee=other, starts_at=at(9, day=self.monday))
        self.book(hour=9, employee=None, starts_at=at(9, day=self.monday + timedelta(days=6)))
        self.book(hour=9, starts_at=at(9, day=self.monday + timedelta(days=7)))  # next week
        self.book(hour=9, starts_at=at(9, day=self.monday - timedelta(days=1)))  # last week
        body = self.get().json()
        self.assertEqual((body["start"], body["end"]), (self.monday.isoformat(), (self.monday + timedelta(days=6)).isoformat()))
        by_staff = {group["staff"]: group for group in body["staff"]}
        self.assertEqual(set(by_staff), {self.tech.pk, other.pk, None})
        row = by_staff[self.tech.pk]["appointments"]
        self.assertEqual(len(row), 1)
        day = self.monday + timedelta(days=2)
        self.assertEqual(row[0], {"id": inside.pk, "staff": self.tech.pk, "start": at(10, day=day).isoformat(), "end": at(11, day=day).isoformat(),
                                  "customer": "مدام سعاد", "service": "صيانة تكييف", "status": "booked", "status_label": "محجوز"})
        self.assertEqual(len(by_staff[None]["appointments"]), 1)
        text = self.get().content.decode()
        for forbidden in ("price", "invoice", "300"):
            self.assertNotIn(forbidden, text)

    def test_english_labels_and_a_cancelled_one_still_shows_its_status(self):
        appointment = self.book(hour=10, starts_at=at(10, day=self.monday))
        services.set_status(appointment, AppointmentStatus.CANCELLED, self.owner)
        row = self.get(lang="en").json()["staff"][0]["appointments"][0]
        self.assertEqual((row["status"], row["status_label"]), ("cancelled", "Cancelled"))

    def test_bad_start_is_400_and_a_missing_one_means_this_week(self):
        self.assertEqual(self.get(start="2026-13-99").status_code, 400)
        self.assertEqual(self.client.get(reverse("appointments:week_json")).json()["start"], TODAY.isoformat())

    def test_it_needs_the_view_permission(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "week_keeper"))
        self.assertEqual(self.get().status_code, 403)
