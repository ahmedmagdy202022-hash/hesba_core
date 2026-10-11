"""SVC-002: service contracts billed per period through the ordinary sales engine."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from permissions.services import user_has_permission
from reports.tests_dashboard import prepared_client
from settings_core.setup_services import complete_setup
from sales.models import CustomerLedgerEntry, SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice

from . import services
from .models import ContractBill, ServiceContract

TODAY = timezone.localdate()
MODULES = "customers,items_services,sales_operations,cashboxes,reports,employees_technicians,appointments_visits"


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class ContractSetup(TestCase):
    sub = "maintenance"

    def setUp(self):
        self.profile = prepared_client(activity="services", sub_activity=self.sub, modules=MODULES)
        self.owner = person(RoleCode.OWNER, "sc_owner")
        make_location(location_code="SHOP", is_default=True)
        self.customer = make_customer(customer_code="C-1", name="شركة النور")
        self.service = make_item(item_code="SRV-AC", item_name="صيانة تكييفات", default_sale_price="300.00", is_stock_tracked=False)

    def contract(self, **extra):
        data = {"customer": self.customer, "service": self.service, "amount": "1500", "every": "month", "starts_on": date(2026, 1, 31)}
        data.update(extra)
        return services.save_contract(data, self.owner)


class PeriodTests(ContractSetup):
    def test_month_ends_are_kept_and_periods_run_to_the_end_date(self):
        contract = self.contract(ends_on=date(2026, 5, 15))
        self.assertEqual(services.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(services.periods(contract, date(2026, 12, 31)),
                         [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])
        self.assertEqual(services.period_end(contract, date(2026, 2, 28)), date(2026, 3, 30))  # the next one starts on the 31st again
        self.assertEqual(services.period_end(contract, date(2026, 4, 30)), date(2026, 5, 30))
        quarterly = self.contract(every="quarter", starts_on=date(2026, 1, 1), ends_on=None)
        self.assertEqual(services.periods(quarterly, date(2026, 10, 1)), [date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)])
        self.assertEqual(services.period_end(quarterly, date(2026, 1, 1)), date(2026, 3, 31))
        yearly = self.contract(every="year", starts_on=date(2024, 2, 29))
        self.assertEqual(services.periods(yearly, date(2026, 3, 1)), [date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28)])

    def test_nothing_is_due_before_the_start_or_while_paused(self):
        contract = self.contract(starts_on=TODAY + timedelta(days=3))
        self.assertEqual(services.due_periods(contract), [])
        self.assertEqual(services.overview(contract)["next"], contract.starts_on)
        started = self.contract(starts_on=TODAY - timedelta(days=40))
        self.assertEqual(len(services.due_periods(started)), 2)
        paused = services.save_contract({"customer": self.customer, "service": self.service, "amount": "1500", "every": "month",
                                         "starts_on": started.starts_on, "status": "paused"}, self.owner, started)
        self.assertEqual(services.due_periods(paused), [])
        self.assertIsNone(services.overview(paused)["next"])


class SaveTests(ContractSetup):
    def test_numbered_and_audited(self):
        first, second = self.contract(), self.contract()
        self.assertEqual((first.number, second.number), ("SC-0001", "SC-0002"))
        self.assertEqual(first.amount, D("1500.00"))
        self.assertTrue(AuditLog.objects.filter(action="save_service_contract", object_id=str(first.pk)).exists())

    def test_refusals(self):
        for extra, text in (({"customer": None}, "اختار العميل"), ({"service": None}, "اختار الخدمة"), ({"amount": "0"}, "قيمة الفترة"),
                            ({"amount": "abc"}, "قيمة الفترة"), ({"amount": "NaN"}, "قيمة الفترة"), ({"every": "week"}, "كل قد إيه"),
                            ({"starts_on": "2026-13-01"}, "التاريخ"), ({"ends_on": date(2025, 1, 1)}, "بعد البداية"),
                            ({"visits_per_period": "99"}, "الزيارات"), ({"status": "gone"}, "الحالة")):
            with self.subTest(extra=extra), self.assertRaisesMessage(ValidationError, text):
                self.contract(**extra)
        self.assertFalse(ServiceContract.objects.exists())

    def test_a_billed_contract_keeps_its_start_and_period(self):
        contract = self.contract(starts_on=TODAY)
        services.bill_due(self.owner)
        base = {"customer": self.customer, "service": self.service, "amount": "1800", "every": "month", "starts_on": TODAY}
        for change in ({"every": "quarter"}, {"starts_on": TODAY - timedelta(days=1)}):
            with self.subTest(change=change), self.assertRaisesMessage(ValidationError, "اتفوتر قبل كده"):
                services.save_contract({**base, **change}, self.owner, contract)
        self.assertEqual(services.save_contract(base, self.owner, contract).amount, D("1800.00"))  # the amount may change

    def test_needs_the_create_invoice_permission(self):
        stock = person(RoleCode.STOCK_KEEPER, "sc_stock")
        self.assertFalse(user_has_permission(stock, services.MANAGE))
        with self.assertRaises(PermissionDenied):
            services.save_contract({"customer": self.customer, "service": self.service, "amount": "1"}, stock)
        with self.assertRaises(PermissionDenied):
            services.bill_due(stock)


class BillingTests(ContractSetup):
    def test_bill_due_makes_one_draft_per_period_and_never_twice(self):
        contract = self.contract(starts_on=services.add_months(TODAY, -2))
        ledger_before = CustomerLedgerEntry.objects.count()
        bills = services.bill_due(self.owner)
        self.assertEqual([bill.period_start for bill in sorted(bills, key=lambda b: b.period_start)],
                         [services.add_months(TODAY, -2), services.add_months(TODAY, -1), TODAY])
        invoice = bills[0].invoice
        self.assertEqual((invoice.status, invoice.customer, invoice.total_amount, invoice.paid_now), ("draft", self.customer, D("1500.00"), D("0.00")))
        line = invoice.lines.get()
        self.assertEqual((line.item, line.quantity, line.unit_sale_price), (self.service, D("1.000"), D("1500.00")))
        self.assertIn(contract.number, invoice.notes)
        self.assertEqual(CustomerLedgerEntry.objects.count(), ledger_before)  # a draft touches nobody's account
        self.assertEqual(services.due_periods(contract), [])
        with self.assertRaisesMessage(ValidationError, "مفيش فترات مستحقة"):
            services.bill_due(self.owner)
        self.assertEqual(ContractBill.objects.count(), 3)
        self.assertEqual(AuditLog.objects.filter(action="bill_service_contract").count(), 3)

    def test_posting_is_what_charges_the_customer_and_unpaid_follows_it(self):
        contract = self.contract(starts_on=TODAY)
        bill = services.bill_due(self.owner)[0]
        self.assertEqual(services.unpaid(contract), D("0.00"))
        post_sales_invoice(bill.invoice.id, user=self.owner)
        bill.invoice.refresh_from_db()
        self.assertEqual(bill.invoice.remaining_due, D("1500.00"))
        self.assertEqual(services.unpaid(contract), D("1500.00"))

    def test_a_cancelled_invoice_frees_its_period(self):
        contract = self.contract(starts_on=TODAY)
        bill = services.bill_due(self.owner)[0]
        post_sales_invoice(bill.invoice.id, user=self.owner)
        cancel_posted_sales_invoice(bill.invoice.id, user=self.owner, reason="خطأ")
        self.assertEqual(services.due_periods(contract), [TODAY])
        again = services.bill_due(self.owner)
        self.assertEqual([b.period_start for b in again], [TODAY])
        self.assertNotEqual(again[0].invoice_id, bill.invoice_id)
        self.assertEqual(services.unpaid(contract), D("0.00"))

    def test_only_the_given_contracts_and_only_active_ones(self):
        mine = self.contract(starts_on=TODAY)
        other = self.contract(starts_on=TODAY)
        ended = self.contract(starts_on=TODAY, status="ended")
        services.bill_due(self.owner, contracts=[mine])
        self.assertEqual((mine.bills.count(), other.bills.count()), (1, 0))
        services.bill_due(self.owner)
        self.assertEqual((other.bills.count(), ended.bills.count()), (1, 0))

    def test_visits_count_the_done_appointments_of_this_period(self):
        from appointments.models import AppointmentStatus
        from appointments.services import book, set_status

        contract = self.contract(starts_on=TODAY - timedelta(days=3), visits_per_period=2)
        for hours, done in ((1, True), (3, False)):
            appointment = book({"customer": self.customer, "service": self.service, "duration_minutes": 30,
                                "starts_at": timezone.make_aware(datetime.combine(TODAY, time(8 + hours)))}, self.owner)
            if done:
                for status in (AppointmentStatus.ARRIVED, AppointmentStatus.DONE):
                    set_status(appointment, status, self.owner)
        self.assertEqual(services.overview(contract)["visits"], 1)


class ScreenTests(ContractSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_create_from_the_list_then_bill_from_the_detail(self):
        response = self.client.post(reverse("agreements:list"), {"customer": self.customer.pk, "service": self.service.pk, "amount": "1200",
                                                                 "every": "month", "starts_on": TODAY.isoformat(), "visits_per_period": "1"})
        contract = ServiceContract.objects.get()
        self.assertRedirects(response, f"{reverse('agreements:detail', args=[contract.pk])}?lang=ar")
        page = self.client.get(reverse("agreements:detail", args=[contract.pk]))
        self.assertContains(page, "فوتر المستحق (1 فترة)")
        self.assertContains(page, 'data-contract-visits>0 من 1<', html=False)
        self.client.post(reverse("agreements:detail", args=[contract.pk]), {"action": "bill_due"})
        invoice = SalesInvoice.objects.get()
        page = self.client.get(reverse("agreements:detail", args=[contract.pk]))
        self.assertContains(page, invoice.invoice_number)
        self.assertContains(page, "1,200.00")
        self.assertNotContains(page, "data-bill-due")

    def test_list_shows_what_is_due_and_bills_it(self):
        self.contract(starts_on=services.add_months(TODAY, -1))
        page = self.client.get(reverse("agreements:list"))
        self.assertContains(page, "فوتر المستحق (2 فترة)")
        self.assertContains(page, 'data-contract-due="2"')
        english = self.client.get(reverse("agreements:list") + "?lang=en")
        self.assertContains(english, "Bill what is due (2 periods)")
        self.assertContains(english, "Every month")
        self.client.post(reverse("agreements:list"), {"action": "bill_due"})
        self.assertEqual(SalesInvoice.objects.filter(status="draft").count(), 2)

    def test_a_bad_form_keeps_what_was_typed(self):
        response = self.client.post(reverse("agreements:list"), {"customer": self.customer.pk, "service": self.service.pk, "amount": "-5",
                                                                 "description": "عقد سنوي", "starts_on": TODAY.isoformat()})
        self.assertContains(response, "قيمة الفترة لازم تكون رقم أكبر من صفر.")
        self.assertContains(response, 'value="عقد سنوي"')
        self.assertFalse(ServiceContract.objects.exists())

    def test_the_menu_carries_it(self):
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), f'href="{reverse("agreements:list")}?lang')


class AccessTests(ContractSetup):
    def test_view_only_users_see_but_cannot_change(self):
        contract = self.contract(starts_on=TODAY)
        checked = set()
        for code in RoleCode.values:
            user = person(code, f"sc_role_{code}")
            if user_has_permission(user, services.VIEW) and not user_has_permission(user, services.MANAGE):
                self.client.force_login(user)
                page = self.client.get(reverse("agreements:detail", args=[contract.pk]))
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, "data-contract-form")
                self.assertEqual(self.client.post(reverse("agreements:detail", args=[contract.pk]), {"action": "bill_due"}).status_code, 403)
                self.assertFalse(contract.bills.exists())
                checked.add("view_only")
            elif not user_has_permission(user, services.VIEW):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("agreements:list")).status_code, 403)
                checked.add("none")
        self.assertEqual(checked, {"view_only", "none"})  # the seeded roles cover both cases

    def test_not_for_a_shop_or_a_salon(self):
        self.client.force_login(self.owner)
        for activity, sub in (("commercial", "retail"), ("services", "beauty"), ("services", "clinic")):
            with self.subTest(sub=sub):
                complete_setup(self.profile, activity, sub, MODULES)
                self.assertEqual(self.client.get(reverse("agreements:list")).status_code, 404)
                self.assertNotContains(self.client.get(reverse("dashboard_snapshot")), f'href="{reverse("agreements:list")}?lang')


class MaintenanceContractorTests(ContractSetup):
    def setUp(self):
        super().setUp()
        complete_setup(self.profile, "contracting", "maintenance_contracts", MODULES + ",projects")

    def test_a_maintenance_contractor_has_them_too(self):
        self.assertTrue(services.is_contract_business())
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("agreements:list")).status_code, 200)
