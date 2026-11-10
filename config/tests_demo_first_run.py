"""DEMO-FIRST-RUN: one tap on the kind of business opens a ready, populated shop.

The fast tests run the flow on the test database. A demo without private copies never wipes the shared
shop (SharedDemoStartTests); StartTests instead stand the test database in for the visitor's private copy
(``_replace_in_place``: emptied, set up and filled, which is what a copy of the ready shop gives). The slow
one at the end runs the real thing in a subprocess: templates built by prepare_demo_templates, copied per
visitor by the sandbox middleware.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from settings_core.models import ClientProfile

from . import demo_first_run as first_run
from . import demo_templates as templates
from .demo import DEMO_ACTIVITIES, DEMO_TILES, demo_subs, demo_tour
from .demo_templates import complete_demo_setup, owner_name, populated_template


def start(client, activity, sub, mode="sample", lang="ar"):
    return client.post(reverse("demo_start"), {"activity": activity, "sub_activity": sub, "mode": mode, "lang": lang})


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class LandingTests(TestCase):
    def test_root_shows_the_activity_tiles_to_a_visitor(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-demo-tile", count=len(DEMO_TILES))
        self.assertContains(page, "جرّب حِسبة على نشاطك")
        # No login wall, no credentials, no confirm, no hello card on the landing.
        for absent in ("id_password", "Demo-pass-1", "data-demo-restart", "confirm(", "data-hello"):
            self.assertNotContains(page, absent)
        self.assertContains(page, reverse("login"))  # «عندك حساب؟» is a plain link

    def test_an_activity_with_one_demo_variant_starts_in_one_tap_the_others_open_their_sheet(self):
        page = self.client.get("/?lang=ar")
        html = page.content.decode()
        for activity, hint_ar, _ in DEMO_TILES:
            with self.subTest(activity=activity):
                self.assertIn(hint_ar, html)
                if activity == "other" or len(demo_subs(activity)) == 1:
                    self.assertIn(f'<form method="post" action="{reverse("demo_start")}" role="listitem" data-demo-tile="{activity}"', html)
                else:
                    self.assertIn(f'href="{reverse("demo_sub_activities", args=[activity])}?lang=ar" data-demo-tile="{activity}"', html)

    def test_the_sheet_lists_the_sub_activities_and_a_not_sure_default(self):
        page = self.client.get(reverse("demo_sub_activities", args=["commercial"]), {"lang": "ar"})
        self.assertEqual(page.status_code, 200)
        for sub in demo_subs("commercial"):
            self.assertContains(page, f'data-demo-sub="{sub}"')
        self.assertContains(page, "مش متأكد؟ افتح محل تجزئة")
        self.assertContains(page, "data-demo-default")
        self.assertEqual(self.client.get(reverse("demo_sub_activities", args=["medical"])).status_code, 404)  # one variant: no sheet
        self.assertEqual(self.client.get(reverse("demo_sub_activities", args=["nope"])).status_code, 404)

    def test_english(self):
        page = self.client.get("/?lang=en")
        self.assertContains(page, 'dir="ltr"')
        self.assertContains(page, "Try Hesba on your business")
        self.assertContains(self.client.get(reverse("demo_sub_activities", args=["services"]), {"lang": "en"}), "Not sure? Open a")

    def test_a_signed_in_visitor_keeps_the_usual_gate(self):
        from reports.tests_dashboard import prepared_client

        prepared_client()
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x-Pass-12345"))
        self.assertRedirects(self.client.get("/"), reverse("after_login"), fetch_redirect_response=False)


class LandingOffTests(TestCase):
    def test_a_real_install_keeps_its_login_wall_and_has_no_demo_pages(self):
        self.assertRedirects(self.client.get("/"), reverse("login"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("demo_landing")).status_code, 404)
        self.assertEqual(self.client.get(reverse("demo_sub_activities", args=["commercial"])).status_code, 404)
        self.assertEqual(self.client.post(reverse("demo_start"), {"activity": "commercial", "sub_activity": "retail"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("demo_preparing")).status_code, 404)
        self.assertEqual(self.client.get(reverse("demo_start")).status_code, 404)  # a 404 before the 405 of a GET
        login = self.client.get(reverse("login"))
        self.assertNotContains(login, "data-demo-try")
        self.assertNotContains(login, "data-hello")

    def test_signed_in_on_a_real_install_the_demo_pages_are_404_too(self):
        from reports.tests_dashboard import prepared_client

        prepared_client()
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x-Pass-12345"))
        self.assertEqual(self.client.get(reverse("demo_begin")).status_code, 404)
        self.assertEqual(self.client.get(reverse("demo_login_as")).status_code, 404)
        self.assertEqual(self.client.post(reverse("demo_login_as"), {"username": "owner"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("demo_start")).status_code, 404)


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
@mock.patch.object(first_run, "_start_shared", first_run._replace_in_place)  # the test database is the visitor's own copy
class StartTests(TestCase):
    def test_get_is_refused_and_so_are_unknown_activities_and_posts_without_csrf(self):
        self.assertEqual(self.client.get(reverse("demo_start")).status_code, 405)
        self.assertEqual(start(self.client, "commercial", "spaceships").status_code, 400)
        self.assertEqual(start(self.client, "", "").status_code, 400)
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(start(strict, "commercial", "pharmacy").status_code, 403)
        self.assertFalse(ClientProfile.objects.exists())  # nothing was touched

    def test_one_tap_opens_a_populated_pharmacy_signed_in_as_its_owner(self):
        from sales.models import SalesInvoice
        from settings_core.capabilities import enabled_capabilities
        from shifts.services import open_shift_for

        response = start(self.client, "commercial", "pharmacy")
        self.assertRedirects(response, "/dashboard/?lang=ar", fetch_redirect_response=False)
        profile = ClientProfile.get_active()
        self.assertEqual((profile.activity_slug, profile.sub_activity_slug), ("commercial", "pharmacy"))
        self.assertTrue(profile.setup_is_complete)
        self.assertEqual(profile.display_name, "صيدلية حِسبة التجريبية")
        owner = get_user_model().objects.get(username="owner")
        self.assertEqual(owner.hesba_profile.display_name, "د. أحمد الشاذلي")
        self.assertGreater(SalesInvoice.objects.filter(status="posted").count(), 20)  # a month of trade, not a blank shop
        self.assertIsNotNone(open_shift_for(owner))  # the till works on the first tap
        self.assertTrue({"pos", "shift_required", "batches_expiry", "drugs"} <= set(enabled_capabilities()))  # the pharmacy's presets
        self.assertEqual(int(self.client.session["_auth_user_id"]), owner.pk)

        page = self.client.get("/dashboard/?lang=ar")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("data-demo-bar", html)
        self.assertIn("ديمو · صيدلية", html)
        self.assertIn("غيّر النشاط", html)
        self.assertIn("data-demo-empty", html)
        self.assertIn('data-demo-login-as="cashier"', html)
        self.assertNotIn("data-demo-restart", html)
        self.assertNotIn("confirm(", html)
        self.assertIn("د. أحمد الشاذلي", html)  # greeted by name, not as «المالك»
        self.assertNotIn("dash-greeting__name\">المالك", html)
        # «جرّب دلوقتي»: three steps in the pharmacy's words, each a real screen.
        self.assertIn('data-demo-tour="commercial:pharmacy"', html)
        self.assertEqual(html.count("data-demo-tour-step="), 3)
        self.assertIn("قربت تنتهي صلاحيتها", html)
        self.assertIn("data-demo-tour-dismiss", html)
        for _, _, _, _, url_name in demo_tour("commercial", "pharmacy"):
            with self.subTest(url=url_name):
                self.assertEqual(self.client.get(f"{reverse(url_name)}?lang=ar").status_code, 200)
        # The hello banner, once, here; the notice is in it and in the note dialog.
        self.assertEqual(html.count("data-hello"), 1 + html.count("data-hello-"))
        self.assertIn("بتتسجل عشان نحسّن البرنامج", html)
        self.assertNotIn("data-hello", self.client.get(reverse("login")).content.decode())

    def test_changing_the_activity_replaces_the_shop_without_a_confirm(self):
        from restaurant.models import DiningTable
        from sales.models import SalesInvoice

        start(self.client, "commercial", "pharmacy")
        pharmacy_sales = SalesInvoice.objects.count()
        self.assertFalse(DiningTable.objects.exists())
        page = self.client.get("/dashboard/?lang=ar")
        self.assertContains(page, f'href="{reverse("demo_landing")}?lang=ar" data-demo-change')
        landing = self.client.get(reverse("demo_landing"), {"lang": "ar"})
        self.assertEqual(landing.status_code, 200)  # signed in, the tiles still show
        response = start(self.client, "restaurants", "restaurant")
        self.assertRedirects(response, "/dashboard/?lang=ar", fetch_redirect_response=False)
        profile = ClientProfile.get_active()
        self.assertEqual(profile.display_name, "مطعم حِسبة التجريبي")
        self.assertTrue(DiningTable.objects.exists())
        self.assertNotEqual(SalesInvoice.objects.count(), pharmacy_sales)
        html = self.client.get("/dashboard/?lang=ar").content.decode()
        self.assertIn('data-demo-tour="restaurants:restaurant"', html)
        self.assertIn(reverse("restaurant:board"), html)  # the restaurant's first step is the tables board
        # §7: a restaurant's preset has no till capability; no link on the dashboard, the shell or «ابدأ من هنا»
        # may lead to the closed till (the audit saw a 403 fetched from the bottom nav).
        from settings_core.capabilities import capability_enabled

        self.assertFalse(capability_enabled("pos"))
        self.assertNotIn(reverse("sales:pos"), html)
        start(self.client, "restaurants", "restaurant", mode="empty")
        self.assertNotIn(reverse("sales:pos"), self.client.get("/demo/begin/?lang=ar").content.decode())

    def test_start_empty_lands_on_begin_and_the_fill_card_brings_the_sample(self):
        from sales.models import SalesInvoice

        response = start(self.client, "commercial", "pharmacy", mode="empty")
        self.assertRedirects(response, "/demo/begin/?lang=ar", fetch_redirect_response=False)
        profile = ClientProfile.get_active()
        self.assertEqual((profile.activity_slug, profile.sub_activity_slug, profile.setup_is_complete), ("commercial", "pharmacy", True))
        self.assertEqual(profile.display_name, "صيدلية حِسبة التجريبية")
        self.assertFalse(SalesInvoice.objects.exists())
        page = self.client.get("/demo/begin/?lang=ar")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("ابدأ من هنا", html)
        self.assertEqual(html.count("data-begin-step="), 4)
        for url_name in ("master_data:item_create", "cashboxes:list", "purchases:create", "sales:pos"):
            self.assertIn(reverse(url_name), html)
        self.assertIn("data-demo-fill-card", html)
        self.assertIn("ديمو · صيدلية · فاضي", html)
        self.assertIn(f'{reverse("setup_questions")}?lang=ar&amp;activity=commercial&amp;sub_activity=pharmacy', html)  # «عايز تعدّل تفاصيل شغلك؟»
        self.assertNotIn("data-hello", html)  # the hello waits for the dashboard
        # The fill card swaps the sample in.
        self.assertRedirects(start(self.client, "commercial", "pharmacy"), "/dashboard/?lang=ar", fetch_redirect_response=False)
        self.assertGreater(SalesInvoice.objects.filter(status="posted").count(), 20)

    def test_the_cashier_link_works_both_ways(self):
        start(self.client, "commercial", "pharmacy")
        response = self.client.post(reverse("demo_login_as"), {"username": "cashier", "lang": "ar"})
        self.assertRedirects(response, "/start/?lang=ar", fetch_redirect_response=False)
        cashier = get_user_model().objects.get(username="cashier")
        self.assertEqual(int(self.client.session["_auth_user_id"]), cashier.pk)
        html = self.client.get("/dashboard/?lang=ar").content.decode()
        self.assertIn('data-demo-login-as="owner"', html)
        self.assertIn("ارجع كمالك", html)
        self.assertNotIn("data-demo-empty", html)  # a cashier cannot wipe the shop
        self.assertEqual(self.client.post(reverse("demo_login_as"), {"username": "root"}).status_code, 400)
        self.client.post(reverse("demo_login_as"), {"username": "owner", "lang": "ar"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), get_user_model().objects.get(username="owner").pk)

    def test_the_wizard_stays_reachable_under_settings(self):
        start(self.client, "commercial", "pharmacy", mode="empty")
        overview = self.client.get(reverse("settings_core:overview"), {"lang": "ar"})
        self.assertContains(overview, "data-setup-wizard-link")
        questions = self.client.get(reverse("setup_questions"), {"lang": "ar", "activity": "commercial", "sub_activity": "pharmacy"})
        self.assertEqual(questions.status_code, 200)
        self.assertContains(questions, "data-questions-form")


    def test_a_cashier_on_begin_gets_no_fill_card(self):
        start(self.client, "commercial", "pharmacy", mode="empty")
        self.client.post(reverse("demo_login_as"), {"username": "cashier", "lang": "ar"})
        html = self.client.get("/demo/begin/?lang=ar").content.decode()
        self.assertIn("data-demo-bar", html)
        self.assertNotIn("data-demo-fill-card", html)
        self.assertNotIn("data-demo-fill", html)

    def test_the_tour_says_the_shift_is_open_only_where_you_sell_at_a_till(self):
        start(self.client, "commercial", "pharmacy")
        html = self.client.get("/dashboard/?lang=ar").content.decode()
        self.assertIn("عندك شهر شغل كامل في صيدلية. الوردية مفتوحة، فتقدر تبيع على طول.", html)
        self.assertIn("Your pharmacy has a full month of trade. The shift is open, so you can sell straight away.",
                      self.client.get("/dashboard/?lang=en").content.decode())
        start(self.client, "restaurants", "restaurant")  # no till capability: no promise to sell straight away
        html = self.client.get("/dashboard/?lang=ar").content.decode()
        self.assertIn("عندك شهر شغل كامل في مطعم.", html)
        self.assertNotIn("الوردية مفتوحة", html)
        start(self.client, "contracting", "general")
        english = self.client.get("/dashboard/?lang=en").content.decode()
        self.assertIn("Your contracting firm has a full month of trade.", english)
        self.assertNotIn("General building has", english)


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class SharedDemoStartTests(TestCase):
    """DEMO_SANDBOXES off (or PostgreSQL): one demo everyone shares. A tap switches it in place and never wipes it."""

    def test_a_tap_never_wipes_what_another_tester_did_nor_signs_them_out(self):
        from master_data.models import Customer
        from sales.models import SalesInvoice

        tester_a, tester_b = Client(), Client()
        self.assertRedirects(start(tester_a, "commercial", "pharmacy"), "/dashboard/?lang=ar", fetch_redirect_response=False)  # an empty DB: set up
        Customer.objects.create(customer_code="A-1", name="عميل المختبر أ")
        sales = SalesInvoice.objects.filter(status="posted").count()
        self.assertGreater(sales, 20)
        self.assertRedirects(start(tester_b, "restaurants", "restaurant"), "/dashboard/?lang=ar", fetch_redirect_response=False)
        profile = ClientProfile.get_active()
        self.assertEqual((profile.activity_slug, profile.sub_activity_slug), ("restaurants", "restaurant"))
        self.assertTrue(Customer.objects.filter(customer_code="A-1").exists())
        self.assertEqual(SalesInvoice.objects.filter(status="posted").count(), sales)
        self.assertEqual(tester_a.get("/dashboard/?lang=ar").status_code, 200)  # still signed in
        # «ابدأ فاضي» on a shared demo cannot empty it for everyone: the shop stays, and the visitor lands on it.
        self.assertRedirects(start(tester_b, "restaurants", "restaurant", mode="empty"), "/dashboard/?lang=ar", fetch_redirect_response=False)
        self.assertTrue(Customer.objects.filter(customer_code="A-1").exists())
        self.assertEqual(SalesInvoice.objects.filter(status="posted").count(), sales)
        self.assertEqual(tester_a.get("/dashboard/?lang=ar").status_code, 200)

    def test_a_preparing_replay_without_its_token_touches_nothing(self):
        response = self.client.post(reverse("demo_start"), {"activity": "commercial", "sub_activity": "pharmacy", "mode": "sample",
                                                            "lang": "ar", "prepared": "stale-or-forged"})
        self.assertRedirects(response, f"{reverse('demo_landing')}?lang=ar", fetch_redirect_response=False)
        self.assertFalse(ClientProfile.objects.exists())  # without the check this would have set up a shop
        self.client.cookies[first_run.PREPARE_COOKIE] = "garbage"
        response = self.client.post(reverse("demo_start"), {"activity": "commercial", "sub_activity": "pharmacy", "lang": "ar", "prepared": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ClientProfile.objects.exists())

    def test_the_preparing_page_belongs_to_private_copies_only(self):
        self.assertRedirects(self.client.get(reverse("demo_preparing"), {"activity": "commercial", "sub_activity": "pharmacy", "lang": "en"}),
                             f"{reverse('demo_landing')}?lang=en", fetch_redirect_response=False)

    def test_the_emptied_demo_missing_is_a_503_in_the_visitors_language(self):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        for lang, words in (("ar", "الديمو الفاضي لسه بيتجهّز"), ("en", "The empty demo is still being prepared")):
            request = RequestFactory().post(reverse("demo_start"), {"activity": "commercial", "sub_activity": "pharmacy", "mode": "empty", "lang": lang})
            request.user, request.session, request.demo_switch = AnonymousUser(), {}, lambda source: None
            with mock.patch.object(templates, "fresh_template", return_value=Path("/nonexistent/demo.fresh.sqlite3")):
                response = first_run.start(request)
            self.assertEqual(response.status_code, 503)
            self.assertIn(words, response.content.decode())


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class PreparingFailureTests(TestCase):
    """A template whose build keeps failing: the page stops refreshing and offers a way out."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name) / "demo.sqlite3"
        for patcher in (mock.patch.object(templates, "base_template", return_value=self.base),
                        # Only the page's own check: patching demo_sandbox.enabled would switch the middleware's
                        # connection to a "template" that is the test database (and break PostgreSQL runs).
                        mock.patch.object(first_run, "_private_copies", return_value=True),
                        mock.patch.object(templates, "builder", templates.BackgroundBuilder())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.query = {"activity": "commercial", "sub_activity": "pharmacy"}

    def test_it_refreshes_while_the_build_is_going(self):
        with mock.patch.object(templates.builder, "request", return_value=True):
            page = self.client.get(reverse("demo_preparing"), {**self.query, "lang": "ar"})
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page["Refresh"].startswith("3; url="))
        self.assertContains(page, 'data-demo-preparing="building"')
        self.assertContains(page, 'http-equiv="refresh"')

    def test_after_three_failed_builds_it_stops_and_offers_the_default_shop(self):
        with mock.patch.object(templates, "build_template", side_effect=RuntimeError("seeding broke")), \
                self.assertLogs("hesba.demo", "ERROR") as logged:
            for _ in range(3):
                templates.builder._run("commercial", "pharmacy")
        self.assertEqual(len(logged.records), 3)
        self.assertTrue(templates.builder.gave_up("commercial", "pharmacy"))
        self.assertTrue(templates.failed_stamp(populated_template("commercial", "pharmacy")).is_file())
        self.assertTrue(templates.BackgroundBuilder().gave_up("commercial", "pharmacy"))  # the other worker sees it too
        self.assertFalse(templates.builder.request("commercial", "pharmacy"))  # and nobody starts it again
        for lang, title in (("ar", "معرفناش نجهّز النسخة دي"), ("en", "We could not prepare this copy")):
            page = self.client.get(reverse("demo_preparing"), {**self.query, "lang": lang})
            self.assertEqual(page.status_code, 200)
            self.assertNotIn("Refresh", page)
            self.assertNotContains(page, 'http-equiv="refresh"')
            self.assertContains(page, 'data-demo-preparing="failed"')
            self.assertContains(page, title)
            self.assertContains(page, "data-demo-default-shop")
            self.assertContains(page, '<input type="hidden" name="activity" value="commercial"><input type="hidden" name="sub_activity" value="retail">')
            self.assertContains(page, f'href="{reverse("demo_landing")}?lang={lang}"')

    def test_one_failure_a_minute_old_is_enough_and_an_old_one_is_forgotten(self):
        now = time.time()
        templates.builder.record_failure("commercial", "pharmacy", now=now - templates.FAILURE_WINDOW_SECONDS - 1)
        self.assertTrue(templates.builder.gave_up("commercial", "pharmacy", now=now))
        self.assertFalse(templates.builder.gave_up("commercial", "pharmacy", now=now + templates.FORGET_FAILURE_SECONDS))

    def test_it_gives_up_waiting_after_the_longest_wait(self):
        with mock.patch.object(templates.builder, "request", return_value=True):
            page = self.client.get(reverse("demo_preparing"), {**self.query, "lang": "ar", "since": str(int(time.time() - templates.MAX_WAIT_SECONDS - 5))})
        self.assertContains(page, 'data-demo-preparing="failed"')
        self.assertNotIn("Refresh", page)


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class TopUpTests(TestCase):
    def test_a_shop_built_three_days_ago_gets_todays_trade_and_a_shift_dated_today(self):
        from django.utils import timezone

        from sales.models import SalesInvoice
        from shifts.models import Shift
        from shifts.services import open_shift_for

        from .demo_restart import restart_demo

        today = timezone.localdate()
        built = timezone.now() - timedelta(days=3)
        with mock.patch("django.utils.timezone.now", return_value=built):
            owner = restart_demo()
            complete_demo_setup("commercial", "pharmacy")
            templates.seed_sample_business()
        posted = SalesInvoice.objects.filter(status="posted")
        self.assertEqual(posted.order_by("-invoice_date").first().invoice_date, today - timedelta(days=3))
        old_shift = open_shift_for(owner)
        self.assertEqual(timezone.localtime(old_shift.opened_at).date(), today - timedelta(days=3))

        templates.top_up_current_db()
        self.assertTrue(posted.filter(invoice_date=today).exists())
        shift = open_shift_for(owner)
        self.assertEqual(timezone.localtime(shift.opened_at).date(), today)
        old_shift.refresh_from_db()
        self.assertEqual(old_shift.status, "closed")
        self.assertEqual(old_shift.difference, 0)  # closed at what it expected: nothing to post
        self.assertIsNone(old_shift.difference_operation_id)
        self.assertEqual(Shift.objects.filter(cashier=owner, status="open").count(), 1)
        # Idempotent: a second top-up the same day adds nothing and keeps today's shift.
        count = posted.count()
        templates.top_up_current_db()
        self.assertEqual(posted.count(), count)
        self.assertEqual(open_shift_for(owner).pk, shift.pk)


class TemplateBuilderLockTests(SimpleTestCase):
    """Two builders of one template (two threads here; two processes on the server) never share a file."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.base = self.folder / "demo.sqlite3"
        with sqlite3.connect(self.folder / "demo.fresh.sqlite3") as db:
            db.execute("create table shop (name text)")
        patcher = mock.patch.object(templates, "base_template", return_value=self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.target = populated_template("commercial", "pharmacy")
        self.calls = []
        self.entered, self.release = threading.Event(), threading.Event()

    def fake_fill(self, path, work):  # stands in for setting up and seeding the copy
        self.calls.append(path)
        self.entered.set()
        self.release.wait(10)
        with sqlite3.connect(path) as db:
            db.execute("insert into shop values ('pharmacy')")

    def test_two_threads_one_builds_the_other_skips_and_the_file_is_whole(self):
        results = {}
        with mock.patch.object(templates, "_with_db", self.fake_fill):
            first = threading.Thread(target=lambda: results.setdefault("first", templates.build_template("commercial", "pharmacy")))
            first.start()
            self.assertTrue(self.entered.wait(10))
            self.assertTrue(templates.is_building("commercial", "pharmacy"))
            results["second"] = templates.build_template("commercial", "pharmacy")  # the lock is held: skipped at once
            self.assertFalse(self.target.exists())  # nothing half-built was published meanwhile
            self.release.set()
            first.join(10)
            self.assertEqual(templates.build_template("commercial", "pharmacy"), self.target)  # now it simply exists
        self.assertEqual(results, {"first": self.target, "second": None})
        self.assertEqual(len(self.calls), 1)
        with sqlite3.connect(f"file:{self.target}?mode=ro", uri=True) as db:
            self.assertEqual(db.execute("select name from shop").fetchall(), [("pharmacy",)])
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["demo.commercial.pharmacy.sqlite3", "demo.fresh.sqlite3"])

    def test_a_stale_lock_is_taken_over_and_a_live_one_is_respected(self):
        lock = templates.lock_path(self.target)
        lock.write_text("123 456 0\n")
        self.release.set()
        with mock.patch.object(templates, "_with_db", self.fake_fill):
            self.assertIsNone(templates.build_template("commercial", "pharmacy"))  # someone is building it right now
            old = time.time() - templates.STALE_BUILD_SECONDS - 60
            os.utime(lock, (old, old))
            self.assertFalse(templates.is_building("commercial", "pharmacy"))
            self.assertEqual(templates.build_template("commercial", "pharmacy"), self.target)  # a crashed build's lock
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(lock.exists())

    def test_a_failed_build_leaves_nothing_behind(self):
        def broken(path, work):
            raise RuntimeError("seeding broke")

        with mock.patch.object(templates, "_with_db", broken), self.assertRaises(RuntimeError):
            templates.build_template("commercial", "pharmacy")
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["demo.fresh.sqlite3"])

    def test_leftovers_of_crashed_builds_are_cleaned_only_once_stale(self):
        old = time.time() - templates.STALE_BUILD_SECONDS - 60
        names = ("demo.commercial.pharmacy.sqlite3.lock", "demo.commercial.pharmacy.sqlite3.1-2.building",
                 "demo.commercial.pharmacy.sqlite3.1-2.partial", "demo.commercial.grocery.sqlite3.failed", "demo.sqlite3-journal")
        for name in names:
            (self.folder / name).write_text("x")
            os.utime(self.folder / name, (old, old))
        (self.folder / "demo.commercial.fashion.sqlite3.lock").write_text("x")  # a worker building it right now
        self.assertEqual(templates.clean_leftovers(), 4)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()),
                         ["demo.commercial.fashion.sqlite3.lock", "demo.fresh.sqlite3", "demo.sqlite3-journal"])

    def test_the_command_skips_a_locked_template_and_reports_a_failed_one_after_the_rest(self):
        from django.core.management import CommandError, call_command

        templates.lock_path(self.target).write_text("busy")
        self.release.set()
        out, err = StringIO(), StringIO()
        sqlite = SimpleNamespace(DEMO_MODE=True, DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(self.base)}})
        with mock.patch("settings_core.management.commands.prepare_demo_templates.settings", sqlite), \
                mock.patch.object(templates, "_with_db", self.fake_fill):
            call_command("prepare_demo_templates", "--only", "commercial:pharmacy", stdout=out, stderr=err)
            self.assertIn("commercial:pharmacy is being built by another builder; skipped.", out.getvalue())
            self.assertIn("0 built", out.getvalue())

            def grocery_breaks(path, work):
                if "grocery" in str(path):
                    raise RuntimeError("seeding broke")
                self.fake_fill(path, work)

            with mock.patch.object(templates, "_with_db", grocery_breaks), self.assertRaisesMessage(CommandError, "Failed: commercial:grocery"):
                call_command("prepare_demo_templates", "--only", "commercial:grocery", "--only", "commercial:fashion", stdout=out, stderr=err)
        self.assertIn("WARNING: commercial:grocery failed", err.getvalue())
        self.assertTrue(populated_template("commercial", "fashion").is_file())  # built although grocery failed first


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class EveryActivityTests(TestCase):
    def test_every_demo_activity_sets_up_without_a_question_and_its_tour_links_open(self):
        from .demo_restart import restart_demo

        owner = restart_demo()
        self.client.force_login(owner)
        for activity, sub in DEMO_ACTIVITIES:
            with self.subTest(activity=f"{activity}:{sub}"):
                profile = complete_demo_setup(activity, sub)
                self.assertTrue(profile.setup_is_complete)
                owner.hesba_profile.refresh_from_db()
                self.assertEqual(owner.hesba_profile.display_name, owner_name(activity, sub))
                steps = demo_tour(activity, sub)
                self.assertEqual(len(steps), 3)
                for _, _, _, _, url_name in steps:
                    self.assertEqual(self.client.get(f"{reverse(url_name)}?lang=ar").status_code, 200, url_name)
                    self.assertEqual(self.client.get(f"{reverse(url_name)}?lang=en").status_code, 200, url_name)
                self.assertEqual(self.client.get("/dashboard/?lang=ar").status_code, 200)
                self.assertEqual(self.client.get("/demo/begin/?lang=ar").status_code, 200)

    def test_template_files_are_named_for_the_activity_and_the_retail_shop_is_the_base(self):
        base = Path(settings.DATABASES["default"]["NAME"])
        self.assertEqual(populated_template("commercial", "retail"), base)
        self.assertEqual(populated_template("commercial", "pharmacy").name, f"{base.stem}.commercial.pharmacy{base.suffix}")


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
class LoginPageTests(TestCase):
    def test_the_demo_login_page_offers_the_demo_and_shows_no_credentials(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, "data-demo-try")
        self.assertContains(page, "جرّب حِسبة دلوقتي")
        self.assertContains(page, reverse("demo_landing"))
        for absent in ("Demo-pass-1", "data-demo-login", "data-demo-restart", "data-hello", ">owner<", "cashier"):
            self.assertNotContains(page, absent)


class SharedFixesTests(TestCase):
    """§7: the small fixes both a showcase and a real install get."""

    def test_dashboard_sends_an_unfinished_setup_to_the_wizard(self):
        ClientProfile.objects.create(client_code="NEW", legal_name="New", display_name="New shop")
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x-Pass-12345"))
        self.assertRedirects(self.client.get("/dashboard/"), reverse("after_login"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("after_login")), reverse("setup_gate"), fetch_redirect_response=False)

    def test_on_a_real_install_someone_who_cannot_finish_setup_is_not_bounced(self):
        ClientProfile.objects.create(client_code="NEW", legal_name="New", display_name="New shop")
        self.client.force_login(get_user_model().objects.create_user("clerk", password="x-Pass-12345"))
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_the_interview_speaks_arabic(self):
        from settings_core import setup_interview as interview

        for activity, sub in (("commercial", "retail"), ("commercial", "electronics"), ("medical", "clinic"), ("contracting", "general")):
            for row in interview.questions(activity, sub, "ar"):
                with self.subTest(activity=activity, key=row["key"]):
                    self.assertNotIn("80mm", row["ask"] + row["gives"])
                    self.assertNotIn(" PDF", (row["ask"] + row["gives"]).replace("ملف PDF", ""))
        electronics = {row["key"]: row["ask"] for row in interview.questions("commercial", "electronics", "ar")}
        self.assertIn("(IMEI)", electronics["serials"])

    def test_one_source_of_step_names_and_buttons_that_say_next(self):
        from reports.tests_dashboard import prepared_client

        prepared_client()
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x-Pass-12345"))
        for url in ("/setup/activity/", "/setup/activity/commercial/", "/setup/activity/medical/", "/setup/activity/services/",
                    "/setup/modules/?activity=commercial&sub_activity=retail", "/setup/review/?activity=commercial&sub_activity=retail&modules="):
            with self.subTest(url=url):
                html = self.client.get(url + ("&" if "?" in url else "?") + "lang=ar").content.decode()
                self.assertIn('data-setup-stepper=', html)
                self.assertIn("شغلك", html)
                self.assertNotIn("التالي:", html)
        complete = self.client.get(reverse("setup_complete"), {"lang": "ar"}).content.decode()
        self.assertEqual(complete.count("حِسبة جاهزة لشغلك"), 1 + complete.count("title: 'حِسبة جاهزة لشغلك'"))
        self.assertNotIn("تم إنهاء الإعداد", complete)
        self.assertIn(f'href="{reverse("setup_questions")}?lang=ar&amp;activity=commercial&amp;sub_activity=retail" data-back-link', complete)

    @override_settings(DEMO_MODE=True)
    def test_on_a_showcase_the_complete_page_leads_with_sample_data(self):
        from reports.tests_dashboard import prepared_client

        prepared_client()
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x-Pass-12345"))
        html = self.client.get(reverse("setup_complete"), {"lang": "ar"}).content.decode()
        self.assertIn("data-sample-primary", html)
        self.assertIn('class="complete-empty-link"', html)
        self.assertNotIn("activity-next complete-empty", html)


class TemplatesCommandTests(SimpleTestCase):
    def test_refused_off_a_showcase_and_skipped_on_postgresql(self):
        from io import StringIO

        from django.core.management import CommandError, call_command

        with override_settings(DEMO_MODE=False), self.assertRaisesMessage(CommandError, "DEMO_MODE"):
            call_command("prepare_demo_templates")
        postgres = SimpleNamespace(DEMO_MODE=True, DATABASES={"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "demo"}})
        out = StringIO()
        with mock.patch("settings_core.management.commands.prepare_demo_templates.settings", postgres):
            call_command("prepare_demo_templates", stdout=out)
        self.assertIn("nothing to build", out.getvalue())


# The whole thing as it runs on the demo server: templates built by the start script's commands,
# real requests through every middleware, each visitor on a private copy of the ready shop.
DRIVER = r"""
import json, os, sqlite3, sys, time
import django
django.setup()
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command("migrate", verbosity=0, interactive=False)
call_command("prepare_demo", verbosity=0)
call_command("prepare_demo_fresh", verbosity=0)
call_command("migrate", database="feedback", verbosity=0, interactive=False)
from config.demo import DEMO_ACTIVITIES
from config.demo_templates import populated_template
started = time.time()
call_command("prepare_demo_templates", verbosity=0)
out = {"build_seconds": time.time() - started, "templates": {}}
import io
topped = io.StringIO()
call_command("prepare_demo_templates", "--top-up", "--only", "commercial:pharmacy", stdout=topped)
out["top_up"] = topped.getvalue()
for activity, sub in DEMO_ACTIVITIES:
    path = populated_template(activity, sub)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        out["templates"][f"{activity}:{sub}"] = [
            path.is_file(), path.stat().st_size,
            db.execute("select count(*) from sales_salesinvoice where status='posted'").fetchone()[0],
            db.execute("select count(*) from shifts_shift s join auth_user u on u.id=s.cashier_id where s.status='open' and u.username='owner'").fetchone()[0],
            db.execute("select display_name from settings_core_clientprofile where is_active").fetchone()[0],
            db.execute("select p.display_name from accounts_userprofile p join auth_user u on u.id=p.user_id where u.username='owner'").fetchone()[0],
        ]
boxes = os.environ["DEMO_SANDBOX_DIR"]
copies = lambda: sorted(name for name in os.listdir(boxes) if name.endswith(".sqlite3"))
def sandbox(client): return client.cookies["hesba_sandbox"].value.split(":")[0]
def invoices(sandbox_id):
    with sqlite3.connect(os.path.join(boxes, sandbox_id + ".sqlite3")) as db:
        return db.execute("select count(*) from sales_salesinvoice where status='posted'").fetchone()[0]
a = Client()
out["landing"] = [a.get("/").status_code, len(copies())]
t0 = time.time()
r = a.post("/demo/start/", {"activity": "commercial", "sub_activity": "pharmacy", "mode": "sample", "lang": "ar"})
out["start"] = [r.status_code, r.headers.get("Location", ""), round(time.time() - t0, 3), len(copies())]
first = sandbox(a)
page = a.get("/dashboard/?lang=ar")
html = page.content.decode()
out["dashboard"] = [page.status_code, "data-demo-bar" in html, "ديمو · صيدلية" in html, "د. أحمد الشاذلي" in html, 'data-demo-tour="commercial:pharmacy"' in html, invoices(first)]
out["pos"] = a.get("/sales/pos/?lang=ar").status_code
# «غيّر النشاط»: a copy of another ready shop replaces this one; no confirm, the old file goes.
r = a.post("/demo/start/", {"activity": "services", "sub_activity": "maintenance", "mode": "sample", "lang": "ar"})
second = sandbox(a)
out["change"] = [r.status_code, second != first, os.path.exists(os.path.join(boxes, first + ".sqlite3")), invoices(second) > 0,
                 'data-demo-tour="services:maintenance"' in a.get("/dashboard/?lang=ar").content.decode(), len(copies())]
# «ابدأ فاضي»: the emptied demo, set up for the same activity, lands on «ابدأ من هنا»; the fill card brings the sample.
r = a.post("/demo/start/", {"activity": "services", "sub_activity": "maintenance", "mode": "empty", "lang": "ar"})
third = sandbox(a)
begin = a.get("/demo/begin/?lang=ar")
out["empty"] = [r.status_code, r.headers.get("Location", ""), invoices(third), begin.status_code, "data-demo-fill-card" in begin.content.decode(), len(copies())]
r = a.post("/demo/start/", {"activity": "services", "sub_activity": "maintenance", "mode": "sample", "lang": "ar"})
out["fill"] = [r.status_code, invoices(sandbox(a)) > 0]
# A template that is missing is built on demand behind the preparing page.
missing = populated_template("education", "training")
missing.unlink()
b = Client()
r = b.post("/demo/start/", {"activity": "education", "sub_activity": "training", "mode": "sample", "lang": "ar"})
out["missing_start"] = [r.status_code, r.headers.get("Location", "")]
page = b.get(r.headers["Location"])
out["preparing"] = [page.status_code, page.headers.get("Refresh", ""), 'data-demo-preparing="building"' in page.content.decode()]
for _ in range(120):
    if missing.is_file(): break
    time.sleep(0.5)
page = b.get(r.headers["Location"])
html = page.content.decode()
out["prepared"] = [missing.is_file(), page.status_code, 'data-demo-preparing="ready"' in html, "data-demo-continue" in html]
# What the page's auto-POST sends: its one-shot token.
import re
token = re.search(r'name="prepared" value="([^"]+)"', html).group(1)
form = {"activity": "education", "sub_activity": "training", "mode": "sample", "lang": "ar", "prepared": token}
r = b.post("/demo/start/", form)
opened = sandbox(b)
out["after_prepared"] = [r.status_code, r.headers.get("Location", ""), invoices(opened) > 0, b.get("/dashboard/?lang=ar").status_code]
# Back to the preparing page, or a replay of its POST: the shop the visitor is working in stays.
page = b.get("/demo/preparing/?lang=ar&activity=education&sub_activity=training").content.decode()
r = b.post("/demo/start/", form)
out["replay"] = ["data-demo-continue" in page, "data-demo-to-dashboard" in page, r.status_code, r.headers.get("Location", ""),
                 sandbox(b) == opened, os.path.exists(os.path.join(boxes, opened + ".sqlite3"))]
# A's copy is untouched by B; the base template never changed.
out["a_still"] = a.get("/dashboard/?lang=ar").status_code
print("RESULT" + json.dumps(out, ensure_ascii=False))
"""


class EndToEndTests(SimpleTestCase):
    def test_the_first_run_on_a_real_sandboxed_demo(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {key: value for key, value in os.environ.items() if not key.startswith(("DATABASE_", "POSTGRES_", "FEEDBACK_"))}
            env.update({
                "DJANGO_SETTINGS_MODULE": "config.settings", "DATABASE_BACKEND": "sqlite", "DEMO_MODE": "True", "DEMO_SANDBOXES": "True",
                "DEBUG": "True", "SQLITE_PATH": str(Path(folder) / "demo.sqlite3"), "DEMO_SANDBOX_DIR": str(Path(folder) / "boxes"),
                "FEEDBACK_SQLITE_PATH": str(Path(folder) / "feedback.sqlite3"),
            })
            done = subprocess.run([sys.executable, "-c", DRIVER], cwd=settings.BASE_DIR, env=env, capture_output=True, text=True, timeout=900)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        out = json.loads(done.stdout.rsplit("RESULT", 1)[1])
        # Every demo activity has its ready shop: a file of a few MB with a month of posted trade, the owner's shift open,
        # the shop and its owner named.
        self.assertEqual(set(out["templates"]), {f"{a}:{s}" for a, s in DEMO_ACTIVITIES})
        for key, (exists, size, posted, owner_shift, shop, owner) in out["templates"].items():
            with self.subTest(template=key):
                self.assertTrue(exists)
                self.assertLess(size, 12 * 1024 * 1024)
                self.assertGreater(posted, 10)
                self.assertEqual(owner_shift, 1)
                self.assertIn("حِسبة", shop)
                self.assertNotEqual(owner, "المالك")
        self.assertLess(out["build_seconds"], 600)
        # Looking at the landing makes no copy; one tap makes one, in well under a second, and lands on a full dashboard.
        self.assertEqual(out["landing"], [200, 0])
        status, location, seconds, count = out["start"]
        self.assertEqual((status, location, count), (302, "/dashboard/?lang=ar", 1))
        self.assertLess(seconds, 1.5)
        self.assertEqual(out["dashboard"][:5], [200, True, True, True, True])
        self.assertGreater(out["dashboard"][5], 20)
        self.assertEqual(out["pos"], 200)
        self.assertEqual(out["change"], [302, True, False, True, True, 1])
        self.assertEqual(out["empty"][0], 302)
        self.assertEqual(out["empty"][1], "/demo/begin/?lang=ar")
        self.assertEqual(out["empty"][2:], [0, 200, True, 1])
        self.assertEqual(out["fill"], [302, True])
        self.assertEqual(out["missing_start"][0], 302)
        self.assertTrue(out["missing_start"][1].startswith("/demo/preparing/"))
        self.assertEqual(out["preparing"][0], 200)
        self.assertTrue(out["preparing"][1].startswith("3; url=/demo/preparing/"))
        self.assertTrue(out["preparing"][2])
        self.assertEqual(out["prepared"], [True, 200, True, True])
        self.assertEqual(out["after_prepared"], [302, "/dashboard/?lang=ar", True, 200])
        self.assertEqual(out["replay"], [False, True, 302, "/dashboard/?lang=ar", True, True])
        self.assertIn("commercial:pharmacy topped up", out["top_up"])
        self.assertIn("1 topped up", out["top_up"])
        self.assertEqual(out["a_still"], 200)
