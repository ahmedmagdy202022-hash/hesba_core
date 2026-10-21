"""T2 (MARKET-PARITY): purchase orders received in parts on ordinary purchase invoices (HG-054)."""

from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile
from inventory.services import get_item_stock_quantity
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice
from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice
from reports.tests_dashboard import prepared_client

from . import purchasing as po
from .models import OrderReceiptLine, PurchaseStatus as Status

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class PurchaseOrderSetup(TestCase):
    def setUp(self):
        prepared_client("commercial", "wholesale")
        self.owner = person(RoleCode.OWNER, "po_owner")
        self.location = make_location(location_code="STORE", is_default=True)
        self.sugar = make_item(item_code="SUG-1", item_name="سكر 1 كيلو", default_sale_price=D("40.00"), default_purchase_price=D("32.00"), is_stock_tracked=True)
        self.tea = make_item(item_code="TEA-1", item_name="شاي 250 جم", default_sale_price=D("60.00"), default_purchase_price=D("45.00"), is_stock_tracked=True)
        self.supplier = make_supplier(supplier_code="SUP-PO", name="شركة السكر المصرية", phone="01009998887")

    def order(self, confirm=True):
        order = po.create_order({"supplier": self.supplier}, self.owner)
        po.add_line(order, self.sugar, self.owner, quantity="100")
        po.add_line(order, self.tea, self.owner, quantity="40", unit_price="44", discount_percent="5")
        return po.move(order, Status.CONFIRMED, self.owner) if confirm else order

    def receive(self, order, quantities, post=True):
        from taxes.services import create_purchase_draft_with_tax

        lines = []
        for line in order.lines.all():
            if line.item_id in quantities:
                q = D(quantities[line.item_id])
                lines.append({"item": line.item, "quantity": q, "unit_purchase_price": line.unit_price, "line_discount_amount": line.discount_for(q)})
        invoice = create_purchase_draft_with_tax({"invoice_number": f"PI-T2-{PurchaseInvoice.objects.count() + 1}", "invoice_date": TODAY,
                                                  "supplier": order.supplier, "receiving_location": self.location, "paid_now": D("0")}, lines, self.owner)
        po.link_receipt(order, invoice, self.owner)
        if post:
            post_purchase_invoice(invoice.pk, self.owner)
            invoice.refresh_from_db()
        return invoice

    def left(self, order):
        return {row["line"].item_id: row["left"] for row in po.quantities(order)}


class PurchaseOrderTests(PurchaseOrderSetup):
    def test_an_order_has_its_number_and_the_items_cost_and_moves_nothing(self):
        order = self.order()
        self.assertTrue(order.number.startswith("PO-"))
        self.assertEqual(order.lines.get(item=self.sugar).unit_price, D("32.00"))  # the item's purchase price
        self.assertEqual((get_item_stock_quantity(self.sugar), PurchaseInvoice.objects.count()), (D("0"), 0))

    def test_receiving_in_parts_brings_the_stock_in_and_counts_down(self):
        order = self.order()
        self.receive(order, {self.sugar.pk: "60"})
        self.assertEqual(get_item_stock_quantity(self.sugar), D("60"))
        self.assertEqual(self.left(order), {self.sugar.pk: D("40"), self.tea.pk: D("40")})
        self.receive(order, {self.sugar.pk: "40", self.tea.pk: "40"})
        self.assertEqual(po.state(order), "done")
        tea = OrderReceiptLine.objects.filter(order_line__item=self.tea).get().purchase_line
        self.assertEqual((tea.line_discount_amount, tea.line_total_amount), (D("88.00"), D("1672.00")))  # 40 × 44 less 5%

    def test_a_cancelled_receipt_gives_the_quantity_back(self):
        order = self.order()
        invoice = self.receive(order, {self.sugar.pk: "30"})
        cancel_posted_purchase_invoice(invoice.pk, self.owner, reason="خطأ")
        self.assertEqual(self.left(order)[self.sugar.pk], D("100"))

    def test_another_suppliers_invoice_is_refused(self):
        from taxes.services import create_purchase_draft_with_tax

        order = self.order()
        other = make_supplier(supplier_code="SUP-X", name="مورد تاني")
        invoice = create_purchase_draft_with_tax({"invoice_number": "PI-X", "invoice_date": TODAY, "supplier": other, "receiving_location": self.location,
                                                  "paid_now": D("0")}, [{"item": self.sugar, "quantity": D("5"), "unit_purchase_price": D("32")}], self.owner)
        with self.assertRaises(ValidationError):
            po.link_receipt(order, invoice, self.owner)

    def test_received_orders_close_and_confirmed_ones_are_locked(self):
        order = self.order()
        with self.assertRaises(ValidationError):
            po.add_line(order, self.sugar, self.owner, quantity="1")
        self.receive(order, {self.sugar.pk: "10"})
        with self.assertRaises(ValidationError):
            po.move(order, Status.CANCELLED, self.owner)
        po.move(order, Status.CLOSED, self.owner)

    def test_the_purchase_form_opens_with_what_is_left_and_links_on_save(self):
        self.client.force_login(self.owner)
        order = self.order()
        page = self.client.get(reverse("purchases:create") + f"?lang=ar&order={order.pk}")
        self.assertContains(page, "data-from-order-note")
        self.assertEqual([(row["item"], row["quantity"]) for row in page.context["line_formset"].initial], [(self.sugar, D("100")), (self.tea, D("40"))])
        post = {"lang": "ar", "order": str(order.pk), "invoice_number": "PI-FORM-1", "invoice_date": TODAY.isoformat(), "supplier": str(self.supplier.pk),
                "receiving_location": str(self.location.pk), "cashbox": "", "discount_amount": "0", "tax_amount": "0", "paid_now": "0", "notes": "",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20",
                "lines-0-item": str(self.tea.pk), "lines-0-quantity": "15", "lines-0-unit_purchase_price": "44", "lines-0-line_discount_amount": "0"}
        self.assertEqual(self.client.post(reverse("purchases:create"), post).status_code, 302)
        rows = {row["line"].item_id: row for row in po.quantities(order)}
        self.assertEqual((rows[self.tea.pk]["drafted"], rows[self.tea.pk]["left"]), (D("15"), D("25")))

    def test_the_screens_and_print(self):
        self.client.force_login(self.owner)
        order = self.order()
        self.assertContains(self.client.get(reverse("orders:po_list") + "?lang=ar"), order.number)
        detail = self.client.get(reverse("orders:po_detail", args=[order.pk]) + "?lang=ar")
        self.assertContains(detail, "data-receive-order")
        self.assertContains(detail, "data-share-whatsapp")
        self.assertContains(self.client.get(reverse("orders:po_print", args=[order.pk]) + "?lang=ar"), "أمر شراء")
        self.assertContains(self.client.get(reverse("purchases:list") + "?lang=ar"), "data-purchase-orders-link")
        board = self.client.get(reverse("orders:po_list") + "?lang=ar").content.decode()
        self.assertRegex(board, r'is-current" href="/purchases/')  # the menu marks purchases, not quotations

    def test_only_buyers_make_purchase_orders(self):
        cashier = person(RoleCode.CASHIER, "po_cashier")
        with self.assertRaises(PermissionDenied):
            po.create_order({"supplier": self.supplier}, cashier)
