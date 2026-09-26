"""SHELL-001: the sidebar and phone tab bar on every operations and master-data screen.

The shell reads the same section list as the dashboard, so the rules pinned
there (module switched off, permission not held) must hold here too, on the
pages people actually work in.
"""

import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.navigation import TAB_COUNT, app_shell, current_key
from reports.tests_dashboard import prepared_client
from settings_core.setup_services import usable_modules


def signed_in(test, role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code), display_name=f"{username} name")
    test.client.force_login(user)
    return user


def sidebar_links(response):
    aside = re.search(r'<aside class="hs-sidebar".*?</aside>', response.content.decode(), re.S).group(0)
    return re.findall(r'<a class="hs-nav__link[^"]*" href="([^"?]+)', aside)


class CurrentSectionTests(SimpleTestCase):
    ITEMS = [
        {"key": "dashboard", "url": "/dashboard/"},
        {"key": "operations", "url": "/sales/"},
        {"key": "customers", "url": "/master-data/customers/"},
        {"key": "items", "url": "/master-data/items/"},
    ]

    def test_deeper_pages_belong_to_their_section(self):
        self.assertEqual(current_key(self.ITEMS, "/sales/12/"), "operations")
        self.assertEqual(current_key(self.ITEMS, "/master-data/items/new/"), "items")
        self.assertEqual(current_key(self.ITEMS, "/master-data/customers/"), "customers")

    def test_a_page_outside_every_section_marks_nothing(self):
        self.assertIsNone(current_key(self.ITEMS, "/master-data/"))
        self.assertIsNone(current_key(self.ITEMS, "/sales"))


class ShellRenderingTests(TestCase):
    def setUp(self):
        prepared_client()

    def test_operations_and_master_data_screens_carry_the_shell(self):
        signed_in(self, RoleCode.OWNER, "shell_owner")
        for name in ("sales:list", "inventory:stock", "cashboxes:list", "report_hub", "master_data:customers", "master_data:hub", "accounts:profile"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, 'class="hs-sidebar"')
                self.assertContains(response, "hesba/brand/hesba-logo-reversed.png")
                self.assertContains(response, 'class="hs-tabbar"')
                self.assertContains(response, "hesba/css/shell.css")
                self.assertContains(response, "hesba/js/shell.js")
                # The old text-only header is gone.
                self.assertNotContains(response, "op-header")
                self.assertNotContains(response, "md-header")

    def test_the_page_being_viewed_is_marked_current(self):
        signed_in(self, RoleCode.OWNER, "current_owner")
        response = self.client.get(reverse("master_data:items"))
        current = re.findall(r'<a class="hs-nav__link is-current" href="([^"?]+)[^>]*aria-current="page"', response.content.decode())
        self.assertEqual(current, [reverse("master_data:items")])

    def test_logout_is_a_csrf_protected_post(self):
        signed_in(self, RoleCode.OWNER, "logout_owner")
        body = self.client.get(reverse("sales:list")).content.decode()
        form = re.search(r'<form method="post" action="' + re.escape(reverse("logout")) + r'">(.*?)</form>', body, re.S)
        self.assertIsNotNone(form)
        self.assertIn("csrfmiddlewaretoken", form.group(1))

    def test_the_signed_in_name_is_shown(self):
        signed_in(self, RoleCode.OWNER, "named_owner")
        self.assertContains(self.client.get(reverse("sales:list")), "named_owner name")

    def test_english_screens_get_english_shell_words(self):
        signed_in(self, RoleCode.OWNER, "english_owner")
        response = self.client.get(reverse("sales:list"), {"lang": "en"})
        self.assertContains(response, "Main navigation")
        self.assertContains(response, ">Sales<")
        self.assertNotContains(response, "التنقل الرئيسي")


class DashboardInShellTests(TestCase):
    """SHELL-002: the landing page uses the same shell as every other screen."""

    def setUp(self):
        prepared_client()
        signed_in(self, RoleCode.OWNER, "dash_shell_owner")

    def test_dashboard_carries_the_shell_and_is_current(self):
        response = self.client.get(reverse("dashboard_snapshot"))
        self.assertContains(response, 'class="hs-sidebar"')
        self.assertContains(response, 'class="hs-tabbar"')
        self.assertContains(response, "hesba/js/shell.js")
        current = re.findall(r'<a class="hs-nav__link is-current" href="([^"?]+)', response.content.decode())
        self.assertEqual(current, [reverse("dashboard_snapshot")])

    def test_the_old_dashboard_navigation_is_gone(self):
        response = self.client.get(reverse("dashboard_snapshot"))
        for fragment in ("dash-nav", "data-menu-toggle", "dash-logout", "dash-pill"):
            with self.subTest(fragment=fragment):
                self.assertNotContains(response, fragment)
        # One logout, in the shell.
        self.assertEqual(response.content.decode().count(f'action="{reverse("logout")}"'), 1)

    def test_dashboard_sections_match_its_own_list(self):
        response = self.client.get(reverse("dashboard_snapshot"))
        self.assertEqual(sidebar_links(response), [reverse(item["url_name"]) for item in response.context["nav_items"]])


class ShellPermissionTests(TestCase):
    def test_sections_without_the_permission_are_hidden(self):
        prepared_client()
        signed_in(self, RoleCode.CASHIER, "shell_cashier")
        links = sidebar_links(self.client.get(reverse("accounts:profile")))
        self.assertIn(reverse("accounts:profile"), links)
        self.assertNotIn(reverse("purchases:list"), links)
        self.assertNotIn(reverse("closing:list"), links)
        self.assertNotIn(reverse("settings_core:overview"), links)

    def test_owner_sees_every_enabled_section(self):
        prepared_client()
        signed_in(self, RoleCode.OWNER, "shell_full_owner")
        links = sidebar_links(self.client.get(reverse("accounts:profile")))
        for name in ("dashboard_snapshot", "sales:list", "purchases:list", "inventory:stock", "master_data:customers", "master_data:items", "cashboxes:list", "report_hub", "closing:list", "settings_core:overview"):
            with self.subTest(route=name):
                self.assertIn(reverse(name), links)

    def test_switched_off_modules_are_hidden(self):
        prepared_client(modules="customers,items_services,cashboxes,reports,sales_operations")
        signed_in(self, RoleCode.OWNER, "shell_module_owner")
        links = sidebar_links(self.client.get(reverse("sales:list")))
        self.assertIn(reverse("master_data:customers"), links)
        self.assertNotIn(reverse("master_data:suppliers"), links)
        self.assertNotIn(reverse("purchases:list"), links)
        self.assertNotIn(reverse("inventory:stock"), links)

    def test_shell_and_dashboard_offer_the_same_sections(self):
        prepared_client()
        signed_in(self, RoleCode.STOCK_KEEPER, "shell_keeper")
        dashboard = [reverse(item["url_name"]) for item in self.client.get(reverse("dashboard_snapshot")).context["nav_items"]]
        self.assertEqual(sidebar_links(self.client.get(reverse("accounts:profile"))), dashboard)


class TabBarTests(TestCase):
    def test_tabs_are_capped_and_more_marks_hidden_sections(self):
        prepared_client()
        owner = make_user(username="tab_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        modules = set(usable_modules())
        on_sales = app_shell(owner, "ar", modules, reverse("sales:list"))
        self.assertEqual(len(on_sales["tabs"]), TAB_COUNT)
        self.assertEqual([tab["key"] for tab in on_sales["tabs"]], ["dashboard", "operations", "inventory", "customers"])
        self.assertFalse(on_sales["more_current"])

        on_closing = app_shell(owner, "ar", modules, reverse("closing:list"))
        self.assertTrue(on_closing["more_current"])
        self.assertFalse(any(tab["current"] for tab in on_closing["tabs"]))

    def test_a_hidden_section_frees_its_tab(self):
        prepared_client(modules="customers,items_services,cashboxes,reports,sales_operations")
        owner = make_user(username="tab_small_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        shell = app_shell(owner, "en", set(usable_modules()), reverse("sales:list"))
        self.assertEqual([tab["key"] for tab in shell["tabs"]], ["dashboard", "operations", "customers", "items"])
        self.assertEqual(shell["tabs"][1]["label"], "Sales")


class ShellStylesheetTests(SimpleTestCase):
    def test_shell_styles_come_from_the_tokens(self):
        with open(finders.find("hesba/css/shell.css"), encoding="utf-8") as handle:
            css = handle.read()
        with open(finders.find("hesba/css/tokens.css"), encoding="utf-8") as handle:
            defined = set(re.findall(r"(--hs-[\w-]+)\s*:", handle.read()))
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])

    def test_the_drawer_is_below_the_sidebar_breakpoint_only(self):
        with open(finders.find("hesba/css/shell.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertIn("@media (max-width: 1023px)", css)
        self.assertIn("@media (max-width: 700px)", css)

    def test_tables_fit_beside_the_sidebar_on_tablet_landscape(self):
        # At 1024px the sidebar leaves ~740px; a fixed 760px table minimum made
        # every ordinary list scroll sideways inside its frame.
        for sheet, table in (("hesba/css/operations.css", ".op-table"), ("hesba/css/master_data.css", ".md-table")):
            with self.subTest(sheet=sheet):
                with open(finders.find(sheet), encoding="utf-8") as handle:
                    css = handle.read()
                band = css[css.index("@media (min-width: 1024px) and (max-width: 1439px)"):]
                self.assertIn(table + "{min-width:0}", band)


class SidebarRailTests(TestCase):
    """SHELL-003: the sidebar folds to an icon rail from 1024px up."""

    def test_every_section_has_an_icon(self):
        import pathlib

        from django.conf import settings

        from reports.navigation import NAV_ITEMS

        icons = (pathlib.Path(settings.BASE_DIR) / "templates/partials/shell_icons.html").read_text(encoding="utf-8")
        for item in NAV_ITEMS:
            with self.subTest(key=item["key"]):
                self.assertIn(f'id="hs-i-{item["key"]}"', icons)

    def test_shell_ships_the_toggle_and_icons(self):
        prepared_client()
        signed_in(self, RoleCode.OWNER, "rail_owner")
        response = self.client.get(reverse("sales:list"))
        self.assertContains(response, "data-rail-toggle")
        self.assertContains(response, 'href="#hs-i-operations"')
        self.assertContains(response, 'class="hs-nav__label"')
        # Labels stay in the page for screen readers and tooltips when folded.
        self.assertContains(response, 'title="عمليات البيع"')

    def test_rail_rules_only_apply_beside_the_sidebar(self):
        with open(finders.find("hesba/css/shell.css"), encoding="utf-8") as handle:
            css = handle.read()
        rail = css[css.index("/* Icons and the rail (SHELL-003) */"):]
        self.assertIn("@media (min-width: 1024px){", rail)
        self.assertIn("body.hs-rail .hs-app{grid-template-columns:80px minmax(0,1fr)}", rail)
