"""APPT-001: appointments & visits, billed through the ordinary sales engine."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from urllib.parse import unquote

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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
