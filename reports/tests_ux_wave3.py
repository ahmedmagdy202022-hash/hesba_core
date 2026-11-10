"""UX Wave 3: branded 404/500 pages, the old /home/ bookmark, English dates on English pages, numbers that keep their
order in Arabic lines, the report pages' shared header, the profit report's period chips, the settings menu and the
one back arrow. Presentation only; these pin the structure, while the figures, permissions and posting stay covered by
their own suites."""

import pathlib
import re
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.decorators import login_not_required
from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import path, reverse
from django.utils import timezone, translation
from django.views.defaults import server_error

from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from reports.analytics import period_bounds
from reports.selectors import profit_totals
from reports.tests_dashboard import prepared_client, sell
from settings_core.templatetags.hesba_format import day_text, ltr, ltr_numbers

TODAY = timezone.localdate()


def read_static(path_):
    with open(finders.find(path_), encoding="utf-8") as handle:
        return handle.read()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def tokens_only(test, css):
    """A stylesheet of this wave carries no colour literal and only tokens that exist."""

    test.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
    defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
    test.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])


@login_not_required
def _boom(request):
    raise RuntimeError("UX Wave 3: a deliberate failure to render the 500 page")


#: The 500 test serves this module as the URLconf: one view that fails.
urlpatterns = [path("boom/", _boom)]


@override_settings(DEBUG=False)
class NotFoundPageTests(TestCase):
    """The test runner keeps DEBUG off, so a missing URL renders templates/404.html as production does."""

    def test_a_missing_page_is_branded_with_one_button_home(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux3_404_owner"))
        page = self.client.get("/no-such-page/")
        self.assertEqual(page.status_code, 404)
        body = page.content.decode()
        self.assertIn('data-error-page="404"', body)
        self.assertIn("الصفحة دي مش موجودة", body)
        self.assertIn('dir="rtl"', body)
        self.assertIn("hesba/brand/hesba-logo-reversed.png", body)
        self.assertEqual(body.count('class="op-primary"'), 1)  # one primary action
        self.assertIn(f'href="{reverse("dashboard_snapshot")}?lang=ar" data-error-home', body)

    def test_english_follows_the_page_language(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux3_404_en"))
        body = self.client.get("/no-such-page/?lang=en").content.decode()
        self.assertIn('<html lang="en" dir="ltr">', body)
        self.assertIn("This page doesn't exist", body)
        self.assertIn("Back to the dashboard", body)
        self.assertNotIn("الصفحة دي مش موجودة", body)

    def test_signed_out_it_offers_the_login(self):
        page = self.client.get("/no-such-page/")
        self.assertEqual(page.status_code, 404)
        self.assertIn(f'href="{reverse("login")}" data-error-home', page.content.decode())

    @override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
    def test_on_a_showcase_a_visitor_goes_back_to_the_demo_landing(self):
        page = self.client.get("/no-such-page/")
        self.assertEqual(page.status_code, 404)
        self.assertIn(f'href="{reverse("demo_landing")}?lang=ar" data-error-home', page.content.decode())


@override_settings(ROOT_URLCONF=__name__, DEBUG=False)
class ServerErrorPageTests(TestCase):
    def test_a_failing_view_renders_the_branded_500(self):
        client = Client(raise_request_exception=False)
        page = client.get("/boom/")
        self.assertEqual(page.status_code, 500)
        body = page.content.decode()
        self.assertIn('data-error-page="500"', body)
        self.assertIn("حصلت مشكلة عندنا", body)
        self.assertIn("Something went wrong on our side", body)  # the English copy, shown by the script on ?lang=en
        self.assertIn('href="/" data-error-home', body)

    def test_the_500_page_needs_no_context_and_no_database(self):
        # server_error renders with an empty context: no request, no context processors.
        with self.assertNumQueries(0):
            page = server_error(RequestFactory().get("/"))
        self.assertEqual(page.status_code, 500)
        self.assertIn("حصلت مشكلة عندنا", page.content.decode())

    def test_the_500_template_loads_only_static(self):
        source = open(get_template("500.html").origin.name, encoding="utf-8").read()
        self.assertEqual(re.findall(r"{%\s*load\s+([^%]+)%}", source), ["static "])
        self.assertNotIn("{{", source)  # nothing from a context
        self.assertNotIn("{% url", source)


class ErrorStyleTests(SimpleTestCase):
    def test_the_error_sheet_uses_tokens_only(self):
        css = read_static("hesba/css/errors.css")
        tokens_only(self, css)
        self.assertIn("background:var(--hs-navy)", css)


class LegacyHomeTests(TestCase):
    def test_a_signed_in_bookmark_lands_on_the_dashboard_for_good(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux3_home"))
        page = self.client.get("/home/")
        self.assertEqual(page.status_code, 301)
        self.assertEqual(page["Location"], reverse("dashboard_snapshot"))
        self.assertEqual(self.client.get("/home/?lang=en")["Location"], reverse("dashboard_snapshot") + "?lang=en")
        self.assertEqual(reverse("home"), "/home/")  # the name stays, for whatever still reverses it

    def test_signed_out_it_goes_through_the_login_and_back(self):
        page = self.client.get("/home/")
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page["Location"], reverse("login") + "?next=/home/")


class EnglishFormatsTests(TestCase):
    """An English page (?lang=en) renders Django's own dates in English; an Arabic page is unchanged."""

    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ux3_formats")
        self.client.force_login(self.owner)
        item, location, cashbox = make_item(), make_location(), make_cashbox()
        stock_in(item, location, "10", "5.00")
        recalculate_item_average_cost(item)
        self.when = date(TODAY.year, TODAY.month, 1)
        sell(item, location, cashbox, "2", "12.50", "25.00", self.owner, number="SI-UX3", when=self.when)

    def test_the_profit_report_dates_follow_the_page_language(self):
        from django.utils.formats import date_format

        with translation.override("en"):
            english = date_format(self.when)  # Django's English DATE_FORMAT, e.g. "Nov. 1, 2026"
        with translation.override("ar"):
            arabic = date_format(self.when, "F")
        en = self.client.get(reverse("reports:profit") + "?lang=en").content.decode()
        ar = self.client.get(reverse("reports:profit") + "?lang=ar").content.decode()
        self.assertIn(english, en)
        self.assertNotIn(arabic, en)
        self.assertIn(arabic, ar)
        self.assertIn("25.00", en)  # money keeps its one style in both languages
        self.assertIn("25.00", ar)

    def test_the_language_switch_lasts_only_for_the_request(self):
        self.client.get(reverse("reports:profit") + "?lang=en")
        self.assertEqual(translation.get_language(), "ar")

    def test_a_bare_decimal_follows_the_page_language(self):
        from django.http import HttpResponse
        from django.template import Context, Template

        from config.middleware import PageLanguageMiddleware

        view = PageLanguageMiddleware(lambda request: HttpResponse(Template("{{ v }}").render(Context({"v": D("12.5")}))))
        self.assertEqual(view(RequestFactory().get("/", {"lang": "en"})).content.decode(), "12.5")
        self.assertEqual(view(RequestFactory().get("/", {"lang": "ar"})).content.decode(), "12,5")  # Arabic as before
        self.assertEqual(view(RequestFactory().get("/")).content.decode(), "12,5")

    def test_day_text_follows_the_active_language_without_an_argument(self):
        with translation.override("en"):
            self.assertEqual(day_text(date(2026, 11, 9)), "9 November 2026")
        with translation.override("ar"):
            self.assertEqual(day_text(date(2026, 11, 9)), "9 نوفمبر 2026")
        self.assertEqual(day_text(date(2026, 11, 9), "en"), "9 November 2026")  # an explicit language still wins


class BidiFilterTests(SimpleTestCase):
    def test_a_range_keeps_its_order_and_the_word_stays_outside(self):
        self.assertEqual(ltr_numbers("31–60"), '<bdi dir="ltr">31–60</bdi>')
        self.assertEqual(ltr_numbers("0–30 يوم"), '<bdi dir="ltr">0–30</bdi> يوم')
        self.assertEqual(ltr_numbers("أكتر من 90"), 'أكتر من <bdi dir="ltr">90</bdi>')
        self.assertEqual(ltr_numbers("+20 100 123 4567"), '<bdi dir="ltr">+20 100 123 4567</bdi>')

    def test_both_filters_escape(self):
        self.assertEqual(ltr("<b>INV-1</b>"), '<bdi dir="ltr">&lt;b&gt;INV-1&lt;/b&gt;</bdi>')
        self.assertEqual(ltr_numbers("<i>1–2</i>"), '&lt;i&gt;<bdi dir="ltr">1–2</bdi>&lt;/i&gt;')
        self.assertEqual(ltr(None), "")
        self.assertEqual(ltr(""), "")


REPORT_PAGES = ("reports:sales", "reports:purchases", "reports:inventory", "reports:customers", "reports:suppliers",
                "reports:cashboxes", "reports:profit", "reports:aging", "reports:daily", "reports:insights")


class ReportHeaderTests(TestCase):
    def setUp(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux3_reports"))

    def test_every_report_goes_back_to_the_hub_and_wears_its_card_mark(self):
        hub = reverse("report_hub")
        for name in REPORT_PAGES:
            for lang, word in (("ar", "التقارير"), ("en", "Reports")):
                with self.subTest(report=name, lang=lang):
                    page = self.client.get(f"{reverse(name)}?lang={lang}")
                    self.assertEqual(page.status_code, 200)
                    body = page.content.decode()
                    self.assertIn(f'<a class="op-back" href="{hub}?lang={lang}" data-report-back><svg class="hs-back-arrow"', body)
                    self.assertRegex(body, rf'data-report-back><svg class="hs-back-arrow"[^>]*>.*?</svg>{word}</a>')
                    self.assertIn('<h1 class="hs-report-title"><span class="hs-report-mark hs-report-mark--', body)
                    self.assertNotIn("← ", body[body.index("<main"):] if "<main" in body else body)

    def test_the_mark_matches_the_hub_card(self):
        from reports.functional_views import CARD_LOOK

        body = self.client.get(reverse("reports:profit")).content.decode()
        icon, tone = CARD_LOOK["reports:profit"]
        self.assertIn(f'hs-report-mark--{tone}" aria-hidden="true" data-report-mark><svg class="hs-icon"><use href="#hs-i-{icon}">', body)

    def test_the_hub_has_a_real_back_link_instead_of_a_raw_one(self):
        body = self.client.get(reverse("report_hub")).content.decode()
        self.assertIn(f'<a class="op-back" href="{reverse("dashboard_snapshot")}?lang=ar" data-report-back>', body)
        self.assertNotIn('<nav class="op-form-actions">', body)

    def test_every_report_table_sits_in_a_card(self):
        for name in REPORT_PAGES:
            with self.subTest(report=name):
                body = self.client.get(reverse(name)).content.decode()
                for match in re.finditer(r'<table class="op-table', body):
                    self.assertIn('class="op-table-wrap"', body[max(0, match.start() - 200):match.start()], name)

    def test_the_heading_sheet_uses_tokens_only(self):
        css = read_static("hesba/css/report_tools.css")
        tokens_only(self, css[css.index("/* UX Wave 3"):])  # this wave's part (the print sheet above keeps its black ink)
        self.assertIn(".hs-report-mark--gold{--tone:var(--hs-gold);--tone-wash:var(--hs-gold-wash)}", css)
        self.assertIn(".hs-period{display:inline-flex;align-items:center;justify-content:center;min-height:var(--hs-control-height)", css)


class AgingBidiTests(TestCase):
    def test_the_age_ranges_keep_their_order_in_arabic(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux3_aging"))
        body = self.client.get(reverse("reports:aging") + "?lang=ar").content.decode()
        self.assertIn('<th><bdi dir="ltr">31–60</bdi></th>', body)
        self.assertIn('<small><bdi dir="ltr">0–30</bdi> يوم</small>', body)
        self.assertIn('<th>أكتر من <bdi dir="ltr">90</bdi></th>', body)

    def test_phone_card_labels_carry_the_pinned_direction(self):
        # On a phone the header text becomes the card label through CSS attr(), which cannot hold
        # markup; the script turns dir="ltr" into Unicode isolates so «31–60» still reads in order.
        script = pathlib.Path(finders.find("hesba/js/table_cards.js")).read_text(encoding="utf-8")
        self.assertIn("\\u2066", script)
        self.assertIn("\\u2069", script)
        self.assertIn("names.push(text(cell).trim())", script)


class ProfitPeriodChipsTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ux3_chips")
        self.client.force_login(self.owner)
        item, location, cashbox = make_item(), make_location(), make_cashbox()
        stock_in(item, location, "20", "5.00")
        recalculate_item_average_cost(item)
        sell(item, location, cashbox, "2", "12.50", "25.00", self.owner, number="SI-UX3-T", when=TODAY)
        if TODAY.day > 1:
            sell(item, location, cashbox, "1", "10.00", "10.00", self.owner, number="SI-UX3-M", when=TODAY.replace(day=1))
        sell(item, location, cashbox, "3", "10.00", "30.00", self.owner, number="SI-UX3-OLD", when=TODAY - timedelta(days=70))

    def chips(self, query=""):
        page = self.client.get(reverse("reports:profit") + "?lang=ar" + query)
        self.assertEqual(page.status_code, 200)
        return page, {chip["key"]: chip for chip in _chips_of(page)}

    def test_four_chips_each_44px_link_with_plain_words(self):
        page, chips = self.chips()
        self.assertEqual([chip["label"] for chip in chips.values()], ["النهارده", "الأسبوع", "الشهر", "مخصوص"])
        self.assertTrue(chips["custom"]["current"])  # no dates: the whole history, as before, under «مخصوص»
        self.assertEqual(chips["custom"]["url"], "#report-dates")
        self.assertContains(page, 'id="report-dates"')

    def test_each_chip_fills_the_dates_and_the_totals_are_the_reports_own(self):
        for key, period in (("today", "today"), ("week", "7d"), ("month", "month")):
            with self.subTest(chip=key):
                start, end = period_bounds(period, TODAY)[:2]
                page, chips = self.chips(f"&date_from={start.isoformat()}&date_to={end.isoformat()}")
                self.assertTrue(chips[key]["current"])
                self.assertFalse(chips["custom"]["current"])
                self.assertIn(f"date_from={start.isoformat()}&date_to={end.isoformat()}", chips[key]["url"])
                self.assertEqual(page.context["totals"]["sales"], profit_totals(start, end)["sales"])
                self.assertEqual(page.context["totals"]["profit"], profit_totals(start, end)["profit"])

    def test_no_chip_changes_the_unfiltered_report(self):
        page, _ = self.chips()
        self.assertEqual(page.context["totals"]["sales"], profit_totals(None, None)["sales"])
        self.assertEqual(page.context["totals"]["sales"], D("65.00") if TODAY.day > 1 else D("55.00"))

    def test_english_chips(self):
        page = self.client.get(reverse("reports:profit") + "?lang=en")
        self.assertEqual([chip["label"] for chip in _chips_of(page)], ["Today", "This week", "This month", "Custom"])


def _chips_of(page):
    body = page.content.decode()
    found = re.findall(r'<a class="hs-period" href="([^"]+)" data-period-chip="(\w+)"( aria-current="true")?>([^<]+)</a>', body)
    return [{"url": url.replace("&amp;", "&"), "key": key, "current": bool(current), "label": label} for url, key, current, label in found]


class SettingsMenuTests(TestCase):
    def setUp(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux3_settings"))

    def test_each_destination_is_listed_once(self):
        body = self.client.get(reverse("settings_core:currency") + "?lang=ar").content.decode()
        self.assertNotIn("set-hero__edit", body)  # the card no longer repeats «بيانات الشركة»
        nav = body[body.index("data-settings-nav>"):body.index("</nav>", body.index("data-settings-nav>"))]
        hrefs = re.findall(r'href="([^"]+)"', nav)
        self.assertEqual(len(hrefs), len(set(hrefs)))
        self.assertNotIn("set-nav--overview", body)

    def test_on_the_overview_the_tiles_are_the_phone_menu(self):
        body = self.client.get(reverse("settings_core:overview") + "?lang=ar").content.decode()
        self.assertIn('class="set-nav set-nav--overview"', body)  # kept on a desktop, hidden on a phone by the sheet
        css = read_static("hesba/css/settings.css")
        phone = css[css.index("@media (max-width:900px)"):]
        self.assertIn(".set-nav--overview{display:none}", phone[:phone.index("\n}")])

    def test_every_settings_link_is_a_44px_target(self):
        css = read_static("hesba/css/settings.css")
        self.assertIn(".set-nav__link{position:relative;display:grid;grid-template-columns:22px minmax(0,1fr);align-items:center;column-gap:10px;\n  min-height:var(--hs-control-height)", css)
        self.assertIn("  .set-nav__link{flex:none;display:inline-flex;align-items:center;gap:6px;min-height:var(--hs-control-height)", css)
        self.assertIn("settings.css' %}?v=st-4", open(get_template("settings_core/base.html").origin.name, encoding="utf-8").read())


class BackArrowTests(SimpleTestCase):
    """One back arrow: the inline SVG partial inside every back link, never a typed «←»/«→» that points the wrong way."""

    def test_no_back_link_carries_a_typed_arrow(self):
        import pathlib

        root = pathlib.Path(get_template("base_app.html").origin.name).parent
        typed, with_svg = [], 0
        for path_ in root.rglob("*.html"):
            rel = str(path_.relative_to(root))
            if rel.startswith(("emails/", "setup/")):
                continue
            text = path_.read_text(encoding="utf-8")
            for match in re.finditer(r'<a\b[^>]*class="(?:op-back|md-back-link|pr-button pr-button--ghost|lb-button)[^"]*"[^>]*>(.*?)</a>', text, re.S):
                if re.search(r"[←→‹›]", match.group(1)):
                    typed.append(rel)
                with_svg += 'partials/back_arrow.html' in match.group(1)
        self.assertEqual(typed, [])
        self.assertGreater(with_svg, 100)

    def test_the_print_toolbars_go_back_the_same_way(self):
        for name in ("barcode/labels_print.html", "parties/statement_print.html", "printing/document.html",
                     "projects/certificate_print.html", "workorders/receipt.html", "restaurant/ticket.html"):
            with self.subTest(template=name):
                text = open(get_template(name).origin.name, encoding="utf-8").read()
                self.assertNotIn("← ", text)
                self.assertIn('{% include "partials/back_arrow.html" %}', text)
        for sheet in ("hesba/css/print.css", "hesba/css/labels.css"):
            css = read_static(sheet)
            self.assertIn('[dir="rtl"] .hs-back-arrow{transform:scaleX(-1)}', css)
            self.assertIn("button{display:inline-flex;align-items:center;min-height:var(--hs-control-height)", css)

    def test_the_arrow_turns_with_the_page(self):
        svg = open(get_template("partials/back_arrow.html").origin.name, encoding="utf-8").read()
        self.assertIn('class="hs-back-arrow"', svg)
        self.assertIn('aria-hidden="true"', svg)
        css = read_static("hesba/css/buttons.css")
        self.assertIn('[dir="rtl"] .hs-back-arrow{transform:scaleX(-1)}', css)
        self.assertIn(".op-back{display:inline-flex;align-items:center;min-height:var(--hs-control-height);min-width:var(--hs-control-height)", css)
        tokens_only(self, css[css.index("/* UX Wave 3"):])


class GlossaryTests(TestCase):
    def setUp(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux3_words"))

    def test_the_posted_total_reads_final(self):
        body = self.client.get(reverse("reports:sales") + "?lang=ar").content.decode()
        self.assertNotIn("المرحّل", body)
        self.assertNotIn("لا توجد", body)

    def test_the_hub_and_lists_speak_plainly(self):
        for name in ("report_hub", "reports:profit", "inventory:movements", "cashboxes:list"):
            with self.subTest(page=name):
                body = self.client.get(reverse(name) + "?lang=ar").content.decode()
                for word in ("مرحلة والمسودة", "لا توجد", "يرجى", "العودة للتقارير"):
                    self.assertNotIn(word, body)

    def test_the_remaining_choice_lists_say_choose(self):
        for url in (reverse("cashboxes:operation_create"), reverse("inventory:transfer"),
                    reverse("inventory:adjustment"), reverse("settings_core:user_create")):
            with self.subTest(url=url):
                body = self.client.get(url + "?lang=ar").content.decode()
                self.assertNotIn(">---------<", body)
                self.assertIn(">اختار …<", body)
