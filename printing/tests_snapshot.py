"""PRINT-003: reprinting a posted document shows the company details of the day it was posted."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import (
    DEFAULT_DATE, add_sales_line, make_cashbox, make_customer, make_draft_sales_invoice, make_location, make_seeded_role, make_user,
    make_user_profile, posted_invoice_ready, recalculate_invoice_totals,
)
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.services import post_sales_invoice, record_customer_payment

from .company import save_company_details
from .models import DocumentCompanySnapshot


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class SnapshotTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,pdf_printing,reports")
        Period.objects.create(period_code="SN", name="snap", start_date=DEFAULT_DATE.replace(day=1), end_date=DEFAULT_DATE.replace(day=28))
        self.owner = person(RoleCode.OWNER, "snap_owner")
        self.client.force_login(self.owner)
        save_company_details({"company.address": "12 Old Street", "company.tax_number": "111-111-111"}, self.owner)

    def test_old_invoice_keeps_the_old_address_new_one_gets_the_new(self):
        old, *_ = posted_invoice_ready()
        post_sales_invoice(old.pk, self.owner)
        self.assertTrue(DocumentCompanySnapshot.objects.filter(kind="sales_invoice", object_id=old.pk).exists())
        save_company_details({"company.address": "99 New Avenue", "company.tax_number": "222-222-222"}, self.owner)
        page = self.client.get(reverse("printing:sales_invoice", args=[old.pk]))
        self.assertContains(page, "12 Old Street")
        self.assertContains(page, "111-111-111")
        self.assertNotContains(page, "99 New Avenue")
        new = make_draft_sales_invoice(invoice_number="SI-SNAP-2", location=make_location(), cashbox=make_cashbox())
        add_sales_line(new, old.lines.get().item, quantity=1, unit_sale_price="30.00")
        recalculate_invoice_totals(new, paid_now="0.00")
        post_sales_invoice(new.pk, self.owner)
        page = self.client.get(reverse("printing:sales_invoice", args=[new.pk]))
        self.assertContains(page, "99 New Avenue")
        self.assertContains(page, "222-222-222")

    def test_drafts_follow_current_details_and_payments_are_frozen_too(self):
        draft, *_ = posted_invoice_ready()
        self.assertFalse(DocumentCompanySnapshot.objects.filter(kind="sales_invoice", object_id=draft.pk).exists())
        save_company_details({"company.address": "99 New Avenue"}, self.owner)
        self.assertContains(self.client.get(reverse("printing:sales_invoice", args=[draft.pk])), "99 New Avenue")
        payment = record_customer_payment("CP-SN", DEFAULT_DATE, make_customer(customer_code="SN"), make_cashbox(cashbox_code="SN-CASH"), D("10.00"), self.owner)
        save_company_details({"company.address": "7 Third Road"}, self.owner)
        voucher = self.client.get(reverse("printing:customer_payment", args=[payment.pk]))
        self.assertContains(voucher, "99 New Avenue")
        self.assertEqual(DocumentCompanySnapshot.objects.get(kind="customer_payment", object_id=payment.pk).data["address"], "99 New Avenue")
