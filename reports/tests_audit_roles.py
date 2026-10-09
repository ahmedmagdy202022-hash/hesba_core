"""AUDIT-1/2: each role sees its own work, and no link leads to a closed door."""


from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from settings_core import setup_catalog as catalog

from .tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class RoleScreensTests(TestCase):
    def setUp(self):
        prepared_client("medical", "clinic", ",".join(catalog.default_modules("medical")))

    def nav(self, user):
        self.client.force_login(user)
        page = self.client.get("/dashboard/?lang=ar")
        return page, {item["key"] for item in page.context["nav_items"]}

    def test_a_stock_keeper_never_sees_patients_or_staff(self):
        keeper = person(RoleCode.STOCK_KEEPER, "audit_keeper")
        page, keys = self.nav(keeper)
        self.assertNotIn("customers", keys)
        self.assertNotIn("staff", keys)
        self.assertEqual(self.client.get(reverse("master_data:customers")).status_code, 403)
        self.assertEqual(self.client.get(reverse("staff:list")).status_code, 403)
        # The setup steps are the owner's job.
        self.assertFalse(page.context["show_onboarding"])

    def test_a_cashier_serves_patients_but_not_the_staff_list(self):
        cashier = person(RoleCode.CASHIER, "audit_cashier")
        _, keys = self.nav(cashier)
        self.assertIn("customers", keys)
        self.assertNotIn("staff", keys)
        self.assertEqual(self.client.get(reverse("staff:list")).status_code, 403)

    def test_the_accountant_and_manager_see_the_staff(self):
        for role in (RoleCode.ACCOUNTANT, RoleCode.MANAGER):
            with self.subTest(role=role):
                user = person(role, f"audit_{role}")
                _, keys = self.nav(user)
                self.assertIn("staff", keys)
                self.assertEqual(self.client.get(reverse("staff:list")).status_code, 200)

    def test_the_owner_still_gets_the_setup_steps_on_an_empty_business(self):
        owner = person(RoleCode.OWNER, "audit_owner")
        page, _ = self.nav(owner)
        self.assertTrue(page.context["show_onboarding"])


class NoClosedDoorsTests(TestCase):
    def setUp(self):
        prepared_client("commercial", "retail", ",".join(catalog.default_modules("commercial")))

    def test_the_accountant_sees_no_new_invoice_or_till_button(self):
        self.client.force_login(person(RoleCode.ACCOUNTANT, "audit_acc"))
        sales = self.client.get(reverse("sales:list") + "?lang=ar")
        self.assertNotContains(sales, reverse("sales:create"))
        self.assertNotContains(sales, reverse("sales:pos"))
        purchases = self.client.get(reverse("purchases:list") + "?lang=ar")
        self.assertNotContains(purchases, reverse("purchases:create"))

    def test_the_account_link_shows_only_to_whoever_the_card_opens_for(self):
        from hesba_testing.factories import make_customer, make_supplier

        make_customer()
        make_supplier()
        self.client.force_login(person(RoleCode.MANAGER, "audit_mgr"))
        self.assertContains(self.client.get(reverse("master_data:customers")), "data-party-card")
        self.assertNotContains(self.client.get(reverse("master_data:suppliers")), "data-party-card")   # no supplier report

    def test_roles_and_profile_speak_in_words(self):
        self.client.force_login(person(RoleCode.OWNER, "audit_roles"))
        roles = self.client.get(reverse("settings_core:roles") + "?lang=ar")
        self.assertNotContains(roles, "sales.create_sales_invoice")
        self.assertContains(roles, "إنشاء فاتورة بيع")
        self.assertContains(roles, "البيع والتحصيل من العملاء")       # what a cashier does
        profile = self.client.get(reverse("accounts:profile") + "?lang=ar")
        self.assertNotContains(profile, "sales.create_sales_invoice")


class ServicesCanInvoiceTests(TestCase):
    def test_a_services_business_starts_with_invoicing(self):
        self.assertIn("sales_operations", catalog.default_modules("services"))
        self.assertEqual(catalog.preset_state("services", "sales_operations"), catalog.REQUIRED)


class StoredTextInArabicTests(TestCase):
    def test_posting_descriptions_read_in_arabic(self):
        from django.template import Context, Template

        template = Template("{% load hesba_labels %}{% posted_text text %}|{% entry_ref ref %}")
        for text, ref, expected in (
            ("Sales invoice SI-0001 paid now", "opening_cashbox-3", "فاتورة بيع SI-0001 — المدفوع|رصيد افتتاحي — خزنة #3"),
            ("Cancel customer payment CP-1", "SI-0001", "إلغاء تحصيل CP-1|SI-0001"),
            ("Something typed by a person", "x", "Something typed by a person|x"),
        ):
            with self.subTest(text=text):
                self.assertEqual(template.render(Context({"lang": "ar", "text": text, "ref": ref})), expected)
        self.assertEqual(template.render(Context({"lang": "en", "text": "Sales invoice SI-1 paid now", "ref": "opening_cashbox-3"})),
                         "Sales invoice SI-1 paid now|Opening balance — cashbox #3")


class StockKeeperHomeTests(TestCase):
    def test_the_transfers_waiting_on_a_keeper_lead_their_alerts(self):
        from django.utils import timezone

        from hesba_testing.factories import make_item, make_location
        from inventory import transfer_requests as tr
        from inventory.models import StockAdjustmentDirection
        from inventory.services import adjust_stock

        prepared_client("commercial", "retail", ",".join(catalog.default_modules("commercial")))
        keeper = person(RoleCode.STOCK_KEEPER, "audit_home_keeper")
        main = make_location(location_code="AUD-MAIN", is_default=True)
        branch = make_location(location_code="AUD-BR")
        item = make_item(item_code="AUD-IT", is_stock_tracked=True)
        owner = person(RoleCode.OWNER, "audit_home_owner")
        adjust_stock("AUD-IN", timezone.localdate(), item, main, StockAdjustmentDirection.IN, 10, "opening", owner, unit_cost=5)
        tr.create_request(main, branch, [(item, "2")], keeper)
        self.client.force_login(keeper)
        page = self.client.get("/dashboard/?lang=ar")
        keys = {alert["key"] for alert in page.context["alerts"]}
        self.assertIn("transfers_to_send", keys)
        self.assertContains(page, "طلب تحويل مستني تبعته")
        cashier = person(RoleCode.CASHIER, "audit_home_cashier")
        self.client.force_login(cashier)
        self.assertNotIn("transfers_to_send", {a["key"] for a in self.client.get("/dashboard/?lang=ar").context["alerts"]})


class CustomRoleNavTests(TestCase):
    def test_an_audience_item_also_needs_the_page_permission(self):
        from hesba_testing.factories import grant, make_role
        from permissions.models import Permission
        from settings_core.capabilities import _write

        prepared_client("commercial", "retail", ",".join(catalog.default_modules("commercial")))
        _write("vat", True)
        role = make_role(code="AUD-CUSTOM")
        for code in ("settings.view_settings", "sales.view_sales_invoices"):
            grant(role, Permission.objects.get(code=code))
        user = make_user(username="audit_custom")
        make_user_profile(user=user, role=role)
        self.client.force_login(user)
        keys = {item["key"] for item in self.client.get("/dashboard/?lang=ar").context["nav_items"]}
        self.assertNotIn("taxes", keys)       # tax settings need master_data.view_master_data too
        self.assertNotIn("customers", keys)   # so does the customer list
