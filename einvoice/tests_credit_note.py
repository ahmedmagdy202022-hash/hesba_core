"""T7 (HG-059): a sales return's credit note — built from the return, pointing to the accepted original, queued,
and sent through the same claim-then-send path as an invoice, without the return ever waiting for the authority."""

import os
from decimal import Decimal as D
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from sales.services import create_sales_return

from . import portal
from .models import Submission
from .services import build_credit_note, credit_notes_due, current_credit_note, current_submission, refresh_submission, send_credit_note, send_invoice
from .tests import TODAY, DocumentSetup
from .tests_portal import ENV, FakePortal


@mock.patch.dict(os.environ, ENV)
class CreditNoteTests(DocumentSetup):
    def setUp(self):
        super().setUp()
        portal._TOKEN.update(value="", expires=0.0, key="")
        self.complete_data()
        self.posted = self.invoice()
        line_a = self.posted.lines.get(item=self.a)
        self.sales_return = create_sales_return("RET-1", TODAY, self.posted.pk, [{"source_line": line_a, "quantity": D("1")}], "تالف", self.owner)

    def accept_original(self):
        with mock.patch.object(portal, "_http", FakePortal()):
            refresh_submission(send_invoice(self.posted, self.owner, "en"), self.owner, "en")

    def test_the_credit_note_points_to_the_accepted_original_and_matches_the_return(self):
        _document, problems = build_credit_note(self.sales_return, "en")
        self.assertIn("The original invoice must be sent to the portal and accepted first (the credit note points to it).", problems)
        self.accept_original()
        document, problems = build_credit_note(self.sales_return, "en")
        self.assertEqual(problems, [])
        self.assertEqual((document["documentType"], document["internalID"], document["references"]), ("c", "RET-1", ["U1"]))
        line = document["invoiceLines"][0]
        self.assertEqual((line["quantity"], line["salesTotal"], line["internalCode"]), (1.0, 100.0, "A"))
        self.assertEqual(document["totalAmount"], float(self.sales_return.total_amount))  # every discount's share and the tax given back
        self.assertEqual(round(line["netTotal"] + line["taxableItems"][0]["amount"], 2), line["total"])
        self.assertEqual(round(line["salesTotal"] - line["discount"]["amount"], 2), line["netTotal"])

    def test_sending_is_claimed_like_an_invoice_and_kept_apart_from_it(self):
        with self.assertRaisesMessage(ValidationError, "accepted first"):
            send_credit_note(self.sales_return, self.owner, "en")
        self.accept_original()
        fake = FakePortal()
        with mock.patch.object(portal, "_http", fake):
            note = send_credit_note(self.sales_return, self.owner, "en")
            sent = next(call for call in fake.calls if call[1].endswith("/documentsubmissions/"))[2]["documents"][0]
            self.assertEqual((sent["documentType"], sent["references"]), ("c", ["U1"]))
            with self.assertRaisesMessage(ValidationError, "still checking"):
                send_credit_note(self.sales_return, self.owner, "en")
        self.assertEqual((note.status, note.sales_return, note.invoice), ("submitted", self.sales_return, self.posted))
        self.assertEqual(current_submission(self.posted).sales_return, None)  # the invoice's own sending is unchanged
        self.assertEqual(current_credit_note(self.sales_return), note)
        self.sales_return.refresh_from_db()
        self.assertEqual(self.sales_return.status, "posted")  # the return itself is never touched

    def test_the_queue_sends_what_is_due_and_keeps_what_failed(self):
        self.assertEqual(credit_notes_due(), [])  # the original is not accepted yet
        self.accept_original()
        self.assertEqual(credit_notes_due(), [self.sales_return])
        out = StringIO()
        with mock.patch.object(portal, "_http", FakePortal(signer_ok=False)):
            call_command("send_credit_notes", user=self.owner.username, stdout=out)
        self.assertIn("0 sent, 1 still waiting", out.getvalue())
        self.assertEqual(credit_notes_due(), [self.sales_return])  # nothing reached the authority: still queued
        with mock.patch.object(portal, "_http", FakePortal()):
            call_command("send_credit_notes", user=self.owner.username, stdout=out)
        self.assertIn("RET-1: submitted", out.getvalue())
        self.assertEqual(credit_notes_due(), [])
        self.assertEqual(Submission.objects.filter(sales_return=self.sales_return).count(), 1)

    def test_a_return_posts_with_the_authority_down(self):
        self.accept_original()
        line_b = self.posted.lines.get(item=self.b)
        with mock.patch.object(portal, "_http", FakePortal(submit_fail=503)):
            second = create_sales_return("RET-2", TODAY, self.posted.pk, [{"source_line": line_b, "quantity": D("1")}], "مش عاجبه", self.owner)
        self.assertEqual(second.status, "posted")
        self.assertIn(second, credit_notes_due())

    def test_the_screen(self):
        self.client.force_login(self.owner)
        detail = self.client.get(reverse("sales:return_detail", args=[self.sales_return.pk]) + "?lang=ar")
        self.assertContains(detail, "data-credit-note")
        page = self.client.get(reverse("einvoice:credit_note", args=[self.sales_return.pk]) + "?lang=ar")
        self.assertContains(page, "RET-1")
        self.assertContains(page, "data-einvoice-problems")  # the original is not accepted yet
        self.assertNotContains(page, "data-eta-cancel")
        self.accept_original()
        with mock.patch.object(portal, "_http", FakePortal()):
            self.client.post(reverse("einvoice:credit_note", args=[self.sales_return.pk]), {"lang": "ar", "action": "send"})
        self.assertEqual(current_credit_note(self.sales_return).status, "submitted")
        json_file = self.client.get(reverse("einvoice:credit_note", args=[self.sales_return.pk]) + "?format=json")
        self.assertIn('"documentType": "c"', json_file.content.decode())
        invoice_page = self.client.get(reverse("einvoice:sales_document", args=[self.posted.pk]) + "?lang=en")
        self.assertContains(invoice_page, 'data-eta-status="valid"')  # the invoice page still shows the invoice's own sending
