"""ASSET-001: straight-line monthly depreciation, disposal, and the profit report line."""

from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxOperation
from cashboxes.services import get_cashbox_balance
from closing.models import Period
from cashboxes.models import CashboxDirection
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile

from .models import FixedAsset
from .services import accumulated, book_value, cancel_asset, charged_months, depreciation_between, dispose_asset, disposal_result, monthly_amounts, register_asset, schedule


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class AssetSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        set_capability_enabled(self.profile, "fixed_assets", True)
        Period.objects.create(period_code="A", name="a", start_date=date(2025, 1, 1), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "asset_owner")
        self.cashbox = make_cashbox(cashbox_code="MAIN-CASH", is_default=True)
        make_cashbox_movement(self.cashbox, CashboxDirection.IN, D("100000.00"), movement_date=date(2025, 1, 1))

    def van(self, **extra):
        values = dict(name="Delivery van", category="vehicles", in_service_on=date(2025, 3, 15), cost=D("120000.00"), salvage_value=D("12000.00"), useful_months=60, user=self.owner)
        values.update(extra)
        return register_asset(**values)


class DepreciationTests(AssetSetup):
    def test_straight_line_months_to_the_piastre(self):
        asset = self.van()
        self.assertEqual(asset.code, "FA-00001")
        amounts = monthly_amounts(asset)
        self.assertEqual((len(amounts), amounts[0], sum(amounts)), (60, D("1800.00"), D("108000.00")))
        odd = self.van(name="Laptop", cost=D("1000.00"), salvage_value=D("0"), useful_months=3, in_service_on=date(2025, 1, 31))
        self.assertEqual(monthly_amounts(odd), [D("333.33"), D("333.33"), D("333.34")])
        self.assertEqual([month for month, _ in charged_months(odd)], [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)])

    def test_accumulated_book_value_and_report_windows(self):
        asset = self.van()
        self.assertEqual(accumulated(asset, date(2025, 3, 31)), D("1800.00"))  # full month from the month in service
        self.assertEqual(book_value(asset, date(2025, 12, 31)), D("120000.00") - D("1800.00") * 10)
        self.assertEqual(depreciation_between(date(2025, 4, 1), date(2025, 6, 30)), D("5400.00"))
        self.assertEqual(depreciation_between(date(2025, 4, 2), date(2025, 4, 30)), D("0.00"))  # charged on the 1st
        self.assertEqual(book_value(asset, date(2035, 1, 1)), D("12000.00"))  # never below salvage
        rows = schedule(asset, date(2025, 5, 10))
        self.assertEqual((rows[2]["month"], rows[2]["accumulated"], rows[2]["charged"], rows[3]["charged"]), (date(2025, 5, 1), D("5400.00"), True, False))

    def test_disposal_stops_depreciation_and_records_the_result(self):
        asset = self.van()
        balance = get_cashbox_balance(self.cashbox)
        dispose_asset(asset, disposed_on=date(2025, 9, 20), proceeds=D("100000.00"), cashbox=self.cashbox, user=self.owner)
        asset.refresh_from_db()
        # March..August = 6 months charged; none in September, the month of sale.
        self.assertEqual(accumulated(asset, date(2030, 1, 1)), D("10800.00"))
        self.assertEqual(disposal_result(asset), D("100000.00") - (D("120000.00") - D("10800.00")))  # loss of 9,200
        self.assertEqual(get_cashbox_balance(self.cashbox) - balance, D("100000.00"))
        with self.assertRaises(ValidationError):
            dispose_asset(asset, disposed_on=date(2025, 10, 1), proceeds=D("1"), user=self.owner)

    def test_paid_from_a_cashbox_and_cancelled_by_mistake(self):
        before = get_cashbox_balance(self.cashbox)
        asset = self.van(cost=D("50000.00"), salvage_value=D("0"), cashbox=self.cashbox, in_service_on=TODAY)
        self.assertEqual(before - get_cashbox_balance(self.cashbox), D("50000.00"))
        self.assertEqual(CashboxOperation.objects.get(reference_number=f"{asset.code}-BUY").amount, D("50000.00"))
        with self.assertRaises(ValidationError):
            self.van(cost=D("900000.00"), cashbox=self.cashbox, in_service_on=TODAY)  # the cashbox cannot go negative
        cancel_asset(asset, self.owner, "typed twice")
        asset.refresh_from_db()
        self.assertEqual((asset.status, charged_months(asset)), ("cancelled", []))
        self.assertEqual(get_cashbox_balance(self.cashbox), before)
        self.assertEqual(set(AuditLog.objects.filter(module="fixed_assets").values_list("action", flat=True)), {"register_asset", "cancel_asset"})

    def test_refusals(self):
        for extra, message in (({"name": " "}, "اسم الأصل"), ({"cost": D("0")}, "أكبر من صفر"), ({"salvage_value": D("120000.00")}, "قيمة الخردة"), ({"useful_months": 0}, "العمر الإنتاجي")):
            with self.subTest(extra=extra):
                with self.assertRaisesMessage(ValidationError, message):
                    self.van(**extra)
        self.assertFalse(FixedAsset.objects.exists())


class ScreenTests(AssetSetup):
    def test_profit_report_shows_depreciation_under_net_profit(self):
        self.van()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("reports:profit"), {"date_from": "2025-04-01", "date_to": "2025-06-30"})
        totals = page.context["totals"]
        self.assertEqual((totals["depreciation"], totals["net_after_depreciation"]), (D("5400.00"), totals["net_profit"] - D("5400.00")))
        self.assertContains(page, "data-net-after-depreciation")
        set_capability_enabled(self.profile, "fixed_assets", False)
        self.assertNotContains(self.client.get(reverse("reports:profit")), "data-profit-depreciation")

    def test_register_view_dispose_from_the_screens(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("expenses:list")), reverse("fixed_assets:list"))
        response = self.client.post(reverse("fixed_assets:list"), {"name": "Fridge", "category": "equipment", "in_service_on": "2025-01-10", "cost": "36,000", "salvage_value": "", "useful_months": "36", "cashbox": ""})
        asset = FixedAsset.objects.get()
        self.assertRedirects(response, f"{reverse('fixed_assets:detail', args=[asset.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertEqual((asset.cost, asset.salvage_value, asset.payment_operation), (D("36000.00"), D("0.00"), None))
        detail = self.client.get(reverse("fixed_assets:detail", args=[asset.pk]))
        self.assertContains(detail, 'data-dep-row="', count=36)
        self.assertContains(self.client.get(reverse("fixed_assets:list"), {"lang": "en"}), f'data-asset-row="{asset.code}"')
        bad = self.client.post(reverse("fixed_assets:list"), {"name": "X", "in_service_on": "bad", "cost": "1", "useful_months": "1"}, follow=True)
        self.assertContains(bad, "بيانات غير صحيحة")
        self.client.post(reverse("fixed_assets:detail", args=[asset.pk]), {"action": "dispose", "disposed_on": "2025-07-05", "proceeds": "20000", "cashbox": ""})
        asset.refresh_from_db()
        self.assertEqual((asset.status, disposal_result(asset)), ("disposed", D("20000.00") - (D("36000.00") - D("6000.00"))))

    def test_permissions_and_capability_gate(self):
        self.client.force_login(person(RoleCode.CASHIER, "asset_cashier"))
        self.assertEqual(self.client.post(reverse("fixed_assets:list"), {"name": "X", "in_service_on": "2025-01-01", "cost": "1", "useful_months": "1"}).status_code, 403)
        self.assertFalse(FixedAsset.objects.exists())
        set_capability_enabled(ClientProfile.get_active(), "fixed_assets", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("fixed_assets:list")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("expenses:list")), reverse("fixed_assets:list"))
