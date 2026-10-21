"""T1 (MARKET-PARITY): quotations become sales orders invoiced in parts on the ordinary invoice form (HG-054)."""

from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import get_item_stock_quantity
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice
from settings_core import capabilities as caps

from . import services
from .models import OrderInvoiceLine, Status

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class OrdersSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client("commercial", "wholesale")
        caps.set_capability_enabled(self.profile, "quotations", True)
        self.owner = person(RoleCode.OWNER, "so_owner")
        self.location = make_location(location_code="STORE", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="CASH-SO", is_default=True)
        self.oil = make_item(item_code="OIL-1", item_name="زيت 1 لتر", default_sale_price=D("80.00"), is_stock_tracked=True)
        stock_in(self.oil, self.location, "100", "60.00")
        self.rice = make_item(item_code="RICE-1", item_name="أرز 5 كيلو", default_sale_price=D("150.00"), is_stock_tracked=True)
        stock_in(self.rice, self.location, "50", "110.00")
        self.customer = make_customer(customer_code="CUS-SO", name="سوبر ماركت النور", phone="01001234567")

    def quote(self, **extra):
        order = services.create_quote({"customer": self.customer, **extra}, self.owner)
        services.add_line(order, self.oil, self.owner, quantity="40", discount_percent="5")
        services.add_line(order, self.rice, self.owner, quantity="10", unit_price="145")
        return order

    def invoice(self, order, quantities, post=True):
        """An ordinary draft for ``quantities`` ({item: qty}) taken off ``order``, as the invoice form saves it."""

        from taxes.services import create_sales_draft_with_tax

        lines = []
        for line in order.lines.all():
            if line.item_id in quantities:
                lines.append({"item": line.item, "quantity": D(quantities[line.item_id]), "unit_sale_price": line.unit_price,
                              "line_discount_amount": line.discount_for(D(quantities[line.item_id]))})
        number = f"SI-T1-{SalesInvoice.objects.count() + 1}"
        invoice = create_sales_draft_with_tax({"invoice_number": number, "invoice_date": TODAY, "customer": order.customer,
                                               "selling_location": self.location, "paid_now": D("0")}, lines, self.owner)
        services.link_invoice(order, invoice, self.owner)
        if post:
            post_sales_invoice(invoice.pk, self.owner)
            invoice.refresh_from_db()
        return invoice


class QuoteToOrderTests(OrdersSetup):
    def test_a_quotation_has_its_own_number_prices_and_totals(self):
        order = self.quote()
        self.assertTrue(order.number.startswith("QT-"))
        self.assertIsNone(order.order_number)
        self.assertEqual(order.valid_until, TODAY + timezone.timedelta(days=services.DEFAULT_VALID_DAYS))
        numbers = services.totals(order)
        # 40 × 80 = 3200 less 5% = 160; 10 × 145 = 1450 (the price typed, not the item's); a wholesaler charges 14% VAT
        self.assertEqual((numbers["gross"], numbers["discount"], numbers["net"]), (D("4650.00"), D("160.00"), D("4490.00")))
        self.assertEqual((numbers["tax"], numbers["total"]), (D("628.60"), D("5118.60")))

    def test_a_quotation_moves_nothing_until_it_is_invoiced(self):
        order = self.quote()
        services.move(order, Status.SENT, self.owner)
        services.move(order, Status.CONFIRMED, self.owner)
        order.refresh_from_db()
        self.assertTrue(order.order_number.startswith("SO-"))
        self.assertEqual((get_item_stock_quantity(self.oil), get_item_stock_quantity(self.rice)), (D("100"), D("50")))
        self.assertFalse(SalesInvoice.objects.exists())

    def test_only_an_accepted_quotation_with_lines_becomes_an_order(self):
        empty = services.create_quote({"customer": self.customer}, self.owner)
        with self.assertRaises(ValidationError):
            services.move(empty, Status.CONFIRMED, self.owner)
        order = self.quote()
        services.move(order, Status.REJECTED, self.owner)
        with self.assertRaises(ValidationError):  # rejected goes back to draft first
            services.move(order, Status.CONFIRMED, self.owner)

    def test_an_order_no_longer_changes(self):
        order = self.quote()
        services.move(order, Status.CONFIRMED, self.owner)
        with self.assertRaises(ValidationError):
            services.add_line(order, self.oil, self.owner, quantity="1")
        with self.assertRaises(ValidationError):
            services.update_header(order, {"notes": "x"}, self.owner)


class InvoicingTests(OrdersSetup):
    def setUp(self):
        super().setUp()
        self.order = services.move(self.quote(), Status.CONFIRMED, self.owner)

    def left(self):
        return {row["line"].item_id: row["left"] for row in services.quantities(self.order)}

    def test_invoicing_in_parts_takes_the_quantities_off_the_order(self):
        first = self.invoice(self.order, {self.oil.pk: "25"})
        self.assertEqual(first.status, "posted")
        self.assertEqual(self.left(), {self.oil.pk: D("15"), self.rice.pk: D("10")})
        self.assertEqual(get_item_stock_quantity(self.oil), D("75"))  # stock left with the posted invoice, like any sale
        line = first.lines.get()
        self.assertEqual((line.unit_sale_price, line.line_discount_amount, line.line_total_amount), (D("80.00"), D("100.00"), D("1900.00")))
        self.invoice(self.order, {self.oil.pk: "15", self.rice.pk: "10"})
        self.assertEqual(self.left(), {self.oil.pk: D("0"), self.rice.pk: D("0")})
        self.assertEqual(services.state(self.order), "done")
        self.assertEqual(sum(i.total_amount for i in services.invoices(self.order)), services.totals(self.order)["total"])

    def test_a_draft_holds_its_quantities_and_a_cancelled_invoice_frees_them(self):
        draft = self.invoice(self.order, {self.oil.pk: "10"}, post=False)
        rows = {row["line"].item_id: row for row in services.quantities(self.order)}
        self.assertEqual((rows[self.oil.pk]["drafted"], rows[self.oil.pk]["left"]), (D("10"), D("30")))
        post_sales_invoice(draft.pk, self.owner)
        cancel_posted_sales_invoice(draft.pk, self.owner, reason="خطأ")
        self.assertEqual(self.left()[self.oil.pk], D("40"))
        self.assertEqual(get_item_stock_quantity(self.oil), D("100"))

    def test_quantities_beyond_the_order_are_sold_but_not_linked(self):
        invoice = self.invoice(self.order, {self.oil.pk: "55"})
        self.assertEqual(OrderInvoiceLine.objects.get(sales_line__invoice=invoice).quantity, D("40"))
        self.assertEqual(invoice.lines.get().quantity, D("55"))
        self.assertEqual(self.left()[self.oil.pk], D("0"))

    def test_an_order_with_invoices_closes_instead_of_cancelling(self):
        self.invoice(self.order, {self.oil.pk: "5"})
        with self.assertRaises(ValidationError):
            services.move(self.order, Status.CANCELLED, self.owner)
        services.move(self.order, Status.CLOSED, self.owner)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.CLOSED)

    def test_the_invoice_form_opens_with_what_is_left_and_links_on_save(self):
        self.client.force_login(self.owner)
        self.invoice(self.order, {self.oil.pk: "30"})
        page = self.client.get(reverse("sales:create") + f"?lang=ar&order={self.order.pk}")
        self.assertContains(page, "data-from-order")
        lines = page.context["line_formset"].initial
        self.assertEqual([(row["item"], row["quantity"]) for row in lines], [(self.oil, D("10")), (self.rice, D("10"))])
        post = {"lang": "ar", "order": str(self.order.pk), "invoice_number": "SI-FORM-1", "invoice_date": TODAY.isoformat(),
                "customer": str(self.customer.pk), "selling_location": str(self.location.pk), "cashbox": "", "discount_amount": "0",
                "tax_amount": "0", "paid_now": "0", "notes": "", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20", "lines-0-item": str(self.rice.pk), "lines-0-quantity": "4",
                "lines-0-unit_sale_price": "145", "lines-0-line_discount_amount": "0"}
        response = self.client.post(reverse("sales:create"), post)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.left(), {self.oil.pk: D("10"), self.rice.pk: D("6")})


class CodexFixesTests(OrdersSetup):
    """Codex on #260: the accepted VAT, discount rounding across parts, the order's own customer, one line per item."""

    def test_an_order_keeps_the_vat_it_was_accepted_at(self):
        from taxes.models import TaxRate

        order = services.move(self.quote(), Status.CONFIRMED, self.owner)
        before = services.totals(order)["total"]
        TaxRate.objects.update(rate=D("10.00"))  # the rate changes after the customer accepted
        self.assertEqual(services.totals(order)["total"], before)

    def test_partial_discounts_add_back_to_the_line_discount(self):
        pen = make_item(item_code="PEN-1", item_name="قلم", default_sale_price=D("0.10"), is_stock_tracked=False)
        order = services.create_quote({"customer": self.customer}, self.owner)
        line = services.add_line(order, pen, self.owner, quantity="3", discount_percent="5")
        parts = [line.discount_for(D("1"), D(str(n))) for n in range(3)]
        self.assertEqual(sum(parts), line.discount)  # 0.02, never 0.03

    def test_another_customers_invoice_does_not_take_the_order(self):
        order = services.move(self.quote(), Status.CONFIRMED, self.owner)
        other = make_customer(customer_code="CUS-OTHER", name="عميل تاني")
        from taxes.services import create_sales_draft_with_tax

        invoice = create_sales_draft_with_tax({"invoice_number": "SI-OTHER", "invoice_date": TODAY, "customer": other,
                                               "selling_location": self.location, "paid_now": D("0")},
                                              [{"item": self.oil, "quantity": D("5"), "unit_sale_price": D("80")}], self.owner)
        with self.assertRaises(ValidationError):
            services.link_invoice(order, invoice, self.owner)

    def test_an_item_goes_on_a_quotation_once(self):
        order = self.quote()
        with self.assertRaises(ValidationError):
            services.add_line(order, self.oil, self.owner, quantity="2")


class ScreensTests(OrdersSetup):
    def test_the_screens_print_and_share(self):
        self.client.force_login(self.owner)
        order = self.quote(terms="الدفع 50% مقدم")
        self.assertContains(self.client.get(reverse("orders:list") + "?lang=ar"), order.number)
        detail = self.client.get(reverse("orders:detail", args=[order.pk]) + "?lang=ar")
        self.assertContains(detail, "data-share-whatsapp")
        self.assertContains(detail, "data-add-line")
        printed = self.client.get(reverse("orders:print", args=[order.pk]) + "?lang=ar")
        self.assertContains(printed, "عرض سعر")
        self.assertContains(printed, "الدفع 50% مقدم")
        from printing.share import make_token

        shared = self.client.get(reverse("share_document", args=[make_token("sales_quote", order.pk)]))
        self.assertContains(shared, order.number)
        services.move(order, Status.CONFIRMED, self.owner)
        invoice = InvoicingTests.invoice(self, order, {self.oil.pk: "2"})
        note = self.client.get(reverse("orders:delivery_note", args=[order.pk, invoice.pk]) + "?lang=ar")
        self.assertContains(note, "إذن تسليم")
        self.assertNotContains(note, "data-print-total")  # a delivery note carries no prices

    def test_the_menu_and_the_screens_follow_the_feature_switch(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot") + "?lang=ar"), 'href="/orders/')
        caps.set_capability_enabled(self.profile, "quotations", False)
        self.assertEqual(self.client.get(reverse("orders:list")).status_code, 403)  # the feature gate closes its pages

    def test_a_cashier_cannot_make_quotations(self):
        cashier = person(RoleCode.STOCK_KEEPER, "so_keeper")
        with self.assertRaises(PermissionDenied):
            services.create_quote({"customer": self.customer}, cashier)

    def test_the_feature_is_offered_where_it_fits(self):
        self.assertTrue(caps.fits("commercial", "quotations", "wholesale"))
        self.assertEqual(caps.preset_state("commercial", "wholesale", "quotations"), caps.SUGGESTED)
        self.assertFalse(caps.fits("restaurants", "quotations", "restaurant"))
        self.assertFalse(caps.fits("commercial", "quotations", "grocery"))
        self.assertEqual(caps.needs("quotations"), ("customers",))
