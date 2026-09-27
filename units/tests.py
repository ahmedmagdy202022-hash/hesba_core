"""UNITS-001: lines entered in bigger units are stored in base units, totals exact."""

import json
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from closing.models import Period
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice
from purchases.services import post_purchase_invoice
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from .models import ItemUnit
from .services import convert_line, split_quantity, units_catalog


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class UnitsSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client()  # retail: units off until switched on
        set_capability_enabled(self.profile, "units", True)
        Period.objects.create(period_code="U", name="u", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "units_owner")
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.juice = make_item(item_code="JUICE", item_name="Juice", barcode="6220000000011", default_sale_price=D("9.00"), default_purchase_price=D("7.00"))
        self.carton = ItemUnit.objects.create(item=self.juice, name_ar="كرتونة", name_en="Carton", factor=D("12"), barcode="6220000000028", sale_price=D("100.00"))
        self.box = ItemUnit.objects.create(item=self.juice, name_ar="علبة", name_en="Box", factor=D("24"), purchase_price=D("250.00"))
        stock_in(self.juice, self.location, 100, "7.00", movement_date=TODAY - timedelta(days=3))
        recalculate_item_average_cost(self.juice)


class ConversionTests(UnitsSetup):
    def test_carton_line_becomes_pieces_with_an_exact_total(self):
        line = convert_line({"item": self.juice, "unit": self.carton, "quantity": D("5"), "unit_sale_price": D("100.00"), "line_discount_amount": D("0")}, "unit_sale_price")
        # 500 / 60 = 8.333... -> 8.34 a piece; 60 x 8.34 = 500.40, so 0.40 comes back as discount.
        self.assertEqual((line["quantity"], line["unit_sale_price"], line["line_discount_amount"]), (D("60.000"), D("8.34"), D("0.40")))
        self.assertEqual(line["quantity"] * line["unit_sale_price"] - line["line_discount_amount"], D("500.00"))
        self.assertEqual(line["description"], "Juice — 5 كرتونة × 100.00")
        self.assertNotIn("unit", line)

    def test_an_entered_discount_is_kept_on_top(self):
        line = convert_line({"item": self.juice, "unit": self.carton, "quantity": D("2"), "unit_sale_price": D("100.00"), "line_discount_amount": D("15.00"), "description": "Promo"}, "unit_sale_price", "en")
        self.assertEqual(line["quantity"] * line["unit_sale_price"] - line["line_discount_amount"], D("185.00"))
        self.assertEqual(line["description"], "Promo — 2 Carton × 100.00")

    def test_no_unit_means_no_change_and_a_foreign_unit_is_refused(self):
        plain = {"item": self.juice, "quantity": D("3"), "unit_sale_price": D("9.00")}
        self.assertIs(convert_line(plain, "unit_sale_price"), plain)
        other = make_item(item_code="OTHER")
        with self.assertRaises(ValidationError):
            convert_line({"item": other, "unit": self.carton, "quantity": D("1"), "unit_sale_price": D("1")}, "unit_sale_price")

    def test_default_prices_catalog_and_breakdown(self):
        self.assertEqual((self.carton.default_sale_price(), self.box.default_sale_price()), (D("100.00"), D("216.00")))
        self.assertEqual((self.carton.default_purchase_price(), self.box.default_purchase_price()), (D("84.00"), D("250.00")))
        sale = units_catalog()[str(self.juice.pk)]
        self.assertEqual([(row["name_ar"], row["factor"], row["price"]) for row in sale], [("كرتونة", "12.000", "100.00"), ("علبة", "24.000", "216.00")])
        self.assertEqual(units_catalog(purchase=True)[str(self.juice.pk)][0]["price"], "84.00")
        self.assertEqual(split_quantity(self.juice, D("62"), "en"), f"2 Box + 1 Carton + 2 {self.juice.unit}")
        set_capability_enabled(self.profile, "units", False)
        self.assertIsNone(units_catalog())


class InvoiceTests(UnitsSetup):
    def payload(self, **lines):
        data = {
            "lang": "ar", "invoice_number": "SI-U-1", "invoice_date": TODAY.isoformat(), "customer": make_customer().pk,
            "selling_location": self.location.pk, "cashbox": self.cashbox.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0", "notes": "",
            "lines-TOTAL_FORMS": "5", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20",
        }
        data.update(lines)
        return data

    def test_sales_form_saves_base_units_and_posting_moves_pieces(self):
        self.client.force_login(self.owner)
        form = self.client.get(reverse("sales:create"))
        self.assertContains(form, 'id="hs-units"')
        self.assertIn("كرتونة", json.dumps(form.context["units"], ensure_ascii=False))
        response = self.client.post(reverse("sales:create"), self.payload(**{
            "lines-0-item": self.juice.pk, "lines-0-unit": self.carton.pk, "lines-0-quantity": "5", "lines-0-unit_sale_price": "100.00", "lines-0-line_discount_amount": "0",
            "lines-1-item": self.juice.pk, "lines-1-quantity": "3", "lines-1-unit_sale_price": "9.00", "lines-1-line_discount_amount": "0",
        }))
        self.assertEqual(response.status_code, 302, response.context and (response.context["form"].errors, response.context["line_formset"].errors, [str(m) for m in response.context["messages"]]))
        invoice = SalesInvoice.objects.get()
        self.assertEqual(invoice.total_amount, D("527.00"))  # 500 for the cartons + 27 for 3 pieces
        carton_line = invoice.lines.order_by("pk").first()
        self.assertEqual((carton_line.quantity, carton_line.unit_sale_price, carton_line.line_total_amount), (D("60.000"), D("8.34"), D("500.00")))
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(sum(m.quantity for m in StockMovement.objects.filter(sales_invoice=invoice)), D("63"))
        self.assertContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), "5 كرتونة × 100.00")

    def test_a_unit_from_another_item_is_rejected(self):
        self.client.force_login(self.owner)
        other = make_item(item_code="OTHER")
        response = self.client.post(reverse("sales:create"), self.payload(**{
            "lines-0-item": other.pk, "lines-0-unit": self.carton.pk, "lines-0-quantity": "1", "lines-0-unit_sale_price": "100.00", "lines-0-line_discount_amount": "0",
        }))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesInvoice.objects.exists())

    def test_purchase_by_the_box_costs_each_piece(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("purchases:create")), 'id="hs-units"')
        response = self.client.post(reverse("purchases:create"), {
            "lang": "ar", "invoice_number": "PI-U-1", "invoice_date": TODAY.isoformat(), "supplier": make_supplier().pk,
            "receiving_location": self.location.pk, "cashbox": self.cashbox.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0", "notes": "",
            "lines-TOTAL_FORMS": "5", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20",
            "lines-0-item": self.juice.pk, "lines-0-unit": self.box.pk, "lines-0-quantity": "2", "lines-0-unit_purchase_price": "250.00", "lines-0-line_discount_amount": "0",
        })
        self.assertEqual(response.status_code, 302)
        invoice = PurchaseInvoice.objects.get()
        self.assertEqual(invoice.total_amount, D("500.00"))
        line = invoice.lines.get()
        # 48 pieces; 500 / 48 = 10.4166 -> 10.42 a piece, 0.16 back as discount.
        self.assertEqual((line.quantity, line.unit_purchase_price, line.line_discount_amount, line.line_total_amount), (D("48.000"), D("10.42"), D("0.16"), D("500.00")))
        post_purchase_invoice(invoice.pk, self.owner)
        movement = StockMovement.objects.get(purchase_invoice=invoice)
        self.assertEqual(movement.quantity, D("48"))
        self.assertAlmostEqual(movement.quantity * movement.unit_cost, D("500"), delta=D("0.01"))

    def test_without_the_capability_there_is_no_unit_field(self):
        set_capability_enabled(self.profile, "units", False)
        self.client.force_login(self.owner)
        form = self.client.get(reverse("sales:create"))
        self.assertNotContains(form, 'name="lines-0-unit"')
        self.assertIsNone(form.context["units"])


class PosTests(UnitsSetup):
    def test_scanned_carton_is_sold_as_pieces(self):
        cashier = person(RoleCode.CASHIER, "units_cashier")
        self.client.force_login(cashier)
        page = self.client.get(reverse("sales:pos"))
        self.assertContains(page, 'id="hs-units"')
        response = self.client.post(reverse("sales:pos"), {
            "line_count": "2", "customer": "", "location": str(self.location.pk), "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "600", "print": "0",
            "item_0": str(self.juice.pk), "qty_0": "5", "price_0": "100.00", "unit_0": str(self.carton.pk),
            "item_1": str(self.juice.pk), "qty_1": "2", "price_1": "9.00",
        })
        self.assertEqual(response.status_code, 302, getattr(response, "content", b"")[:400])
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.total_amount), ("posted", D("518.00")))
        self.assertEqual(sum(m.quantity for m in StockMovement.objects.filter(sales_invoice=invoice)), D("62"))

    def test_a_unit_of_another_item_is_refused_at_the_till(self):
        self.client.force_login(person(RoleCode.CASHIER, "units_cashier2"))
        other = make_item(item_code="OTHER", default_sale_price=D("5"))
        self.client.post(reverse("sales:pos"), {
            "line_count": "1", "customer": "", "location": str(self.location.pk), "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "100", "print": "0",
            "item_0": str(other.pk), "qty_0": "1", "price_0": "100.00", "unit_0": str(self.carton.pk),
        })
        self.assertFalse(SalesInvoice.objects.exists())


class ScreenTests(UnitsSetup):
    def test_owner_adds_edits_and_retires_units_with_audit(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("master_data:items")), reverse("units:index"))
        index = self.client.get(reverse("units:index"))
        self.assertContains(index, "JUICE")
        url = reverse("units:item", args=[self.juice.pk])
        self.assertContains(self.client.get(url), "كرتونة")
        self.client.post(url, {
            f"name_ar_{self.carton.pk}": "كرتونة", f"factor_{self.carton.pk}": "12", f"sale_price_{self.carton.pk}": "99.50", f"active_{self.carton.pk}": "1",
            f"name_ar_{self.box.pk}": "",  # emptied: retired, not deleted
            "name_ar_new0": "دستة", "name_en_new0": "Dozen", "factor_new0": "12", "barcode_new0": "D-1",
        })
        self.carton.refresh_from_db()
        self.box.refresh_from_db()
        self.assertEqual(self.carton.sale_price, D("99.50"))
        self.assertFalse(self.box.active)
        self.assertTrue(ItemUnit.objects.filter(item=self.juice, name_ar="دستة", factor=D("12.000")).exists())
        self.assertTrue(AuditLog.objects.filter(module="units", action="set_item_units", object_id=str(self.juice.pk)).exists())

    def test_bad_factor_saves_nothing(self):
        self.client.force_login(self.owner)
        url = reverse("units:item", args=[self.juice.pk])
        page = self.client.post(url, {"name_ar_new0": "نص", "factor_new0": "1"}, follow=True)
        self.assertContains(page, "المعامل لازم أكبر من 1")
        self.assertFalse(ItemUnit.objects.filter(name_ar="نص").exists())
        self.client.post(url, {f"name_ar_{self.carton.pk}": "كرتونة", f"factor_{self.carton.pk}": "12", "name_ar_new0": "كرتونة", "factor_new0": "6"})
        self.assertEqual(ItemUnit.objects.filter(item=self.juice, name_ar="كرتونة").count(), 1)

    def test_permissions_and_capability_gate(self):
        self.client.force_login(person(RoleCode.CASHIER, "units_viewer"))
        url = reverse("units:item", args=[self.juice.pk])
        self.assertEqual(self.client.post(url, {"name_ar_new0": "دستة", "factor_new0": "12"}).status_code, 403)
        self.assertFalse(ItemUnit.objects.filter(name_ar="دستة").exists())
        set_capability_enabled(ClientProfile.get_active(), "units", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("units:index")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("master_data:items")), reverse("units:index"))
