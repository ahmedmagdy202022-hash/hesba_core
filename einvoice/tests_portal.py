"""ETA-002: signing and sending to the authority, against a fake portal (no network)."""

import os
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse

from audit.models import AuditLog

from . import portal
from .models import Submission
from .services import cancel_submission, refresh_submission, send_invoice
from .tests import DocumentSetup

ENV = {"ETA_ENVIRONMENT": "preprod", "ETA_CLIENT_ID": "cid", "ETA_CLIENT_SECRET": "secret", "ETA_SIGNER_URL": "http://127.0.0.1:9999/sign", "ETA_SIGNER_TOKEN": "tok"}


class FakePortal:
    """Answers like the authority and the signer, and remembers what it was sent."""

    def __init__(self, accept=True, valid=True, signer_ok=True):
        self.accept, self.valid, self.signer_ok, self.calls = accept, valid, signer_ok, []

    def __call__(self, method, url, *, body=None, form=None, headers=None):
        self.calls.append((method, url, body, form, headers))
        if url.endswith("/sign"):
            return (200, {"signature": "MIIB-signature"}) if self.signer_ok else (500, {})
        if url.endswith("/connect/token"):
            return 200, {"access_token": "T", "expires_in": 3600}
        if url.endswith("/documentsubmissions"):
            internal = body["documents"][0]["internalID"]
            if self.accept:
                return 202, {"submissionId": "S1", "acceptedDocuments": [{"uuid": "U1", "longId": "L1", "internalId": internal}], "rejectedDocuments": []}
            return 202, {"submissionId": "S1", "acceptedDocuments": [],
                         "rejectedDocuments": [{"internalId": internal, "error": {"message": "Validation Error", "details": [{"propertyPath": "receiver.id", "message": "Invalid TIN"}]}}]}
        if "/details" in url:
            if self.valid:
                return 200, {"status": "Valid", "longId": "L1", "validationResults": {"status": "Valid", "validationSteps": []}}
            return 200, {"status": "Invalid", "validationResults": {"validationSteps": [{"status": "Invalid", "error": {"message": "Total mismatch"}}]}}
        if "/state" in url:
            return 200, {}
        return 404, {}


@mock.patch.dict(os.environ, ENV)
class PortalTests(DocumentSetup):
    def setUp(self):
        super().setUp()
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.complete_data()
        self.posted = self.invoice()

    def test_serialization_follows_the_authority_rule(self):
        text = portal.serialize({"issuer": {"type": "B", "id": "1"}, "invoiceLines": [{"quantity": 2.0}, {"quantity": 1}], "totalAmount": 10.5})
        self.assertEqual(text, '"ISSUER""TYPE""B""ID""1""INVOICELINES""INVOICELINES""QUANTITY""2.0""INVOICELINES""QUANTITY""1""TOTALAMOUNT""10.5"')

    def test_send_signs_submits_and_then_reads_the_status(self):
        fake = FakePortal()
        with mock.patch.object(portal, "_http", fake):
            submission = send_invoice(self.posted, self.owner, "en")
            self.assertEqual((submission.status, submission.uuid, submission.environment), ("submitted", "U1", "preprod"))
            sent = next(call for call in fake.calls if call[1].endswith("/documentsubmissions"))
            document = sent[2]["documents"][0]
            self.assertEqual(document["signatures"], [{"signatureType": "I", "value": "MIIB-signature"}])
            signed_text = next(call for call in fake.calls if call[1].endswith("/sign"))[2]["serialized"]
            self.assertEqual(signed_text, portal.serialize({k: v for k, v in document.items() if k != "signatures"}))
            self.assertEqual(next(call for call in fake.calls if call[1].endswith("/sign"))[4], {"Authorization": "Bearer tok"})
            self.assertEqual(sent[4], {"Authorization": "Bearer T"})
            with self.assertRaisesMessage(ValidationError, "still checking"):
                send_invoice(self.posted, self.owner, "en")
            refresh_submission(submission, self.owner, "en")
            self.assertEqual(Submission.objects.get().status, "valid")
            with self.assertRaisesMessage(ValidationError, "already sent and accepted"):
                send_invoice(self.posted, self.owner, "en")
            cancel_submission(submission, self.owner, "مرتجع كامل", "ar")
        self.assertEqual(Submission.objects.get().status, "cancelled")
        self.assertEqual(AuditLog.objects.filter(module="einvoice", action__startswith="eta_").count(), 3)
        self.posted.refresh_from_db()
        self.assertEqual(self.posted.status, "posted")  # the invoice itself is never touched

    def test_a_rejection_is_kept_with_the_authoritys_reason_and_can_be_sent_again(self):
        with mock.patch.object(portal, "_http", FakePortal(accept=False)):
            first = send_invoice(self.posted, self.owner, "en")
        self.assertEqual(first.status, "rejected")
        self.assertIn("Invalid TIN", first.message)
        with mock.patch.object(portal, "_http", FakePortal()):
            second = send_invoice(self.posted, self.owner, "en")
        self.assertEqual((second.status, Submission.objects.count()), ("submitted", 2))

    def test_invalid_after_checking_shows_why(self):
        with mock.patch.object(portal, "_http", FakePortal(valid=False)):
            submission = refresh_submission(send_invoice(self.posted, self.owner, "en"), self.owner, "en")
        self.assertEqual((submission.status, submission.message), ("invalid", "Total mismatch"))

    def test_signer_down_or_missing_setup_send_nothing(self):
        with mock.patch.object(portal, "_http", FakePortal(signer_ok=False)) as fake, self.assertRaisesMessage(ValidationError, "جهاز التوقيع"):
            send_invoice(self.posted, self.owner, "ar")
        self.assertFalse(any(call[1].endswith("/documentsubmissions") for call in fake.calls))
        with mock.patch.dict(os.environ, {"ETA_CLIENT_SECRET": ""}), self.assertRaisesMessage(ValidationError, "ETA_CLIENT_SECRET"):
            send_invoice(self.posted, self.owner, "ar")
        self.assertFalse(Submission.objects.exists())

    def test_incomplete_data_is_never_sent(self):
        self.posted.customer.einvoice_profile.delete()
        self.posted.customer.refresh_from_db()
        from .models import ReceiverProfile

        ReceiverProfile.objects.create(customer=self.posted.customer, receiver_type="B", tax_id="12")
        with mock.patch.object(portal, "_http", FakePortal()) as fake, self.assertRaisesMessage(ValidationError, "incomplete"):
            send_invoice(self.posted, self.owner, "en")
        self.assertEqual(fake.calls, [])

    def test_the_screen_sends_and_shows_the_status(self):
        self.client.force_login(self.owner)
        url = reverse("einvoice:sales_document", args=[self.posted.pk])
        self.assertContains(self.client.get(url), "data-eta-send")
        with mock.patch.object(portal, "_http", FakePortal()):
            self.client.post(url, {"action": "send"})
            self.client.post(url, {"action": "refresh"})
        page = self.client.get(url + "?lang=ar")
        self.assertContains(page, 'data-eta-status="valid"')
        self.assertContains(page, "data-eta-public")
        self.assertContains(page, "data-eta-cancel")

    def test_without_setup_the_screen_says_what_is_missing(self):
        self.client.force_login(self.owner)
        with mock.patch.dict(os.environ, {"ETA_CLIENT_ID": "", "ETA_SIGNER_URL": ""}):
            page = self.client.get(reverse("einvoice:sales_document", args=[self.posted.pk]) + "?lang=ar")
        self.assertContains(page, "data-eta-not-set")
        self.assertContains(page, "رقم النظام على المنظومة")
        self.assertNotContains(page, "data-eta-send")

    def test_only_invoice_issuers_may_send(self):
        from permissions.models import RoleCode

        from .tests import person

        accountant = person(RoleCode.ACCOUNTANT, "ei_acc")
        self.client.force_login(accountant)
        url = reverse("einvoice:sales_document", args=[self.posted.pk])
        self.assertNotContains(self.client.get(url), "data-eta-send")
        with mock.patch.object(portal, "_http", FakePortal()):
            self.assertEqual(self.client.post(url, {"action": "send"}).status_code, 403)
        self.assertFalse(Submission.objects.exists())

    def test_the_connection_check_reports_each_part(self):
        with mock.patch.object(portal, "_http", FakePortal(signer_ok=False)):
            ok, problems = portal.check_connection()
        self.assertFalse(ok)
        self.assertEqual(problems, ["signer:500"])
        with mock.patch.object(portal, "_http", FakePortal()):
            self.assertEqual(portal.check_connection(), (True, []))


@mock.patch.dict(os.environ, ENV)
class ConnectionScreenTests(DocumentSetup):
    def test_the_settings_page_tests_the_connection(self):
        self.client.force_login(self.owner)
        url = reverse("einvoice:issuer")
        self.assertContains(self.client.get(url), "data-eta-check")
        with mock.patch.object(portal, "_http", FakePortal()):
            page = self.client.post(url, {"action": "check_connection"}, follow=True)
        self.assertContains(page, "الاتصال شغال")
        with mock.patch.dict(os.environ, {"ETA_SIGNER_URL": ""}):
            self.assertContains(self.client.get(url + "?lang=ar"), "عنوان برنامج التوقيع")
