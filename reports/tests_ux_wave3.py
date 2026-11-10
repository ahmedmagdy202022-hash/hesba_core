"""UX Wave 3: branded 404/500 pages, the old /home/ bookmark, English dates on English pages, numbers that keep their
order in Arabic lines, the report pages' shared header, the profit report's period chips, the settings menu and the
one back arrow. Presentation only; these pin the structure, while the figures, permissions and posting stay covered by
their own suites."""

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


@override_settings(ROOT_URLCONF=__name__)
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
