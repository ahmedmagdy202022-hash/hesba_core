from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.decorators import permitted_codes, require_any_permission, require_permission
from permissions.models import RoleCode


PROFIT = "reports.view_profit_report"
SALES = "reports.view_sales_report"
COST = "inventory.view_cost"


@require_permission(PROFIT)
def profit_view(request):
    return HttpResponse("profit")


@require_any_permission(PROFIT, SALES)
def either_view(request):
    return HttpResponse("either")


class RequirePermissionTests(TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def request_as(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def user_with_role(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        return user

    def test_the_owner_is_let_through(self):
        owner = self.user_with_role(RoleCode.OWNER, "owner_tester")
        self.assertEqual(profit_view(self.request_as(owner)).status_code, 200)

    def test_a_cashier_is_refused(self):
        cashier = self.user_with_role(RoleCode.CASHIER, "cashier_tester")
        with self.assertRaises(PermissionDenied):
            profit_view(self.request_as(cashier))

    def test_a_user_with_no_profile_is_refused(self):
        with self.assertRaises(PermissionDenied):
            profit_view(self.request_as(make_user(username="roleless")))

    def test_an_inactive_profile_is_refused(self):
        user = make_user(username="retired")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER), active=False)
        with self.assertRaises(PermissionDenied):
            profit_view(self.request_as(user))

    def test_the_refusal_names_the_permission(self):
        cashier = self.user_with_role(RoleCode.CASHIER, "cashier_named")
        with self.assertRaisesMessage(PermissionDenied, PROFIT):
            profit_view(self.request_as(cashier))

    def test_the_decorator_records_what_it_requires(self):
        self.assertEqual(profit_view.required_permission, PROFIT)

    def test_it_keeps_the_view_name(self):
        self.assertEqual(profit_view.__name__, "profit_view")


class RequireAnyPermissionTests(RequirePermissionTests):
    def test_holding_only_one_of_them_is_enough(self):
        manager = self.user_with_role(RoleCode.MANAGER, "manager_any")

        # A manager sees sales but never profit, so the "any" gate should still
        # open where the strict profit gate would not.
        self.assertEqual(either_view(self.request_as(manager)).status_code, 200)
        with self.assertRaises(PermissionDenied):
            profit_view(self.request_as(manager))

    def test_holding_none_of_them_is_refused(self):
        with self.assertRaises(PermissionDenied):
            either_view(self.request_as(make_user(username="nobody")))

    def test_it_records_what_it_requires(self):
        self.assertEqual(either_view.required_permissions, (PROFIT, SALES))


class PermittedCodesTests(TestCase):
    def user_with_role(self, role_code, username):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        return user

    def test_it_returns_only_what_the_role_holds(self):
        cashier = self.user_with_role(RoleCode.CASHIER, "cashier_codes")

        held = permitted_codes(cashier, [PROFIT, COST, SALES])

        self.assertNotIn(PROFIT, held)
        self.assertNotIn(COST, held)

    def test_the_owner_holds_the_sensitive_ones(self):
        owner = self.user_with_role(RoleCode.OWNER, "owner_codes")

        held = permitted_codes(owner, [PROFIT, COST, SALES])

        self.assertIn(PROFIT, held)
        self.assertIn(COST, held)

    def test_a_roleless_user_holds_nothing(self):
        held = permitted_codes(make_user(username="empty_codes"), [PROFIT, COST, SALES])
        self.assertEqual(held, frozenset())


class SeededRoleMatrixTests(TestCase):
    """Pins the split the dashboard relies on to decide what to show."""

    def user_with_role(self, role_code):
        user = make_user(username=f"matrix_{role_code}")
        make_user_profile(user=user, role=make_seeded_role(role_code))
        return user

    def test_only_the_owner_sees_profit(self):
        from permissions.services import user_has_permission

        for role_code in RoleCode.values:
            if role_code == RoleCode.SUPPORT:
                continue
            with self.subTest(role=role_code):
                holds = user_has_permission(self.user_with_role(role_code), PROFIT)
                self.assertEqual(holds, role_code == RoleCode.OWNER)

    def test_only_the_owner_sees_cost(self):
        from permissions.services import user_has_permission

        for role_code in RoleCode.values:
            if role_code == RoleCode.SUPPORT:
                continue
            with self.subTest(role=role_code):
                holds = user_has_permission(self.user_with_role(role_code), COST)
                self.assertEqual(holds, role_code == RoleCode.OWNER)

    def test_cashbox_finance_is_owner_and_accountant_only(self):
        from permissions.services import user_has_permission

        allowed = {RoleCode.OWNER, RoleCode.ACCOUNTANT}
        for role_code in RoleCode.values:
            if role_code == RoleCode.SUPPORT:
                continue
            with self.subTest(role=role_code):
                holds = user_has_permission(self.user_with_role(role_code), "cashboxes.view_finance")
                self.assertEqual(holds, role_code in allowed)
