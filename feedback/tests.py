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

    SHEETS = ("hesba/css/demo_feedback.css", "hesba/css/demo_feedback_inbox.css")

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
