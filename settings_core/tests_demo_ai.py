"""AI demo (HG-078 deviation 7): the showcase shop has the three AI features on, on the mock, with data that answers.

``prepare_demo`` runs once for the class, as the showcase builds itself, with ``DEBUG`` off, ``DEMO_MODE`` on and an empty
``AI_MODE``: the demo's own resolution (mock), not the test run's. Every AI call is made in a test method (never inside
``setUpTestData``'s transaction) on the AI no-network base.
"""

import re
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from ai import providers
from ai.no_network import AiTestCase
from ai.providers.mock import SAMPLE_READING
from ai.summary import build_summary
from cashboxes.models import CashboxMovement
from inventory.models import StockMovement
from master_data.models import Item, Supplier
from purchases.models import PurchaseInvoice, SupplierLedgerEntry
from sales.models import CustomerPayment
from settings_core import demo_ai
from settings_core.capabilities import capability_enabled

DEMO = {"DEMO_MODE": True, "DEMO_PASSWORD": "Demo-pass-1", "AI_MODE": "", "DEBUG": False}
SAMPLE_PHOTO = Path(settings.BASE_DIR) / "static" / "hesba" / "img" / "demo" / "sample-invoice.jpg"


@override_settings(**DEMO)
class DemoAiTests(AiTestCase):
    databases = {"default", "feedback"}  # DEMO-TRACK notes the pages a showcase visitor opens

    @classmethod
    def setUpTestData(cls):
        call_command("prepare_demo", stdout=StringIO())
        users = get_user_model().objects
        cls.owner, cls.cashier = users.get(username="owner"), users.get(username="cashier")

    def ask(self, question, user=None):
        self.client.force_login(user or self.owner)
        response = self.client.post("/ai/ask/?lang=ar", {"q": question, "lang": "ar"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-ai-sample="ask"')  # labelled as a sample
        return response.context["answer"]

    def test_the_demo_runs_the_mock_with_the_three_features_on(self):
        self.assertEqual(providers.mode(), providers.MOCK)
        for slug in demo_ai.CAPABILITIES:
            self.assertTrue(capability_enabled(slug), slug)

    def test_the_voice_summary_has_yesterday_s_sales_and_collections_and_is_labelled(self):
        summary = build_summary(self.owner, "ar")
        self.assertTrue(summary.has_figures)
        self.assertNotIn("المبيعات كانت 0.00", summary.text)
        self.assertNotIn("التحصيل كان 0.00", summary.text)
        self.assertTrue(CustomerPayment.objects.filter(payment_number=demo_ai.COLLECTION_NUMBER,
                                                       payment_date=timezone.localdate() - timedelta(days=1)).exists())
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/dashboard/?lang=ar"), 'data-ai-sample="voice_summary"')
        response = self.client.post("/ai/voice-summary/", {"lang": "ar"})  # the mock voices it: no call out
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(response.json()["audio_url"])

    def test_ask_hesba_answers_from_the_demo_s_own_figures(self):
        for question, tool in (("مبيعات النهارده", "sales_total"), ("إيه اللي قرب يخلص؟", "low_stock"),
                               ("مين أكتر عميل عليه فلوس؟", "top_debtors"), ("الخزنة فيها كام؟", "cashbox_balances"),
                               ("تحصيل الأسبوع ده", "collections_total")):
            with self.subTest(question=question):
                answer = self.ask(question)
                self.assertEqual((answer.kind, answer.tool), ("figure", tool))
                self.assertRegex(answer.value, r"[1-9]")  # never a zero or an empty figure
        self.assertTrue(self.ask("مين أكتر عميل عليه فلوس؟").headline)

    def test_a_cashier_is_refused_a_report_they_may_not_see(self):
        self.client.force_login(self.cashier)
        page = self.client.get("/ai/ask/?lang=ar")
        self.assertNotContains(page, "إيه اللي قرب يخلص؟")  # the chip is not offered
        answer = self.ask("إيه اللي قرب يخلص؟", self.cashier)
        self.assertEqual((answer.kind, answer.tool), ("refused", "low_stock"))
        self.assertFalse(answer.value)
        self.assertEqual(self.ask("مبيعات النهارده", self.cashier).kind, "figure")  # their own sales

    def test_the_sample_invoice_photo_matches_two_lines_and_leaves_one_to_map_then_saves_a_draft(self):
        self.assertEqual(Supplier.objects.get(supplier_code=demo_ai.SUPPLIER_CODE).name, SAMPLE_READING["supplier_name"])
        self.client.force_login(self.owner)
        upload = self.client.post("/ai/invoice-photo/?lang=ar", {"photo": SimpleUploadedFile("sample-invoice.jpg", SAMPLE_PHOTO.read_bytes(),
                                                                                              content_type="image/jpeg")})
        self.assertEqual(upload.status_code, 302, upload.content[:400])
        token = re.search(r"/ai/invoice-photo/([^/]+)/", upload["Location"]).group(1)
        review = self.client.get(f"/ai/invoice-photo/{token}/?lang=ar")
        self.assertContains(review, 'data-ai-sample="invoice_photo"')
        self.assertContains(review, 'data-iph-supplier-chip="matched"')
        reading = review.context["review"]
        self.assertEqual([line["matched"] for line in reading.lines], [True, True, False])
        oil, oil_filter, brake = (Item.objects.get(item_code=code) for code, *_ in demo_ai.ITEMS)
        self.assertEqual([line["item_id"] for line in reading.lines[:2]], [oil.pk, oil_filter.pk])

        before = (StockMovement.objects.count(), CashboxMovement.objects.count(), SupplierLedgerEntry.objects.count())
        data = {"lang": "ar", "supplier": str(reading.supplier_id), "invoice_number": SAMPLE_READING["invoice_number"],
                "invoice_date": SAMPLE_READING["invoice_date"], "receiving_location": str(reading.store_id)}
        for index, item in enumerate((oil, oil_filter, brake)):
            line = SAMPLE_READING["lines"][index]
            data.update({f"line-{index}-item": str(item.pk), f"line-{index}-quantity": line["quantity"],
                         f"line-{index}-price": line["unit_price"], f"line-{index}-include": "1"})
        saved = self.client.post(f"/ai/invoice-photo/{token}/?lang=ar", data)
        self.assertEqual(saved.status_code, 302, saved.content[:400])
        invoice = PurchaseInvoice.objects.get(invoice_number=SAMPLE_READING["invoice_number"])
        self.assertEqual(invoice.status, "draft")
        self.assertEqual(invoice.supplier.supplier_code, demo_ai.SUPPLIER_CODE)
        self.assertEqual(str(invoice.total_amount), "8440.00")  # computed from the lines, not the 8,540.00 on the paper
        self.assertEqual((StockMovement.objects.count(), CashboxMovement.objects.count(), SupplierLedgerEntry.objects.count()), before)

    def test_the_parts_are_in_stock_so_they_never_show_as_sold_out(self):
        answer = self.ask("إيه اللي قرب يخلص؟")
        self.assertFalse(any("DEMO-AI" in label for label, _ in answer.lines))
        self.assertEqual(PurchaseInvoice.objects.get(invoice_number=demo_ai.EARLIER_PURCHASE).status, "posted")

    def test_seeding_again_adds_nothing(self):
        from reports.management.commands.seed_demo_business import Command as Seed

        counts = (Item.objects.count(), PurchaseInvoice.objects.count(), CustomerPayment.objects.count())
        self.assertFalse(demo_ai.seed(Seed()._seed_master_data(), self.owner, {"purchases"}))
        self.assertEqual((Item.objects.count(), PurchaseInvoice.objects.count(), CustomerPayment.objects.count()), counts)
