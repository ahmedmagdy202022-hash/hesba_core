"""PARTY-001: the card and statement agree with the party reports to the piastre."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from purchases.services import create_purchase_draft, post_purchase_invoice, record_supplier_payment
from reports.selectors import customer_report, supplier_report
from reports.tests_dashboard import prepared_client
from sales.services import cancel_posted_sales_invoice, create_sales_draft, create_sales_return, post_sales_invoice, record_customer_payment

from .services import balance, statement


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class PartySetup(TestCase):
    def setUp(self):
        prepared_client()
        Period.objects.create(period_code="P", name="p", start_date=TODAY - timedelta(days=90), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "party_owner")
        self.location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.item = make_item(item_code="RICE", item_name="Rice", default_sale_price=D("100.00"))
        stock_in(self.item, self.location, 50, "60.00", movement_date=TODAY - timedelta(days=60))
        recalculate_item_average_cost(self.item)
        record_customer_payment("SEED-CASH", TODAY - timedelta(days=60), make_customer(customer_code="SEED", name="Seed"), self.cashbox, D("5000.00"), self.owner)
        self.customer = make_customer(customer_code="KARIM", name="Karim", phone="01001234567", opening_balance=D("150.00"))
        self.supplier = make_supplier(supplier_code="NILE", name="Nile Foods", phone="0225551111", opening_balance=D("400.00"))

    def sale(self, number, days_ago, quantity, paid):
        invoice = create_sales_draft({"invoice_number": number, "invoice_date": TODAY - timedelta(days=days_ago), "customer": self.customer, "selling_location": self.location,
                                      "cashbox": self.cashbox, "paid_now": D(paid)}, [{"item": self.item, "quantity": D(quantity), "unit_sale_price": D("100.00")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice


class StatementTests(PartySetup):
    def test_statement_rows_running_balance_and_report_agree(self):
        first = self.sale("SI-1", 40, "10", "200.00")    # due +800
        record_customer_payment("CP-1", TODAY - timedelta(days=30), self.customer, self.cashbox, D("300.00"), self.owner)
        self.sale("SI-2", 20, "3", "300.00")              # fully paid: nothing due, no row
        create_sales_return(return_number="SR-1", return_date=TODAY - timedelta(days=10), source_invoice_id=first.pk,
                            lines=[{"source_line": first.lines.get(), "quantity": D("2")}], reason="damaged", user=self.owner)
        data = statement("customer", self.customer)
        self.assertEqual(data["opening"], D("150.00"))
        self.assertEqual([(row["entry"].entry_type, row["increase"], row["decrease"], row["balance"]) for row in data["rows"]][:2],
                         [("sales_due", D("800.00"), D("0.00"), D("950.00")), ("customer_payment", D("0.00"), D("300.00"), D("650.00"))])
        report = next(row for row in customer_report() if row["customer_id"] == self.customer.pk)
        self.assertEqual(data["closing"], report["balance"])
        self.assertEqual(balance("customer", self.customer), report["balance"])
        self.assertEqual(data["rows"][0]["reference"]["number"], "SI-1")

    def test_a_period_starts_from_the_balance_brought_forward(self):
        self.sale("SI-1", 40, "10", "200.00")
        record_customer_payment("CP-1", TODAY - timedelta(days=30), self.customer, self.cashbox, D("300.00"), self.owner)
        record_customer_payment("CP-2", TODAY - timedelta(days=5), self.customer, self.cashbox, D("100.00"), self.owner)
        data = statement("customer", self.customer, TODAY - timedelta(days=31), TODAY - timedelta(days=6))
        self.assertEqual((data["opening"], [r["entry"].customer_payment.payment_number for r in data["rows"]], data["closing"]), (D("950.00"), ["CP-1"], D("650.00")))
        self.assertEqual(statement("customer", self.customer)["closing"], D("550.00"))

    def test_a_cancelled_sale_nets_to_zero_and_the_supplier_side_works_the_same(self):
        invoice = self.sale("SI-X", 15, "4", "0")
        cancel_posted_sales_invoice(invoice.pk, self.owner, "wrong customer")
        self.assertEqual(statement("customer", self.customer)["closing"], D("150.00"))
        purchase = create_purchase_draft({"invoice_number": "PI-1", "invoice_date": TODAY - timedelta(days=12), "supplier": self.supplier, "receiving_location": self.location,
                                          "cashbox": self.cashbox, "paid_now": D("100.00")}, [{"item": self.item, "quantity": D("10"), "unit_purchase_price": D("60.00")}], self.owner)
        post_purchase_invoice(purchase.pk, self.owner)
        record_supplier_payment("SP-1", TODAY - timedelta(days=2), self.supplier, self.cashbox, D("250.00"), self.owner)
        data = statement("supplier", self.supplier)
        report = next(row for row in supplier_report() if row["supplier_id"] == self.supplier.pk)
        self.assertEqual((data["opening"], data["closing"], report["balance"]), (D("400.00"), D("650.00"), D("650.00")))


class ScreenTests(PartySetup):
    def test_card_shows_balance_actions_statement_and_print(self):
        self.sale("SI-1", 40, "10", "200.00")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("master_data:customers")), reverse("parties:card", args=["customer", self.customer.pk]))
        self.assertContains(self.client.get(reverse("reports:customers")), "data-party-link")
        page = self.client.get(reverse("parties:card", args=["customer", self.customer.pk]))
        self.assertContains(page, "مستحق على العميل")
        self.assertContains(page, "950.00")
        self.assertContains(page, "https://wa.me/201001234567")
        self.assertContains(page, f"customer={self.customer.pk}")
        form = self.client.get(reverse("sales:payment_create"), {"customer": self.customer.pk})
        self.assertEqual(form.context["form"].initial.get("customer"), str(self.customer.pk))
        printed = self.client.get(reverse("parties:statement", args=["customer", self.customer.pk]), {"from": (TODAY - timedelta(days=45)).isoformat()})
        self.assertContains(printed, "data-statement-closing")
        self.assertContains(printed, "950.00")
        english = self.client.get(reverse("parties:card", args=["supplier", self.supplier.pk]), {"lang": "en"})
        self.assertContains(english, "Owed to the supplier")

    def test_permissions_and_unknown_kinds(self):
        self.client.force_login(person(RoleCode.CASHIER, "party_cashier"))
        self.assertEqual(self.client.get(reverse("parties:card", args=["supplier", self.supplier.pk])).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/parties/bogus/1/").status_code, 404)
        self.assertEqual(self.client.get(reverse("parties:card", args=["customer", 99999])).status_code, 404)
