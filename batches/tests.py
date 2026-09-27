"""BATCH-001: batches follow the real stock, earliest expiry out first."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from closing.models import Period
from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_stock_movement, make_supplier, make_user, make_user_profile, stock_in
from inventory.models import StockMovementType
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice
from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice
from reports.dashboard_data import build_alerts
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import capability_enabled, set_capability_enabled
from settings_core.models import ClientProfile

from .models import Batch
from .services import batch_positions, expiry_alerts, register_batch


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class BatchSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(sub_activity="pharmacy")  # batches & expiry suggested -> on
        Period.objects.create(period_code="B", name="b", start_date=TODAY - timedelta(days=60), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "batch_owner")
        self.location = make_location(location_code="PH", is_default=True, is_receiving_location=True)
        self.cashbox = make_cashbox(cashbox_code="PH-CASH", is_default=True)
        self.drug = make_item(item_code="PARA", item_name="Paracetamol", default_sale_price=D("20.00"), default_purchase_price=D("12.00"))

    def register(self, batch_no, expiry_days, quantity, received_days=-10):
        return register_batch(item=self.drug, location=self.location, batch_no=batch_no, expiry_date=TODAY + timedelta(days=expiry_days) if expiry_days is not None else None,
                              quantity=D(quantity), received_on=TODAY + timedelta(days=received_days), user=self.owner)

    def sell(self, quantity):
        make_stock_movement(self.drug, self.location, StockMovementType.SALE_OUT, quantity, "12.00", movement_date=TODAY)

    def remaining(self):
        rows, uncovered = batch_positions()
        return {row["batch"].batch_no: row["remaining"] for row in rows}, uncovered


class PositionTests(BatchSetup):
    def test_the_batch_expiring_first_is_sold_first(self):
        self.assertTrue(capability_enabled("batches_expiry"))
        stock_in(self.drug, self.location, 30, "12.00", movement_date=TODAY - timedelta(days=10))
        self.register("EARLY", 10, "10")
        self.register("LATE", 200, "20")
        self.assertEqual(self.remaining(), ({"EARLY": D("10"), "LATE": D("20")}, {}))
        self.sell(15)  # 10 from EARLY, 5 from LATE
        self.assertEqual(self.remaining(), ({"EARLY": D("0"), "LATE": D("15")}, {}))
        self.sell(15)
        self.assertEqual(self.remaining(), ({"EARLY": D("0"), "LATE": D("0")}, {}))

    def test_stock_no_batch_covers_is_reported_and_undated_batches_go_last(self):
        stock_in(self.drug, self.location, 12, "12.00", movement_date=TODAY - timedelta(days=10))
        self.register("DATED", 30, "4")
        self.register("NODATE", None, "5")
        # 12 on hand: the undated batch counts as expiring last, then the dated one; 3 are uncovered.
        self.assertEqual(self.remaining(), ({"DATED": D("4"), "NODATE": D("5")}, {self.drug.pk: D("3")}))
        self.sell(5)
        self.assertEqual(self.remaining(), ({"DATED": D("2"), "NODATE": D("5")}, {}))

    def test_alerts_split_expired_and_soon_and_stop_when_switched_off(self):
        stock_in(self.drug, self.location, 30, "12.00", movement_date=TODAY - timedelta(days=100))
        self.register("OLD", -3, "5", received_days=-100)
        self.register("SOON", 20, "5")
        self.register("FINE", 400, "20")
        alerts = expiry_alerts(TODAY)
        self.assertEqual(([r["batch"].batch_no for r in alerts["expired"]], [r["batch"].batch_no for r in alerts["soon"]]), (["OLD"], ["SOON"]))
        dashboard = {alert["key"]: alert for alert in build_alerts({"reports.view_inventory_report"}, TODAY)}
        self.assertIn("1 تشغيلة منتهية", dashboard["batches_expired"]["ar"])
        self.assertEqual(dashboard["batches_expiring"]["severity"], "soon")
        self.sell(30)  # everything sold: nothing left to warn about
        self.assertEqual(expiry_alerts(TODAY), {"expired": [], "soon": []})
        stock_in(self.drug, self.location, 30, "12.00", movement_date=TODAY)
        set_capability_enabled(self.profile, "batches_expiry", False)
        self.assertEqual(expiry_alerts(TODAY), {"expired": [], "soon": []})
        self.assertNotIn("batches_expired", {alert["key"] for alert in build_alerts({"reports.view_inventory_report"}, TODAY)})


class PurchaseTests(BatchSetup):
    def purchase(self, number="PI-B-1"):
        self.client.force_login(self.owner)
        return self.client.post(reverse("purchases:create"), {
            "lang": "ar", "invoice_number": number, "invoice_date": TODAY.isoformat(), "supplier": make_supplier().pk,
            "receiving_location": self.location.pk, "cashbox": self.cashbox.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0", "notes": "",
            "lines-TOTAL_FORMS": "5", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20",
            "lines-0-item": self.drug.pk, "lines-0-quantity": "24", "lines-0-unit_purchase_price": "12.00", "lines-0-line_discount_amount": "0",
            "lines-0-batch_no": "LOT-7", "lines-0-expiry_date": (TODAY + timedelta(days=45)).isoformat(),
            "lines-1-item": self.drug.pk, "lines-1-quantity": "6", "lines-1-unit_purchase_price": "12.00", "lines-1-line_discount_amount": "0",
        })

    def test_purchase_form_records_the_batch_and_it_counts_once_posted(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("purchases:create")), 'name="lines-0-expiry_date"')
        self.assertEqual(self.purchase().status_code, 302)
        invoice = PurchaseInvoice.objects.get()
        batch = Batch.objects.get()
        self.assertEqual((batch.batch_no, batch.quantity, batch.expiry_date, batch.purchase_line.invoice), ("LOT-7", D("24.000"), TODAY + timedelta(days=45), invoice))
        self.assertEqual(batch_positions(), ([], {}))  # a draft is not stock yet
        post_purchase_invoice(invoice.pk, self.owner)
        rows, uncovered = batch_positions()
        self.assertEqual(([(r["batch"].batch_no, r["remaining"]) for r in rows], uncovered), ([("LOT-7", D("24"))], {self.drug.pk: D("6")}))
        detail = self.client.get(reverse("purchases:detail", args=[invoice.pk]))
        self.assertContains(detail, "data-line-batch")
        self.assertContains(detail, "LOT-7")
        self.assertIn("LOT-7", [r["batch"].batch_no for r in expiry_alerts(TODAY)["soon"]])
        cancel_posted_purchase_invoice(invoice.pk, self.owner, reason="wrong supplier")
        self.assertEqual(batch_positions(), ([], {}))

    def test_without_the_capability_the_form_has_no_batch_fields(self):
        set_capability_enabled(self.profile, "batches_expiry", False)
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("purchases:create")), 'name="lines-0-batch_no"')


class ScreenTests(BatchSetup):
    def test_screen_lists_states_and_registers_old_stock(self):
        stock_in(self.drug, self.location, 10, "12.00", movement_date=TODAY - timedelta(days=100))
        self.register("OLD", -1, "4", received_days=-100)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("inventory:stock")), reverse("batches:index"))
        page = self.client.get(reverse("batches:index"))
        self.assertEqual(page.context["counts"], {"expired": 1, "soon": 0})
        self.assertContains(page, 'data-batch-row="expired"')
        self.assertContains(page, "data-uncovered")  # 6 on hand without a batch
        self.client.post(reverse("batches:index"), {"item_code": "PARA", "batch_no": "SHELF", "expiry_date": (TODAY + timedelta(days=300)).isoformat(), "quantity": "6", "location": self.location.pk})
        self.assertTrue(Batch.objects.filter(batch_no="SHELF", quantity=D("6.000")).exists())
        self.assertNotContains(self.client.get(reverse("batches:index")), "data-uncovered")
        self.assertEqual(AuditLog.objects.filter(module="batches", action="register_batch").count(), 2)
        english = self.client.get(reverse("batches:index"), {"lang": "en", "state": "expired"})
        self.assertContains(english, "Expired")
        self.assertNotContains(english, "SHELF")

    def test_validation_and_retiring_a_registered_batch(self):
        self.client.force_login(self.owner)
        for data, message in (({"item_code": "NOPE", "quantity": "1", "batch_no": "X"}, "الصنف مش موجود"), ({"item_code": "PARA", "quantity": "0", "batch_no": "X"}, "الكمية لازم"),
                              ({"item_code": "PARA", "quantity": "2"}, "اكتب رقم التشغيلة أو تاريخ الصلاحية"), ({"item_code": "PARA", "quantity": "2", "expiry_date": "31-12-2026"}, "التاريخ مش صحيح")):
            with self.subTest(message=message):
                self.assertContains(self.client.post(reverse("batches:index"), data, follow=True), message)
        self.assertFalse(Batch.objects.exists())
        batch = self.register("OOPS", 30, "3")
        self.client.post(reverse("batches:index"), {"retire": batch.pk})
        batch.refresh_from_db()
        self.assertFalse(batch.active)
        self.assertTrue(AuditLog.objects.filter(action="retire_batch", object_id=str(batch.pk)).exists())

    def test_permissions_and_capability_gate(self):
        self.client.force_login(person(RoleCode.CASHIER, "batch_cashier"))
        self.assertEqual(self.client.post(reverse("batches:index"), {"item_code": "PARA", "quantity": "1", "batch_no": "X"}).status_code, 403)
        self.assertFalse(Batch.objects.exists())
        set_capability_enabled(ClientProfile.get_active(), "batches_expiry", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("batches:index")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("inventory:stock")), reverse("batches:index"))
