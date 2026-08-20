from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from accounts.models import UserProfile
from hesba_testing.factories import grant, make_permission, make_role, make_user
from permissions.services import user_has_permission


PERMISSION_CODE = "sales.post_invoice"


class UserHasPermissionTests(TestCase):
    def setUp(self):
        super().setUp()
        self.permission = make_permission(code=PERMISSION_CODE)
        self.role = make_role()
        self.user = make_user()

    def attach_profile(self, **kwargs):
        defaults = {"user": self.user, "role": self.role, "active": True}
        defaults.update(kwargs)
        return UserProfile.objects.create(**defaults)

    def test_none_user_is_denied(self):
        self.assertFalse(user_has_permission(None, PERMISSION_CODE))

    def test_anonymous_user_is_denied(self):
        self.assertFalse(user_has_permission(AnonymousUser(), PERMISSION_CODE))

    def test_superuser_is_allowed_without_a_profile(self):
        superuser = make_user(username="root", is_superuser=True)

        self.assertTrue(user_has_permission(superuser, PERMISSION_CODE))

    def test_superuser_is_allowed_for_a_permission_that_does_not_exist(self):
        superuser = make_user(username="root", is_superuser=True)

        self.assertTrue(user_has_permission(superuser, "no.such.permission"))

    def test_user_without_a_profile_is_denied(self):
        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))

    def test_inactive_profile_is_denied(self):
        self.attach_profile(active=False)
        grant(self.role, self.permission)

        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))

    def test_profile_without_a_role_is_denied(self):
        self.attach_profile(role=None)

        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))

    def test_inactive_role_is_denied(self):
        self.role.active = False
        self.role.save(update_fields=["active"])
        self.attach_profile()
        grant(self.role, self.permission)

        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))

    def test_granted_permission_is_allowed(self):
        self.attach_profile()
        grant(self.role, self.permission)

        self.assertTrue(user_has_permission(self.user, PERMISSION_CODE))

    def test_permission_denied_with_allow_false(self):
        self.attach_profile()
        grant(self.role, self.permission, allow=False)

        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))

    def test_inactive_permission_is_denied(self):
        self.permission.active = False
        self.permission.save(update_fields=["active"])
        self.attach_profile()
        grant(self.role, self.permission)

        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))

    def test_ungranted_permission_is_denied(self):
        self.attach_profile()
        grant(self.role, make_permission(code="other.permission"))

        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))

    def test_permission_is_scoped_to_the_users_own_role(self):
        other_role = make_role(code="ROLE-OTHER")
        grant(other_role, self.permission)
        self.attach_profile()

        self.assertFalse(user_has_permission(self.user, PERMISSION_CODE))
