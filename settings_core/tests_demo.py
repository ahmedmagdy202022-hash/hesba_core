"""DEMO-001: the showcase install fills itself once, and only in demo mode."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from master_data.models import Item
from restaurant.models import DiningTable
from sales.models import SalesInvoice
from settings_core.models import ClientProfile


class DemoTests(TestCase):
    def test_refuses_outside_demo_mode(self):
        with self.assertRaises(CommandError):
            call_command("prepare_demo", stdout=StringIO())
        self.assertFalse(ClientProfile.objects.exists())

    @override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
    def test_fills_an_empty_database_once_and_the_logins_work(self):
        call_command("prepare_demo", stdout=StringIO())
        self.assertTrue(SalesInvoice.objects.filter(status="posted").exists())
        # R2-5: the ready-made demo is a household-goods shop; tables are a restaurant's.
        self.assertEqual(Item.objects.get(item_code="DEMO-ITEM-01").item_name, "طقم كوبايات زجاج (6)")
        self.assertFalse(DiningTable.objects.exists())
        # DEMO-FEEDBACK: a month of history, at shop hours rather than all "now".
        past = SalesInvoice.objects.filter(invoice_date__lt=timezone.localdate())
        self.assertGreaterEqual(past.count(), 20)
        self.assertGreater(len({timezone.localtime(i.created_at).hour for i in past}), 5)
        invoices = SalesInvoice.objects.count()
        out = StringIO()
        call_command("prepare_demo", stdout=out)
        self.assertIn("already present", out.getvalue())
        self.assertFalse(DiningTable.objects.exists())
        self.assertEqual(SalesInvoice.objects.count(), invoices)  # today was already topped up
        self.assertTrue(self.client.login(username="cashier", password="Demo-pass-1"))
        self.assertContains(self.client.get(reverse("login")), "data-demo-login")

    @override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
    def test_the_showcase_shop_shows_the_market_parity_features(self):
        """DEMO-NEW (Ahmed, 26 Oct): offers, points, couriers, store orders, the bank, cheques and payroll come with their
        own sample, and the planted alerts stay as they were."""

        from cashboxes.models import Cashbox
        from cheques.models import Cheque
        from loyalty.models import PointsEntry
        from onlinestore.models import StoreOrder
        from payroll.models import PayrollPayment, PayrollRun
        from promotions.models import InvoicePromotion, Promotion
        from reports import selectors
        from reports.dashboard_data import SharedReads
        from settings_core.capabilities import enabled_capabilities
        from shipping.models import Shipment

        call_command("prepare_demo", stdout=StringIO())
        self.assertTrue({"promotions", "loyalty", "shipping", "online_store"} <= set(enabled_capabilities()))
        self.assertEqual((Promotion.objects.count(), InvoicePromotion.objects.count()), (3, 3))
        self.assertTrue(InvoicePromotion.objects.filter(coupon_code="HESBA").exists())
        self.assertEqual(set(PointsEntry.objects.values_list("kind", flat=True)), {"earn", "spend"})
        self.assertEqual(sorted(Shipment.objects.values_list("status", flat=True)), ["delivered", "settled", "with_courier"])
        self.assertEqual(StoreOrder.objects.filter(invoice__status="draft").count(), 2)
        self.assertTrue(Cashbox.objects.filter(kind="bank", cashbox_code="DEMO-BANK-01").exists())
        self.assertEqual(sorted(Cheque.objects.values_list("status", flat=True)), ["cleared", "in_hand", "issued"])
        run = PayrollRun.objects.get()
        self.assertEqual(run.status, "posted")
        self.assertGreaterEqual(run.lines.count(), 4)
        self.assertFalse(run.lines.filter(absent_days__gt=3).exists())  # last month's attendance is there, not missing
        self.assertTrue(PayrollPayment.objects.filter(run=run, pay_to="net").exists())
        alerts = selectors.stock_alert_counts()
        self.assertEqual((alerts["out_of_stock"], alerts["low_stock"]), (1, 1))
        self.assertTrue(SharedReads().customers_over_limit())
        self.assertTrue(all(row["balance"] >= 0 for row in selectors.cashbox_report() if row.get("kind", "cash") == "cash"))
        self.assertTrue(self.client.login(username="owner", password="Demo-pass-1"))
        for url in ("/promotions/", "/loyalty/", "/shipping/", "/store/", "/banks/", "/cheques/", "/staff/payroll/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url + "?lang=ar").status_code, 200)

    def test_no_demo_banner_on_a_real_install(self):
        self.assertNotContains(self.client.get(reverse("login")), "data-demo-login")
