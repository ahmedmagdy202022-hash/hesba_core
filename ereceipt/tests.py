"""T8 (MARKET-PARITY): e-receipts v1.2 for till sales — built, chained per device, queued for 24 hours, printed with a QR."""

import hashlib
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from einvoice.portal import serialize
from einvoice.tests import DocumentSetup
from sales.pos import checkout, walk_in_customer
from sales.services import create_sales_draft, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from . import services
from .models import Device, Receipt, ReceiptStatus


def line(item, quantity, price):
    return {"item": item, "quantity": D(str(quantity)), "unit_sale_price": D(price), "line_discount_amount": D("0"), "description": ""}


class ReceiptSetup(DocumentSetup):
    def setUp(self):
        super().setUp()
        set_capability_enabled(ClientProfile.get_active(), "e_receipt", True)
        self.complete_data()

    def sale(self, quantity=1, discount="0"):
        invoice, _change = checkout(lines=[line(self.a, quantity, "100.00"), line(self.b, 1, "50.00")], customer=walk_in_customer(), location=self.location,
                                    cashbox=self.cashbox, discount=D(discount), tendered=D("10000"), user=self.owner)
        return invoice


class ReceiptTests(ReceiptSetup):
    def test_without_a_device_it_waits_and_is_issued_once_registered(self):
        invoice = self.sale()
        receipt = Receipt.objects.get(invoice=invoice)
        self.assertEqual(receipt.status, ReceiptStatus.NOT_ISSUED)
        self.assertTrue(any("جهاز الكاشير" in text for text in receipt.problems))
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        receipt = services.issue_again(receipt)
        self.assertEqual((receipt.status, len(receipt.uuid)), (ReceiptStatus.QUEUED, 64))

    def test_the_receipt_follows_v1_2_and_its_uuid_is_its_own_hash(self):
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        invoice = self.sale(quantity=2, discount="10.00")
        receipt = Receipt.objects.get(invoice=invoice)
        doc = receipt.document
        self.assertEqual((doc["documentType"], doc["header"]["receiptNumber"], doc["header"]["previousUUID"]), ({"receiptType": "s", "typeVersion": "1.2"}, invoice.invoice_number, ""))
        self.assertEqual((doc["seller"]["rin"], doc["seller"]["deviceSerialNumber"], doc["buyer"]), ("100200300", "POS-SN-1", {"type": "P"}))
        self.assertEqual(doc["totalAmount"], float(invoice.total_amount))
        self.assertEqual(doc["extraReceiptDiscountData"][0]["amount"], 10.0)
        self.assertEqual(doc["paymentMethod"], "C")
        blank = {**doc, "header": {**doc["header"], "uuid": ""}}
        self.assertEqual(receipt.uuid, hashlib.sha256(serialize(blank).encode("utf-8")).hexdigest())
        self.assertEqual(doc["header"]["uuid"], receipt.uuid)

    def test_receipts_chain_on_their_device(self):
        till = Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        first, second = Receipt.objects.get(invoice=self.sale()), Receipt.objects.get(invoice=self.sale())
        self.assertEqual(first.previous_uuid, "")
        self.assertEqual(second.previous_uuid, first.uuid)
        self.assertEqual(second.document["header"]["previousUUID"], first.uuid)
        till.refresh_from_db()
        self.assertEqual(till.last_uuid, second.uuid)
        self.assertNotEqual(first.uuid, second.uuid)
        self.assertEqual(services.issue(first.invoice), first)  # issued once only

    def test_only_till_sales_and_only_when_switched_on(self):
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        invoice = create_sales_draft({"invoice_number": "INV-9", "invoice_date": timezone.localdate(), "customer": self.customer, "selling_location": self.location,
                                      "cashbox": self.cashbox, "paid_now": D("0"), "discount_amount": D("0")}, [line(self.b, 1, "50.00")], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        self.assertFalse(Receipt.objects.filter(invoice=invoice).exists())  # an invoice to a business goes as an e-invoice
        set_capability_enabled(ClientProfile.get_active(), "e_receipt", False)
        self.assertFalse(Receipt.objects.filter(invoice=self.sale()).exists())

    def test_the_24_hour_queue_and_the_qr(self):
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        receipt = Receipt.objects.get(invoice=self.sale())
        self.assertEqual([row["late"] for row in services.queue()], [False])
        Receipt.objects.filter(pk=receipt.pk).update(issued_at=timezone.now() - timedelta(hours=25))
        self.assertEqual([row["late"] for row in services.queue()], [True])
        text = services.qr_text(receipt, "prod")
        self.assertTrue(text.startswith(f"https://invoicing.eta.gov.eg/receipts/search/{receipt.uuid}/share/{receipt.document['header']['dateTimeIssued']}#Total:"))
        self.assertTrue(text.endswith(",IssuerRIN:100200300"))
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(ClientProfile.get_active(), "pdf_printing", True, user=self.owner)
        self.client.force_login(self.owner)
        printed = self.client.get(reverse("printing:sales_invoice", args=[receipt.invoice_id]) + "?format=receipt&lang=ar")
        self.assertContains(printed, "data-qr-text")
        self.assertContains(printed, "hesba/vendor/qrcode/qrcode-1.4.4.js")


class ScreenTests(ReceiptSetup):
    def test_the_board(self):
        self.client.force_login(self.owner)
        url = reverse("ereceipt:board")
        for lang in ("ar", "en"):
            self.assertContains(self.client.get(url + f"?lang={lang}"), "data-device-form")
        self.client.post(url, {"lang": "ar", "action": "device", "serial_number": "POS-SN-9", "branch_code": "3"})
        self.assertEqual(Device.objects.get().branch_code, "3")
        receipt = Receipt.objects.get(invoice=self.sale())
        self.assertContains(self.client.get(url + "?lang=ar"), f'data-receipt="{receipt.pk}"')
        self.assertIn('"receiptType": "s"', self.client.get(url + "?format=json").content.decode())
        self.assertIn(receipt.uuid, self.client.get(reverse("ereceipt:json", args=[receipt.pk])).content.decode())
