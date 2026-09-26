"""DUE-001: aging by FIFO, overdue by credit days, WhatsApp collection links."""

from datetime import timedelta
from decimal import Decimal as D
from urllib.parse import unquote

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer, make_seeded_role, make_supplier, make_user, make_user_profile
from master_data.models import Customer
from permissions.models import RoleCode
from purchases.models import SupplierLedgerEntry, SupplierLedgerEntryType
from sales.models import CustomerLedgerEntry, CustomerLedgerEntryType

from .aging import aging_rows, aging_totals, credit_days, reminder_link, set_credit_days, whatsapp_number
from .selectors import customer_report


TODAY = timezone.localdate()


def days_ago(n):
    return TODAY - timedelta(days=n)


def owe(customer, amount, when):
    CustomerLedgerEntry.objects.create(customer=customer, entry_date=when, entry_type=CustomerLedgerEntryType.SALES_DUE, due_increase=D(amount))


def pay(customer, amount, when):
    CustomerLedgerEntry.objects.create(customer=customer, entry_date=when, entry_type=CustomerLedgerEntryType.CUSTOMER_PAYMENT, due_decrease=D(amount))


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class AgingMathTests(TestCase):
    def setUp(self):
        self.ali = make_customer(customer_code="ALI", name="Ali", phone="01001234567", opening_balance=D("100.00"))
        Customer.objects.filter(pk=self.ali.pk).update(created_at=timezone.now() - timedelta(days=200))
        owe(self.ali, "200.00", days_ago(80))
        owe(self.ali, "300.00", days_ago(40))
        owe(self.ali, "150.00", days_ago(10))
        pay(self.ali, "250.00", days_ago(1))

    def test_fifo_settles_the_oldest_first(self):
        row = aging_rows("customers", TODAY)[0]
        # 250 paid: the 100 opening balance, then 150 of the 200 invoice (80 days old).
        self.assertEqual((row["b0_30"], row["b31_60"], row["b61_90"], row["b90_plus"]), (D("150.00"), D("300.00"), D("50.00"), D("0.00")))
        self.assertEqual(row["total"], D("500.00"))
        self.assertEqual(row["overdue"], D("350.00"))  # older than 30 days
        self.assertEqual(row["oldest"], days_ago(80))
        self.assertEqual(row["last_payment"], days_ago(1))

    def test_totals_match_the_customer_report_balance(self):
        mona = make_customer(customer_code="MONA", name="Mona")
        owe(mona, "75.50", days_ago(5))
        balances = {row["customer_id"]: row["balance"] for row in customer_report()}
        for row in aging_rows("customers", TODAY):
            with self.subTest(customer=row["code"]):
                self.assertEqual(row["total"], balances[row["party"].pk])
        self.assertEqual(aging_totals(aging_rows("customers", TODAY))["total"], D("575.50"))

    def test_overpaid_customer_shows_a_credit_balance(self):
        pay(self.ali, "1000.00", TODAY)
        row = aging_rows("customers", TODAY)[0]
        self.assertEqual((row["total"], row["credit"], row["overdue"]), (D("0.00"), D("500.00"), D("0.00")))

    def test_as_of_ignores_later_entries_and_ages_from_that_day(self):
        row = aging_rows("customers", days_ago(20))[0]
        # Before the payment and the 10-day invoice: 100 opening + 200 + 300.
        self.assertEqual(row["total"], D("600.00"))
        self.assertEqual(row["b0_30"], D("300.00"))  # 40 days old today = 20 days old then

    def test_credit_days_setting_changes_overdue(self):
        set_credit_days(90)
        self.assertEqual(credit_days(), 90)
        self.assertEqual(aging_rows("customers", TODAY)[0]["overdue"], D("0.00"))
        set_credit_days(0)
        self.assertEqual(aging_rows("customers", TODAY)[0]["overdue"], D("500.00"))
        with self.assertRaises(ValueError):
            set_credit_days(400)

    def test_customers_without_balance_are_left_out(self):
        make_customer(customer_code="ZERO", name="Zero")
        self.assertEqual([row["code"] for row in aging_rows("customers", TODAY)], ["ALI"])

    def test_suppliers(self):
        supplier = make_supplier(supplier_code="SUP-A", name="Delta")
        SupplierLedgerEntry.objects.create(supplier=supplier, entry_date=days_ago(100), entry_type=SupplierLedgerEntryType.PURCHASE_DUE, due_increase=D("900.00"))
        row = aging_rows("suppliers", TODAY)[0]
        self.assertEqual((row["b90_plus"], row["total"]), (D("900.00"), D("900.00")))


class WhatsappTests(SimpleTestCase):
    def test_numbers(self):
        self.assertEqual(whatsapp_number("01001234567"), "201001234567")
        self.assertEqual(whatsapp_number("+966 50 123 4567"), "966501234567")
        self.assertEqual(whatsapp_number("00971501234567"), "971501234567")
        self.assertEqual(whatsapp_number(""), "")

    def test_reminder_text(self):
        link = reminder_link({"whatsapp": "01001234567", "name": "علي", "overdue": D("350"), "total": D("500")}, "Demo Store", "EGP")
        self.assertTrue(link.startswith("https://wa.me/201001234567?text="))
        self.assertIn("350.00 EGP", unquote(link))
        self.assertIn("Demo Store", unquote(link))
        self.assertEqual(reminder_link({"whatsapp": "", "name": "x", "overdue": D("1"), "total": D("1")}, "S", "EGP"), "")


class AgingScreenTests(TestCase):
    def setUp(self):
        customer = make_customer(customer_code="SC", name="Screen Customer", phone="01009998887")
        owe(customer, "400.00", days_ago(45))

    def test_owner_sees_buckets_overdue_and_reminder(self):
        self.client.force_login(person(RoleCode.OWNER, "aging_owner"))
        page = self.client.get(reverse("reports:aging"))
        self.assertContains(page, "Screen Customer")
        self.assertContains(page, "400.00")
        self.assertContains(page, "https://wa.me/201009998887")
        self.assertContains(page, "تذكير واتساب")
        self.assertContains(self.client.get(reverse("report_hub")), reverse("reports:aging"))
        self.client.post(reverse("reports:aging"), {"credit_days": "60"})
        self.assertEqual(credit_days(), 60)
        self.assertEqual(self.client.get(reverse("reports:aging")).context["totals"]["overdue"], D("0.00"))

    def test_english(self):
        self.client.force_login(person(RoleCode.OWNER, "aging_owner_en"))
        self.assertContains(self.client.get(reverse("reports:aging"), {"lang": "en"}), "Customer aging and collections")

    def test_permissions(self):
        self.client.force_login(person(RoleCode.CASHIER, "aging_cashier"))
        self.assertEqual(self.client.get(reverse("reports:aging")).status_code, 403)
        accountant = person(RoleCode.ACCOUNTANT, "aging_accountant")
        self.client.force_login(accountant)
        self.assertEqual(self.client.get(reverse("reports:aging")).status_code, 200)
        self.assertEqual(self.client.get(reverse("reports:aging"), {"party": "suppliers"}).status_code, 200)
        self.assertEqual(self.client.post(reverse("reports:aging"), {"credit_days": "5"}).status_code, 403)
        self.assertEqual(credit_days(), 30)
