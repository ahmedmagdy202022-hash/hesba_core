from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import UserProfile
from permissions.models import Role


class AuthenticatedTestCase(TestCase):
    """Base for tests hitting routes behind LoginRequiredMiddleware.

    Set ``role_code`` on a subclass to sign in as someone holding one of the
    seeded roles. Views check permissions through the profile row rather than
    the user, so a test that needs a gated view has to have one; leaving
    ``role_code`` unset keeps the plain authenticated-but-unprivileged user the
    route-auth tests rely on.
    """

    #: A seeded RoleCode, or None for a user with no role at all.
    role_code = None

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username="smoke_tester",
            password="smoke-tests-only",
        )
        cls.role = None
        cls.profile = None
        if cls.role_code is not None:
            cls.role = Role.objects.get(code=cls.role_code)
            cls.profile = UserProfile.objects.create(
                user=cls.user,
                role=cls.role,
                active=True,
            )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
