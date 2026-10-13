"""FB-STAFF: the team page reads like a team page: who, what they earn this month, and one click to their work."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import posted_invoice_ready
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import post_sales_invoice

from .services import save_employee
from .tests import person


class StaffScreenTests(TestCase):
    def setUp(self):
        prepared_client(activity="commercial", sub_activity="retail",
                        modules="customers,items_services,sales_operations,inventory,cashboxes,reports,employees_technicians")
        self.owner = person(RoleCode.OWNER, "fs_owner")
        self.client.force_login(self.owner)
        self.rep = save_employee({"name": "سمير المندوب", "title": "مندوب", "commission_percent": "3", "phone": "01001234567"}, self.owner)
        self.tech = save_employee({"name": "منى الفنية", "title": "فنية", "commission_percent": "2.50"}, self.owner)
        self.left = save_employee({"name": "قديم", "active": False}, self.owner)

    def test_the_list_shows_plain_percentages_links_and_filters(self):
        page = self.client.get(reverse("staff:list"))
        self.assertContains(page, '<td dir="ltr" data-commission>3%</td>', html=False)
        self.assertContains(page, "2.5%")
        self.assertNotContains(page, "3,00")
        self.assertContains(page, reverse("staff:detail", args=[self.rep.pk]))
        self.assertContains(page, "data-employee-link")
        self.assertNotContains(page, "قديم")  # active only by default
        self.assertContains(self.client.get(reverse("staff:list"), {"status": "off"}), "قديم")
        found = self.client.get(reverse("staff:list"), {"q": "فنية"})
        self.assertContains(found, "منى الفنية")
        self.assertNotContains(found, "سمير المندوب")
        self.assertContains(page, "data-employee-new")  # the form folds away under "+ new employee"
        self.assertNotContains(page, 'data-employee-new open')

    def test_a_reps_month_shows_on_their_page_and_in_the_list(self):
        invoice, *_ = posted_invoice_ready(paid_now="60.00")
        SalesInvoice.objects.filter(pk=invoice.pk).update(salesperson=self.rep, invoice_date=timezone.localdate())
        post_sales_invoice(invoice.pk, user=self.owner)
        detail = self.client.get(reverse("staff:detail", args=[self.rep.pk]))
        self.assertContains(detail, 'data-work="rep"')
        self.assertContains(detail, "60.00")
        self.assertContains(detail, "1.80")  # 3% of 60
        listing = self.client.get(reverse("staff:list"))
        self.assertContains(listing, "1.80")
        quiet = self.client.get(reverse("staff:detail", args=[self.tech.pk]))
        self.assertContains(quiet, "data-work-none")
        self.assertContains(quiet, "data-employee-edit")
