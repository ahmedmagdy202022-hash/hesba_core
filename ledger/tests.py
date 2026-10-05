"""GL-001: the chart of accounts."""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from audit.models import AuditLog
from expenses.models import ExpenseCategory
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import chart, services
from .models import Account


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class ChartDataTests(SimpleTestCase):
    def test_every_activity_chart_is_a_complete_tree_with_unique_controls(self):
        from settings_core.setup_catalog import ACTIVITY_LABELS

        for activity in list(ACTIVITY_LABELS) + [""]:
            rows = chart.rows_for(activity)
            codes = {row[0] for row in rows}
            controls = [row[4] for row in rows if row[4]]
            with self.subTest(activity=activity):
                self.assertEqual([r[0] for r in rows if chart.parent_code(r[0]) and chart.parent_code(r[0]) not in codes], [])
                self.assertEqual(len(controls), len(set(controls)))
                for needed in ("cash", "receivable", "payable", "sales", "cogs", "vat_in", "vat_out", "opening_equity", "general_expense"):
                    self.assertIn(needed, controls)
                inventory_controls = {"inventory", "raw_materials", "finished_goods"} & set(controls)
                self.assertTrue(inventory_controls)


class EnsureChartTests(TestCase):
    def test_seeds_once_links_parents_and_expense_categories(self):
        prepared_client("manufacturing", "food", "customers,suppliers,items_services,sales_operations,cashboxes,reports,manufacturing")
        count = services.ensure_chart()
        self.assertEqual(services.ensure_chart(), count)  # idempotent
        self.assertEqual(Account.objects.get(code="110502").parent.code, "1105")
        self.assertFalse(Account.objects.get(code="1105").is_postable)
        self.assertEqual(services.account_for("wip").code, "110502")
        self.assertEqual(ExpenseCategory.objects.get(code="rent").ledger_link.account.code, "5202")
        for account in Account.objects.filter(is_postable=True):
            self.assertIsNotNone(account.parent, account.code)
        self.assertTrue(Account.objects.get(code="1101").debit_normal)
        self.assertFalse(Account.objects.get(code="2101").debit_normal)
        self.assertFalse(Account.objects.get(code="1202").debit_normal)  # contra asset

    def test_activity_names_revenue_its_own_way(self):
        prepared_client("medical", "clinic", "customers,items_services,sales_operations,cashboxes,reports")
        services.ensure_chart()
        self.assertEqual(Account.objects.get(code="4101").name_ar, "إيراد الكشوفات والخدمات الطبية")


class ScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "gl_owner")
        self.client.force_login(self.owner)

    def test_owner_sees_the_tree_and_adds_a_bank_account(self):
        page = self.client.get(reverse("ledger:accounts"))
        self.assertContains(page, 'data-account="1101"')
        self.assertContains(page, "النقدية بالخزن")
        banks = Account.objects.get(code="1102")
        # A leaf cannot take children until it is made a group.
        with self.assertRaises(ValidationError):
            services.save_account({"parent": banks, "code": "110201", "name_ar": "بنك مصر"}, self.owner)
        operating = Account.objects.get(code="52")
        response = self.client.post(reverse("ledger:account_new"), {"parent": operating.pk, "code": "5213", "name_ar": "عمولات بيع", "active": "on"})
        self.assertRedirects(response, "/accounting/accounts/?lang=ar", fetch_redirect_response=False)
        new = Account.objects.get(code="5213")
        self.assertEqual((new.account_type, new.is_postable, new.parent), ("expense", True, operating))
        self.assertTrue(AuditLog.objects.filter(action="create_account").exists())

    def test_core_accounts_can_only_be_renamed(self):
        services.ensure_chart()
        cash = Account.objects.get(code="1101")
        self.client.post(reverse("ledger:account_edit", args=[cash.pk]), {"name_ar": "الخزينة", "code": "9999", "active": ""})
        cash.refresh_from_db()
        self.assertEqual((cash.code, cash.name_ar, cash.active, cash.control), ("1101", "الخزينة", True, "cash"))

    def test_rules_and_permissions(self):
        services.ensure_chart()
        group = Account.objects.get(code="52")
        for bad in ("abc", "41", "52"):
            with self.subTest(code=bad), self.assertRaises(ValidationError):
                services.save_account({"parent": group, "code": bad, "name_ar": "x"}, self.owner)
        self.client.force_login(person(RoleCode.CASHIER, "gl_cashier"))
        self.assertEqual(self.client.get(reverse("ledger:accounts")).status_code, 403)
        self.client.force_login(person(RoleCode.MANAGER, "gl_manager"))
        self.assertEqual(self.client.get(reverse("ledger:accounts")).status_code, 200)
        self.assertEqual(self.client.post(reverse("ledger:account_new"), {"parent": group.pk, "code": "5290", "name_ar": "x"}).status_code, 403)
