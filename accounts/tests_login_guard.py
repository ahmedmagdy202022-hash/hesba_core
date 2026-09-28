"""SEC-001: repeated wrong passwords pause sign-in; idle sessions expire."""

from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .models import LoginFailure


LOGIN = reverse("login")


class LoginGuardTests(TestCase):
    def setUp(self):
        self.user = make_user(username="guard_owner")
        make_user_profile(user=self.user, role=make_seeded_role(RoleCode.OWNER))

    def attempt(self, password, username="guard_owner", ip="10.0.0.1"):
        return self.client.post(LOGIN, {"username": username, "password": password}, REMOTE_ADDR=ip)

    def test_five_wrong_passwords_lock_the_username_even_for_the_right_one(self):
        for _ in range(4):
            self.assertNotContains(self.attempt("wrong"), "data-login-locked")
        page = self.attempt("wrong")
        self.assertContains(page, "data-login-locked")
        self.assertContains(page, "استنى 15 دقيقة")
        blocked = self.attempt("service-tests-only")
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "data-login-locked")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditLog.objects.filter(action="login_locked", object_id="guard_owner").exists())

    def test_the_lock_ends_and_success_clears_the_count(self):
        for _ in range(5):
            self.attempt("wrong")
        LoginFailure.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        response = self.attempt("service-tests-only")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LoginFailure.objects.filter(username="guard_owner").exists())

    def test_a_few_mistakes_then_success_is_fine_and_other_users_are_not_affected(self):
        for _ in range(3):
            self.attempt("wrong")
        other = make_user(username="guard_other")
        make_user_profile(user=other, role=make_seeded_role(RoleCode.CASHIER))
        for _ in range(5):
            self.attempt("wrong", username="guard_other", ip="10.0.0.2")
        self.assertEqual(self.attempt("service-tests-only").status_code, 302)

    @override_settings(LOGIN_MAX_FAILURES_PER_IP=6)
    def test_one_address_guessing_many_usernames_is_locked(self):
        for n in range(6):
            self.attempt("wrong", username=f"nobody{n}")
        self.assertContains(self.attempt("service-tests-only"), "data-login-locked")
        self.assertEqual(self.attempt("service-tests-only", ip="10.9.9.9").status_code, 302)

    def test_english_message_and_idle_session_settings(self):
        for _ in range(5):
            self.attempt("wrong")
        self.assertContains(self.client.get(LOGIN), "Too many wrong attempts")
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(settings.SESSION_COOKIE_AGE, 10 * 3600)
