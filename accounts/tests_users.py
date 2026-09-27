"""USERS-001 / ADMIN-001: staff accounts from inside Hesba; Django Admin for superusers only."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from permissions.services import user_has_permission

from .models import UserProfile
from .user_services import create_user_account, reset_user_password, update_user_account


GOOD = "Kashier-2026"


def person(role_code, username, **kwargs):
    user = make_user(username=username, **kwargs)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class UserServiceTests(TestCase):
    def setUp(self):
        self.owner = person(RoleCode.OWNER, "svc_owner")

    def test_created_account_is_plain_audited_and_must_change_password(self):
        user = create_user_account(username="cashier1", password=GOOD, role=make_seeded_role(RoleCode.CASHIER), actor=self.owner, display_name="منى")
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertTrue(user.check_password(GOOD))
        profile = user.hesba_profile
        self.assertEqual((profile.role.code, profile.display_name, profile.must_change_password), ("cashier", "منى", True))
        self.assertTrue(user_has_permission(user, "sales.create_sales_invoice"))
        log = AuditLog.objects.get(action="create_user")
        self.assertEqual((log.actor, log.after_data["role"]), (self.owner, "cashier"))
        self.assertNotIn(GOOD, str(log.after_data))

    def test_weak_duplicate_and_support_are_refused(self):
        cashier = make_seeded_role(RoleCode.CASHIER)
        for password in ("123", "12345678", "password"):
            with self.subTest(password=password):
                with self.assertRaises(ValidationError):
                    create_user_account(username=f"weak{len(password)}", password=password, role=cashier, actor=self.owner)
        create_user_account(username="dup", password=GOOD, role=cashier, actor=self.owner)
        with self.assertRaisesMessage(ValidationError, "username_taken"):
            create_user_account(username="DUP", password=GOOD, role=cashier, actor=self.owner)
        with self.assertRaisesMessage(ValidationError, "role_invalid"):
            create_user_account(username="sup", password=GOOD, role=make_seeded_role(RoleCode.SUPPORT), actor=self.owner)

    def test_no_self_lockout_and_the_last_owner_stays(self):
        owner_role = make_seeded_role(RoleCode.OWNER)
        with self.assertRaisesMessage(ValidationError, "no_self_lockout"):
            update_user_account(self.owner, actor=self.owner, role=owner_role, active=False)
        with self.assertRaisesMessage(ValidationError, "no_self_lockout"):
            update_user_account(self.owner, actor=self.owner, role=make_seeded_role(RoleCode.CASHIER), active=True)
        second = person(RoleCode.OWNER, "second_owner")
        # With two owners, one may demote the other...
        update_user_account(second, actor=self.owner, role=make_seeded_role(RoleCode.MANAGER), active=True)
        self.assertEqual(UserProfile.objects.get(user=second).role.code, "manager")
        # ...but a superuser cannot remove the last active owner either.
        root = get_user_model().objects.create_superuser(username="root", password="x-Root-pass-9")
        with self.assertRaisesMessage(ValidationError, "last_owner"):
            update_user_account(self.owner, actor=root, role=owner_role, active=False)

    def test_disabling_blocks_sign_in_and_is_audited(self):
        user = create_user_account(username="leaver", password=GOOD, role=make_seeded_role(RoleCode.CASHIER), actor=self.owner)
        update_user_account(user, actor=self.owner, role=user.hesba_profile.role, active=False)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(self.client.login(username="leaver", password=GOOD))
        log = AuditLog.objects.get(action="update_user")
        self.assertEqual((log.before_data["active"], log.after_data["active"]), (True, False))

    def test_reset_sets_a_temporary_password(self):
        user = create_user_account(username="forgot", password=GOOD, role=make_seeded_role(RoleCode.CASHIER), actor=self.owner)
        UserProfile.objects.filter(user=user).update(must_change_password=False)
        reset_user_password(user, password="Temp-pass-77", actor=self.owner)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Temp-pass-77"))
        self.assertTrue(user.hesba_profile.must_change_password)
        self.assertTrue(AuditLog.objects.filter(action="reset_password").exists())


class UserScreenTests(TestCase):
    def setUp(self):
        self.owner = person(RoleCode.OWNER, "screen_owner")
        self.client.force_login(self.owner)

    def test_owner_adds_a_cashier_from_the_screen(self):
        cashier = make_seeded_role(RoleCode.CASHIER)
        response = self.client.post(reverse("settings_core:user_create"), {"username": "mona", "display_name": "منى", "phone": "0100", "role": cashier.pk, "password": GOOD})
        self.assertRedirects(response, "/settings/users/?lang=ar", fetch_redirect_response=False)
        page = self.client.get(reverse("settings_core:users"))
        self.assertContains(page, "mona")
        self.assertContains(page, "هيغيّر كلمة السر")

    def test_form_shows_password_rules_in_arabic(self):
        cashier = make_seeded_role(RoleCode.CASHIER)
        response = self.client.post(reverse("settings_core:user_create"), {"username": "weak", "role": cashier.pk, "password": "123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertFalse(get_user_model().objects.filter(username="weak").exists())

    def test_edit_and_self_lockout_message(self):
        response = self.client.post(reverse("settings_core:user_edit", args=[self.owner.pk]), {"role": make_seeded_role(RoleCode.OWNER).pk, "display_name": "", "phone": ""})
        self.assertContains(response, "مينفعش توقف حسابك")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_only_owner_manages_users(self):
        for role in (RoleCode.MANAGER, RoleCode.CASHIER, RoleCode.ACCOUNTANT):
            with self.subTest(role=role):
                self.client.force_login(person(role, f"no_{role}"))
                self.assertEqual(self.client.get(reverse("settings_core:users")).status_code, 403)
                self.assertEqual(self.client.post(reverse("settings_core:user_create"), {"username": "x"}).status_code, 403)

    def test_superusers_are_not_listed_or_editable(self):
        root = get_user_model().objects.create_superuser(username="root_hidden", password="x-Root-pass-9")
        self.assertNotContains(self.client.get(reverse("settings_core:users")), "root_hidden")
        self.assertEqual(self.client.get(reverse("settings_core:user_edit", args=[root.pk])).status_code, 404)


class ForcedPasswordChangeTests(TestCase):
    def setUp(self):
        owner = person(RoleCode.OWNER, "force_owner")
        self.user = create_user_account(username="newbie", password=GOOD, role=make_seeded_role(RoleCode.CASHIER), actor=owner)

    def test_first_sign_in_goes_to_the_password_page_until_changed(self):
        self.client.login(username="newbie", password=GOOD)
        response = self.client.get(reverse("sales:list"))
        self.assertRedirects(response, "/profile/password/?next=/sales/", fetch_redirect_response=False)
        page = self.client.get(response.url)
        self.assertContains(page, "لازم تغيّر كلمة السر")
        done = self.client.post("/profile/password/", {"old_password": GOOD, "new_password1": "Brand-new-55", "new_password2": "Brand-new-55", "next": "/sales/"})
        self.assertRedirects(done, "/sales/", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("sales:list")).status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.hesba_profile.must_change_password)

    def test_offsite_next_is_ignored(self):
        self.client.login(username="newbie", password=GOOD)
        done = self.client.post("/profile/password/", {"old_password": GOOD, "new_password1": "Brand-new-55", "new_password2": "Brand-new-55", "next": "https://evil.example/"})
        self.assertRedirects(done, "/start/", fetch_redirect_response=False)

    def test_logout_stays_reachable(self):
        self.client.login(username="newbie", password=GOOD)
        self.assertEqual(self.client.post("/logout/").status_code, 302)


class AdminClosedTests(TestCase):
    def test_staff_owner_cannot_open_admin_but_superuser_can(self):
        owner = person(RoleCode.OWNER, "staff_owner", is_staff=True)
        self.client.force_login(owner)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)
        root = get_user_model().objects.create_superuser(username="root_admin", password="x-Root-pass-9")
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_admin_address_comes_from_settings(self):
        from django.conf import settings

        self.assertEqual(reverse("admin:index"), "/" + settings.ADMIN_URL)
