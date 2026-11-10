"""UX Wave 4 (part 1): the calm interior every signed-in screen inherits.

Ahmed (10 Nov 2026): «الأبيض الكتير بيزغلل العين». The app stands on the neutral wash, its cards are white with no
border, phones get a navy header band and a floating navy tab bar with «بيع» raised in the middle. These tests pin
the tokens, the shared rules that read them, the tab bar's order, and that the public pages are left alone.
"""

import pathlib
import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.navigation import app_shell, tab_bar
from reports.tests_dashboard import prepared_client
from settings_core.setup_services import usable_modules

CARD = "border:var(--hs-card-border);border-radius:var(--hs-card-radius);background:var(--hs-card-bg);box-shadow:var(--hs-card-shadow)"
TEMPLATES = pathlib.Path(settings.BASE_DIR) / "templates"


def read_static(path):
    found = finders.find(path)
    assert found, f"{path} is not collectable"
    with open(found, encoding="utf-8") as handle:
        return handle.read()


def rule(css, selector):
    """The declarations of the first rule whose selector is exactly ``selector``."""
    match = re.search(r"(?:^|[}\n])\s*" + re.escape(selector) + r"\{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


def defined_tokens():
    return dict(re.findall(r"(--hs-[\w-]+)\s*:\s*([^;]+);", read_static("hesba/css/tokens.css")))


class CalmTokensTests(SimpleTestCase):
    def test_the_app_ground_is_the_neutral_wash_and_the_public_ground_is_unchanged(self):
        tokens = defined_tokens()
        self.assertEqual(tokens["--hs-page"], "var(--hs-neutral-wash)")
        self.assertEqual(tokens["--hs-neutral-wash"].upper(), "#EEF2F6")
        # login, /demo/, customer links, print and the error pages still stand on --hs-ground
        self.assertEqual(tokens["--hs-ground"].upper(), "#F6F8FA")

    def test_card_band_and_tab_bar_tokens_exist(self):
        tokens = defined_tokens()
        for name in ("--hs-card-bg", "--hs-card-border", "--hs-card-radius", "--hs-card-radius-sm", "--hs-card-shadow",
                     "--hs-input-radius", "--hs-chip-size", "--hs-chip-radius", "--hs-gutter", "--hs-band-radius",
                     "--hs-on-navy", "--hs-on-navy-wash", "--hs-focus-ring-on-navy", "--hs-tab-active-wash",
                     "--hs-tabbar-gap", "--hs-tabbar-space", "--hs-tabbar-clear", "--hs-tab-rise"):
            with self.subTest(token=name):
                self.assertIn(name, tokens)
        # transparent, not none: box sizes and border-color states keep working, forced-colours mode still draws it
        self.assertEqual(tokens["--hs-card-border"], "1px solid transparent")

    def test_app_bodies_stand_on_the_page_token(self):
        self.assertIn("--op-bg:var(--hs-page)", read_static("hesba/css/operations.css"))
        self.assertIn("--md-bg:var(--hs-page)", read_static("hesba/css/master_data.css"))
        self.assertIn("background:var(--hs-page)", rule(read_static("hesba/css/dashboard.css"), ".dashboard-page"))

    def test_shared_card_containers_read_the_card_tokens(self):
        cases = (
            ("hesba/css/operations.css", (".op-table-wrap", ".op-form", ".op-detail-meta", ".op-card")),
            ("hesba/css/master_data.css", (".md-table-wrap", ".md-form")),
            ("hesba/css/form_controls.css", (".hs-section",)),
            ("hesba/css/settings.css", (".set-card", ".set-list")),
        )
        for sheet, selectors in cases:
            css = read_static(sheet)
            for selector in selectors:
                with self.subTest(sheet=sheet, selector=selector):
                    self.assertIn(CARD, rule(css, selector))
        self.assertIn("var(--hs-card-border)", read_static("hesba/css/table_cards.css"))
        self.assertIn("border:var(--hs-card-border)", rule(read_static("hesba/css/form_controls.css"), ".hs-tile"))

    def test_changed_shared_sheets_carry_no_colour_literals_and_only_defined_tokens(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        for sheet in ("shell.css", "buttons.css", "lists.css", "form_controls.css", "table_cards.css", "pager.css",
                      "dashboard.css", "demo_feedback.css", "pos.css"):
            with self.subTest(sheet=sheet):
                css = read_static(f"hesba/css/{sheet}")
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
                self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])

    def test_secondary_buttons_are_quiet(self):
        css = read_static("hesba/css/buttons.css")
        self.assertIn(".op-secondary{border:1px solid transparent;background:var(--hs-surface);color:var(--hs-navy)}", css)
        self.assertRegex(css, r":is\([^)]*\.op-form[^)]*\) \.op-secondary\{background:var\(--hs-page\)\}")

    def test_the_icon_chip_is_shared(self):
        css = read_static("hesba/css/form_controls.css")
        self.assertIn("border-radius:var(--hs-chip-radius)", rule(css, ".hs-ichip"))
        for tone in ("teal", "gold", "info", "success", "danger", "warning"):
            with self.subTest(tone=tone):
                self.assertIn(f".hs-ichip--{tone}{{", css)
        self.assertIn("border-radius:var(--hs-chip-radius)", read_static("hesba/css/report_tools.css"))


class PhoneHeaderAndTabBarCssTests(SimpleTestCase):
    def test_the_header_band_is_phone_and_screen_only(self):
        css = read_static("hesba/css/shell.css")
        band = css[css.index("/* UX Wave 4: the phone header band."):]
        self.assertTrue(band.split("*/", 1)[1].lstrip().startswith("@media screen and (max-width: 700px){"))
        self.assertIn("border-radius:0 0 var(--hs-band-radius) var(--hs-band-radius);background:var(--hs-navy)", band)
        self.assertIn("margin:0 calc(-1 * var(--hs-gutter))", band)
        self.assertIn("color:var(--hs-on-navy)", band)

    def test_the_top_bar_is_navy_and_its_controls_are_scoped_past_lists_css(self):
        css = read_static("hesba/css/shell.css")
        self.assertRegex(css, r"\.hs-topbar\{[^}]*background:var\(--hs-navy\)")
        self.assertIn(".hs-topbar .hs-icon-button,.hs-topbar .hs-chip{", css)

    def test_the_tab_bar_floats_and_fixed_bars_clear_it(self):
        css = read_static("hesba/css/shell.css")
        self.assertIn(".hs-topbar,.hs-tabbar{display:none}", css)  # only below the sidebar breakpoint
        phone = css[css.index("/* UX Wave 4: the phone tab bar"):]
        self.assertIn("bottom:calc(var(--hs-tabbar-gap) + env(safe-area-inset-bottom))", phone)
        self.assertIn("border-radius:var(--hs-tabbar-radius);background:var(--hs-navy)", phone)
        self.assertIn(".hs-main{padding-bottom:calc(var(--hs-tabbar-clear)", phone)
        self.assertIn("(prefers-reduced-motion: no-preference)", phone)
        # the till's pay bar reaches the bottom edge and the bar rides on it; forms' save rows stop above it
        self.assertIn(".pos-paybar{bottom:0;padding-bottom:calc(var(--hs-tabbar-space)", read_static("hesba/css/pos.css"))
        self.assertIn("bottom:calc(var(--hs-tabbar-clear) + env(safe-area-inset-bottom))", read_static("hesba/css/lists.css"))
        self.assertIn("bottom:calc(var(--hs-tabbar-clear) + env(safe-area-inset-bottom,0px))", read_static("hesba/css/form_controls.css"))
        self.assertNotIn("--hs-tabbar-height) + env", read_static("hesba/css/pos.css"))

    def test_demo_feedback_moves_only_on_app_pages(self):
        css = read_static("hesba/css/demo_feedback.css")
        moved = [line for line in css.splitlines() if "--hs-tabbar-clear" in line]
        self.assertTrue(moved)
        self.assertTrue(all("body:has(.hs-tabbar)" in line for line in moved))


class PublicPagesUntouchedTests(SimpleTestCase):
    def test_public_sheets_do_not_read_the_app_tokens(self):
        for sheet in ("login.css", "errors.css", "customer_link.css", "booking.css", "print.css", "demo_first_run.css",
                      "activity_selection.css"):
            with self.subTest(sheet=sheet):
                css = read_static(f"hesba/css/{sheet}")
                self.assertNotRegex(css, r"var\(--hs-(page|card-|band-|on-navy-wash|tabbar-(space|clear|gap))")

    def test_public_templates_link_no_app_sheet(self):
        for name in ("registration/login.html", "demo/landing.html", "demo/sub_activities.html", "404.html", "500.html",
                     "printing/share_base.html", "appointments/book_public.html"):
            with self.subTest(template=name):
                text = (TEMPLATES / name).read_text(encoding="utf-8")
                for sheet in ("shell.css", "lists.css", "form_controls.css", "operations.css", "master_data.css"):
                    self.assertNotIn(f"hesba/css/{sheet}", text)


class StylesheetVersionTests(SimpleTestCase):
    def test_changed_sheets_are_cache_busted_where_linked(self):
        expected = {
            "base_app.html": ("tokens.css' %}?v=w4-1", "buttons.css' %}?v=w4-1", "lists.css' %}?v=w4-1",
                              "table_cards.css' %}?v=w4-1", "form_controls.css' %}?v=w4-1", "pager.css' %}?v=w4-1",
                              "report_tools.css' %}?v=w4-1"),
            "operations/base.html": ("operations.css' %}?v=w4-1", "shell.css' %}?v=w4-1"),
            "master_data/base.html": ("master_data.css' %}?v=w4-1", "shell.css' %}?v=w4-1"),
            "master_data/popup_base.html": ("master_data.css' %}?v=w4-1",),
            "reports/dashboard.html": ("dashboard.css' %}?v=w4-1", "shell.css' %}?v=w4-1"),
            "demo/begin.html": ("shell.css' %}?v=w4-1",),
            "sales/pos.html": ("pos.css' %}?v=w4-1",),
            "settings_core/base.html": ("settings.css' %}?v=st-5",),
            "partials/demo_feedback.html": ("demo_feedback.css' %}?v=demo-feedback-7",),
        }
        for name, links in expected.items():
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            for link in links:
                with self.subTest(template=name, link=link):
                    self.assertIn(link, text)


class TabBarOrderTests(SimpleTestCase):
    def tabs(self, *keys):
        return [{"key": key, "label": key, "url": f"/{key}/", "current": False} for key in keys]

    def test_the_selling_tab_moves_to_the_middle_and_is_raised(self):
        bar = tab_bar(self.tabs("dashboard", "operations", "inventory", "customers"))
        self.assertEqual([tab["key"] for tab in bar], ["dashboard", "inventory", "operations", "customers"])
        self.assertEqual([tab["raised"] for tab in bar], [False, False, True, False])

    def test_the_till_counts_as_selling(self):
        bar = tab_bar(self.tabs("dashboard", "restaurant", "pos", "inventory"))
        self.assertEqual([tab["key"] for tab in bar if tab["raised"]], ["pos"])
        self.assertEqual(bar[2]["key"], "pos")

    def test_without_a_selling_tab_or_a_middle_the_bar_is_plain(self):
        for keys in (("dashboard", "appointments", "medical", "customers"), ("dashboard", "operations")):
            with self.subTest(keys=keys):
                bar = tab_bar(self.tabs(*keys))
                self.assertEqual([tab["key"] for tab in bar], list(keys))
                self.assertFalse(any(tab["raised"] for tab in bar))

    def test_the_priority_list_is_not_reordered(self):
        tabs = self.tabs("dashboard", "operations", "inventory", "customers")
        tab_bar(tabs)
        self.assertEqual([tab["key"] for tab in tabs], ["dashboard", "operations", "inventory", "customers"])
        self.assertNotIn("raised", tabs[1])


class TabBarMarkupTests(TestCase):
    def test_the_phone_bar_renders_in_display_order_with_one_icon_per_tab(self):
        prepared_client()
        owner = make_user(username="w4_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        html = self.client.get(reverse("sales:list")).content.decode()
        bar = re.search(r'<nav class="hs-tabbar".*?</nav>', html, re.S).group(0)
        self.assertEqual(re.findall(r'data-tab="(\w+)"', bar), ["dashboard", "inventory", "operations", "customers"])
        self.assertIn('class="hs-tab hs-tab--sell is-current"', bar)
        self.assertIn('<span class="hs-tab__orb" aria-hidden="true"><svg class="hs-icon"', bar)
        self.assertEqual(bar.count('class="hs-icon"'), len(re.findall(r'<(?:a|button) class="hs-tab[ "]', bar)))
        # the sidebar and every reader of shell["tabs"] keep the priority order
        shell = app_shell(owner, "ar", set(usable_modules()), reverse("sales:list"))
        self.assertEqual([tab["key"] for tab in shell["tabs"]], ["dashboard", "operations", "inventory", "customers"])
