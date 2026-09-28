"""SEC-002: two-step sign-in with an authenticator app."""

import time
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from . import two_factor
from .login_guard import PENDING_KEY
from .models import LoginFailure, TwoFactor


LOGIN, VERIFY, SETUP = reverse("login"), reverse("login_verify"), reverse("accounts:two_factor")
PASSWORD = "service-tests-only"


def now_code(secret, shift=0):
    return two_factor.code_at(secret, two_factor.current_step() + shift)


class TotpTests(TestCase):
    def test_rfc_6238_vectors_and_clock_drift(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
        self.assertEqual(two_factor.code_at(secret, 59 // 30), "287082")
        self.assertEqual(two_factor.code_at(secret, 1111111109 // 30), "081804")
        at = 1111111109
        self.assertEqual(two_factor.matching_step(secret, "081804", now=at), at // 30)
        self.assertEqual(two_factor.matching_step(secret, "081 804", now=at + 30), at // 30)  # the previous 30 seconds
        self.assertIsNone(two_factor.matching_step(secret, "081804", now=at + 90))
        self.assertIsNone(two_factor.matching_step(secret, "081804", now=at, after=at // 30))  # already used
        self.assertEqual(two_factor.clean_code("٠٨١ ٨٠٤"), "081804")
        uri = two_factor.otpauth_uri(secret, "owner")
        self.assertTrue(uri.startswith("otpauth://totp/Hesba%3Aowner?secret=" + secret))


class TwoFactorFlowTests(TestCase):
    def setUp(self):
        # One fixed 30-second step, so a test never straddles a step boundary.
        patcher = mock.patch("accounts.two_factor.current_step", return_value=int(time.time() // 30))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = make_user(username="tf_owner")
        make_user_profile(user=self.user, role=make_seeded_role(RoleCode.OWNER))

    def switch_on(self):
        self.client.force_login(self.user)
        self.client.get(SETUP)
        secret = self.client.session["hesba_two_factor_setup_secret"]
        wrong = self.client.post(SETUP, {"action": "enable", "code": "000000" if now_code(secret) != "000000" else "111111"})
        self.assertContains(wrong, "data-two-factor-error")
        self.assertFalse(TwoFactor.objects.exists())
        page = self.client.post(SETUP, {"action": "enable", "code": now_code(secret)})
        self.assertContains(page, "data-recovery-codes")
        codes = page.context["codes"]
        self.client.logout()
        return secret, codes

    def password_step(self, next_url=""):
        data = {"username": "tf_owner", "password": PASSWORD}
        if next_url:
            data["next"] = next_url
        return self.client.post(LOGIN, data, REMOTE_ADDR="10.1.1.1")

    def test_switch_on_then_the_password_alone_does_not_sign_in(self):
        secret, codes = self.switch_on()
        self.assertEqual((len(codes), len(TwoFactor.objects.get().recovery_hashes)), (8, 8))
        self.assertTrue(AuditLog.objects.filter(action="enable_two_factor", object_id=str(self.user.pk)).exists())
        response = self.password_step(next_url="/sales/")
        self.assertRedirects(response, f"{VERIFY}?lang=ar", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/start/").status_code, 302)
        wrong = self.client.post(VERIFY, {"code": "12"}, REMOTE_ADDR="10.1.1.1")
        self.assertContains(wrong, "data-code-wrong")
        self.assertNotIn("_auth_user_id", self.client.session)
        # The code used to switch it on cannot sign in again; the next 30 seconds' code can.
        self.assertContains(self.client.post(VERIFY, {"code": now_code(secret)}, REMOTE_ADDR="10.1.1.1"), "data-code-wrong")
        done = self.client.post(VERIFY, {"code": now_code(secret, 1)}, REMOTE_ADDR="10.1.1.1")
        self.assertRedirects(done, "/sales/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        self.assertNotIn(PENDING_KEY, self.client.session)
        self.assertFalse(LoginFailure.objects.filter(username="tf_owner").exists())

    def test_a_code_works_once_and_a_recovery_code_works_once(self):
        secret, codes = self.switch_on()
        used = now_code(secret, 1)
        self.password_step()
        self.assertRedirects(self.client.post(VERIFY, {"code": used}, REMOTE_ADDR="10.1.1.1"), "/start/", fetch_redirect_response=False)
        self.client.logout()
        self.password_step()
        self.assertContains(self.client.post(VERIFY, {"code": used}, REMOTE_ADDR="10.1.1.1"), "data-code-wrong")
        self.assertRedirects(self.client.post(VERIFY, {"code": codes[0].upper()}, REMOTE_ADDR="10.1.1.1"), "/start/", fetch_redirect_response=False)
        self.assertEqual(len(TwoFactor.objects.get().recovery_hashes), 7)
        self.client.logout()
        self.password_step()
        self.assertContains(self.client.post(VERIFY, {"code": codes[0]}, REMOTE_ADDR="10.1.1.1"), "data-code-wrong")

    def test_wrong_codes_lock_and_the_password_cannot_reset_the_count(self):
        self.switch_on()
        for _ in range(2):
            self.password_step()
            self.client.post(VERIFY, {"code": "000001"}, REMOTE_ADDR="10.1.1.1")
            self.client.post(VERIFY, {"code": "000002"}, REMOTE_ADDR="10.1.1.1")
        self.password_step()
        page = self.client.post(VERIFY, {"code": "000003"}, REMOTE_ADDR="10.1.1.1")
        self.assertContains(page, "data-login-locked")
        self.assertNotIn(PENDING_KEY, self.client.session)
        self.assertContains(self.password_step(), "data-login-locked")  # the right password is refused while locked
        self.assertTrue(AuditLog.objects.filter(action="login_locked", object_id="tf_owner").exists())

    @override_settings(TWO_FACTOR_PENDING_SECONDS=60)
    def test_the_password_step_expires(self):
        secret, _ = self.switch_on()
        self.password_step()
        later = time.time() + 120
        with mock.patch("accounts.login_guard.time.time", return_value=later):
            self.assertRedirects(self.client.post(VERIFY, {"code": now_code(secret)}), LOGIN, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertRedirects(self.client.get(VERIFY), LOGIN, fetch_redirect_response=False)

    def test_english_and_users_without_the_app_sign_in_as_before(self):
        other = make_user(username="tf_plain")
        make_user_profile(user=other, role=make_seeded_role(RoleCode.CASHIER))
        response = self.client.post(LOGIN, {"username": "tf_plain", "password": PASSWORD})
        self.assertRedirects(response, "/start/", fetch_redirect_response=False)
        self.client.logout()
        self.switch_on()
        self.client.post(LOGIN, {"username": "tf_owner", "password": PASSWORD, "hesba_lang": "en"})
        page = self.client.get(VERIFY)
        self.assertContains(page, "Two-step sign-in")
        self.assertContains(page, 'dir="ltr"')

    def test_switch_off_needs_password_and_code_and_new_codes_replace_old(self):
        secret, codes = self.switch_on()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("accounts:profile")), "التحقق بخطوتين: مفعّل")
        fresh = self.client.post(SETUP, {"action": "new_codes", "code": codes[1]}).context["codes"]
        stored = TwoFactor.objects.get().recovery_hashes
        self.assertNotIn(two_factor._hash(codes[2]), stored)
        self.assertIn(two_factor._hash(fresh[0]), stored)
        self.assertContains(self.client.post(SETUP, {"action": "disable", "password": "nope", "code": fresh[0]}), "كلمة السر غلط")
        self.assertContains(self.client.post(SETUP, {"action": "disable", "password": PASSWORD, "code": "999999"}), "data-two-factor-error")
        self.assertTrue(TwoFactor.objects.exists())
        self.client.post(SETUP, {"action": "disable", "password": PASSWORD, "code": fresh[0]})
        self.assertFalse(TwoFactor.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="disable_two_factor").exists())

    def test_a_manager_can_switch_it_off_for_a_user_who_lost_the_phone(self):
        self.switch_on()
        cashier = make_user(username="tf_cashier")
        make_user_profile(user=cashier, role=make_seeded_role(RoleCode.CASHIER))
        url = reverse("settings_core:user_reset_two_factor", args=[self.user.pk])
        self.client.force_login(cashier)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(TwoFactor.objects.exists())
        manager = make_user(username="tf_manager")
        make_user_profile(user=manager, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(manager)
        self.assertContains(self.client.get(reverse("settings_core:user_edit", args=[self.user.pk])), "data-reset-two-factor")
        self.client.post(url)
        self.assertFalse(TwoFactor.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="reset_two_factor", actor=manager).exists())
