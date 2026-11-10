"""UX Wave 1: the till's pay bar, the grouped menu, the tab bar, the dashboard that says each figure once, and one
button look everywhere. Presentation only; these pin the structure, while the sections a user sees, the figures and the
posting stay covered by their own suites."""

import re
from datetime import date
from decimal import Decimal as D

from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_user, make_user_profile, open_test_shift, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from permissions.services import user_has_permission
from reports.navigation import DRAWER_SHORTCUTS, NAV_GROUPS, NAV_ITEMS, SHORTCUTS, TOP_GROUP, app_shell, nav_items
from reports.tests_dashboard import prepared_client, sign_in_as
from sales.pos import walk_in_customer
from settings_core.setup_services import usable_modules
from settings_core.templatetags.hesba_format import day_text, percent


def read_static(path):
    with open(finders.find(path), encoding="utf-8") as handle:
        return handle.read()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class GroupedMenuTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ux_owner")

    def test_every_section_belongs_to_exactly_one_group(self):
        grouped = [key for group in NAV_GROUPS for key in group["keys"]]
        self.assertEqual(len(grouped), len(set(grouped)))
        for item in NAV_ITEMS:
            with self.subTest(key=item["key"]):
                self.assertTrue(item["key"] == "dashboard" or item["key"] in grouped)

    def test_groups_come_in_the_agreed_order_after_the_dashboard(self):
        items = nav_items(self.owner, "ar", set(usable_modules()))
        self.assertEqual(items[0]["key"], "dashboard")
        self.assertEqual(items[0]["group"], TOP_GROUP)
        order = [group["key"] for group in NAV_GROUPS]
        seen = [item["group"] for item in items[1:]]
        self.assertEqual(seen, sorted(seen, key=order.index))  # each group in one run, in NAV_GROUPS order
        keys = [item["key"] for item in items]
        self.assertLess(keys.index("pos"), keys.index("operations"))  # البيع opens with the till
        self.assertLess(keys.index("operations"), keys.index("items"))
        self.assertLess(keys.index("items"), keys.index("customers"))
        self.assertLess(keys.index("customers"), keys.index("cashboxes"))
        self.assertLess(keys.index("cashboxes"), keys.index("reports"))

    def test_the_sidebar_shows_group_headings_and_the_drawer_shortcuts(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("sales:list")).content.decode()
        aside = re.search(r'<aside class="hs-sidebar".*?</aside>', html, re.S).group(0)
        headings = re.findall(r'<p class="hs-nav__heading"[^>]*>([^<]+)</p>', aside)
        self.assertEqual(headings[:4], ["البيع", "المخزون", "الناس", "الفلوس"])
        self.assertIn("data-shell-shortcuts", aside)
        self.assertIn("الأكثر استخداماً", aside)
        self.assertNotIn('class="hs-nav__link" href="/sales/pos/?lang=ar" data-shortcut', aside)  # shortcuts are not menu links

    def test_shortcuts_follow_the_same_gates(self):
        modules = set(usable_modules())
        for role in (RoleCode.OWNER, RoleCode.CASHIER, RoleCode.STOCK_KEEPER):
            user = person(role, f"ux_short_{role}")
            shell = app_shell(user, "ar", modules, reverse("dashboard_snapshot"))
            sections = {item["key"] for item in shell["items"]}
            with self.subTest(role=role):
                for row in shell["shortcuts"]:
                    spec = SHORTCUTS[row["key"]]
                    self.assertIn(row["key"], DRAWER_SHORTCUTS)
                    if spec.get("permission"):
                        self.assertTrue(user_has_permission(user, spec["permission"]))
                    if spec.get("section"):
                        self.assertIn(spec["section"], sections)
        owner = app_shell(self.owner, "en", modules, reverse("dashboard_snapshot"))
        self.assertEqual([row["key"] for row in owner["shortcuts"]], list(DRAWER_SHORTCUTS))


class TabBarTests(TestCase):
    def test_the_till_lights_the_sales_tab_and_keeps_its_own_menu_link(self):
        prepared_client()
        owner = person(RoleCode.OWNER, "ux_tab_owner")
        shell = app_shell(owner, "ar", set(usable_modules()), reverse("sales:pos"))
        self.assertEqual([tab["key"] for tab in shell["tabs"] if tab["current"]], ["operations"])
        self.assertFalse(shell["more_current"])
        self.assertEqual([item["key"] for item in shell["items"] if item["current"]], ["pos"])

    def test_tabs_carry_icons(self):
        prepared_client()
        sign_in_as(self, RoleCode.OWNER, "ux_tab_icons")
        html = self.client.get(reverse("dashboard_snapshot")).content.decode()
        bar = re.search(r'<nav class="hs-tabbar".*?</nav>', html, re.S).group(0)
        self.assertEqual(bar.count('class="hs-icon"'), len(re.findall(r'<(?:a|button) class="hs-tab[ "]', bar)))
        self.assertIn('href="#hs-i-more"', bar)
        self.assertIn('id="hs-i-more"', get_template("partials/shell_icons.html").template.source)


class DashboardOnceTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = sign_in_as(self, RoleCode.OWNER, "ux_dash_owner")

    def test_shortcuts_under_the_hero_and_not_again_below(self):
        response = self.client.get(reverse("dashboard_snapshot"))
        html = response.content.decode()
        top = [action["key"] for action in response.context["top_actions"]]
        self.assertEqual(top, ["pos", "collect", "new_item", "new_purchase"])
        self.assertIn("data-top-actions", html)
        for key in ("collect", "new_item", "record_purchase"):
            with self.subTest(key=key):
                self.assertNotIn(f'data-action="{key}"', html)
        self.assertIn('data-action="close_day"', html)  # the rest of «ابدأ من هنا» stays

    def test_each_figure_is_shown_once(self):
        response = self.client.get(reverse("dashboard_snapshot"))
        html = response.content.decode()
        metrics = {metric["key"] for metric in response.context["analytics"]["metrics"]}
        shown = {metric["key"] for metric in response.context["shown_metrics"]}
        self.assertIn("cash", metrics)
        self.assertNotIn("net_sales", shown)  # the hero carries it
        self.assertEqual(html.count('data-kpi="cashbox_balance"'), 0)  # the grid's «السيولة في الخزن» carries it
        self.assertEqual(html.count('data-metric="cash"'), 1)
        today = {card["key"] for card in response.context["today_cards"]}
        self.assertFalse(today & {"sales_today", "invoice_count_today", "cashbox_balance", "customer_dues", "supplier_dues"})
        self.assertEqual(html.count('data-kpi="sales_today"'), 1)
        # every card is still worked out (other code and the tests read them); only the page skips the repeats
        self.assertTrue({"cashbox_balance", "customer_dues", "supplier_dues"} <= {card["key"] for card in response.context["cards"]})
        self.assertIn("data-today-cards", html)  # «أرقام اليوم» lives in the period section now
        self.assertNotIn(">أرقام اليوم<", html)

    def test_trends_never_double_their_sign_and_dates_read_one_way(self):
        html = self.client.get(reverse("dashboard_snapshot")).content.decode()
        self.assertNotRegex(html, r"▼</span> <bdi>-")
        self.assertNotIn("■", html)
        self.assertIsNone(re.search(r"\b20\d\d-\d\d-\d\d\b", re.sub(r'datetime="[^"]*"', "", html)))  # no ISO dates on screen

    def test_a_cashier_without_analytics_keeps_their_cards(self):
        sign_in_as(self, RoleCode.CASHIER, "ux_dash_cashier")
        response = self.client.get(reverse("dashboard_snapshot"))
        self.assertFalse(response.context["analytics"].get("available"))
        cards = {card["key"] for card in response.context["cards"]}
        shown = {card["key"] for card in response.context["today_cards"]} | {"sales_today", "invoice_count_today"}
        if response.context["hero_shows_cash"]:
            shown.add("cashbox_balance")  # in the hero instead
        self.assertTrue(cards)
        self.assertLessEqual(cards, shown)  # nothing dropped: no grid repeats them for this role
        self.assertContains(response, ">أرقام اليوم<")

    def test_the_voice_card_says_whose_figures_it_reads(self):
        from ai.summary import WORDS

        self.assertEqual(WORDS["ar"]["title"], "ملخص امبارح")
        self.assertEqual(WORDS["en"]["title"], "Yesterday's summary")


class FormattingTests(SimpleTestCase):
    def test_percent_reads_like_money(self):
        self.assertEqual(percent(D("31.84")), "31.8%")
        self.assertEqual(percent(31.85), "31.9%")
        self.assertEqual(percent(None), "—")

    def test_one_date_format_in_the_page_language(self):
        self.assertEqual(day_text(date(2026, 11, 9), "ar"), "9 نوفمبر 2026")
        self.assertEqual(day_text(date(2026, 11, 9), "en"), "9 November 2026")


class PosPayBarTests(TestCase):
    def setUp(self):
        prepared_client()
        self.cashier = person(RoleCode.CASHIER, "ux_pos_cashier")
        self.client.force_login(self.cashier)
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        open_test_shift(self.cashier, self.cashbox)
        self.belt = make_item(item_code="BELT", default_sale_price="120.00")
        stock_in(self.belt, self.location, 10, "100.00", movement_date=timezone.localdate())
        recalculate_item_average_cost(self.belt)

    def test_one_pay_button_of_each_kind_with_the_primary_first(self):
        html = self.client.get(reverse("sales:pos"), {"lang": "ar"}).content.decode()
        self.assertIn("data-pos-paybar", html)
        self.assertIn("data-pos-bar-total", html)
        pays = re.findall(r'data-pos-pay="(\w+)"', html)
        self.assertEqual(sorted(pays), sorted(set(pays)))  # never a second submit of the same kind
        self.assertIn('class="op-primary pos-pay"', html.split("data-pos-pay=")[0][-80:])  # F9 presses the first: the primary
        self.assertIn("data-pos-details", html)
        self.assertIn('<details class="pos-more">', html)
        self.assertIn("pos-keys", html)
        self.assertIn("امسح أو اكتب الكود", html)

    def test_after_a_sale_the_banner_shows_the_change_due_once(self):
        data = {"line_count": "1", "item_0": str(self.belt.pk), "qty_0": "1", "price_0": "120.00", "customer": str(walk_in_customer().pk),
                "location": str(self.location.pk), "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "200", "print": "0"}
        page = self.client.post(reverse("sales:pos"), data, follow=True).content.decode()
        self.assertIn("data-pos-last", page)
        self.assertEqual(page.count("الباقي للعميل 80.00"), 1)
        self.assertIn("data-pos-last-change", page)
        self.assertNotIn("op-message op-message-pos-sale", page)  # the sale's message moved into the banner


class SharedButtonTests(TestCase):
    def test_every_signed_in_screen_loads_the_shared_buttons(self):
        source = get_template("base_app.html").template.source
        self.assertLess(source.index("hesba/css/tokens.css"), source.index("hesba/css/buttons.css"))
        prepared_client()
        sign_in_as(self, RoleCode.OWNER, "ux_buttons")
        for name in ("master_data:items", "master_data:customers", "master_data:suppliers", "sales:list"):
            with self.subTest(route=name):
                self.assertContains(self.client.get(reverse(name)), "hesba/css/buttons.css")

    def test_a_button_matches_a_link(self):
        css = read_static("hesba/css/buttons.css")
        rule = re.search(r"\.op-primary,\.op-secondary,\.op-danger\{([^}]*)\}", css).group(1)
        self.assertIn("font:inherit", rule)
        self.assertIn("border-radius:var(--hs-radius-md)", rule)
        self.assertIn("min-height:var(--hs-control-height)", rule)

    def test_new_sheets_take_colours_from_the_tokens(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        for sheet in ("hesba/css/buttons.css", "hesba/css/pos.css"):
            with self.subTest(sheet=sheet):
                css = read_static(sheet)
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
                self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])
