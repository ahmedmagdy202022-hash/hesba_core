"""SERIAL-001: each unit followed by its serial from purchase to sale, return and warranty."""

from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from closing.models import Period
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice
from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import cancel_posted_sales_invoice, create_sales_return
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from .models import SerialNumber, SerialSetting
from .services import add_months, available_serials, history, parse_serials, record_return, register_serials, state_of


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class HelperTests(SimpleTestCase):
    def test_parsing_and_month_arithmetic(self):
        self.assertEqual(parse_serials("356938 035643809, abc-1;\nABC-2 ،x"), ["356938035643809", "ABC-1", "ABC-2", "X"])
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 11, 15), 14), date(2028, 1, 15))


class SerialSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(sub_activity="electronics")  # serials suggested -> on
        Period.objects.create(period_code="S", name="s", start_date=TODAY - timedelta(days=60), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "serial_owner")
        self.location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.phone = make_item(item_code="PHONE", item_name="Phone X", default_sale_price=D("9000.00"), default_purchase_price=D("7500.00"))
        self.cable = make_item(item_code="CABLE", item_name="Cable", default_sale_price=D("50.00"), default_purchase_price=D("20.00"))
        SerialSetting.objects.create(item=self.phone, tracked=True, warranty_months=12)
        self.client.force_login(self.owner)

    def purchase(self, serials, quantity="2", number="PI-S-1", post=True):
        response = self.client.post(reverse("purchases:create"), {
            "lang": "ar", "invoice_number": number, "invoice_date": TODAY.isoformat(), "supplier": make_supplier().pk,
            "receiving_location": self.location.pk, "cashbox": self.cashbox.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0", "notes": "",
            "lines-TOTAL_FORMS": "5", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20",
            "lines-0-item": self.phone.pk, "lines-0-quantity": quantity, "lines-0-unit_purchase_price": "7500.00", "lines-0-line_discount_amount": "0", "lines-0-serials": serials,
            "lines-1-item": self.cable.pk, "lines-1-quantity": "10", "lines-1-unit_purchase_price": "20.00", "lines-1-line_discount_amount": "0",
        })
        invoice = PurchaseInvoice.objects.filter(invoice_number=number).first()
        if invoice and post:
            post_purchase_invoice(invoice.pk, self.owner)
        return response, invoice

    def sale_payload(self, serials, quantity="1", number="SI-S-1"):
        return {
            "lang": "ar", "invoice_number": number, "invoice_date": TODAY.isoformat(), "customer": make_customer(customer_code="C1", name="Karim").pk,
            "selling_location": self.location.pk, "cashbox": self.cashbox.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0", "notes": "",
            "lines-TOTAL_FORMS": "5", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20",
            "lines-0-item": self.phone.pk, "lines-0-quantity": quantity, "lines-0-unit_sale_price": "9000.00", "lines-0-line_discount_amount": "0", "lines-0-serials": serials,
        }

    def serial(self, text):
        return SerialNumber.objects.get(serial=text)


class PurchaseTests(SerialSetup):
    def test_serials_come_in_with_the_posted_purchase(self):
        response, invoice = self.purchase("imei-111, IMEI-222", post=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(state_of(self.serial("IMEI-111")), "pending")
        self.assertFalse(available_serials().exists())
        post_purchase_invoice(invoice.pk, self.owner)
        self.assertEqual(set(available_serials().values_list("serial", flat=True)), {"IMEI-111", "IMEI-222"})
        self.assertContains(self.client.get(reverse("purchases:detail", args=[invoice.pk])), "data-line-serials")
        cancel_posted_purchase_invoice(invoice.pk, self.owner, reason="wrong")
        self.assertEqual(state_of(self.serial("IMEI-111")), "void")
        # A cancelled purchase frees the numbers for the corrected invoice.
        self.assertEqual(self.purchase("IMEI-111, IMEI-222", number="PI-S-2")[0].status_code, 302)

    def test_count_duplicates_and_untracked_items_are_refused(self):
        for serials, quantity, message in (("A1", "2", "عدد السيريالات (1) لازم يساوي الكمية (2)"), ("A1, a1", "2", "السيريال A1 مكرر"), ("A1", "1.5", "الكمية لازم رقم صحيح")):
            with self.subTest(serials=serials):
                response, _ = self.purchase(serials, quantity=quantity, number=f"PI-BAD-{quantity}-{len(serials)}")
                self.assertContains(response, message)
        self.purchase("A1, A2")
        response, _ = self.purchase("A2, A3", number="PI-S-9")
        self.assertContains(response, "السيريال A2 متسجل قبل كده")
        self.assertEqual(PurchaseInvoice.objects.count(), 1)  # refused invoices left nothing half-saved
        self.assertEqual(SerialNumber.objects.count(), 2)


class SaleTests(SerialSetup):
    def setUp(self):
        super().setUp()
        self.purchase("IMEI-111, IMEI-222")

    def test_form_sale_takes_the_serial_out_and_starts_the_warranty(self):
        response = self.client.post(reverse("sales:create"), self.sale_payload("imei-111"))
        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.get()
        self.assertIn("S/N: IMEI-111", invoice.lines.get().description)
        self.assertEqual(state_of(self.serial("IMEI-111")), "in_stock")  # a draft keeps it on the shelf
        self.client.post(reverse("sales:post", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")
        info = history(self.serial("IMEI-111"))
        self.assertEqual((info["state"], info["last_sale"], info["warranty_until"]), ("sold", invoice, add_months(TODAY, 12)))
        page = self.client.get(reverse("serials:detail", args=[self.serial("IMEI-111").pk]))
        self.assertContains(page, 'data-serial-state="sold"')
        self.assertContains(page, "Karim")
        self.assertContains(page, f"ساري لحد {add_months(TODAY, 12).isoformat()}")
        # Selling it again is refused; cancelling the sale puts it back.
        self.assertContains(self.client.post(reverse("sales:create"), self.sale_payload("IMEI-111", number="SI-S-2")), "السيريال IMEI-111 مش في المخزون")
        cancel_posted_sales_invoice(invoice.pk, self.owner, "customer changed mind")
        self.assertEqual(state_of(self.serial("IMEI-111")), "in_stock")

    def test_tracked_item_needs_serials_and_a_draft_cannot_post_a_serial_sold_meanwhile(self):
        self.assertContains(self.client.post(reverse("sales:create"), self.sale_payload("")), "عدد السيريالات (0) لازم يساوي الكمية (1)")
        self.assertContains(self.client.post(reverse("sales:create"), self.sale_payload("NOPE")), "السيريال NOPE مش في المخزون")
        self.client.post(reverse("sales:create"), self.sale_payload("IMEI-222", number="SI-A"))
        self.client.post(reverse("sales:create"), self.sale_payload("IMEI-222", number="SI-B"))
        first, second = SalesInvoice.objects.order_by("pk")
        self.client.post(reverse("sales:post", args=[first.pk]))
        page = self.client.post(reverse("sales:post", args=[second.pk]), follow=True)
        second.refresh_from_db()
        self.assertEqual(second.status, "draft")
        self.assertContains(page, "السيريال IMEI-222 مش في المخزون")

    def test_the_till_sells_by_scanning_the_serial(self):
        cashier = person(RoleCode.CASHIER, "serial_cashier")
        self.client.force_login(cashier)
        page = self.client.get(reverse("sales:pos"))
        self.assertContains(page, 'id="hs-serials"')
        self.assertIn("IMEI-111", page.context["serial_catalog"]["serials"])
        base = {"customer": "", "location": str(self.location.pk), "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "9000", "print": "0"}
        refused = self.client.post(reverse("sales:pos"), dict(base, line_count="1", item_0=str(self.phone.pk), qty_0="1", price_0="9000.00"), follow=True)
        self.assertContains(refused, "عدد السيريالات (0)")
        self.client.post(reverse("sales:pos"), dict(base, line_count="1", item_0=str(self.phone.pk), qty_0="1", price_0="9000.00", serial_0="IMEI-222"))
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, state_of(self.serial("IMEI-222"))), ("posted", "sold"))
        self.assertNotIn("IMEI-222", self.client.get(reverse("sales:pos")).context["serial_catalog"]["serials"])

    def test_a_returned_serial_comes_back_into_stock(self):
        self.client.post(reverse("sales:create"), self.sale_payload("IMEI-111"))
        invoice = SalesInvoice.objects.get()
        self.client.post(reverse("sales:post", args=[invoice.pk]))
        serial = self.serial("IMEI-111")
        detail = reverse("serials:detail", args=[serial.pk])
        self.assertContains(self.client.get(detail), "اعمل مرتجع البيع الأول")
        sales_return = create_sales_return(return_number="SR-S-1", return_date=TODAY, source_invoice_id=invoice.pk,
                                           lines=[{"source_line": invoice.lines.get(), "quantity": D("1")}], reason="faulty", user=self.owner)
        self.client.post(detail, {"action": "return", "return_line": sales_return.lines.get().pk})
        self.assertEqual(state_of(serial), "in_stock")
        with self.assertRaises(ValidationError):
            record_return(serial, sales_return.lines.get(), self.owner)
        self.assertTrue(AuditLog.objects.filter(module="serials", action="return_serial").exists())
        # Back in stock, so it can be sold again.
        self.assertEqual(self.client.post(reverse("sales:create"), self.sale_payload("IMEI-111", number="SI-S-3")).status_code, 302)


class ScreenTests(SerialSetup):
    def test_register_lookup_retire_and_item_settings(self):
        self.assertContains(self.client.get(reverse("inventory:stock")), reverse("serials:index"))
        self.client.post(reverse("serials:index"), {"item_code": "PHONE", "serials": "OLD-1\nOLD-2"})
        self.assertEqual(set(available_serials().values_list("serial", flat=True)), {"OLD-1", "OLD-2"})
        self.assertContains(self.client.post(reverse("serials:index"), {"item_code": "PHONE", "serials": "OLD-1"}, follow=True), "السيريال OLD-1 متسجل قبل كده")
        self.assertContains(self.client.post(reverse("serials:index"), {"item_code": "CABLE", "serials": "C-1"}, follow=True), "مش متتبع بالسيريال")
        found = self.client.get(reverse("serials:index"), {"q": "old-2"})
        self.assertRedirects(found, f"{reverse('serials:detail', args=[self.serial('OLD-2').pk])}?lang=ar", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("serials:index"), {"q": "zzz"}), "data-serial-not-found")
        self.client.post(reverse("serials:detail", args=[self.serial("OLD-2").pk]), {"action": "retire", "reason": "returned to supplier"})
        self.assertEqual(state_of(self.serial("OLD-2")), "retired")
        self.client.post(reverse("serials:items"), {f"shown_{self.cable.pk}": "1", f"tracked_{self.cable.pk}": "1", f"months_{self.cable.pk}": "3",
                                                    f"shown_{self.phone.pk}": "1", f"tracked_{self.phone.pk}": "1", f"months_{self.phone.pk}": "24"})
        self.assertEqual(list(SerialSetting.objects.order_by("item__item_code").values_list("item__item_code", "tracked", "warranty_months")), [("CABLE", True, 3), ("PHONE", True, 24)])
        self.assertEqual(set(AuditLog.objects.filter(module="serials").values_list("action", flat=True)), {"register_serials", "retire_serial", "set_serial_items"})

    def test_permissions_and_capability_gate(self):
        self.client.force_login(person(RoleCode.CASHIER, "serial_viewer"))
        self.assertEqual(self.client.post(reverse("serials:index"), {"item_code": "PHONE", "serials": "X-1"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("serials:items"), {f"shown_{self.cable.pk}": "1", f"tracked_{self.cable.pk}": "1"}).status_code, 403)
        self.assertFalse(SerialNumber.objects.exists())
        set_capability_enabled(ClientProfile.get_active(), "serials", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("serials:index")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("purchases:create")), 'name="lines-0-serials"')
        # With the capability off, a tracked item sells without serials again.
        self.assertIsNone(self.client.get(reverse("sales:pos")).context["serial_catalog"])
