"""DEMO-FEEDBACK: a note from any demo page, read by Ahmed in one inbox."""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import Feedback

KEY = "k" * 40


@override_settings(DEMO_MODE=True, FEEDBACK_VIEW_TOKEN=KEY)
class SendTests(TestCase):
    databases = {"default", "feedback"}

    def send(self, **data):
        return self.client.post(reverse("feedback:send"), {"lang": "ar", **data})

    def test_a_note_is_kept_with_where_the_tester_was(self):
        from reports.tests_dashboard import prepared_client

        prepared_client("medical", "clinic")
        response = self.send(message="  الزرار مش واضح  ", mood="good", contact="منى 0100", path="/setup/review/",
                             viewport="390x844", HTTP_USER_AGENT="Mobile Safari")
        self.assertEqual(response.json(), {"ok": True})
        note = Feedback.objects.using("feedback").get()
        self.assertEqual((note.message, note.mood, note.contact, note.path, note.lang), ("الزرار مش واضح", "good", "منى 0100", "/setup/review/", "ar"))
        self.assertEqual((note.activity, note.sub_activity, note.viewport), ("medical", "clinic", "390x844"))

    def test_the_role_of_a_signed_in_tester(self):
        from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
        from permissions.models import RoleCode

        user = make_user(username="fb_cashier")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(user)
        self.send(message="شاشة البيع سريعة")
        self.assertEqual(Feedback.objects.get().role, RoleCode.CASHIER)

    def test_an_empty_note_or_an_odd_mood(self):
        response = self.send(message=" a ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "اكتب ملاحظتك الأول.")
        self.assertEqual(self.send(message="hi", lang="en").json()["error"], "Write a few words first.")
        self.send(message="long " * 1000, mood="<script>")
        note = Feedback.objects.get()
        self.assertEqual((len(note.message), note.mood), (2000, ""))

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(reverse("feedback:send")).status_code, 405)

    def test_one_tester_cannot_flood_the_inbox(self):
        Feedback.objects.bulk_create([Feedback(message="x", sandbox="tester-1") for _ in range(views.MAX_PER_HOUR)])
        request_attr = {"demo_sandbox": "tester-1"}
        with mock.patch.object(views, "_sandbox_of", lambda request: request_attr["demo_sandbox"]):
            self.assertEqual(self.send(message="one more").status_code, 429)
            request_attr["demo_sandbox"] = "tester-2"
            self.assertEqual(self.send(message="another tester").status_code, 200)
        # An hour later they may write again.
        Feedback.objects.filter(sandbox="tester-1").update(created_at=timezone.now() - timedelta(hours=2))
        with mock.patch.object(views, "_sandbox_of", lambda request: "tester-1"):
            self.assertEqual(self.send(message="back again").status_code, 200)

    def test_visitors_without_a_copy_share_one_allowance(self):
        Feedback.objects.bulk_create([Feedback(message="x") for _ in range(views.MAX_ANONYMOUS_PER_HOUR)])
        self.assertEqual(self.send(message="anyone").status_code, 429)

    @override_settings(FEEDBACK_NOTIFY_EMAIL="ahmed@example.com", EMAIL_HOST="smtp.example.com")
    def test_ahmed_can_get_each_note_by_email(self):
        self.send(message="التقرير مش بيطبع", path="/reports/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ahmed@example.com"])
        self.assertIn("/reports/", mail.outbox[0].body)

    def test_no_email_unless_asked(self):
        self.send(message="ملاحظة بدون إيميل")
        self.assertEqual(mail.outbox, [])


@override_settings(DEMO_MODE=True, FEEDBACK_VIEW_TOKEN=KEY)
class InboxTests(TestCase):
    databases = {"default", "feedback"}

    @classmethod
    def setUpTestData(cls):
        Feedback.objects.create(message="اختيار النشاط سهل", mood="great", sandbox="aaaaaaaaaa", activity="medical", path="/setup/")
        Feedback.objects.create(message="التقرير بطيء", mood="bad", sandbox="bbbbbbbbbb", contact="0100")

    def test_needs_the_key(self):
        url = reverse("feedback:inbox")
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url + "?key=wrong").status_code, 404)
        with override_settings(FEEDBACK_VIEW_TOKEN=""):
            self.assertEqual(self.client.get(url + "?key=").status_code, 404)

    def test_lists_searches_and_downloads(self):
        url = reverse("feedback:inbox") + f"?key={KEY}"
        page = self.client.get(url)
        self.assertContains(page, "data-feedback-note", count=2)
        self.assertContains(page, "2 ملاحظة من 2 مجرّب")
        self.assertIn("no-store", page.headers["Cache-Control"])
        page = self.client.get(url + "&q=التقرير")
        self.assertContains(page, "data-feedback-note", count=1)
        self.assertContains(page, "التقرير بطيء")
        csv = self.client.get(url + "&format=csv")
        self.assertEqual(csv["Content-Type"], "text/csv; charset=utf-8")
        text = csv.content.decode("utf-8")
        self.assertTrue(text.startswith("﻿time,message"))
        self.assertIn("اختيار النشاط سهل", text)
        self.assertIn("aaaaaaaa", text)


class RealInstallTests(TestCase):
    databases = {"default", "feedback"}

    @override_settings(FEEDBACK_VIEW_TOKEN=KEY)
    def test_nothing_of_this_on_a_client_install(self):
        self.assertEqual(self.client.post(reverse("feedback:send"), {"message": "hello"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("feedback:inbox") + f"?key={KEY}").status_code, 404)
        self.assertFalse(Feedback.objects.exists())
        self.assertNotContains(self.client.get(reverse("login")), "data-feedback-open")

    @override_settings(DEMO_MODE=True)
    def test_the_button_is_on_the_demo_pages(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, "data-feedback-open")
        self.assertContains(page, reverse("feedback:send"))


class RouterTests(TestCase):
    def test_feedback_stays_in_its_own_database(self):
        from django.db import router

        from settings_core.models import ClientProfile

        self.assertEqual(router.db_for_write(Feedback), "feedback")
        self.assertEqual(router.db_for_read(Feedback), "feedback")
        self.assertEqual(router.db_for_write(ClientProfile), "default")
        self.assertFalse(router.allow_migrate("default", "feedback"))
        self.assertTrue(router.allow_migrate("feedback", "feedback"))
        self.assertFalse(router.allow_migrate("feedback", "sales"))
        self.assertTrue(router.allow_migrate("default", "sales"))


class StylesheetTests(TestCase):
    """The button, the form and the inbox read the B+ tokens like every other screen."""

    SHEETS = ("hesba/css/demo_feedback.css", "hesba/css/demo_feedback_inbox.css")  # both carry DEMO-TRACK too

    def test_tokens_only(self):
        import re

        from settings_core.tests_tokens_adoption import read_static

        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        for sheet in self.SHEETS:
            with self.subTest(sheet=sheet):
                css = read_static(sheet)
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
                self.assertEqual(re.findall(r"rgba?\(\s*\d", css), [])
                self.assertNotRegex(css, r"font-weight:\s*[89]00")
                self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])


@override_settings(DEMO_MODE=True, FEEDBACK_VIEW_TOKEN=KEY)
class NeverLostTests(TestCase):
    """R2: a note reaches the server log, even when the feedback database is down."""

    databases = {"default", "feedback"}

    def test_every_note_is_in_the_log(self):
        with self.assertLogs("hesba.feedback", level="WARNING") as logged:
            self.client.post(reverse("feedback:send"), {"message": "الزرار ده مش واضح", "lang": "ar", "path": "/sales/new/"})
        self.assertIn("الزرار ده مش واضح", logged.output[0])
        self.assertIn("/sales/new/", logged.output[0])
        self.assertTrue(Feedback.objects.exists())

    def test_a_down_database_still_thanks_the_tester_and_logs_the_note(self):
        from django.db import OperationalError

        with mock.patch.object(Feedback, "save", side_effect=OperationalError("down")), \
                mock.patch("feedback.views.Feedback.objects") as objects, \
                self.assertLogs("hesba.feedback", level="WARNING") as logged:
            objects.filter.side_effect = OperationalError("down")
            response = self.client.post(reverse("feedback:send"), {"message": "ملاحظة وقت العطل", "lang": "ar"})
        self.assertEqual(response.json(), {"ok": True})
        self.assertIn("ملاحظة وقت العطل", "\n".join(logged.output))
        self.assertIn("only copy", "\n".join(logged.output))


    def test_the_hourly_limit_holds_while_the_database_is_down(self):
        from django.core.cache import cache
        from django.db import OperationalError

        from . import views

        cache.delete("feedback-rate:anonymous")
        with mock.patch.object(Feedback, "save", side_effect=OperationalError("down")), \
                mock.patch("feedback.views.Feedback.objects") as objects, \
                mock.patch.object(views, "MAX_ANONYMOUS_PER_HOUR", 2), \
                self.assertLogs("hesba.feedback", level="WARNING"):
            objects.filter.side_effect = OperationalError("down")
            codes = [self.client.post(reverse("feedback:send"), {"message": f"ملاحظة {n}", "lang": "ar"}).status_code for n in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        cache.delete("feedback-rate:anonymous")

    def test_notes_reach_the_console_whatever_the_app_log_level(self):
        import logging

        from django.conf import settings

        logger = logging.getLogger("hesba.feedback")
        self.assertFalse(logger.propagate)
        self.assertTrue(logger.handlers and all(handler.level <= logging.WARNING for handler in logger.handlers))
        self.assertIn("feedback_console", settings.LOGGING["loggers"]["hesba.feedback"]["handlers"])


class ShowcaseWarningsTests(TestCase):
    def test_a_showcase_without_the_key_or_a_kept_database_is_warned(self):
        from .checks import feedback_is_readable_and_kept

        with override_settings(DEMO_MODE=True, FEEDBACK_VIEW_TOKEN="", FEEDBACK_DATABASE_URL=""):
            self.assertEqual([w.id for w in feedback_is_readable_and_kept()], ["feedback.W001", "feedback.W002"])
        with override_settings(DEMO_MODE=True, FEEDBACK_VIEW_TOKEN=KEY, FEEDBACK_DATABASE_URL="postgresql://x"):
            self.assertEqual(feedback_is_readable_and_kept(), [])
        with override_settings(DEMO_MODE=False, FEEDBACK_VIEW_TOKEN=""):
            self.assertEqual(feedback_is_readable_and_kept(), [])


@override_settings(DEMO_MODE=True, FEEDBACK_VIEW_TOKEN=KEY)
class VisitTrackingTests(TestCase):
    """DEMO-TRACK: who visited the demo, in which activity, and which pages they opened."""

    databases = {"default", "feedback"}

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_a_visitor_is_known_from_the_first_page(self):
        from .models import PageView, Visit
        from .tracking import COOKIE

        page = self.client.get(reverse("login"), HTTP_USER_AGENT="Mobile Safari")
        self.assertEqual(page.status_code, 200)
        self.assertIn(COOKIE, page.cookies)
        self.assertTrue(page.cookies[COOKIE]["httponly"])
        visit = Visit.objects.get()
        self.assertEqual((visit.pages, visit.first_path, visit.last_path, visit.user_agent), (1, "/login/", "/login/", "Mobile Safari"))
        # The same cookie on the next pages: one visit, more pages.
        self.client.get(reverse("login") + "?lang=en")
        visit.refresh_from_db()
        self.assertEqual((Visit.objects.count(), visit.pages, visit.last_path, visit.lang), (1, 2, "/login/?lang=en", "en"))
        self.assertEqual(list(PageView.objects.values_list("path", flat=True)), ["/login/", "/login/?lang=en"])

    def test_the_activity_and_role_follow_the_demo_copy(self):
        from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
        from permissions.models import RoleCode
        from reports.tests_dashboard import prepared_client

        from .models import PageView, Visit

        prepared_client("medical", "clinic")
        user = make_user(username="track_cashier")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(user)
        page = self.client.get("/", follow=True)
        self.assertEqual(page.status_code, 200)
        visit = Visit.objects.get()
        self.assertEqual((visit.activity, visit.sub_activity, visit.role), ("medical", "clinic", RoleCode.CASHIER))
        self.assertTrue(PageView.objects.filter(activity="medical", sub_activity="clinic").exists())

    def test_only_whole_pages_are_noted(self):
        from .models import PageView

        self.client.get(reverse("login"))
        self.client.get(reverse("login"))  # the same page again at once: a reload, noted once
        self.client.get(reverse("login"), HTTP_X_REQUESTED_WITH="fetch")
        self.client.get("/no-such-page/")
        self.client.post(reverse("feedback:send"), {"message": "ملاحظة", "lang": "ar"})
        self.client.get(reverse("feedback:inbox") + f"?key={KEY}")
        self.assertEqual(PageView.objects.count(), 1)

    def test_one_visit_keeps_a_bounded_number_of_pages(self):
        from . import tracking
        from .models import PageView, Visit

        with mock.patch.object(tracking, "MAX_VIEWS", 2):
            for lang in ("ar", "en", "ar&x=1"):
                self.client.get(reverse("login") + f"?lang={lang}")
        visit = Visit.objects.get()
        self.assertEqual((PageView.objects.count(), visit.pages, visit.last_path), (2, 2, "/login/?lang=ar&x=1"))

    def test_a_forged_cookie_is_a_new_visitor(self):
        from .models import Visit
        from .tracking import COOKIE

        self.client.cookies[COOKIE] = "someone-elses-visit-id-xyz"
        page = self.client.get(reverse("login"))
        visitor = Visit.objects.get().visitor
        self.assertNotEqual(visitor, "someone-elses-visit-id-xyz")
        self.assertIn(COOKIE, page.cookies)

    def test_a_down_database_never_costs_the_page(self):
        from django.db import OperationalError

        from .models import Visit

        with mock.patch.object(Visit.objects, "get_or_create", side_effect=OperationalError("down")):
            self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        with mock.patch.object(Visit.objects, "get_or_create", side_effect=RuntimeError("bug")), \
                self.assertLogs("hesba.feedback", level="WARNING") as logged:
            self.assertEqual(self.client.get(reverse("login") + "?lang=en").status_code, 200)
        self.assertIn("RuntimeError", logged.output[0])

    def test_a_remote_database_is_written_after_the_page_is_served(self):
        import threading

        from . import tracking

        stored, done = [], threading.Event()

        def write(record):
            stored.append((record, threading.current_thread().name))
            done.set()

        with override_settings(DEMO_TRACK_BACKGROUND=True), mock.patch.object(tracking, "write", write):
            self.assertEqual(self.client.get(reverse("login") + "?lang=en").status_code, 200)
            self.assertTrue(done.wait(5))
        record, thread = stored[0]
        self.assertEqual((record["path"], record["lang"], thread), ("/login/?lang=en", "en", "hesba-demo-track"))
        # A Supabase feedback database is written in the background; the local file is not.
        with override_settings(DATABASES={"feedback": {"ENGINE": "django.db.backends.postgresql"}}):
            self.assertTrue(tracking.in_background())
        self.assertFalse(tracking.in_background())

    def test_a_full_queue_drops_the_page_not_the_server(self):
        import queue

        from . import tracking

        full = queue.Queue(maxsize=1)
        full.put({})
        with mock.patch.object(tracking, "_queue", full), mock.patch.object(tracking, "_writer", mock.Mock(is_alive=lambda: True)), \
                self.assertLogs("hesba.feedback", level="INFO") as logged:
            tracking._enqueue({"path": "/"})
        self.assertIn("not noted", logged.output[0])

    def test_the_visitor_may_leave_a_name_and_phone(self):
        from .models import Visit

        self.client.get(reverse("login"))
        url = reverse("feedback:hello")
        self.assertEqual(self.client.post(url, {"lang": "ar"}).json()["error"], "اكتب اسمك أو رقمك.")
        with self.assertLogs("hesba.feedback", level="WARNING") as logged:
            self.assertEqual(self.client.post(url, {"name": " منى ", "phone": "0100 123", "lang": "ar"}).json(), {"ok": True})
        self.assertIn("منى", logged.output[0])
        visit = Visit.objects.get()
        self.assertEqual((visit.name, visit.phone), ("منى", "0100 123"))
        # A later phone alone keeps the name.
        self.client.post(url, {"phone": "0111", "lang": "ar"})
        visit.refresh_from_db()
        self.assertEqual((visit.name, visit.phone), ("منى", "0111"))
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_a_name_without_a_visit_cookie_is_refused(self):
        from .models import Visit

        response = self.client.post(reverse("feedback:hello"), {"name": "x", "lang": "en"})
        self.assertEqual((response.status_code, response.json()["error"]), (400, "Reload the page first."))
        self.assertFalse(Visit.objects.exists())

    def test_the_welcome_card_and_the_notice_are_on_demo_pages(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, "data-hello")
        self.assertContains(page, reverse("feedback:hello"))
        self.assertContains(page, "data-demo-notice", count=2)
        self.assertContains(page, "بتتسجل عشان نحسّن البرنامج")


@override_settings(DEMO_MODE=True, FEEDBACK_VIEW_TOKEN=KEY)
class VisitsPageTests(TestCase):
    """DEMO-TRACK: Ahmed's page of visitors, behind the inbox key."""

    databases = {"default", "feedback"}

    @classmethod
    def setUpTestData(cls):
        from .models import PageView, Visit

        now = timezone.now()
        cls.mona = Visit.objects.create(visitor="v" * 24, name="منى", phone="0100", activity="medical", sub_activity="clinic",
                                        pages=3, last_seen_at=now, last_path="/sales/new/", sandbox="sbx-mona")
        Visit.objects.filter(pk=cls.mona.pk).update(started_at=now - timedelta(minutes=12))
        cls.anon = Visit.objects.create(visitor="w" * 24, activity="education", pages=1, last_seen_at=now - timedelta(days=3),
                                        last_path="/")
        for path in ("/setup/", "/dashboard/", "/sales/new/"):
            PageView.objects.create(visit=cls.mona, path=path, activity="medical", sub_activity="clinic")
        PageView.objects.create(visit=cls.anon, path="/", activity="education")
        PageView.objects.create(visit=cls.anon, path="/setup/", activity="")
        Feedback.objects.create(message="الفاتورة واضحة", sandbox="sbx-mona")

    def url(self, extra=""):
        return reverse("feedback:visits") + f"?key={KEY}{extra}"

    def test_needs_the_key_and_a_showcase(self):
        self.assertEqual(self.client.get(reverse("feedback:visits")).status_code, 404)
        self.assertEqual(self.client.get(reverse("feedback:visits") + "?key=wrong").status_code, 404)
        with override_settings(DEMO_MODE=False):
            self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_who_visited_and_which_activities_were_tried(self):
        from settings_core.setup_catalog import activity_label, sub_activity_label

        page = self.client.get(self.url())
        self.assertIn("no-store", page.headers["Cache-Control"])
        self.assertContains(page, "2 زيارة · 1 سابوا اسمهم أو رقمهم · 1 آخر 24 ساعة · 4 صفحة اتفتحت")
        self.assertContains(page, "data-visit", count=2)
        self.assertContains(page, "منى")
        self.assertContains(page, f"{activity_label('medical')} / {sub_activity_label('medical', 'clinic')}")
        tried = page.content.decode().split("data-tried", 1)[1].split("</table>", 1)[0]
        self.assertIn(activity_label("education"), tried)
        self.assertEqual(tried.count("<tr>"), 3)  # the header and two activities; a page before choosing is not one
        only = self.client.get(self.url("&activity=education"))
        self.assertContains(only, "data-visit", count=1)
        self.assertNotContains(only, "منى")

    def test_one_visit_in_detail_with_its_notes(self):
        page = self.client.get(self.url(f"&visit={self.mona.pk}"))
        self.assertContains(page, "data-page-view", count=3)
        self.assertContains(page, "3 صفحة في 12 دقيقة")
        self.assertContains(page, "الفاتورة واضحة")
        self.assertEqual(self.client.get(self.url("&visit=999999")).status_code, 404)

    def test_csv(self):
        response = self.client.get(self.url("&format=csv"))
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        text = response.content.decode("utf-8")
        self.assertTrue(text.startswith("\ufeffstarted,last_seen,minutes,name"))
        self.assertIn("منى,0100,medical,clinic", text)
        self.assertEqual(len(text.strip().splitlines()), 3)

    def test_the_inbox_links_here(self):
        self.assertContains(self.client.get(reverse("feedback:inbox") + f"?key={KEY}"), reverse("feedback:visits"))


class NoTrackingOnClientsTests(TestCase):
    databases = {"default", "feedback"}

    def test_a_client_install_notes_nothing(self):
        from .models import Visit
        from .tracking import COOKIE

        page = self.client.get(reverse("login"))
        self.assertNotIn(COOKIE, page.cookies)
        self.assertNotContains(page, "data-hello")
        self.assertFalse(Visit.objects.exists())
        self.assertEqual(self.client.post(reverse("feedback:hello"), {"name": "x"}).status_code, 404)
