"""PRINT-002: the owner's logo on printed documents, checked by file signature."""

import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from closing.models import Period
from hesba_testing.factories import DEFAULT_DATE, make_seeded_role, make_user, make_user_profile, posted_invoice_ready
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.services import post_sales_invoice

from .company import company_details


# A real 1x1 transparent PNG.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
COMPANY = "/settings/company/"


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class LogoTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,pdf_printing,reports")
        Period.objects.create(period_code="LG", name="logo", start_date=DEFAULT_DATE.replace(day=1), end_date=DEFAULT_DATE.replace(day=28))
        self.owner = person(RoleCode.OWNER, "logo_owner")
        self.client.force_login(self.owner)

    def upload(self, content, name="logo.png"):
        return self.client.post(COMPANY, {"action": "logo", "logo": SimpleUploadedFile(name, content)}, follow=True)

    def test_uploaded_logo_prints_on_a4_and_receipt_and_can_be_removed(self):
        self.assertContains(self.client.get(COMPANY), "مفيش لوجو لسه")
        self.upload(PNG)
        self.assertTrue(company_details()["logo"].startswith("data:image/png;base64,"))
        self.assertContains(self.client.get(COMPANY), "data-logo-preview")
        invoice, *_ = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(invoice.pk, self.owner)
        a4 = self.client.get(reverse("printing:sales_invoice", args=[invoice.pk]))
        self.assertContains(a4, "data-company-logo")
        receipt = self.client.get(reverse("printing:sales_invoice", args=[invoice.pk]), {"format": "receipt"})
        self.assertContains(receipt, "data-company-logo")
        self.client.post(COMPANY, {"action": "remove_logo"})
        self.assertEqual(company_details()["logo"], "")
        self.assertNotContains(self.client.get(reverse("printing:sales_invoice", args=[invoice.pk])), "data-company-logo")
        self.assertEqual(set(AuditLog.objects.filter(action__in=["update_company_logo", "remove_company_logo"]).values_list("action", flat=True)), {"update_company_logo", "remove_company_logo"})

    def test_only_real_images_under_the_size_limit_are_accepted(self):
        self.assertContains(self.upload(b"<svg onload=alert(1)></svg>", "logo.png"), "PNG أو JPG أو WebP")
        self.assertContains(self.upload(b"\x89PNG\r\n\x1a\n" + b"0" * (301 * 1024)), "أقل من 300 كيلوبايت")
        self.assertContains(self.client.post(COMPANY, {"action": "logo"}, follow=True), "اختار صورة اللوجو الأول")
        self.assertEqual(company_details()["logo"], "")
        self.upload(b"\xff\xd8\xff\xe0" + b"jpeg-body")
        self.assertTrue(company_details()["logo"].startswith("data:image/jpeg;base64,"))

    def test_hesba_mark_can_be_hidden_and_only_managers_change_things(self):
        invoice, *_ = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(invoice.pk, self.owner)
        url = reverse("printing:sales_invoice", args=[invoice.pk])
        self.assertContains(self.client.get(url), "data-hesba-brand")
        self.client.post(COMPANY, {"action": "brand"})
        self.assertNotContains(self.client.get(url), "data-hesba-brand")
        self.client.post(COMPANY, {"action": "brand", "show_hesba_brand": "1"})
        self.assertContains(self.client.get(url), "data-hesba-brand")
        self.client.force_login(person(RoleCode.CASHIER, "logo_cashier"))
        response = self.client.post(COMPANY, {"action": "logo", "logo": SimpleUploadedFile("l.png", PNG)})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(company_details()["logo"], "")
