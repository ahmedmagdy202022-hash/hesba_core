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
        aged = receipt.document
        aged["header"]["dateTimeIssued"] = (timezone.now() - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
        Receipt.objects.filter(pk=receipt.pk).update(document_text=__import__("json").dumps(aged))
        self.assertEqual([row["late"] for row in services.queue()], [True])
        receipt.refresh_from_db()
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


class CodexFixesTests(ReceiptSetup):
    """Codex on #271: the receipt is kept exactly as hashed, its 24 hours count from the time written in it, and
    another entity's receipts stay out of reach."""

    def test_the_stored_text_still_hashes_to_its_uuid(self):
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        receipt = Receipt.objects.get(invoice=self.sale())
        receipt.refresh_from_db()  # read back as the database keeps it
        doc = receipt.document
        blank = {**doc, "header": {**doc["header"], "uuid": ""}}
        self.assertEqual(hashlib.sha256(serialize(blank).encode("utf-8")).hexdigest(), receipt.uuid)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("ereceipt:json", args=[receipt.pk])).content.decode(), receipt.document_text)

    def test_the_deadline_counts_from_the_issue_time_in_the_receipt(self):
        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        receipt = Receipt.objects.get(invoice=self.sale())
        doc = receipt.document
        doc["header"]["dateTimeIssued"] = (timezone.now() - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        Receipt.objects.filter(pk=receipt.pk).update(document_text=__import__("json").dumps(doc), issued_at=timezone.now())
        self.assertEqual([row["late"] for row in services.queue()], [True])  # made just now, but issued 30 hours ago

    def test_another_entitys_receipts_are_out_of_reach(self):
        from unittest import mock

        Device.objects.create(serial_number="POS-SN-1", branch_code="0")
        receipt = Receipt.objects.get(invoice=self.sale())
        self.client.force_login(self.owner)
        with mock.patch("ereceipt.services.scoped", side_effect=lambda queryset: queryset.none()):
            self.assertEqual(self.client.get(reverse("ereceipt:json", args=[receipt.pk])).status_code, 404)
            self.assertNotContains(self.client.get(reverse("ereceipt:board") + "?lang=ar"), f'data-receipt="{receipt.pk}"')


class SecondReviewTests(ReceiptSetup):
    """Codex on #271 (second look): a new till is a new device with its own chain, and receipts alone open their setup."""

    def test_a_new_serial_starts_its_own_chain_and_the_old_one_keeps_its_own(self):
        old = services.register("POS-SN-1")
        first = Receipt.objects.get(invoice=self.sale())
        self.client.force_login(self.owner)
        self.client.post(reverse("ereceipt:board"), {"lang": "ar", "action": "device", "serial_number": "POS-SN-2", "branch_code": "0"})
        new = services.device()
        self.assertNotEqual(new.pk, old.pk)
        second = Receipt.objects.get(invoice=self.sale())
        self.assertEqual((second.device_id, second.previous_uuid), (new.pk, ""))  # the new device's chain starts empty
        old.refresh_from_db()
        self.assertEqual((old.active, old.last_uuid, first.device_id), (False, first.uuid, old.pk))
        self.assertEqual(services.register("POS-SN-1").pk, old.pk)  # back on the old till: its chain goes on
        self.assertEqual(Receipt.objects.get(invoice=self.sale()).previous_uuid, first.uuid)

    def test_receipts_alone_open_the_issuer_and_item_code_screens(self):
        profile = ClientProfile.get_active()
        set_capability_enabled(profile, "e_invoice", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("einvoice:issuer")).status_code, 200)
        self.assertEqual(self.client.get(reverse("einvoice:items")).status_code, 200)
        self.assertEqual(self.client.get(reverse("einvoice:customers")).status_code, 200)  # buyer IDs: a named receipt over the threshold needs one
        set_capability_enabled(profile, "e_receipt", False)
        self.assertNotEqual(self.client.get(reverse("einvoice:issuer")).status_code, 200)


class ThirdReviewTests(ReceiptSetup):
    """Codex on #271 (third look): a sale cancelled before its receipt was issued never gets one."""

    def test_a_cancelled_sale_is_not_issued_on_retry(self):
        from sales.models import SalesInvoiceStatus

        invoice = self.sale()
        receipt = Receipt.objects.get(invoice=invoice)
        self.assertEqual(receipt.status, ReceiptStatus.NOT_ISSUED)  # no device yet
        type(invoice).objects.filter(pk=invoice.pk).update(status=SalesInvoiceStatus.CANCELLED)
        till = services.register("POS-SN-1")
        self.assertEqual(services.issue_again(receipt).status, ReceiptStatus.NOT_ISSUED)
        till.refresh_from_db()
        self.assertEqual(till.last_uuid, "")  # the chain did not move
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("ereceipt:board") + "?lang=ar"), "data-receipt-missing")
        self.assertEqual(self.client.post(reverse("ereceipt:board"), {"lang": "ar", "action": "again", "receipt": receipt.pk}).status_code, 404)
