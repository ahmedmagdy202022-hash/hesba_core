"""T8 (live part): queued e-receipts go to the authority's receipt API exactly as hashed, and come back valid or not."""

import json
import os
from unittest import mock

from django.core.management import call_command

from einvoice import portal as eta

from . import portal
from .models import Device, Receipt, ReceiptStatus
from .tests import ReceiptSetup

ENV = {"ETA_ENVIRONMENT": "preprod", "ETA_RECEIPT_CLIENT_ID": "pos-client", "ETA_RECEIPT_CLIENT_SECRET": "pos-secret",
       "ETA_RECEIPT_PRESHARED_KEY": "psk-1", "ETA_SIGNER_URL": "", "ETA_SIGNER_TOKEN": ""}


class FakePortal:
    def __init__(self, reject=(), status=202, details_status="Valid"):
        self.reject, self.status, self.details_status, self.calls = set(reject), status, details_status, []

    def __call__(self, method, url, *, body=None, form=None, headers=None, raw=None):
        self.calls.append({"method": method, "url": url, "form": form, "headers": headers or {}, "raw": raw})
        if url.endswith("/connect/token"):
            return 200, {"access_token": "token-1", "expires_in": 3600}
        if url.endswith("/api/v1/receiptsubmissions"):
            if self.status != 202:
                return self.status, {"error": {"message": "Bad structure", "details": [{"propertyPath": "header", "message": "Required"}]}}
            receipts = json.loads(raw)["receipts"]
            accepted = [{"uuid": r["header"]["uuid"], "longId": "LONG-" + r["header"]["receiptNumber"], "receiptNumber": r["header"]["receiptNumber"]}
                        for r in receipts if r["header"]["receiptNumber"] not in self.reject]
            rejected = [{"uuid": r["header"]["uuid"], "receiptNumber": r["header"]["receiptNumber"], "error": {"message": "Invalid total"}}
                        for r in receipts if r["header"]["receiptNumber"] in self.reject]
            return 202, {"submissionUUID": "SUB-1", "acceptedDocuments": accepted, "rejectedDocuments": rejected}
        if "/api/v1/receiptsubmissions/SUB-1/details" in url:
            sent = Receipt.objects.filter(submission_uuid="SUB-1")
            errors = [] if self.details_status == "Valid" else [{"stepName": "Validate", "error": {"propertyPath": "totalAmount", "errorCode": "TotalMismatch",
                                                                                                 "error": "The total does not match the items", "innerError": None}}]
            return 200, {"status": self.details_status, "receipts": [{"uuid": r.uuid, "status": self.details_status, "errors": errors} for r in sent]}
        return 404, {}


class SendingTests(ReceiptSetup):
    def setUp(self):
        super().setUp()
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.till = Device.objects.create(serial_number="POS-SN-1", branch_code="0", os_version="Android 13", model_framework="Sunmi V2")

    def run_send(self, fake, env=ENV):
        with mock.patch.dict(os.environ, env), mock.patch.object(eta, "_http", fake):
            return portal.send(), portal.follow_up()

    def test_nothing_goes_while_a_setting_or_the_device_details_are_missing(self):
        self.sale()
        fake = FakePortal()
        result, _ = self.run_send(fake, {**ENV, "ETA_RECEIPT_PRESHARED_KEY": ""})
        self.assertIn("ETA_RECEIPT_PRESHARED_KEY", result["message"])
        self.assertEqual(fake.calls, [])
        Device.objects.filter(pk=self.till.pk).update(model_framework="")
        with mock.patch.dict(os.environ, ENV):
            self.assertEqual(portal.missing(), ["model_framework"])

    def test_queued_receipts_go_oldest_first_exactly_as_hashed_and_come_back_valid(self):
        first, second = self.sale(), self.sale(quantity=3)
        fake = FakePortal()
        result, changed = self.run_send(fake)
        self.assertEqual((result["accepted"], result["rejected"], changed), (2, 0, 2))
        login, submit = fake.calls[0], fake.calls[1]
        self.assertEqual(login["url"], "https://id.preprod.eta.gov.eg/connect/token")
        self.assertEqual((login["form"]["client_id"], login["headers"]["posserial"], login["headers"]["pososversion"],
                          login["headers"]["posmodelframework"], login["headers"]["presharedkey"]), ("pos-client", "POS-SN-1", "Android 13", "Sunmi V2", "psk-1"))
        self.assertEqual(submit["url"], "https://api.preprod.invoicing.eta.gov.eg/api/v1/receiptsubmissions")
        self.assertEqual(submit["headers"]["Authorization"], "Bearer token-1")
        texts = [Receipt.objects.get(invoice=first).document_text, Receipt.objects.get(invoice=second).document_text]
        self.assertEqual(submit["raw"], '{"receipts": [' + ", ".join(texts) + "]}")  # byte for byte, in chain order
        receipt = Receipt.objects.get(invoice=first)
        self.assertEqual((receipt.status, receipt.submission_uuid, receipt.long_id), (ReceiptStatus.VALID, "SUB-1", f"LONG-{first.invoice_number}"))

    def test_a_rejected_receipt_keeps_the_authoritys_reason(self):
        good, bad = self.sale(), self.sale(quantity=2)
        result, _ = self.run_send(FakePortal(reject={bad.invoice_number}))
        self.assertEqual((result["accepted"], result["rejected"]), (1, 1))
        receipt = Receipt.objects.get(invoice=bad)
        self.assertEqual((receipt.status, receipt.message), (ReceiptStatus.INVALID, "Invalid total"))
        self.assertEqual(Receipt.objects.get(invoice=good).status, ReceiptStatus.VALID)

    def test_a_refused_batch_stays_queued_with_the_reason(self):
        invoice = self.sale()
        result, _ = self.run_send(FakePortal(status=400))
        self.assertIn("refused:400", result["message"])
        receipt = Receipt.objects.get(invoice=invoice)
        self.assertEqual(receipt.status, ReceiptStatus.QUEUED)
        self.assertIn("Bad structure", receipt.message)
        result, _ = self.run_send(FakePortal())  # the next run sends it
        self.assertEqual(result["accepted"], 1)

    def test_the_board_and_the_command_send(self):
        invoice = self.sale()
        fake = FakePortal()
        self.client.force_login(self.owner)
        with mock.patch.dict(os.environ, ENV), mock.patch.object(eta, "_http", fake):
            page = self.client.get("/ereceipt/")
            self.assertContains(page, "data-send-receipts")
            self.client.post("/ereceipt/", {"action": "send", "lang": "ar"})
        self.assertEqual(Receipt.objects.get(invoice=invoice).status, ReceiptStatus.VALID)
        self.sale()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(eta, "_http", FakePortal()):
            call_command("send_ereceipts", verbosity=0)
        self.assertFalse(Receipt.objects.filter(status=ReceiptStatus.QUEUED).exists())

    def test_a_replaced_till_still_sends_and_follows_up_its_own_receipts(self):
        old = self.sale()
        from . import services

        services.register("POS-SN-2", "0", "Android 14", "Sunmi V3")  # the old device is now inactive
        new = self.sale()
        fake = FakePortal()
        result, _ = self.run_send(fake)
        self.assertEqual(result["accepted"], 2)
        serials = [call["headers"]["posserial"] for call in fake.calls if call["url"].endswith("/receiptsubmissions")]
        self.assertEqual(serials, ["POS-SN-1", "POS-SN-2"])
        self.assertEqual({Receipt.objects.get(invoice=old).status, Receipt.objects.get(invoice=new).status}, {ReceiptStatus.VALID})

    def test_an_invalid_receipt_keeps_the_authoritys_reason_from_the_follow_up(self):
        invoice = self.sale()
        self.run_send(FakePortal(details_status="Invalid"))
        receipt = Receipt.objects.get(invoice=invoice)
        self.assertEqual(receipt.status, ReceiptStatus.INVALID)
        self.assertEqual(receipt.message, "totalAmount: The total does not match the items")

