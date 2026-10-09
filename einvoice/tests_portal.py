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

    def __init__(self, accept=True, valid=True, signer_ok=True, refuse_http=None, details=None, on_sign=None):
        self.accept, self.valid, self.signer_ok, self.refuse_http, self.calls = accept, valid, signer_ok, refuse_http, []
        self.details, self.on_sign = details, on_sign  # a fixed details answer; a hook run while "signing"

    def __call__(self, method, url, *, body=None, form=None, headers=None):
        self.calls.append((method, url, body, form, headers))
        if url.endswith("/sign"):
            if self.on_sign:
                self.on_sign()
            return (200, {"signature": "MIIB-signature"}) if self.signer_ok else (500, {})
        if url == "https://id.preprod.eta.gov.eg/connect/token":
            import base64

            if headers.get("Authorization") != "Basic " + base64.b64encode(b"cid:secret").decode() or "client_secret" in (form or {}):
                return 401, {"error": "invalid_client"}
            return 200, {"access_token": "T", "expires_in": 3600}
        if url == "https://api.preprod.invoicing.eta.gov.eg/api/v1.0/documentsubmissions/":
            internal = body["documents"][0]["internalID"]
            if self.refuse_http:
                return self.refuse_http, {"error": {"message": "Bad structure", "details": [{"propertyPath": "issuer.id", "message": "Required"}]}}
            if self.accept:
                return 202, {"submissionUUID": "S1", "acceptedDocuments": [{"uuid": "U1", "longId": "L1", "internalId": internal}], "rejectedDocuments": []}
            return 202, {"submissionUUID": "S1", "acceptedDocuments": [],
                         "rejectedDocuments": [{"internalId": internal, "error": {"message": "Validation Error", "details": [{"propertyPath": "receiver.id", "message": "Invalid TIN"}]}}]}
        if url == "https://api.preprod.invoicing.eta.gov.eg/api/v1.0/documents/U1/details":
            if self.details is not None:
                return 200, self.details
            if self.valid:
                return 200, {"status": "Valid", "longId": "L1", "validationResults": {"status": "Valid", "validationSteps": []}}
            return 200, {"status": "Invalid", "validationResults": {"validationSteps": [{"status": "Invalid", "error": {"message": "Total mismatch"}}]}}
        if url == "https://api.preprod.invoicing.eta.gov.eg/api/v1.0/documents/state/U1/state" and method == "PUT" and body.get("status") == "cancelled":
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
            self.assertEqual((submission.status, submission.uuid, submission.environment, submission.submission_id), ("submitted", "U1", "preprod", "S1"))
            sent = next(call for call in fake.calls if call[1].endswith("/documentsubmissions/"))
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
        self.assertEqual(Submission.objects.get().status, "cancel_requested")  # until the authority confirms it
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
        self.assertFalse(any(call[1].endswith("/documentsubmissions/") for call in fake.calls))
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


@mock.patch.dict(os.environ, ENV)
class EnvironmentTests(DocumentSetup):
    def test_moving_to_production_starts_a_fresh_history(self):
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.complete_data()
        posted = self.invoice()
        with mock.patch.object(portal, "_http", FakePortal()):
            refresh_submission(send_invoice(posted, self.owner, "en"), self.owner, "en")  # valid on preprod
        with mock.patch.dict(os.environ, {"ETA_ENVIRONMENT": "prod"}):
            from .services import current_submission

            self.assertIsNone(current_submission(posted))
            prod = FakePortal()
            calls = []

            def live(method, url, **kwargs):
                calls.append(url)
                return prod(method, url.replace("https://api.invoicing.eta.gov.eg", "https://api.preprod.invoicing.eta.gov.eg")
                            .replace("https://id.eta.gov.eg", "https://id.preprod.eta.gov.eg"), **kwargs)

            with mock.patch.object(portal, "_http", live):
                submission = send_invoice(posted, self.owner, "en")
        self.assertEqual((submission.environment, submission.status), ("prod", "submitted"))
        self.assertIn("https://api.invoicing.eta.gov.eg/api/v1.0/documentsubmissions/", calls)


@mock.patch.dict(os.environ, ENV)
class RefusalAndLinkTests(DocumentSetup):
    def setUp(self):
        super().setUp()
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.complete_data()
        self.posted = self.invoice()

    def test_an_http_refusal_is_kept_with_the_authoritys_answer(self):
        with mock.patch.object(portal, "_http", FakePortal(refuse_http=400)):
            submission = send_invoice(self.posted, self.owner, "en")
        self.assertEqual(submission.status, "rejected")
        self.assertIn("issuer.id Required", submission.message)
        self.assertEqual(submission.response["http_status"], 400)
        self.assertTrue(AuditLog.objects.filter(action="eta_send", object_id=str(submission.pk)).exists())

    def test_the_public_link_follows_the_authority(self):
        from .services import public_url

        with mock.patch.object(portal, "_http", FakePortal()):
            submission = refresh_submission(send_invoice(self.posted, self.owner, "en"), self.owner, "en")
        self.assertEqual(public_url(submission), "https://preprod.invoicing.eta.gov.eg/documents/U1/share/L1")
        submission.response = {"publicUrl": "https://preprod.invoicing.eta.gov.eg/documents/U1/share/OTHER"}
        self.assertEqual(public_url(submission), "https://preprod.invoicing.eta.gov.eg/documents/U1/share/OTHER")


@mock.patch.dict(os.environ, ENV)
class ReviewThreeTests(DocumentSetup):
    """Codex's third review on #181: exact signed text, one sending at a time, cancellation as a request."""

    def setUp(self):
        super().setUp()
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.complete_data()
        self.posted = self.invoice()

    def test_a_string_is_signed_exactly_as_the_json_sends_it(self):
        import json

        value = '15" screen \\ قطعة'
        self.assertEqual(portal.serialize({"description": value}), '"DESCRIPTION"' + json.dumps(value, ensure_ascii=False))
        self.assertIn(portal.serialize({"description": value})[len('"DESCRIPTION"'):], json.dumps({"description": value}, ensure_ascii=False))
        self.assertEqual(portal.serialize({"description": '15" screen'}), '"DESCRIPTION""15\\" screen"')

    def test_a_second_send_while_the_first_is_on_its_way_is_refused(self):
        seen = []

        def again():
            if not seen:
                seen.append(True)
                with self.assertRaisesMessage(ValidationError, "still checking"):
                    send_invoice(self.posted, self.owner, "en")

        fake = FakePortal(on_sign=again)
        with mock.patch.object(portal, "_http", fake):
            submission = send_invoice(self.posted, self.owner, "en")
        self.assertEqual(seen, [True])
        self.assertEqual((Submission.objects.count(), submission.status), (1, "submitted"))
        self.assertEqual(sum(1 for call in fake.calls if call[1].endswith("/documentsubmissions/")), 1)

    def test_a_claim_left_by_a_dead_server_stops_blocking(self):
        from datetime import timedelta

        from django.utils import timezone

        stale = Submission.objects.create(invoice=self.posted, environment="preprod", submitted_by=self.owner, status="sending")
        with mock.patch.object(portal, "_http", FakePortal()), self.assertRaisesMessage(ValidationError, "still checking"):
            send_invoice(self.posted, self.owner, "en")
        Submission.objects.filter(pk=stale.pk).update(submitted_at=timezone.now() - timedelta(minutes=11))
        with mock.patch.object(portal, "_http", FakePortal()):
            fresh = send_invoice(self.posted, self.owner, "en")
        self.assertEqual(fresh.status, "submitted")
        self.assertEqual(Submission.objects.get(pk=stale.pk).status, "rejected")

    def test_a_cancellation_waits_for_the_authority(self):
        with mock.patch.object(portal, "_http", FakePortal()):
            submission = refresh_submission(send_invoice(self.posted, self.owner, "en"), self.owner, "en")
            cancel_submission(submission, self.owner, "مرتجع كامل", "ar")
            self.assertEqual(submission.status, "cancel_requested")
            with self.assertRaisesMessage(ValidationError, "already sent and accepted"):
                send_invoice(self.posted, self.owner, "en")  # no second invoice while the cancellation may be declined
        waiting = {"status": "Valid", "longId": "L1", "cancelRequestDate": "2026-10-09T21:00:00Z", "validationResults": {"validationSteps": []}}
        with mock.patch.object(portal, "_http", FakePortal(details=waiting)):
            self.assertEqual(refresh_submission(submission, self.owner, "en").status, "cancel_requested")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("einvoice:sales_document", args=[self.posted.pk]) + "?lang=ar")
        self.assertContains(page, 'data-eta-status="cancel_requested"')
        self.assertContains(page, "data-eta-refresh")
        self.assertNotContains(page, "data-eta-send")
        declined = dict(waiting, declineCancelRequestDate="2026-10-10T08:00:00Z")
        with mock.patch.object(portal, "_http", FakePortal(details=declined)):
            self.assertEqual(refresh_submission(submission, self.owner, "en").status, "valid")  # the receiver declined: still valid
        with mock.patch.object(portal, "_http", FakePortal()):
            cancel_submission(submission, self.owner, "مرتجع كامل", "ar")
        with mock.patch.object(portal, "_http", FakePortal(details={"status": "Cancelled", "longId": "L1"})):
            done = refresh_submission(submission, self.owner, "en")
        self.assertEqual(done.status, "cancelled")
        self.assertIsNotNone(Submission.objects.get(pk=done.pk).cancelled_at)
