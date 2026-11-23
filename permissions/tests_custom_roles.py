"""FB-ROLES: the owner makes roles of their own on Hesba's screens; the built-in roles stay fixed."""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import Role, RoleCode
from permissions.role_services import create_role, role_codes, update_role
from permissions.services import user_has_permission
from reports.tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=role if isinstance(role, Role) else make_seeded_role(role))
    return user


class CustomRoleServiceTests(TestCase):
    def setUp(self):
        self.owner = person(RoleCode.OWNER, "cr_owner")

    def test_a_cashier_who_also_receives_stock(self):
        codes = role_codes(make_seeded_role(RoleCode.CASHIER)) + ["inventory.view_stock", "purchases.create_purchase_invoice"]
        role = create_role(name_ar="كاشير ومخزن", name_en="Cashier & stock", codes=codes, actor=self.owner)
        self.assertEqual((role.code, role.is_system_role, role.active), ("custom_1", False, True))
        clerk = person(role, "cr_clerk")
        self.assertTrue(user_has_permission(clerk, "sales.create_sales_invoice"))
        self.assertTrue(user_has_permission(clerk, "purchases.create_purchase_invoice"))
        self.assertFalse(user_has_permission(clerk, "reports.view_profit_report"))
        log = AuditLog.objects.get(action="create_role")
        self.assertIn("purchases.create_purchase_invoice", log.after_data["permissions"])
        # the cashier role it was copied from is unchanged
        self.assertNotIn("purchases.create_purchase_invoice", role_codes(make_seeded_role(RoleCode.CASHIER)))

    def test_guards(self):
        for kwargs, message in (({"name_ar": "", "codes": ["sales.view_sales_invoices"]}, "role_name_required"),
                                ({"name_ar": "المالك", "codes": ["sales.view_sales_invoices"]}, "role_name_taken"),
                                ({"name_ar": "فاضي", "codes": []}, "role_needs_permissions"),
                                ({"name_ar": "مدير أدوار", "codes": ["permissions.manage_roles"]}, "role_locked_permission"),
                                ({"name_ar": "غلط", "codes": ["nothing.like_this"]}, "role_unknown_permission")):
            with self.assertRaisesMessage(ValidationError, message):
                create_role(actor=self.owner, **kwargs)
        manager = person(RoleCode.MANAGER, "cr_mgr")
        with self.assertRaisesMessage(ValidationError, "role_manage_denied"):
            create_role(name_ar="أي حاجة", codes=["sales.view_sales_invoices"], actor=manager)
        with self.assertRaisesMessage(ValidationError, "role_system_locked"):
            update_role(make_seeded_role(RoleCode.CASHIER), name_ar="كاشير", codes=["sales.view_sales_invoices"], actor=self.owner)

    def test_no_one_hands_out_what_they_do_not_hold(self):
        from permissions.models import RolePermission

        owner_role = make_seeded_role(RoleCode.OWNER)
        RolePermission.objects.filter(role=owner_role, permission__code="closing.run_closing").delete()
        with self.assertRaisesMessage(ValidationError, "role_beyond_actor"):
            create_role(name_ar="مقفل", codes=["closing.run_closing"], actor=self.owner)

    def test_a_role_in_use_cannot_be_switched_off_and_edits_are_audited(self):
        role = create_role(name_ar="مراجع", codes=["sales.view_sales_invoices"], actor=self.owner)
        person(role, "cr_auditor")
        with self.assertRaisesMessage(ValidationError, "role_in_use"):
            update_role(role, name_ar="مراجع", codes=["sales.view_sales_invoices"], active=False, actor=self.owner)
        update_role(role, name_ar="مراجع مبيعات", codes=["sales.view_sales_invoices", "reports.view_sales_report"], actor=self.owner)
        log = AuditLog.objects.get(action="update_role")
        self.assertEqual(log.before_data["permissions"], ["sales.view_sales_invoices"])
        self.assertEqual(log.after_data["permissions"], ["reports.view_sales_report", "sales.view_sales_invoices"])


class CustomRoleScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "crs_owner")
        self.client.force_login(self.owner)

    def test_copy_a_role_tick_and_give_it_to_a_new_user(self):
        cashier = make_seeded_role(RoleCode.CASHIER)
        page = self.client.get(reverse("settings_core:role_new"), {"from": cashier.pk})
        self.assertContains(page, "data-copy-of")
        self.assertContains(page, 'value="sales.create_sales_invoice" checked')
        self.assertNotContains(page, 'value="permissions.manage_roles"')
        codes = role_codes(cashier) + ["inventory.view_stock"]
        saved = self.client.post(reverse("settings_core:role_new"), {"name_ar": "كاشير بيشوف المخزون", "perm": codes})
        self.assertRedirects(saved, reverse("settings_core:roles") + "?lang=ar", fetch_redirect_response=False)
        role = Role.objects.get(name_ar="كاشير بيشوف المخزون")
        listing = self.client.get(reverse("settings_core:roles"))
        self.assertContains(listing, "data-custom-role")
        self.assertContains(listing, reverse("settings_core:role_edit", args=[role.pk]))
        new_user = self.client.get(reverse("settings_core:user_create"))
        self.assertContains(new_user, "data-new-role-link")
        self.assertContains(new_user, "كاشير بيشوف المخزون")

    def test_a_refused_role_keeps_what_was_typed_and_says_why(self):
        page = self.client.post(reverse("settings_core:role_new"), {"name_ar": "دور فاضي"})
        self.assertContains(page, "data-role-error")
        self.assertContains(page, "اختار حاجة واحدة على الأقل")
        self.assertContains(page, 'value="دور فاضي"')
        self.assertFalse(Role.objects.filter(name_ar="دور فاضي").exists())

    def test_built_in_roles_have_no_edit_screen_and_others_cannot_manage(self):
        self.assertEqual(self.client.get(reverse("settings_core:role_edit", args=[make_seeded_role(RoleCode.CASHIER).pk])).status_code, 404)
        self.client.force_login(person(RoleCode.MANAGER, "crs_mgr"))
        self.assertEqual(self.client.get(reverse("settings_core:role_new")).status_code, 403)
        self.assertEqual(self.client.post(reverse("settings_core:role_new"), {"name_ar": "x", "perm": ["sales.view_sales_invoices"]}).status_code, 403)
