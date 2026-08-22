from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import UserProfile
from permissions.models import Role, RoleCode
from permissions.services import user_has_permission
from settings_core.models import ClientProfile


def bootstrap(**kwargs):
    options = {"password": "bootstrap-tests-only", "verbosity": 0}
    options.update(kwargs)
    call_command("bootstrap_client", **options)


class ClientProfileSingletonTests(TestCase):
    def test_get_active_is_none_before_bootstrap(self):
        self.assertIsNone(ClientProfile.get_active())

    def test_get_active_returns_the_installation_profile(self):
        profile = ClientProfile.objects.create(
            client_code="C-1", legal_name="Legal", display_name="Display"
        )
        self.assertEqual(ClientProfile.get_active(), profile)

    def test_a_second_profile_is_refused(self):
        ClientProfile.objects.create(client_code="C-1", legal_name="Legal", display_name="Display")
        with self.assertRaises(ValidationError):
            ClientProfile.objects.create(client_code="C-2", legal_name="Other", display_name="Other")

    def test_updating_the_existing_profile_still_works(self):
        profile = ClientProfile.objects.create(
            client_code="C-1", legal_name="Legal", display_name="Display"
        )
        profile.display_name = "Renamed"
        profile.save()
        self.assertEqual(ClientProfile.get_active().display_name, "Renamed")

    def test_get_active_skips_an_inactive_profile(self):
        ClientProfile.objects.create(
            client_code="C-1", legal_name="Legal", display_name="Display", is_active=False
        )
        self.assertIsNone(ClientProfile.get_active())


class BootstrapClientTests(TestCase):
    def test_it_creates_profile_user_and_role_link(self):
        bootstrap(username="owner", client_code="DEMO", display_name="Demo Store")

        profile = ClientProfile.get_active()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.client_code, "DEMO")
        self.assertEqual(profile.display_name, "Demo Store")

        user = get_user_model().objects.get(username="owner")
        self.assertTrue(user.is_superuser)

        link = UserProfile.objects.get(user=user)
        self.assertEqual(link.role.code, RoleCode.OWNER)
        self.assertTrue(link.active)

    def test_legal_name_falls_back_to_display_name(self):
        bootstrap(display_name="Demo Store")
        self.assertEqual(ClientProfile.get_active().legal_name, "Demo Store")

    def test_the_owner_can_use_its_role_permissions(self):
        bootstrap(username="owner")
        owner = get_user_model().objects.get(username="owner")

        # A superuser short-circuits the permission check, so assert the role link
        # itself resolves for a plain user holding the same role.
        plain = get_user_model().objects.create_user(username="plain", password="x")
        UserProfile.objects.create(user=plain, role=owner.hesba_profile.role, active=True)
        self.assertTrue(user_has_permission(plain, "reports.view_profit_report"))

    def test_running_it_twice_changes_nothing(self):
        bootstrap(username="owner")
        bootstrap(username="owner")

        self.assertEqual(ClientProfile.objects.count(), 1)
        self.assertEqual(get_user_model().objects.filter(username="owner").count(), 1)
        self.assertEqual(UserProfile.objects.count(), 1)

    def test_it_reuses_an_existing_profile_rather_than_adding_one(self):
        ClientProfile.objects.create(client_code="EXISTING", legal_name="L", display_name="D")
        bootstrap(username="owner", client_code="IGNORED")

        self.assertEqual(ClientProfile.objects.count(), 1)
        self.assertEqual(ClientProfile.get_active().client_code, "EXISTING")

    def test_it_links_a_role_to_a_user_that_already_exists(self):
        get_user_model().objects.create_user(username="owner", password="x")
        bootstrap(username="owner")

        link = UserProfile.objects.get(user__username="owner")
        self.assertEqual(link.role.code, RoleCode.OWNER)

    def test_it_explains_itself_when_the_owner_role_is_missing(self):
        Role.objects.filter(code=RoleCode.OWNER).delete()
        with self.assertRaisesMessage(CommandError, "owner role is missing"):
            bootstrap(username="owner")

    def test_it_reads_the_password_from_the_environment(self):
        with self.settings():
            import os

            os.environ["DJANGO_SUPERUSER_PASSWORD"] = "from-the-environment"
            try:
                call_command("bootstrap_client", username="owner", verbosity=0)
            finally:
                del os.environ["DJANGO_SUPERUSER_PASSWORD"]

        user = get_user_model().objects.get(username="owner")
        self.assertTrue(user.check_password("from-the-environment"))
