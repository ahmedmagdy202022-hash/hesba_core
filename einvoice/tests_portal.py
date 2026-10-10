"""ETA-002: signing and sending to the authority, against a fake portal (no network)."""

import os
from datetime import timedelta
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

    def __init__(self, accept=True, valid=True, signer_ok=True, refuse_http=None, details=None, on_sign=None, found=None, submit_fail=None, cancel_fail=None):
        self.accept, self.valid, self.signer_ok, self.refuse_http, self.calls = accept, valid, signer_ok, refuse_http, []
        self.details, self.on_sign, self.found, self.submit_fail, self.cancel_fail = details, on_sign, found, submit_fail, cancel_fail  # a fixed details answer; a hook run while "signing"; search hits

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
            if self.submit_fail == "cut":
                raise portal.PortalError("unreachable: connection reset")  # it may have arrived
            if self.submit_fail:
                return self.submit_fail, {}
            if self.refuse_http:
                return self.refuse_http, {"error": {"message": "Bad structure", "details": [{"propertyPath": "issuer.id", "message": "Required"}]}}
            if self.accept:
                return 202, {"submissionUUID": "S1", "acceptedDocuments": [{"uuid": "U1", "longId": "L1", "internalId": internal}], "rejectedDocuments": []}
            return 202, {"submissionUUID": "S1", "acceptedDocuments": [],
                         "rejectedDocuments": [{"internalId": internal, "error": {"message": "Validation Error", "details": [{"propertyPath": "receiver.id", "message": "Invalid TIN"}]}}]}
        if url.startswith("https://api.preprod.invoicing.eta.gov.eg/api/v1.0/documents/search?") and method == "GET":
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(url).query)
            if not (query.get("internalID") and query.get("submissionDateFrom") and query.get("submissionDateTo") and query.get("direction") == ["Sent"]):
                return 400, {}
            from datetime import datetime

            since, until = (datetime.strptime(query[key][0], "%Y-%m-%dT%H:%M:%SZ") for key in ("submissionDateFrom", "submissionDateTo"))
            if not timedelta(0) <= until - since <= timedelta(days=30):
                return 400, {"error": "range over 30 days"}
            return 200, {"result": [row for row in (self.found or []) if row.get("internalId") == query["internalID"][0]],
                         "metadata": {"continuationToken": "EndofResultSet"}}
        if url == "https://api.preprod.invoicing.eta.gov.eg/api/v1.0/documents/U1/details":
            if self.details is not None:
                return 200, self.details
            if self.valid:
                return 200, {"status": "Valid", "longId": "L1", "validationResults": {"status": "Valid", "validationSteps": []}}
            return 200, {"status": "Invalid", "validationResults": {"validationSteps": [{"status": "Invalid", "error": {"message": "Total mismatch"}}]}}
        if url == "https://api.preprod.invoicing.eta.gov.eg/api/v1.0/documents/state/U1/state" and method == "PUT" and body.get("status") == "cancelled":
            if self.cancel_fail == "cut":
                raise portal.PortalError("unreachable: timeout")
            if self.cancel_fail:
                return self.cancel_fail, {"error": {"message": "Cancellation period is over"}}
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

    def stale_claim(self):
        from datetime import timedelta

        from django.utils import timezone

        stale = Submission.objects.create(invoice=self.posted, environment="preprod", submitted_by=self.owner, status="sending")
        with mock.patch.object(portal, "_http", FakePortal()), self.assertRaisesMessage(ValidationError, "still checking"):
            send_invoice(self.posted, self.owner, "en")  # a claim still in its window blocks
        Submission.objects.filter(pk=stale.pk).update(submitted_at=timezone.now() - timedelta(minutes=11))
        return stale

    def test_a_dead_servers_claim_is_reconciled_and_never_sent_twice_when_the_authority_has_it(self):
        stale = self.stale_claim()
        found = [{"internalId": self.posted.invoice_number, "uuid": "U1", "longId": "L1", "submissionUUID": "S9", "status": "Valid",
                  "dateTimeReceived": "2026-10-09T21:00:00Z"}]
        fake = FakePortal(found=found)
        with mock.patch.object(portal, "_http", fake), self.assertRaisesMessage(ValidationError, "already sent and accepted"):
            send_invoice(self.posted, self.owner, "en")
        recovered = Submission.objects.get(pk=stale.pk)
        self.assertEqual((recovered.status, recovered.uuid, recovered.long_id, recovered.submission_id), ("valid", "U1", "L1", "S9"))
        self.assertFalse(any(call[1].endswith("/documentsubmissions/") for call in fake.calls))
        self.assertEqual(Submission.objects.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="eta_send_recovered", object_id=str(stale.pk)).exists())

    def test_a_dead_servers_claim_the_authority_never_got_is_closed_then_sent_again(self):
        stale = self.stale_claim()
        with mock.patch.object(portal, "_http", FakePortal(found=[])):
            fresh = send_invoice(self.posted, self.owner, "en")
        self.assertEqual(fresh.status, "submitted")
        self.assertEqual(Submission.objects.get(pk=stale.pk).status, "rejected")
        self.assertTrue(AuditLog.objects.filter(action="eta_send_recovered", object_id=str(stale.pk)).exists())

    def test_when_the_authority_cannot_be_asked_nothing_is_sent(self):
        stale = self.stale_claim()

        def down(method, url, **kwargs):
            if "/documents/search" in url:
                raise portal.PortalError("unreachable: timeout")
            return FakePortal()(method, url, **kwargs)

        with mock.patch.object(portal, "_http", down), self.assertRaisesMessage(ValidationError, "cannot be reached"):
            send_invoice(self.posted, self.owner, "en")
        self.assertEqual(Submission.objects.get(pk=stale.pk).status, "sending")
        self.assertEqual(Submission.objects.count(), 1)

    def test_a_refresh_never_undoes_a_cancellation_saved_meanwhile(self):
        with mock.patch.object(portal, "_http", FakePortal()):
            submission = refresh_submission(send_invoice(self.posted, self.owner, "en"), self.owner, "en")
        plain_valid = {"status": "Valid", "longId": "L1", "validationResults": {"validationSteps": []}}
        inner, raced = FakePortal(), []

        def racing(method, url, **kwargs):
            if url.endswith("/U1/details") and not raced:
                raced.append(True)
                answer = (200, dict(plain_valid))  # read before the cancellation below was made
                cancel_submission(Submission.objects.get(pk=submission.pk), self.owner, "غلط", "ar")
                return answer
            return inner(method, url, **kwargs)

        with mock.patch.object(portal, "_http", racing):
            refresh_submission(Submission.objects.get(pk=submission.pk), self.owner, "en")
        self.assertEqual(Submission.objects.get(pk=submission.pk).status, "cancel_requested")
        with_request = dict(plain_valid, cancelRequestDate="2026-10-09T21:00:00Z")
        other = Submission.objects.get(pk=submission.pk)
        Submission.objects.filter(pk=other.pk).update(status="valid")
        with mock.patch.object(portal, "_http", FakePortal(details=with_request)):
            self.assertEqual(refresh_submission(other, self.owner, "en").status, "cancel_requested")  # read from the answer itself

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


@mock.patch.dict(os.environ, ENV)
class UnknownOutcomeTests(DocumentSetup):
    """Codex on #181: a sending whose answer was lost is never repeated before the authority is asked."""

    def setUp(self):
        super().setUp()
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.complete_data()
        self.posted = self.invoice()

    def check_kept(self, failure):
        from django.utils import timezone

        with mock.patch.object(portal, "_http", FakePortal(submit_fail=failure)), self.assertRaisesMessage(ValidationError, "may have arrived"):
            send_invoice(self.posted, self.owner, "en")
        claim = Submission.objects.get()
        self.assertEqual(claim.status, "sending")
        self.assertIn("outcome unknown", claim.message)
        self.assertTrue(AuditLog.objects.filter(action="eta_send_unknown", object_id=str(claim.pk)).exists())
        fake = FakePortal()
        with mock.patch.object(portal, "_http", fake), self.assertRaisesMessage(ValidationError, "still checking"):
            send_invoice(self.posted, self.owner, "en")  # an immediate retry sends nothing
        self.assertFalse(any(call[1].endswith("/documentsubmissions/") for call in fake.calls))
        Submission.objects.filter(pk=claim.pk).update(submitted_at=timezone.now() - timedelta(minutes=11))
        found = [{"internalId": self.posted.invoice_number, "uuid": "U1", "longId": "L1", "status": "Submitted", "dateTimeReceived": "x"}]
        fake = FakePortal(found=found)
        with mock.patch.object(portal, "_http", fake), self.assertRaisesMessage(ValidationError, "still checking"):
            send_invoice(self.posted, self.owner, "en")  # it had arrived: adopted, not resent
        self.assertEqual((Submission.objects.get().status, Submission.objects.get().uuid, Submission.objects.count()), ("submitted", "U1", 1))
        self.assertFalse(any(call[1].endswith("/documentsubmissions/") for call in fake.calls))

    def test_a_connection_cut_mid_answer_keeps_the_claim(self):
        self.check_kept("cut")

    def test_a_server_error_keeps_the_claim(self):
        self.check_kept(503)

    def test_an_old_claim_is_searched_inside_the_authoritys_window(self):
        from django.utils import timezone

        claim = Submission.objects.create(invoice=self.posted, environment="preprod", submitted_by=self.owner, status="sending")
        Submission.objects.filter(pk=claim.pk).update(submitted_at=timezone.now() - timedelta(days=40))
        with mock.patch.object(portal, "_http", FakePortal(found=[])):
            fresh = send_invoice(self.posted, self.owner, "en")  # the search was accepted (≤ 30 days) and found nothing
        self.assertEqual((fresh.status, Submission.objects.get(pk=claim.pk).status), ("submitted", "rejected"))


@mock.patch.dict(os.environ, ENV)
class CancellationClaimTests(DocumentSetup):
    """Codex on #181: a cancellation whose answer was lost is never asked twice, and stays refreshable."""

    def setUp(self):
        super().setUp()
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.complete_data()
        self.posted = self.invoice()

    def valid(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(portal, "_http", FakePortal()):
            self.submission = refresh_submission(send_invoice(self.posted, self.owner, "en"), self.owner, "en")

    def test_a_cut_connection_keeps_the_request_until_a_refresh_settles_it(self):
        self.valid()
        with mock.patch.object(portal, "_http", FakePortal(cancel_fail="cut")), self.assertRaisesMessage(ValidationError, "may have arrived"):
            cancel_submission(self.submission, self.owner, "غلط", "en")
        row = Submission.objects.get(pk=self.submission.pk)
        self.assertEqual(row.status, "cancel_requested")
        self.assertIsNotNone(row.cancel_requested_at)
        self.assertTrue(AuditLog.objects.filter(action="eta_cancel_unknown", object_id=str(row.pk)).exists())
        with mock.patch.object(portal, "_http", FakePortal()), self.assertRaisesMessage(ValidationError, "Only accepted"):
            cancel_submission(row, self.owner, "غلط", "en")  # never asked twice
        arrived = {"status": "Valid", "longId": "L1", "cancelRequestDate": "2026-10-10T07:00:00Z"}
        with mock.patch.object(portal, "_http", FakePortal(details=arrived)):
            self.assertEqual(refresh_submission(row, self.owner, "en").status, "cancel_requested")

    def test_a_request_that_never_arrived_goes_back_to_valid_on_refresh(self):
        self.valid()
        with mock.patch.object(portal, "_http", FakePortal(cancel_fail="cut")), self.assertRaises(ValidationError):
            cancel_submission(self.submission, self.owner, "غلط", "en")
        none = {"status": "Valid", "longId": "L1"}
        with mock.patch.object(portal, "_http", FakePortal(details=none)):
            row = refresh_submission(Submission.objects.get(pk=self.submission.pk), self.owner, "en")
        self.assertEqual((row.status, row.cancel_requested_at), ("valid", None))
        self.assertIn("never reached", row.message)

    def test_a_definite_refusal_puts_it_back_to_valid(self):
        self.valid()
        with mock.patch.object(portal, "_http", FakePortal(cancel_fail=400)), self.assertRaisesMessage(ValidationError, "Cancellation period is over"):
            cancel_submission(self.submission, self.owner, "غلط", "en")
        row = Submission.objects.get(pk=self.submission.pk)
        self.assertEqual((row.status, row.cancel_requested_at), ("valid", None))
        self.assertTrue(AuditLog.objects.filter(action="eta_cancel_refused", object_id=str(row.pk)).exists())
