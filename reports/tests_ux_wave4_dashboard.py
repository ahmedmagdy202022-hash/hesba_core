"""UX Wave 4 (part 2): the dashboard on a phone.

The phone dashboard opens like the artboard: the navy band with a calm greeting line and the bell, a money card inside
the band (today's sales, their invoice count, today against the average day and the period's trend line, all figures
the view already works out), the shortcuts as tinted chips, then the day's tiles and what needs attention. These tests
pin that the money card shows only the view's own figures, that it never takes a figure's one-place markers (data-kpi,
data-metric, the desktop hero's hooks), that the shortcuts keep their links and permission rules, that nothing a role
saw before is lost, and that the phone-only rules stay phone-and-screen-only and read tokens only.
"""

import re
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_item, make_location, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from settings_core.templatetags.hesba_format import money

from .tests_analytics import person
from .tests_dashboard import prepared_client, sell
from .tests_ux_wave4 import TEMPLATES, read_static

CAIRO = ZoneInfo("Africa/Cairo")
MID = datetime(2026, 12, 15, 12, 0, tzinfo=CAIRO)  # "today" is 15 Dec, Cairo local


def frozen(moment):
    return mock.patch("django.utils.timezone.now", return_value=moment)


def phone_hero(body):
    match = re.search(r'<section class="dash-phero".*?</section>', body, re.S)
    return match.group(0) if match else ""


def phone_block(css):
    marker = "/* UX Wave 4 (part 2): the dashboard on a phone"
    return css[css.index(marker):]


class PhoneMoneyCardTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        Period.objects.create(period_code="W4D", name="w4d", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        self.owner = person(RoleCode.OWNER, "w4d_owner")
        self.location, self.cashbox, self.item = make_location(), make_cashbox(cashbox_code="W4D-CASH"), make_item(item_code="W4D-1")
        stock_in(self.item, self.location, 100, "6.00", movement_date=date(2026, 10, 1))
        recalculate_item_average_cost(self.item)

    def sell(self, number, quantity, when):
        sell(self.item, self.location, self.cashbox, quantity, "10.00", "0.00", self.owner, number=number, when=when)

    def get(self, user=None, params=None):
        self.client.force_login(user or self.owner)
        response = self.client.get(reverse("dashboard_snapshot"), params or {})
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_card_shows_todays_sales_count_share_of_the_average_day_and_the_trend(self):
        with frozen(MID):
            self.sell("W4D-1", 2, date(2026, 12, 15))  # today: 20.00, one invoice
            self.sell("W4D-2", 4, date(2026, 12, 14))
            self.sell("W4D-3", 1, date(2026, 12, 5))
            response = self.get()
        context, body = response.context, response.content.decode()
        sale, count = context["hero_cards"]["sales_today"], context["hero_cards"]["invoice_count_today"]
        hero = context["analytics"]["hero"]
        card = phone_hero(body)
        self.assertTrue(card)
        self.assertIn(f'id="dash-phero-label">{sale["label"]}<', card)
        self.assertIn(f"<bdi>{sale['value']}</bdi>", card)
        self.assertEqual(sale["value"], money(hero["today"]))  # the card and the hero read the same figure
        self.assertIsNotNone(hero["pct"])
        self.assertIn(f"<bdi>{hero['pct']}%</bdi> {context['hero_vs_avg']}", card)  # a share of the average day, no arrow
        self.assertNotRegex(card, r"[▲▼]")
        self.assertIn(f"<bdi>{count['value']}</bdi> {context['invoices_word']}", card)
        self.assertIn('<path class="dash-phero__line" d="M', card)
        self.assertNotIn(context["phero_none"], card)
        # The period's net sales leave the (hidden) hero on a phone: they lead the period's cards there, once.
        self.assertIn('data-phone-metric="net_sales"', body)
        self.assertIn(money(context["headline"]["value"]), body.split('data-phone-metric="net_sales"')[1].split("</article>")[0])

    def test_the_card_takes_no_figure_marker_and_no_desktop_hero_hook(self):
        with frozen(MID):
            self.sell("W4D-4", 1, date(2026, 12, 15))
            body = self.get().content.decode()
        card = phone_hero(body)
        for hook in ("data-kpi=", "data-metric=", "data-hero-stat", "dash-glass", "dash-hstat", "dash-hero"):
            with self.subTest(hook=hook):
                self.assertNotIn(hook, card)
        self.assertEqual(body.count('data-kpi="sales_today"'), 1)
        self.assertEqual(body.count('data-metric="cash"'), 1)
        self.assertLess(body.index("data-phone-hero"), body.index('<section class="dash-hero'))  # the hero is untouched

    def test_a_quiet_day_says_so_instead_of_a_bare_zero(self):
        with frozen(MID):
            self.sell("W4D-5", 3, date(2026, 12, 14))  # nothing today
            response = self.get()
        card = phone_hero(response.content.decode())
        self.assertTrue(response.context["hero_cards"]["sales_today"]["is_zero"])
        self.assertIn("is-zero", card)
        self.assertIn(response.context["phero_none"], card)
        self.assertNotIn("dash-phero__chips", card)
        self.assertNotIn("dash-phero__spark", card)

    def test_a_quiet_day_keeps_the_cash_in_hand_for_someone_without_the_analytics(self):
        # Sees sales and the cashboxes, not the all-sales report: no analytics, so the cash balance lives in the hero only
        # (hero_shows_cash) and, on a phone, in this card only. A day without a sale must not hide it.
        from hesba_testing.factories import grant, make_role, make_user, make_user_profile
        from permissions.models import Permission

        role = make_role(code="W4D-CASHVIEW")
        for code in ("reports.view_sales_report", "cashboxes.view_finance"):
            grant(role, Permission.objects.get(code=code))
        user = make_user(username="w4d_cashview")
        make_user_profile(user=user, role=role)
        with frozen(MID):
            self.sell("W4D-8", 3, date(2026, 12, 14))  # nothing today
            response = self.get(user)
        context, card = response.context, phone_hero(response.content.decode())
        self.assertFalse(context["analytics"].get("available"))
        self.assertTrue(context["hero_shows_cash"])
        self.assertTrue(context["hero_cards"]["sales_today"]["is_zero"])
        cash = context["hero_cards"]["cashbox_balance"]
        self.assertIn(context["phero_none"], card)
        self.assertIn(f'{cash["label"]} <bdi>{cash["value"]}</bdi>', card)
        self.assertNotIn(context["invoices_word"] + "</li>", card)  # no invoice count chip on a day without one

    def test_english_reads_the_same_figures(self):
        with frozen(MID):
            self.sell("W4D-6", 2, date(2026, 12, 15))
            self.sell("W4D-7", 1, date(2026, 12, 10))
            response = self.get(params={"lang": "en"})
        card = phone_hero(response.content.decode())
        self.assertIn("of your average day", card)
        self.assertIn("invoices</li>", card)
        self.assertIn('class="dash-hello"', response.content.decode())

    def test_a_cashier_sees_their_own_sales_and_no_analytics_chip(self):
        cashier = person(RoleCode.CASHIER, "w4d_cashier")
        with frozen(MID):
            response = self.get(cashier)
        context, body = response.context, response.content.decode()
        self.assertFalse(context["analytics"].get("available"))
        sale = context["hero_cards"].get("sales_today")
        card = phone_hero(body)
        if sale:
            self.assertIn(f'id="dash-phero-label">{sale["label"]}<', card)
        self.assertNotIn(context["hero_vs_avg"], card)
        self.assertNotIn("dash-phero__spark", card)
        self.assertNotIn("data-phone-metric", body)

    def test_someone_who_may_see_no_figure_gets_no_card(self):
        from hesba_testing.factories import make_role, make_user, make_user_profile

        user = make_user(username="w4d_nothing")
        make_user_profile(user=user, role=make_role(code="W4D-NONE"))
        with frozen(MID):
            body = self.get(user).content.decode()
        self.assertNotIn("data-phone-hero", body)
        self.assertIn('class="dash-hello"', body)  # the greeting line still stands in the band


class PhoneBandAndListsTests(TestCase):
    def setUp(self):
        prepared_client()

    def test_the_band_greets_with_the_date_and_the_business_and_keeps_the_bell(self):
        owner = person(RoleCode.OWNER, "w4d_band")
        self.client.force_login(owner)
        response = self.client.get(reverse("dashboard_snapshot"))
        body = response.content.decode()
        start = body.index('<header class="dash-header">')
        header = body[start: body.index("</header>", start)]
        hello = header[header.index('class="dash-hello"'):]
        self.assertIn(response.context["now_parts"]["date"], hello)
        self.assertIn(f'title="{response.context["client_name"]}">{response.context["client_name"]}<', hello)
        self.assertIn(response.context["greeting"], hello)
        self.assertIn("data-bell-toggle", header)
        self.assertIn('<h1 class="dash-screen-title">', header)  # the page keeps its heading for screen readers

    def test_shortcuts_keep_their_links_order_and_rules_as_tinted_chips(self):
        for role, username in ((RoleCode.OWNER, "w4d_sc_owner"), (RoleCode.CASHIER, "w4d_sc_cashier")):
            with self.subTest(role=role):
                self.client.force_login(person(role, username))
                response = self.client.get(reverse("dashboard_snapshot"))
                body = response.content.decode()
                actions = response.context["top_actions"]
                shown = re.findall(r'href="([^"]+)" data-top-action="(\w+)"><span class="hs-ichip dash-top-action__chip hs-ichip--(\w+)"', body)
                self.assertEqual([(url, key) for url, key, _ in shown], [(f"{a['url']}?lang=ar", a["key"]) for a in actions])
                tones = {key: tone for _, key, tone in shown}
                self.assertTrue(set(tones.values()) <= {"teal", "gold", "info", "success"})
                if actions:
                    self.assertIn(f'class="dash-top-action is-primary" href="{actions[0]["url"]}?lang=ar"', body)

    def test_every_attention_item_is_still_listed_and_a_row_that_leads_somewhere_is_one_link(self):
        owner = person(RoleCode.OWNER, "w4d_alerts")
        self.client.force_login(owner)
        with mock.patch("reports.dashboard_views.build_alerts", return_value=[
            {"key": "out_of_stock", "severity": "urgent", "ar": "صنف نفد", "en": "Out", "detail_ar": "د", "detail_en": "d", "amount": ""},
            {"key": "w4d_note", "severity": "watch", "ar": "ملاحظة", "en": "Note", "detail_ar": "د", "detail_en": "d", "amount": "12.00"},
        ]):
            response = self.client.get(reverse("dashboard_snapshot"))
        body = response.content.decode()
        alerts = response.context["alerts"]
        self.assertEqual(len(re.findall(r'class="dash-alert is-\w+" data-alert=', body)), len(alerts))
        self.assertIn('data-alert="out_of_stock"><a class="dash-alert__row" href="/reports/inventory/?lang=ar">', body)
        self.assertIn('data-alert="w4d_note"><span class="dash-alert__row">', body)  # no path: no link, no chevron
        row = body.split('data-alert="w4d_note">')[1].split("</li>")[0]
        self.assertNotIn("dm-chevron", row)
        self.assertIn('class="dash-alert__badge">', row)  # the severity is said in words, not only in colour


class PhoneCssTests(SimpleTestCase):
    def test_phone_parts_are_hidden_elsewhere_and_shown_on_phone_screens_only(self):
        css = read_static("hesba/css/dashboard.css")
        block = phone_block(css)
        self.assertIn(".dash-hello,.dash-phero,.dash-metric--phone{display:none}", block)
        phone = block[block.index("@media screen and (max-width:700px){"):]
        self.assertIn(".dash-hero{display:none}", phone)
        self.assertRegex(phone, r"\.dash-phero\{display:block;")
        self.assertIn(".dash-split{display:contents}", phone)  # a plain div: no landmark is lost
        self.assertNotIn("animation", phone)  # nothing moves: reduced motion needs no exception
        self.assertNotIn("@media print", block)

    def test_the_money_card_is_drawn_from_on_navy_tokens(self):
        phone = phone_block(read_static("hesba/css/dashboard.css"))
        self.assertIn("border:1px solid var(--hs-on-navy-line)", phone)
        self.assertIn("background:var(--hs-on-navy-wash)", phone)
        self.assertIn("color:var(--hs-on-navy-muted)", phone)
        self.assertIn("border-radius:0 0 var(--hs-band-radius) var(--hs-band-radius);background:var(--hs-navy)", phone)
        self.assertIn(".dash-header .dash-bell-panel{color:var(--hs-ink)", phone)  # white text never leaks into the panel

    def test_the_shared_chip_is_used_for_metrics_shortcuts_and_attention(self):
        source = (TEMPLATES / "reports/dashboard.html").read_text(encoding="utf-8")
        self.assertIn('class="hs-ichip dash-top-action__chip hs-ichip--', source)
        self.assertIn('class="hs-ichip hs-ichip--sm dash-metric__icon hs-ichip--', source)
        self.assertIn('class="hs-ichip hs-ichip--sm dash-alert__icon hs-ichip--', source)
        css = read_static("hesba/css/dashboard.css")
        self.assertNotIn(".dash-metric__icon--cash{", css)  # the old private washes are gone
        self.assertNotRegex(css, r"\.dash-alert\.is-\w+\{border-inline-start-color")

    def test_the_stylesheet_is_cache_busted(self):
        source = (TEMPLATES / "reports/dashboard.html").read_text(encoding="utf-8")
        self.assertIn("dashboard.css' %}?v=w4-2", source)
