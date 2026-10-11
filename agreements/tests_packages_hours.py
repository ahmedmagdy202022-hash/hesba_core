"""SVC-003: session packages for salons and billable hours for offices, both billed through the ordinary sales engine."""

from datetime import timedelta
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
from sales.models import CustomerLedgerEntry, SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice
from settings_core.setup_services import complete_setup
from staff.services import save_employee

from . import hours, packages
from .models import Package, TimeEntry

TODAY = timezone.localdate()
MODULES = "customers,items_services,sales_operations,cashboxes,reports,employees_technicians,appointments_visits"


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class Setup(TestCase):
    sub = "beauty"

    def setUp(self):
        self.profile = prepared_client(activity="services", sub_activity=self.sub, modules=MODULES)
        self.owner = person(RoleCode.OWNER, "ph_owner")
        make_location(location_code="SHOP", is_default=True)
        self.customer = make_customer(customer_code="C-1", name="منى عبد الله")
        self.service = make_item(item_code="SRV-1", item_name="سشوار", default_sale_price="200.00", is_stock_tracked=False)
        self.staff = save_employee({"name": "منى الكوافيرة"}, self.owner)


class PackageTests(Setup):
    def sell(self, **extra):
        data = {"customer": self.customer, "service": self.service, "sessions": "6", "price": "1000"}
        data.update(extra)
        return packages.sell_package(data, self.owner)

    def test_sold_on_one_draft_invoice_with_the_whole_price(self):
        ledger_before = CustomerLedgerEntry.objects.count()
        package = self.sell()
        self.assertEqual(package.number, "PK-0001")
        invoice = package.invoice
        self.assertEqual((invoice.status, invoice.customer, invoice.total_amount, invoice.paid_now), ("draft", self.customer, D("1000.00"), D("0.00")))
        line = invoice.lines.get()
        self.assertEqual((line.item, line.quantity, line.unit_sale_price), (self.service, D("1.000"), D("1000.00")))
        self.assertIn("6", line.description)
        self.assertEqual(CustomerLedgerEntry.objects.count(), ledger_before)
        self.assertEqual(packages.state(package), "draft")
        self.assertEqual(self.sell(price="").price, D("1200.00"))  # blank: the service price for each session
        self.assertTrue(AuditLog.objects.filter(action="sell_service_package").exists())

    def test_sessions_only_after_posting_and_never_below_zero(self):
        package = self.sell(sessions="2")
        with self.assertRaisesMessage(ValidationError, "رحّلها الأول"):
            packages.use_session(package, self.owner)
        post_sales_invoice(package.invoice.id, user=self.owner)
        package.invoice.refresh_from_db()
        self.assertEqual(package.invoice.remaining_due, D("1000.00"))  # posting is what charges the customer
        packages.use_session(package, self.owner, employee=self.staff)
        packages.use_session(package, self.owner, used_on=TODAY - timedelta(days=1))
        self.assertEqual((packages.remaining(package), packages.state(package)), (0, "used_up"))
        with self.assertRaisesMessage(ValidationError, "الباكدج خلص"):
            packages.use_session(package, self.owner)

    def test_a_cancelled_session_is_given_back_once(self):
        package = self.sell(sessions="1")
        post_sales_invoice(package.invoice.id, user=self.owner)
        use = packages.use_session(package, self.owner)
        packages.cancel_use(use, self.owner)
        self.assertEqual(packages.remaining(package), 1)
        with self.assertRaisesMessage(ValidationError, "اتلغت قبل كده"):
            packages.cancel_use(use, self.owner)
        self.assertEqual(package.uses.count(), 1)  # kept, not deleted

    def test_expired_future_and_cancelled_invoice(self):
        package = self.sell(expires_on=TODAY)
        post_sales_invoice(package.invoice.id, user=self.owner)
        with self.assertRaisesMessage(ValidationError, "لسه مجاش"):
            packages.use_session(package, self.owner, used_on=TODAY + timedelta(days=1))
        Package.objects.filter(pk=package.pk).update(expires_on=TODAY - timedelta(days=1))
        package.refresh_from_db()
        self.assertEqual(packages.state(package), "expired")
        with self.assertRaisesMessage(ValidationError, "انتهت صلاحيته"):
            packages.use_session(package, self.owner)
        packages.use_session(package, self.owner, used_on=TODAY - timedelta(days=1))  # a session done before it expired still counts
        other = self.sell()
        post_sales_invoice(other.invoice.id, user=self.owner)
        cancel_posted_sales_invoice(other.invoice.id, user=self.owner, reason="رجعت في كلامها")
        other.invoice.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "اتلغت"):
            packages.use_session(other, self.owner)

    def test_refusals(self):
        for extra, text in (({"customer": None}, "اختار العميل"), ({"service": None}, "اختار الخدمة"), ({"sessions": "0"}, "عدد الجلسات"),
                            ({"sessions": "x"}, "عدد الجلسات"), ({"price": "-1"}, "سعر الباكدج"), ({"price": "inf"}, "سعر الباكدج"),
                            ({"expires_on": TODAY - timedelta(days=1)}, "تاريخ انتهاء")):
            with self.subTest(extra=extra), self.assertRaisesMessage(ValidationError, text):
                self.sell(**extra)
        self.assertFalse(Package.objects.exists())
        self.assertFalse(SalesInvoice.objects.exists())

    def test_totals_cover_every_package_and_the_list_counts_in_one_query(self):
        for _ in range(3):
            post_sales_invoice(self.sell(sessions="2").invoice.id, user=self.owner)
        every = packages.with_used(Package.objects.select_related("invoice"))
        self.assertEqual(packages.totals(every)["sold"], 6)
        self.client.force_login(self.owner)
        url = reverse("agreements:packages")
        self.client.get(url)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as three:
            self.client.get(url)
        for _ in range(3):
            self.sell(sessions="2")
        with CaptureQueriesContext(connection) as six:
            self.client.get(url)
        self.assertEqual(len(three), len(six))  # no query per package
        package = Package.objects.first()
        self.assertEqual(self.client.post(reverse("agreements:package", args=[package.pk]), {"action": "cancel_use", "use": "x"}).status_code, 404)

    def test_totals_count_posted_packages_only(self):
        posted, draft = self.sell(sessions="4"), self.sell(sessions="3")
        post_sales_invoice(posted.invoice.id, user=self.owner)
        packages.use_session(posted, self.owner)
        posted.invoice.refresh_from_db()
        self.assertEqual(packages.totals([posted, draft]), {"sold": 4, "used": 1, "left": 3, "value": D("1000.00")})

    def test_screens(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("agreements:packages"), {"customer": self.customer.pk, "service": self.service.pk, "sessions": "5", "price": ""})
        package = Package.objects.get()
        self.assertRedirects(response, f"{reverse('agreements:package', args=[package.pk])}?lang=ar")
        page = self.client.get(reverse("agreements:package", args=[package.pk]))
        self.assertContains(page, "رحّل الفاتورة الأول")
        self.assertNotContains(page, "data-use-form")
        post_sales_invoice(package.invoice.id, user=self.owner)
        self.client.post(reverse("agreements:package", args=[package.pk]), {"action": "use", "employee": self.staff.pk})
        page = self.client.get(reverse("agreements:package", args=[package.pk]))
        self.assertContains(page, 'data-package-left>4<', html=False)
        use = package.uses.get()
        self.client.post(reverse("agreements:package", args=[package.pk]), {"action": "cancel_use", "use": use.pk})
        self.assertEqual(packages.remaining(package), 5)
        listing = self.client.get(reverse("agreements:packages") + "?lang=en")
        self.assertContains(listing, "Session packages")
        self.assertContains(listing, 'data-package-state="active"')
        self.assertContains(listing, "1,000.00")
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), f'href="{reverse("agreements:packages")}?lang')

    def test_who_sees_it(self):
        package = self.sell()
        stock = person(RoleCode.STOCK_KEEPER, "ph_stock")
        self.assertFalse(user_has_permission(stock, packages.MANAGE))
        with self.assertRaises(PermissionDenied):
            packages.sell_package({"customer": self.customer, "service": self.service, "sessions": "1"}, stock)
        with self.assertRaises(PermissionDenied):
            packages.use_session(package, stock)
        self.client.force_login(stock)
        self.assertEqual(self.client.get(reverse("agreements:packages")).status_code, 403)
        self.client.force_login(self.owner)
        for sub in ("professional", "clinic"):
            complete_setup(self.profile, "services", sub, MODULES)
            self.assertEqual(self.client.get(reverse("agreements:packages")).status_code, 404)


class HoursTests(Setup):
    sub = "professional"

    def log(self, **extra):
        data = {"customer": self.customer, "service": self.service, "hours": "2", "worked_on": TODAY}
        data.update(extra)
        return hours.log_time(data, self.owner)

    def test_log_takes_the_service_rate_and_bills_on_one_draft(self):
        first = self.log(hours="1.5", worked_on=TODAY - timedelta(days=2), employee=self.staff, description="مراجعة")
        self.log(hours="2", rate="300")
        self.assertEqual((first.rate, first.hours), (D("200.00"), D("1.50")))
        other = make_customer(customer_code="C-2", name="شركة الأمل")
        self.log(customer=other)
        invoice = hours.bill_hours(self.customer, self.owner)
        self.assertEqual(invoice.status, "draft")
        lines = list(invoice.lines.order_by("line_number"))
        self.assertEqual([(line.quantity, line.unit_sale_price) for line in lines], [(D("1.500"), D("200.00")), (D("2.000"), D("300.00"))])
        self.assertIn("منى الكوافيرة", lines[0].description)
        self.assertEqual(invoice.total_amount, D("900.00"))
        self.assertEqual(TimeEntry.objects.filter(invoice=invoice).count(), 2)
        self.assertEqual([row["customer"] for row in hours.by_customer()], [other])  # the other customer still waits
        with self.assertRaisesMessage(ValidationError, "مفيش ساعات"):
            hours.bill_hours(self.customer, self.owner)

    def test_a_cancelled_invoice_frees_its_hours_and_a_billed_hour_stays(self):
        entry = self.log()
        invoice = hours.bill_hours(self.customer, self.owner)
        with self.assertRaisesMessage(ValidationError, "اتفوترت"):
            hours.remove_entry(entry, self.owner)
        post_sales_invoice(invoice.id, user=self.owner)
        cancel_posted_sales_invoice(invoice.id, user=self.owner, reason="غلط")
        again = hours.bill_hours(self.customer, self.owner)
        entry.refresh_from_db()
        self.assertEqual(entry.invoice, again)
        self.assertEqual(again.total_amount, D("400.00"))

    def test_refusals(self):
        for extra, text in (({"customer": None}, "اختار العميل"), ({"service": None}, "اختار الخدمة"), ({"hours": "0.1"}, "الساعات من"),
                            ({"hours": "25"}, "الساعات من"), ({"hours": "NaN"}, "الساعات من"), ({"rate": "-1"}, "سعر الساعة"),
                            ({"worked_on": TODAY + timedelta(days=1)}, "لسه مجاش")):
            with self.subTest(extra=extra), self.assertRaisesMessage(ValidationError, text):
                self.log(**extra)
        self.assertFalse(TimeEntry.objects.exists())

    def test_an_employee_logs_and_removes_their_own_hours_only(self):
        stock = person(RoleCode.STOCK_KEEPER, "ph_consultant")
        self.assertFalse(user_has_permission(stock, hours.MANAGE))
        with self.assertRaises(PermissionDenied):
            hours.log_time({"customer": self.customer, "service": self.service, "hours": "1"}, stock)
        self.staff.user = stock
        self.staff.save()
        mine = hours.log_time({"customer": self.customer, "service": self.service, "hours": "1", "employee": None}, stock)
        self.assertEqual(mine.employee, self.staff)
        theirs = self.log(employee=save_employee({"name": "أ. محمود"}, self.owner))
        with self.assertRaises(PermissionDenied):
            hours.remove_entry(theirs, stock)
        with self.assertRaises(PermissionDenied):
            hours.bill_hours(self.customer, stock)
        hours.remove_entry(mine, stock)
        self.assertFalse(TimeEntry.objects.filter(pk=mine.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="remove_time_entry").exists())
        self.client.force_login(stock)
        page = self.client.get(reverse("agreements:hours"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-bill-hours")
        self.assertNotContains(page, "data-hours-team")
        self.assertNotContains(page, f'data-remove-entry="{theirs.pk}"')  # someone else's hours are not shown

    def test_an_employee_sees_only_their_own_totals_and_cannot_set_a_rate(self):
        consultant = person(RoleCode.STOCK_KEEPER, "ph_consultant2")
        self.staff.user = consultant
        self.staff.save()
        colleague = save_employee({"name": "أ. محمود"}, self.owner)
        self.log(employee=colleague, hours="5")
        mine = hours.log_time({"customer": self.customer, "service": self.service, "hours": "3", "rate": "5000"}, consultant)
        self.assertEqual(mine.rate, D("200.00"))  # the service's price, not the typed one
        self.client.force_login(consultant)
        page = self.client.get(reverse("agreements:hours"))
        self.assertContains(page, 'data-hours-total>3<', html=False)
        self.assertNotContains(page, 'data-hours-total>8<', html=False)
        self.assertNotContains(page, "1,600.00")  # the colleague's 5h × 200 + 3h × 200

    def test_a_bad_id_is_a_404_not_a_crash(self):
        self.client.force_login(self.owner)
        for action, field in (("bill", "customer"), ("remove", "entry")):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(reverse("agreements:hours"), {"action": action, field: "abc"}).status_code, 404)

    def test_team_hours_and_billed_share(self):
        self.log(employee=self.staff, hours="3")
        hours.bill_hours(self.customer, self.owner)
        self.log(employee=self.staff, hours="1")
        row = hours.by_employee(TODAY.replace(day=1), TODAY)[0]
        self.assertEqual((row["employee"], row["hours"], row["billed"], row["billed_pct"]), (self.staff, D("4.00"), D("3.00"), 75))

    def test_screens(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("agreements:hours"), {"action": "log", "customer": self.customer.pk, "service": self.service.pk, "hours": "2.5",
                                                       "worked_on": TODAY.isoformat()})
        page = self.client.get(reverse("agreements:hours"))
        self.assertContains(page, 'data-hours-total>2.5<', html=False)
        self.assertContains(page, "500.00")
        response = self.client.post(reverse("agreements:hours"), {"action": "bill", "customer": self.customer.pk})
        invoice = SalesInvoice.objects.get()
        self.assertRedirects(response, f"{reverse('sales:detail', args=[invoice.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("agreements:hours") + "?lang=en"), "No hours waiting to be billed.")
        bad = self.client.post(reverse("agreements:hours"), {"action": "log", "customer": self.customer.pk, "service": self.service.pk, "hours": "99",
                                                             "description": "تقرير"})
        self.assertContains(bad, "الساعات من 0.25 لـ 24.")
        self.assertContains(bad, 'value="تقرير"')
        nav = self.client.get(reverse("dashboard_snapshot"))
        self.assertContains(nav, f'href="{reverse("agreements:hours")}?lang')
        self.assertNotContains(nav, f'href="{reverse("agreements:packages")}?lang')

    def test_not_for_a_salon_or_a_shop(self):
        self.client.force_login(self.owner)
        for activity, sub in (("services", "beauty"), ("commercial", "retail")):
            complete_setup(self.profile, activity, sub, MODULES)
            self.assertEqual(self.client.get(reverse("agreements:hours")).status_code, 404)
