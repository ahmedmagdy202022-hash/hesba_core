"""R2-8: the books in four plain answers for the owner; the accountant's tools set apart."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile
from inventory.models import StockAdjustmentDirection
from inventory.services import adjust_stock
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice, SalesLine
from sales.services import post_sales_invoice


class SimpleBooksTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client()
        cls.owner = make_user(username="gl_simple_owner")
        make_user_profile(user=cls.owner, role=make_seeded_role(RoleCode.OWNER))
        location = make_location(location_code="GL-L", is_selling_location=True)
        cashbox = make_cashbox(cashbox_code="GL-K", opening_balance=D("1000"))
        cls.customer = make_customer(customer_code="GL-C", name="عميل عليه فلوس")
        make_supplier(supplier_code="GL-S", name="مورد ليه فلوس", opening_balance=D("300"))
        item = make_item(item_code="GL-I", default_sale_price="100")
        today = timezone.localdate()
        adjust_stock("GL-OPEN", today, item, location, StockAdjustmentDirection.IN, D("5"), "opening", cls.owner, unit_cost=D("60"))
        invoice = SalesInvoice.objects.create(invoice_number="GL-SI-1", invoice_date=today, customer=cls.customer, selling_location=location,
                                              cashbox=cashbox, subtotal=D("200"), discount_amount=0, tax_amount=0, total_amount=D("200"),
                                              paid_now=D("50"), remaining_due=D("150"), payment_status="partial", created_by=cls.owner)
        SalesLine.objects.create(invoice=invoice, line_number=1, item=item, quantity=D("2"), unit_sale_price=D("100"), line_discount_amount=0, line_total_amount=D("200"))
        post_sales_invoice(invoice.id, user=cls.owner)

    def test_four_plain_answers(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("ledger:summary") + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        for answer in ("profit", "cash", "owed", "owe"):
            self.assertContains(page, f'data-answer="{answer}"')
        self.assertContains(page, "كسبت كام الشهر ده؟")
        self.assertEqual(page.context["income"]["gross_profit"], D("80.00"))   # 200 sales - 2 x 60 cost
        self.assertEqual(page.context["owed"]["total"], D("150.00"))
        self.assertContains(page, "عميل عليه فلوس")
        self.assertContains(page, "مورد ليه فلوس")
        self.assertContains(page, "data-accountant-tab")  # journal, trial balance... still one click away
        self.assertContains(self.client.get(reverse("ledger:summary") + "?lang=en"), "Where is the money?")
        # The opening stock entered as a count gain: other income, shown so the parts add up to the net profit.
        context = page.context
        self.assertEqual(context["sales"] - context["cost"] - context["expenses"] + context["other"], context["income"]["net_profit"])
        self.assertContains(page, "مكاسب وخسائر تانية")

    def test_party_balances_follow_the_requested_entity(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="GL-BR", name_ar="فرع", active=True)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("ledger:summary") + f"?lang=ar&entity={branch.pk}")
        self.assertEqual(page.context["entity"], branch)
        self.assertEqual(page.context["owed"]["total"], 0)  # the sale belongs to the main entity

    def test_a_cashier_does_not_see_the_books(self):
        cashier = make_user(username="gl_simple_cashier")
        make_user_profile(user=cashier, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("ledger:summary")).status_code, 403)
