from django.contrib.auth import get_user_model
from django.test import TestCase


class AuthenticatedTestCase(TestCase):
    """Base for tests hitting routes behind LoginRequiredMiddleware."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username="smoke_tester",
            password="smoke-tests-only",
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
