"""SHARE-001: signed links a customer opens without an account; forged or expired ones show nothing."""

from datetime import timedelta
from unittest.mock import patch

from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import DEFAULT_DATE, make_seeded_role, make_user, make_user_profile, posted_invoice_ready
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.services import cancel_posted_sales_invoice, post_sales_invoice

from .share import SALT, make_token, read_token


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class ShareTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,pdf_printing,reports")
        Period.objects.create(period_code="SH", name="share", start_date=DEFAULT_DATE.replace(day=1), end_date=DEFAULT_DATE.replace(day=28))
        self.owner = person(RoleCode.OWNER, "share_owner")
        self.invoice, *_ = posted_invoice_ready(paid_now="20.00")
        post_sales_invoice(self.invoice.pk, self.owner)
        self.invoice.customer.phone = "01001234567"
        self.invoice.customer.save()

    def url(self, kind, pk):
        return reverse("share_document", args=[make_token(kind, pk)])

    def test_the_customer_opens_the_invoice_without_signing_in(self):
        page = self.client.get(self.url("sales_invoice", self.invoice.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.invoice.invoice_number)
        self.assertContains(page, "60.00")
        self.assertNotContains(page, "تكلفة")
        self.assertNotContains(page, "طُبع بواسطة")
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", page.get("Cache-Control", "no-store"))

    def test_forged_altered_and_expired_links_show_nothing(self):
        token = make_token("sales_invoice", self.invoice.pk)
        self.assertEqual(read_token(token), ("sales_invoice", self.invoice.pk))
        forged = signing.dumps({"k": "sales_invoice", "id": self.invoice.pk}, salt="other-salt", compress=True)
        for bad in (token[:-2] + "xx", forged, "nonsense", signing.dumps({"k": "purchase_invoice", "id": 1}, salt=SALT)):
            with self.subTest(bad=bad[:20]):
                response = self.client.get(reverse("share_document", args=[bad]))
                self.assertEqual(response.status_code, 404)
                self.assertContains(response, "data-share-expired", status_code=404)
                self.assertNotContains(response, self.invoice.invoice_number, status_code=404)
        with override_settings(SHARE_LINK_DAYS=0):
            with patch("django.core.signing.time.time", return_value=__import__("time").time() + 5):
                self.assertIsNone(read_token(token))

    def test_a_cancelled_invoice_shows_its_watermark_and_the_statement_can_be_shared(self):
        link = self.url("sales_invoice", self.invoice.pk)
        cancel_posted_sales_invoice(self.invoice.pk, self.owner, "mistake")
        self.assertContains(self.client.get(link), "pr-watermark--status")
        statement = self.client.get(self.url("customer_statement", self.invoice.customer.pk))
        self.assertContains(statement, "data-statement-closing")
        self.assertNotContains(statement, "طُبع")

    def test_detail_and_card_offer_whatsapp_with_the_link(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("sales:detail", args=[self.invoice.pk]))
        self.assertContains(page, "data-share-whatsapp")
        self.assertContains(page, "https://wa.me/201001234567?text=")
        self.assertContains(page, "data-copy-link=\"http://testserver/share/")
        card = self.client.get(reverse("parties:card", args=["customer", self.invoice.customer.pk]))
        self.assertContains(card, "data-share-statement")
