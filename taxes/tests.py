"""TAX-001 / HG-015: VAT per sales line; revenue and profit are net of tax."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from reports.analytics import build_analytics
from reports.selectors import profit_report, profit_totals
from reports.tests_analytics import EVERYTHING, by_key
from reports.tests_dashboard import prepared_client
from sales.pos import checkout, walk_in_customer
from sales.services import create_sales_draft, create_sales_return, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from .models import ItemTaxRate, SalesLineTax, SalesReturnLineTax, TaxRate
from .services import compute_lines, create_sales_draft_with_tax, vat_report


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class VatSetup(TestCase):
    """A wholesale shop (VAT suggested): A at the default 14%, B exempt."""

    def setUp(self):
        prepared_client(sub_activity="wholesale")
        Period.objects.create(period_code="TX", name="tx", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "tax_owner")
        self.location = make_location()
        self.cashbox = make_cashbox(cashbox_code="TX-CASH")
        self.a = make_item(item_code="A", item_name="Taxed", default_sale_price=D("100.00"))
        self.b = make_item(item_code="B", item_name="Exempt", default_sale_price=D("50.00"))
        ItemTaxRate.objects.create(item=self.b, tax_rate=TaxRate.objects.get(code="EXEMPT"))
        for item, cost in ((self.a, "60.00"), (self.b, "30.00")):
            stock_in(item, self.location, 10, cost, movement_date=TODAY - timedelta(days=10))
            recalculate_item_average_cost(item)
        self.customer = make_customer(customer_code="TXC", name="Tax Customer")

    def draft(self, number="TX-1", paid="100.00", discount="10.00", lines=None):
        lines = lines or [{"item": self.a, "quantity": D("2"), "unit_sale_price": D("100.00")}, {"item": self.b, "quantity": D("1"), "unit_sale_price": D("50.00")}]
        return create_sales_draft_with_tax(
            {"invoice_number": number, "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location, "cashbox": self.cashbox, "paid_now": D(paid), "discount_amount": D(discount)},
            lines, self.owner,
        )


class ChargingTests(VatSetup):
    def test_tax_per_line_and_the_invoice_total(self):
        invoice = self.draft()
        # 2 x 100 at 14% = 28 tax; 50 exempt; the 10 discount comes after tax.
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.discount_amount, invoice.total_amount, invoice.remaining_due), (D("250.00"), D("28.00"), D("10.00"), D("268.00"), D("168.00")))
        rows = {row.line.item.item_code: (row.rate, row.taxable_amount, row.tax_amount) for row in SalesLineTax.objects.select_related("line__item")}
        self.assertEqual(rows, {"A": (D("14.00"), D("200.00"), D("28.00")), "B": (D("0.00"), D("50.00"), D("0.00"))})

    def test_rounding_is_per_line_to_the_piastre(self):
        taxes = compute_lines([{"item": self.a, "quantity": D("3"), "unit_sale_price": D("3.35")}])
        self.assertEqual(taxes[0][3], D("1.41"))  # 10.05 x 14% = 1.407

    def test_no_tax_when_the_capability_is_off(self):
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        invoice = self.draft(number="TX-OFF")
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("0.00"), D("240.00")))
        self.assertFalse(SalesLineTax.objects.exists())


class PostingAndReturnTests(VatSetup):
    def test_revenue_and_profit_exclude_tax_while_money_includes_it(self):
        invoice = self.draft()
        post_sales_invoice(invoice.pk, self.owner)
        lines = {line.item.item_code: line for line in invoice.lines.select_related("item")}
        revenue = sum((line.line_cost_amount + line.line_profit_amount for line in lines.values()), D("0"))
        self.assertEqual(revenue, D("240.00"))  # 250 - 10 discount; the 28 tax is not revenue
        self.assertEqual(sum((line.line_profit_amount for line in lines.values()), D("0")), D("90.00"))  # 240 - (120 + 30)
        totals = profit_totals(TODAY, TODAY)
        self.assertEqual((totals["sales"], totals["cost"], totals["profit"]), (D("240.00"), D("150.00"), D("90.00")))
        self.assertEqual(sum(row["sales_amount"] for row in profit_report(TODAY, TODAY)), D("240.00"))
        metrics = by_key(build_analytics(EVERYTHING, "today", TODAY))
        self.assertEqual(metrics["net_sales"]["value"], D("240.00"))
        self.assertEqual(metrics["receivables"]["value"], D("168.00"))  # the customer owes the tax too

    def test_a_return_refunds_the_tax_and_the_report_nets_it(self):
        invoice = self.draft(paid="0.00")
        post_sales_invoice(invoice.pk, self.owner)
        line_a = invoice.lines.get(item=self.a)
        sales_return = create_sales_return("TXR-1", TODAY, invoice.pk, [{"source_line": line_a, "quantity": D("1")}], "تالف", self.owner)
        self.assertEqual(SalesReturnLineTax.objects.get().tax_amount, D("14.00"))
        refund = sales_return.total_amount
        # A charged 228 of 278 before the discount; its share of 268 is 219.81, half returned.
        self.assertEqual(refund, D("109.90"))
        totals = profit_totals(TODAY, TODAY)
        self.assertEqual(totals["sales"], D("240.00") - (refund - D("14.00")))
        self.assertEqual(totals["cost"], D("90.00"))
        report = vat_report(TODAY, TODAY)
        by_rate = {row["rate"]: row for row in report["rows"]}
        self.assertEqual((by_rate[D("14.00")]["tax"], by_rate[D("14.00")]["returned_tax"], by_rate[D("14.00")]["net_tax"], by_rate[D("14.00")]["taxable"]), (D("28.00"), D("14.00"), D("14.00"), D("100.00")))
        self.assertEqual(by_rate[D("0.00")]["taxable"], D("50.00"))
        self.assertEqual(report["net_tax"], D("14.00"))

    def test_returning_everything_gives_back_exactly_the_tax(self):
        invoice = self.draft(paid="0.00", discount="0.00", lines=[{"item": self.a, "quantity": D("3"), "unit_sale_price": D("3.35")}])
        post_sales_invoice(invoice.pk, self.owner)
        line = invoice.lines.get()
        create_sales_return("TXR-A", TODAY, invoice.pk, [{"source_line": line, "quantity": D("1")}], "x", self.owner)
        create_sales_return("TXR-B", TODAY, invoice.pk, [{"source_line": line, "quantity": D("2")}], "x", self.owner)
        self.assertEqual(sum(row.tax_amount for row in SalesReturnLineTax.objects.all()), invoice.tax_amount)
        self.assertEqual(vat_report(TODAY, TODAY)["net_tax"], D("0.00"))

    def test_an_invoice_without_tax_posts_exactly_as_before(self):
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        invoice = self.draft(number="TX-PLAIN", discount="0.00")
        post_sales_invoice(invoice.pk, self.owner)
        profits = sorted(line.line_profit_amount for line in invoice.lines.all())
        self.assertEqual(profits, [D("20.00"), D("80.00")])  # 50-30 and 200-120

    def test_a_tax_typed_on_the_header_is_kept_out_of_revenue(self):
        # Before TAX-001 a hand-typed header tax became revenue; now it is not.
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        invoice = create_sales_draft(
            {"invoice_number": "TX-HDR", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location, "cashbox": self.cashbox, "paid_now": D("0"), "tax_amount": D("14.00")},
            [{"item": self.a, "quantity": D("1"), "unit_sale_price": D("100.00")}], self.owner,
        )
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(invoice.lines.get().line_profit_amount, D("40.00"))
        self.assertEqual(vat_report(TODAY, TODAY)["untracked_header_tax"], D("14.00"))


class PosTests(VatSetup):
    def test_the_till_charges_tax_and_the_walk_in_pays_it_all(self):
        set_capability_enabled(ClientProfile.get_active(), "pos", True, user=self.owner)
        invoice, change = checkout(
            lines=[{"item": self.a, "quantity": D("1"), "unit_sale_price": D("100.00")}],
            customer=walk_in_customer(), location=self.location, cashbox=self.cashbox, discount=D("0"), tendered=D("120.00"), user=self.owner,
        )
        self.assertEqual((invoice.tax_amount, invoice.total_amount, invoice.paid_now, change), (D("14.00"), D("114.00"), D("114.00"), D("6.00")))
        self.assertEqual(invoice.status, "posted")


class ScreenTests(VatSetup):
    def test_sales_form_computes_tax_instead_of_asking_for_it(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("sales:create"))
        self.assertNotContains(page, 'name="tax_amount"')
        self.assertContains(page, 'id="hs-tax-rates"')
        self.assertContains(page, "شامل الضريبة")
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        self.assertContains(self.client.get(reverse("sales:create")), 'name="tax_amount"')

    def test_settings_assign_rates_and_report(self):
        self.client.force_login(self.owner)
        vat14 = TaxRate.objects.get(code="VAT14")
        self.client.post(reverse("taxes:settings"), {"action": "items", f"shown_{self.a.pk}": "1", f"rate_{self.a.pk}": str(TaxRate.objects.get(code="EXEMPT").pk), f"shown_{self.b.pk}": "1", f"rate_{self.b.pk}": ""})
        self.assertEqual(ItemTaxRate.objects.get(item=self.a).tax_rate.code, "EXEMPT")
        self.assertFalse(ItemTaxRate.objects.filter(item=self.b).exists())  # back to the default
        self.client.post(reverse("taxes:settings"), {"action": "rate", "code": "vat5", "name_ar": "مخفضة", "rate": "5", "is_default": "1"})
        self.assertTrue(TaxRate.objects.get(code="VAT5").is_default)
        vat14.refresh_from_db()
        self.assertFalse(vat14.is_default)
        self.assertContains(self.client.get(reverse("taxes:report")), "إقرار ضريبة القيمة المضافة")
        self.assertContains(self.client.get(reverse("report_hub")), reverse("taxes:report"))

    def test_permissions(self):
        self.client.force_login(person(RoleCode.CASHIER, "tax_cashier"))
        self.assertEqual(self.client.post(reverse("taxes:settings"), {"action": "rate", "code": "X", "name_ar": "x", "rate": "1"}).status_code, 403)
        self.assertFalse(TaxRate.objects.filter(code="X").exists())
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("taxes:settings")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("report_hub")), reverse("taxes:report"))


# --- TAX-002 / HG-016: purchase (input) VAT is recoverable, not cost ------------

from inventory.services import get_item_stock_value  # noqa: E402
from purchases.services import create_purchase_draft, create_purchase_return, post_purchase_invoice  # noqa: E402
from hesba_testing.factories import make_supplier  # noqa: E402

from .models import PurchaseLineTax, PurchaseReturnLineTax  # noqa: E402
from .services import create_purchase_draft_with_tax, input_vat, vat_return  # noqa: E402


class PurchaseVatTests(TestCase):
    def setUp(self):
        prepared_client(sub_activity="wholesale")  # vat suggested -> on
        Period.objects.create(period_code="PV", name="pv", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "pv_owner")
        self.location = make_location()
        self.cashbox = make_cashbox(cashbox_code="PV-CASH")
        from cashboxes.models import CashboxOperationType
        from cashboxes.services import create_cashbox_operation

        create_cashbox_operation("PV-CAP", TODAY, CashboxOperationType.DIRECT_IN, D("5000"), "capital", self.owner, destination_cashbox=self.cashbox)
        self.a = make_item(item_code="PA", item_name="Taxed", default_sale_price=D("100.00"))
        self.b = make_item(item_code="PB", item_name="Exempt", default_sale_price=D("40.00"))
        ItemTaxRate.objects.create(item=self.b, tax_rate=TaxRate.objects.get(code="EXEMPT"))
        self.supplier = make_supplier(supplier_code="PVS", name="Supplier")

    def purchase(self, number="PV-1", discount="0.00", paid="300.00", with_vat=True):
        header = {"invoice_number": number, "invoice_date": TODAY, "supplier": self.supplier, "receiving_location": self.location, "cashbox": self.cashbox, "paid_now": D(paid), "discount_amount": D(discount)}
        lines = [{"item": self.a, "quantity": D("10"), "unit_purchase_price": D("50.00")}, {"item": self.b, "quantity": D("4"), "unit_purchase_price": D("25.00")}]
        invoice = (create_purchase_draft_with_tax if with_vat else create_purchase_draft)(header, lines, self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice

    def test_input_tax_is_charged_and_kept_out_of_stock_cost(self):
        invoice = self.purchase()
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.remaining_due), (D("600.00"), D("70.00"), D("670.00"), D("370.00")))
        self.assertEqual(PurchaseLineTax.objects.get(line__item=self.a).tax_amount, D("70.00"))
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.average_cost, self.b.average_cost), (D("50.0000"), D("25.0000")))
        self.assertEqual(get_item_stock_value(self.a) + get_item_stock_value(self.b), D("600.0000"))  # the 70 tax is not stock

    def test_invoice_discount_after_tax_lowers_cost_not_tax(self):
        invoice = self.purchase(discount="10.00")
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("70.00"), D("660.00")))
        total_cost = get_item_stock_value(self.a) + get_item_stock_value(self.b)
        self.assertEqual(total_cost.quantize(D("0.01")), D("590.00"))  # 600 - 10; tax excluded

    def test_without_vat_a_typed_header_tax_stays_in_cost_as_before(self):
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        header = {"invoice_number": "PV-OLD", "invoice_date": TODAY, "supplier": self.supplier, "receiving_location": self.location, "cashbox": self.cashbox, "paid_now": D("0"), "tax_amount": D("70.00")}
        invoice = create_purchase_draft(header, [{"item": self.a, "quantity": D("10"), "unit_purchase_price": D("50.00")}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        self.a.refresh_from_db()
        self.assertEqual(self.a.average_cost, D("57.0000"))  # (500 + 70) / 10: not registered, tax is cost
        self.assertFalse(PurchaseLineTax.objects.exists())

    def test_a_purchase_return_takes_back_its_tax(self):
        invoice = self.purchase(paid="0.00")
        line = invoice.lines.get(item=self.a)
        purchase_return = create_purchase_return("PVR-1", TODAY, invoice.pk, [{"source_line": line, "quantity": D("2")}], "damaged", self.owner)
        self.assertEqual(purchase_return.total_amount, D("114.00"))  # 2 x 50 + 14 tax
        self.assertEqual(PurchaseReturnLineTax.objects.get().tax_amount, D("14.00"))
        self.assertEqual(get_item_stock_value(self.a), D("400.0000"))  # 8 left at 50
        incoming = input_vat(TODAY, TODAY)
        self.assertEqual(incoming["net_tax"], D("56.00"))
        row = {r["rate"]: r for r in incoming["rows"]}[D("14.00")]
        self.assertEqual((row["taxable"], row["tax"], row["returned_tax"]), (D("400.00"), D("70.00"), D("14.00")))

    def test_returning_everything_takes_back_exactly_the_tax(self):
        invoice = self.purchase(paid="0.00")
        line = invoice.lines.get(item=self.a)
        create_purchase_return("PVR-A", TODAY, invoice.pk, [{"source_line": line, "quantity": D("3")}], "x", self.owner)
        create_purchase_return("PVR-B", TODAY, invoice.pk, [{"source_line": line, "quantity": D("7")}], "x", self.owner)
        self.assertEqual(sum(r.tax_amount for r in PurchaseReturnLineTax.objects.all()), D("70.00"))
        self.assertEqual(input_vat(TODAY, TODAY)["net_tax"], D("0.00"))

    def test_the_vat_return_nets_output_against_input(self):
        self.purchase()
        customer = make_customer(customer_code="PVC", name="Buyer")
        sale = create_sales_draft_with_tax(
            {"invoice_number": "PV-S1", "invoice_date": TODAY, "customer": customer, "selling_location": self.location, "cashbox": self.cashbox, "paid_now": D("0")},
            [{"item": self.a, "quantity": D("5"), "unit_sale_price": D("100.00")}], self.owner,
        )
        post_sales_invoice(sale.pk, self.owner)
        line = sale.lines.get()
        self.assertEqual(line.line_cost_amount, D("250.00"))  # 5 x 50, the recoverable tax is not cost
        self.assertEqual(line.line_profit_amount, D("250.00"))
        result = vat_return(TODAY, TODAY)
        self.assertEqual((result["output"]["net_tax"], result["input"]["net_tax"], result["payable"]), (D("70.00"), D("70.00"), D("0.00")))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("taxes:report"))
        self.assertContains(page, "ضريبة المشتريات (المدخلات)")
        self.assertEqual(page.context["payable"], D("0.00"))

    def test_purchase_form_computes_tax(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("purchases:create"))
        self.assertNotContains(page, 'name="tax_amount"')
        self.assertContains(page, 'id="hs-tax-rates"')
