"""DIGEST-001: one day's summary matches the reports and shows only what the viewer may see."""

from datetime import timedelta
from decimal import Decimal as D
from urllib.parse import unquote

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from printing.company import company_details
from sales.services import record_customer_payment

from .digest import daily_digest, digest_text
from .selectors import cashbox_report, profit_totals
from .tests_dashboard import prepared_client, sell


TODAY = timezone.localdate()
YESTERDAY = TODAY - timedelta(days=1)


def freeze_today(case, day):
    """TEST-001: pin timezone.localdate() to ``day`` for one test, so a run that
    crosses midnight (Cairo time) cannot move "today" halfway through."""

    from unittest import mock

    real = timezone.localdate

    def pinned(value=None, timezone=None):
        return day if value is None else real(value, timezone)

    patcher = mock.patch("django.utils.timezone.localdate", side_effect=pinned)
    patcher.start()
    case.addCleanup(patcher.stop)


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class DigestTests(TestCase):
    def setUp(self):
        freeze_today(self, TODAY)
        prepared_client()
        Period.objects.create(period_code="D", name="d", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "digest_owner")
        self.location = make_location()
        self.cashbox = make_cashbox()
        self.item = make_item(item_code="TEA", item_name="Tea", default_sale_price=D("50.00"))
        stock_in(self.item, self.location, 100, "30.00", movement_date=TODAY - timedelta(days=10))
        recalculate_item_average_cost(self.item)
        sell(self.item, self.location, self.cashbox, 4, "50.00", "150.00", self.owner, number="SI-D1", when=YESTERDAY)   # 200, 50 on credit
        sell(self.item, self.location, self.cashbox, 2, "50.00", "100.00", self.owner, number="SI-D2", when=YESTERDAY)
        sell(self.item, self.location, self.cashbox, 1, "50.00", "50.00", self.owner, number="SI-D3", when=TODAY)       # another day
        record_customer_payment("CP-D", YESTERDAY, make_customer(customer_code="C-D"), self.cashbox, D("20.00"), self.owner)

    def test_figures_match_the_reports(self):
        data = daily_digest(self.owner, YESTERDAY)
        self.assertEqual((data["sales"]["count"], data["sales"]["total"], data["sales"]["cash"], data["sales"]["credit"]), (2, D("300.00"), D("250.00"), D("50.00")))
        self.assertEqual(data["sales"]["top"][0]["item__item_name"], "Tea")
        self.assertEqual(data["profit"]["gross"], profit_totals(YESTERDAY, YESTERDAY)["profit"])
        self.assertEqual(data["profit"]["gross"], D("120.00"))  # 6 x (50 - 30)
        report = cashbox_report(date_from=YESTERDAY, date_to=YESTERDAY)
        self.assertEqual((data["cash"]["in"], data["cash"]["balance"]), (sum(r["cash_in"] for r in report), sum(r["balance"] for r in report)))
        self.assertEqual(data["cash"]["in"], D("270.00"))  # 250 at the till + 20 collected
        self.assertEqual(data["customers"]["collected"], D("20.00"))
        text = digest_text(data, company_details())
        self.assertIn("300.00", text)
        self.assertIn("مجمل الربح: 120.00", text)

    def test_the_page_defaults_to_yesterday_and_sends_on_whatsapp(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("report_hub")), reverse("reports:daily"))
        page = self.client.get(reverse("reports:daily"))
        self.assertEqual(page.context["day"], YESTERDAY)
        self.assertContains(page, "data-digest-sales")
        link = page.context["whatsapp"]
        self.assertTrue(link.startswith("https://wa.me/?text="))
        self.assertIn("300.00", unquote(link))
        future = self.client.get(reverse("reports:daily"), {"date": (TODAY + timedelta(days=9)).isoformat()})
        self.assertEqual(future.context["day"], TODAY)
        self.assertContains(self.client.get(reverse("reports:daily"), {"lang": "en"}), "Daily summary")

    def test_sections_follow_permissions(self):
        cashier = person(RoleCode.CASHIER, "digest_cashier")
        data = daily_digest(cashier, YESTERDAY)
        self.assertNotIn("profit", data)
        self.assertNotIn("sales", data)  # a cashier only sees their own sales, never the shop total
        keeper = person(RoleCode.STOCK_KEEPER, "digest_keeper")
        self.client.force_login(keeper)
        response = self.client.get(reverse("reports:daily"))
        self.assertIn(response.status_code, (200, 403))
        if response.status_code == 200:
            self.assertNotContains(response, "data-digest-profit")
