from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.management.commands.seed_demo_users import DEMO_USERS
from accounts.models import UserProfile
from permissions.models import Role, RoleCode
from permissions.services import user_has_permission


def seed(**kwargs):
    options = {"verbosity": 0}
    options.update(kwargs)
    call_command("seed_demo_users", **options)


@override_settings(DEBUG=True)
class SeedDemoUsersTests(TestCase):
    """Runs with DEBUG on, which is the only way this command is meant to be used.

    The test runner turns DEBUG off, so without this the guard below would fire
    on every case and hide what is actually being tested.
    """

    def test_it_creates_one_account_per_role(self):
        seed()

        self.assertEqual(UserProfile.objects.count(), len(DEMO_USERS))
        for role_code, username, _ in DEMO_USERS:
            with self.subTest(username=username):
                profile = UserProfile.objects.get(user__username=username)
                self.assertEqual(profile.role.code, role_code)
                self.assertTrue(profile.active)

    def test_the_accounts_can_actually_sign_in(self):
        seed(password="known-password")

        for _, username, _ in DEMO_USERS:
            with self.subTest(username=username):
                self.assertTrue(self.client.login(username=username, password="known-password"))

    def test_the_seeded_roles_carry_their_real_permissions(self):
        seed()
        users = get_user_model().objects

        owner = users.get(username="owner")
        cashier = users.get(username="cashier")

        self.assertTrue(user_has_permission(owner, "reports.view_profit_report"))
        self.assertFalse(user_has_permission(cashier, "reports.view_profit_report"))
        self.assertFalse(user_has_permission(cashier, "inventory.view_cost"))

    def test_none_of_them_is_a_superuser(self):
        seed()

        # A superuser short-circuits every permission check, which would make
        # the role split untestable through the interface.
        for _, username, _ in DEMO_USERS:
            with self.subTest(username=username):
                user = get_user_model().objects.get(username=username)
                self.assertFalse(user.is_superuser)

    def test_it_does_not_seed_a_support_login(self):
        seed()

        self.assertFalse(UserProfile.objects.filter(role__code=RoleCode.SUPPORT).exists())

    def test_running_it_twice_changes_nothing(self):
        seed()
        seed()

        self.assertEqual(UserProfile.objects.count(), len(DEMO_USERS))
        self.assertEqual(get_user_model().objects.filter(username="owner").count(), 1)

    def test_rerunning_it_resets_the_password(self):
        seed(password="first-password")
        seed(password="second-password")

        self.assertTrue(self.client.login(username="owner", password="second-password"))

    def test_it_attaches_a_role_to_a_user_that_already_exists(self):
        get_user_model().objects.create_user(username="cashier", password="x")
        seed()

        self.assertEqual(UserProfile.objects.get(user__username="cashier").role.code, RoleCode.CASHIER)

    @override_settings(DEBUG=False)
    def test_it_refuses_to_run_outside_debug(self):
        with self.assertRaisesMessage(CommandError, "Refusing to seed accounts"):
            seed()

        self.assertEqual(UserProfile.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_force_overrides_the_refusal(self):
        seed(force=True)

        self.assertEqual(UserProfile.objects.count(), len(DEMO_USERS))

    def test_it_explains_itself_when_a_role_is_missing(self):
        Role.objects.filter(code=RoleCode.CASHIER).delete()

        with self.assertRaisesMessage(CommandError, "cashier role is missing"):
            seed()


@override_settings(DEBUG=True)
class UserAdminProfileInlineTests(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser(
            username="admin_inline_tester", email="", password="admin-tests-only"
        )
        self.client.force_login(self.admin)

    def test_the_user_page_carries_the_hesba_role(self):
        seed()
        owner = get_user_model().objects.get(username="owner")

        response = self.client.get(f"/admin/auth/user/{owner.pk}/change/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hesba profile")

    def test_the_user_list_shows_each_role(self):
        seed()

        response = self.client.get("/admin/auth/user/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hesba role")
