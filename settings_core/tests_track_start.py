"""TRACK-START (Ahmed, 18 Oct: restart the tracks from the very first visit; login and settings first)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from hesba_testing.factories import make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def owner(username="ts_owner", superuser=False):
    user = (get_user_model().objects.create_superuser(username=username, password="x-Pass-12345") if superuser
            else make_user(username=username))
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER), display_name="المالك")
    return user


class StartAndSettingsTests(TestCase):
    def setUp(self):
        self.profile = prepared_client(sub_activity="fashion")
        self.client.force_login(owner())

    def page(self, name, *args, **params):
        return self.client.get(reverse(name, args=args), {"lang": "ar", **params}).content.decode()

    @override_settings(DEMO_MODE=True)
    def test_s01_the_demo_bar_names_this_shop_and_trying_another_starts_over(self):
        html = self.page("dashboard_snapshot")
        self.assertIn("data-demo-activity", html)
        self.assertIn("تجاري · ملابس وأحذية", html)
        self.assertNotIn('name="activity"', html)  # no switch that would keep a clothes shop's data in a pharmacy
        self.assertIn("جرّب نشاط تاني (من الأول)", html)

    def test_s02_s03_one_name_and_one_meaning_for_on_and_off(self):
        modules = self.page("settings_core:modules")
        self.assertIn("الأقسام", modules)
        self.assertNotIn("الموديولات", modules)
        self.assertNotIn("مقفول", modules)
        self.assertIn("شغّال", modules)
        capabilities = self.page("settings_core:capabilities")
        self.assertIn("خصائص الشغل", capabilities)  # SETTINGS-DESIGN: one name, in the menu and on the page
        self.assertNotIn("قدرات النشاط", capabilities)
        hub = self.page("settings_core:overview")
        self.assertNotIn("الموديولات", hub)
        self.assertNotIn("مقفولة", hub)

    def test_s06_s07_roles_and_my_permissions_fit_a_clothes_shop(self):
        roles = self.page("settings_core:roles")
        self.assertNotIn("الويتر", roles)
        self.assertNotIn("مهندس الموقع", roles)
        profile = self.client.get("/profile/", {"lang": "ar"}).content.decode()
        self.assertNotIn("الملفات الطبية", profile)
        self.assertNotIn("الطاولات", profile)

    def test_s08_a_user_page_is_titled_with_the_name(self):
        from accounts.user_services import create_user_account

        user = create_user_account(username="accountant2", password="x-Pass-12345", role=make_seeded_role(RoleCode.ACCOUNTANT),
                                   actor=get_user_model().objects.get(username="ts_owner"), display_name="المحاسب")
        html = self.page("settings_core:user_edit", user.pk)
        self.assertIn('<h1>المحاسب <small class="hs-code"><bdi>accountant2</bdi></small></h1>', html)

    def test_s09_the_main_branch_is_the_company(self):
        html = self.page("entities:list")
        self.assertIn("<h1>الفروع</h1>", html)
        self.assertIn(self.profile.display_name, html)
        self.assertNotIn("Main entity", html)

    def test_s10_slow_stock_names_the_size_and_colour(self):
        from django.utils import timezone

        from reports.analytics import stock_health

        tee = make_item(item_code="TS-M", item_name="تيشيرت", size="M", color="أسود")
        stock_in(tee, make_location(), 3, "50.00")
        self.assertEqual(stock_health(timezone.localdate())["slow"][0]["name"], "تيشيرت · M · أسود")

    def test_s11_the_daily_email_says_which_hour_is_which(self):
        html = self.page("settings_core:daily_email")
        self.assertIn("ساعة الصبح", html)
        self.assertIn("ساعة بالليل", html)
        self.assertNotIn("الصبح ...", html)


class ShowcaseLocksTests(TestCase):
    @override_settings(DEMO_MODE=True)
    def test_s04_no_django_admin_in_a_showcase(self):
        prepared_client(sub_activity="fashion")
        self.client.force_login(owner("ts_root", superuser=True))
        self.assertNotEqual(self.client.get("/admin/").status_code, 200)
        self.assertNotIn("set-advanced", self.client.get(reverse("settings_core:overview"), {"lang": "ar"}).content.decode())

    def test_s04_a_real_install_keeps_its_support_corner(self):
        prepared_client(sub_activity="fashion")
        self.client.force_login(owner("ts_root2", superuser=True))
        self.assertIn("set-advanced", self.client.get(reverse("settings_core:overview"), {"lang": "ar"}).content.decode())

    def test_s12_s15_the_gate_counts_the_same_steps_and_the_credit_reads_arabic(self):
        self.client.force_login(owner("ts_gate"))
        gate = self.client.get("/setup/", {"lang": "ar"}).content.decode()
        self.assertIn('data-i18n="step2Title">النشاط الفرعي', gate)
        self.assertNotIn("الدخول للنظام", gate)
        self.client.logout()
        self.assertIn("تطوير <strong>أحمد مجدي</strong>", self.client.get("/login/").content.decode())


class SalesReportTotalTests(TestCase):
    """The dashboard's month sales must be findable on the sales report (consistency check, 18 Oct)."""

    def test_the_sales_report_sums_posted_invoices_and_names_the_dashboard_figure(self):
        from hesba_testing.factories import add_sales_line, make_draft_sales_invoice, posted_invoice_ready
        from sales.services import post_sales_invoice

        user = owner(superuser=True)
        invoice, item, location, _cashbox = posted_invoice_ready(paid_now="20.00")
        post_sales_invoice(invoice.id, user=user)
        draft = make_draft_sales_invoice(customer=invoice.customer, location=location, invoice_number="SI-002")
        add_sales_line(draft, item, quantity=1, unit_sale_price="999.00")
        self.client.force_login(user)
        html = self.client.get("/reports/sales/", {"lang": "ar"}).content.decode()
        total = html[html.index("data-invoices-total"):html.index("</tfoot>")]
        self.assertIn("المرحّل · 1 فاتورة", total)
        self.assertIn("60.00", total)  # 2 × 30.00, the draft's 999.00 is not money yet
        self.assertIn("20.00", total)
        self.assertIn("40.00", total)
        self.assertNotIn("999", total)
        self.assertIn("رقم لوحة القيادة", total)
