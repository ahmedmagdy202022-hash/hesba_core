"""E9 (HG-104): group content: the link rules, the staff screens (guard order, POST only, soft removal) and what a guardian reads.

Hygiene: the portal tests run on the frozen Wednesday noon of ``tests_portal`` (so nothing depends on the time of day or the weekday);
the rules of the link are plain functions with no clock; every assertion on a page reads its own element (a child's content section, a
row, a link), never "is not in" over a whole page that also holds a token, a CSRF value or an id; markers are long Arabic phrases.
"""

import pathlib
import re
from datetime import datetime, timedelta
from unittest import mock

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from entities.models import Entity
from ledger.models import JournalEntry
from messaging.models import OutboxMessage
from sales.models import SalesInvoice

from . import content, portal, services
from .models import ContentKind, Enrollment, GroupContent, StudyGroup
from .tests_exams import ExamSetup
from .tests_portal import FIXED, MARIAM, YOUSEF, PortalSetup, day, normalized

GOOD = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_A, FILE_A, ONLINE_A = "شرح الباب الأول في الرياضيات بالتفصيل", "ملزمة المراجعة النهائية للرياضيات", "حصة المراجعة المباشرة للرياضيات"
HIDDEN, GONE, VIDEO_B = "محتوى مخفي سري عن أولياء الأمور", "محتوى اتشال من المجموعة", "فيديو العلوم الخاص بالمجموعة الصباحية"
OLD, NEAR, NOTIME = "حصة أونلاين قديمة انتهت من يومين", "حصة أونلاين لسه قريبة من ساعات", "حصة أونلاين لسه ماتحددش ميعادها"
BRANCH_ITEM, NOTE_TEXT = "محتوى فرع تاني مفروض ميظهرش هنا", "ملاحظة المدرس عن الحصة دي"


# ---- the link rules ----

class CleanUrlTests(SimpleTestCase):
    def test_what_is_accepted(self):
        for raw, stored in (
            (GOOD, GOOD),
            ("https://drive.google.com/file/d/1AbC_dEf-GhI/view?usp=sharing", "https://drive.google.com/file/d/1AbC_dEf-GhI/view?usp=sharing"),
            ("https://zoom.us/j/123456789?pwd=AbCdEf", "https://zoom.us/j/123456789?pwd=AbCdEf"),
            ("HTTPS://meet.google.com/abc-defg-hij", "https://meet.google.com/abc-defg-hij"),  # only the scheme is lower-cased
            ("  https://example.com/a  \n", "https://example.com/a"),  # pasted whitespace around it is trimmed
            ("https://example.com:8443/x#t=30", "https://example.com:8443/x#t=30"),
            ("https://sub.example.co.uk/Path/CaseSensitiveID", "https://sub.example.co.uk/Path/CaseSensitiveID"),  # a path keeps its case
            ("https://xn--mgbh0fb.xn--mgberp4a5d4ar/x", "https://xn--mgbh0fb.xn--mgberp4a5d4ar/x"),
            ("https://example.com/ملف-المراجعة.pdf", "https://example.com/ملف-المراجعة.pdf"),
            ("https://example.com/?q=a&b=c%20d", "https://example.com/?q=a&b=c%20d"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(content.clean_url(raw), stored)

    def test_what_is_refused(self):
        for raw in (
            "http://example.com/a", "HTTP://example.com/a", "javascript:alert(1)", "JavaScript:alert(document.cookie)", "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)", "ftp://example.com/a", "file:///etc/passwd", "//example.com/a", "example.com/a", "www.example.com", "https:example.com", "https:/example.com",
            "https:///example.com", "https://", "https://user:pass@example.com/a", "https://user@example.com/a", "https://example.com@evil.example/a",
            "https://good.example\\@evil.example/", "https://example.com\\evil", "https://example.com/a b", "https://exa mple.com/", "https://example.com/a\tb",
            "https://example.com/a\nb", "https://example.com/a\rb", "https://example.com/a\x00b", "https://example.com/a‮b", "https://example.com/a​b",
            "https://example.com/a b", "https://exa mple.com/", "https://localhost/a", "https://127.0.0.1/a", "https://192.168.1.5/a", "https://10.0.0.1:8080/a",
            "https://[::1]/a", "https://example.com:abc/a", "https://example.com:99999/a", "https://-bad.example.com/", "https://bad-.example.com/",
            "https://exa_mple.com/", "https://bücher.example/a", "https://example./a", "https://.example.com/a", "https://example..com/a", "https://example.com./a",
            "https://example.c/a", "https://example.123/a", "https://exa%6dple.com/a", "", "   ", None,
            "https://example.com/" + "a" * 500, "https://" + "a" * 60 + "." + "b" * 60 + "." + "c" * 60 + "." + "d" * 60 + "." + "e" * 60 + "." + "f" * 60 + "." + "g" * 60 + ".com/" + "z" * 100,
        ):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                content.clean_url(raw)

    def test_the_longest_address_that_fits_is_kept_and_one_more_is_not(self):
        fits = "https://example.com/" + "a" * (content.URL_MAX - len("https://example.com/"))
        self.assertEqual(len(fits), 500)
        self.assertEqual(content.clean_url(fits), fits)
        with self.assertRaises(ValidationError):
            content.clean_url(fits + "a")

    def test_the_refusal_speaks_the_page_language(self):
        with self.assertRaises(ValidationError) as arabic:
            content.clean_url("http://example.com", "ar")
        with self.assertRaises(ValidationError) as english:
            content.clean_url("http://example.com", "en")
        self.assertIn("https://", arabic.exception.messages[0])
        self.assertEqual(english.exception.messages, [content.MESSAGES["en"]["url_bad"]])
        self.assertNotEqual(arabic.exception.messages, english.exception.messages)

    def test_every_other_part_goes_through_the_one_function(self):
        source = pathlib.Path(settings.BASE_DIR, "education", "views_content.py").read_text(encoding="utf-8")
        self.assertNotIn("urlsplit", source)
        self.assertNotIn("startswith(\"https", source)
        self.assertNotIn("messaging", pathlib.Path(settings.BASE_DIR, "education", "content.py").read_text(encoding="utf-8"))


# ---- the staff side ----

class StaffSetup(ExamSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def add(self, group=None, title=VIDEO_A, kind="video", url=GOOD, user=None, **extra):
        return content.save_content(group or self.a, {"title": title, "kind": kind, "url": url, **extra}, user or self.owner)

    def post(self, name, *args, user=None, **data):
        client = self.client if user is None else Client()
        if user is not None:
            client.force_login(user)
        return client.post(reverse(f"education:{name}", args=args), {"lang": "ar", **data})

    def get(self, name, *args, lang="ar", user=None):
        client = self.client if user is None else Client()
        if user is not None:
            client.force_login(user)
        return client.get(reverse(f"education:{name}", args=args) + f"?lang={lang}")

    def branch(self):
        return Entity.objects.create(code="E-CNT", name_ar="فرع المحتوى", activity_slug="education", sub_activity_slug="tutoring_center")

    def other_entity_group(self, branch):
        return StudyGroup.objects.create(code="G-BRANCH", course=self.maths, name="مجموعة الفرع", entity=branch)

    def work_in(self, branch):
        session = self.client.session
        session["hesba_entity"] = branch.pk
        session.save()

    def row(self, html, item):
        found = re.search(rf'<tr data-content="{item.pk}".*?</tr>', html, re.S)
        self.assertIsNotNone(found, f"row {item.pk}")
        return found.group(0)


class RulesTests(StaffSetup):
    def test_a_new_row_is_published_active_and_audited_without_its_address(self):
        item = self.add(url="https://drive.google.com/file/d/SECRETKEY123/view", note="  ملاحظة  ")
        self.assertEqual((item.group, item.kind, item.title, item.note, item.starts_at, item.published, item.active, item.created_by),
                         (self.a, "video", VIDEO_A, "ملاحظة", None, True, True, self.owner))
        log = AuditLog.objects.get(action="save_group_content")
        self.assertEqual((log.object_id, log.before_data, log.after_data["host"], log.after_data["published"]), (str(item.pk), {}, "drive.google.com", True))
        self.assertNotIn("SECRETKEY123", str(log.after_data) + str(log.before_data))
        changed = content.save_content(self.a, {"title": "عنوان جديد", "kind": "file", "url": GOOD}, self.owner, item)
        self.assertEqual((changed.pk, changed.title, changed.kind, changed.url), (item.pk, "عنوان جديد", "file", GOOD))
        update = AuditLog.objects.filter(action="save_group_content").latest("pk")
        self.assertEqual((update.before_data["title"], update.after_data["title"], update.before_data["host"], update.after_data["host"]), (VIDEO_A, "عنوان جديد", "drive.google.com", "www.youtube.com"))

    def test_bad_input_is_refused_and_nothing_is_saved(self):
        base = {"title": "x", "kind": "video", "url": GOOD}
        for patch, message in (({"title": "  "}, "عنوان المحتوى"), ({"title": "ع" * 161}, "العنوان أطول"), ({"kind": "audio"}, "نوع المحتوى"),
                               ({"url": ""}, "اكتب الرابط"), ({"url": "http://example.com"}, "https://"), ({"url": "javascript:alert(1)"}, "https://"),
                               ({"note": "ن" * 256}, "الملاحظة أطول"), ({"kind": "online", "starts_at": "غدا الساعة خمسة"}, "ميعاد الحصة")):
            with self.subTest(patch=patch), self.assertRaisesMessage(ValidationError, message):
                content.save_content(self.a, {**base, **patch}, self.owner)
        self.assertFalse(GroupContent.objects.exists())
        self.assertEqual(AuditLog.objects.filter(action="save_group_content").count(), 0)

    def test_the_longest_title_and_note_are_kept(self):
        item = self.add(title="ع" * 160, note="ن" * 255)
        item.refresh_from_db()
        self.assertEqual((len(item.title), len(item.note)), (160, 255))

    def test_an_online_class_keeps_its_start_in_the_centres_time_and_other_kinds_drop_it(self):
        item = self.add(kind="online", starts_at="2026-11-20T18:30")
        self.assertEqual(timezone.localtime(item.starts_at).strftime("%Y-%m-%d %H:%M"), "2026-11-20 18:30")
        self.assertIsNotNone(item.starts_at.tzinfo)
        self.assertIsNone(self.add(kind="online", starts_at="").starts_at)  # an online class with no time yet is allowed
        video = self.add(kind="video", starts_at="2026-11-20T18:30")
        self.assertIsNone(video.starts_at)  # a time on any other kind is not kept
        edited = content.save_content(self.a, {"title": ONLINE_A, "kind": "video", "url": GOOD, "starts_at": "2026-11-21T10:00"}, self.owner, item)
        self.assertIsNone(edited.starts_at)

    def test_a_closed_group_takes_no_new_content_but_a_row_can_still_be_taken_down(self):
        item = self.add()
        self.a.active = False
        self.a.save()
        with self.assertRaisesMessage(ValidationError, "مقفولة"):
            self.add(title="جديد")
        self.assertEqual(content.remove(item, self.owner).active, False)

    def test_only_a_manager_or_the_groups_teacher_may_change_it(self):
        item = self.add()
        content.save_content(self.a, {"title": "من المدرس", "kind": "file", "url": GOOD}, self.teacher_user)  # the group's own teacher
        for user in (self.cashier, self.other_user):
            with self.subTest(user=user.username):
                with self.assertRaises(PermissionDenied):
                    content.save_content(self.a, {"title": "x", "kind": "video", "url": GOOD}, user)
                for call in (lambda: content.save_content(self.a, {"title": "x", "kind": "video", "url": GOOD}, user, item), lambda: content.set_published(item, False, user),
                             lambda: content.remove(item, user)):
                    with self.assertRaises(PermissionDenied):
                        call()
        item.refresh_from_db()
        self.assertEqual((item.title, item.published, item.active, GroupContent.objects.count()), (VIDEO_A, True, True, 2))

    def test_hide_show_and_remove_are_soft_and_audited(self):
        item = self.add()
        content.set_published(item, False, self.owner)
        self.assertEqual(GroupContent.objects.get(pk=item.pk).published, False)
        content.set_published(item, True, self.teacher_user)
        self.assertEqual(GroupContent.objects.get(pk=item.pk).published, True)
        content.remove(item, self.owner)
        content.remove(item, self.owner)  # twice is not an error and writes one row only
        row = GroupContent.objects.get(pk=item.pk)
        self.assertEqual((row.active, row.title, row.url), (False, VIDEO_A, GOOD))  # nothing is deleted
        actions = list(AuditLog.objects.filter(module="education", object_type="education.GroupContent").order_by("pk").values_list("action", flat=True))
        self.assertEqual(actions, ["save_group_content", "hide_group_content", "show_group_content", "remove_group_content"])
        with self.assertRaisesMessage(ValidationError, "اتشال"):
            content.set_published(item, False, self.owner)
        with self.assertRaisesMessage(ValidationError, "اتشال"):
            content.save_content(self.a, {"title": "x", "kind": "video", "url": GOOD}, self.owner, item)
        self.assertEqual(content.group_items(self.a), [])

    def test_staff_still_see_a_hidden_row_and_an_old_online_class(self):
        hidden = self.add(title=HIDDEN)
        content.set_published(hidden, False, self.owner)
        old = self.add(kind="online", title=OLD, starts_at="2026-01-02T10:00")
        gone = self.add(title=GONE)
        content.remove(gone, self.owner)
        self.assertEqual({row.pk for row in content.group_items(self.a)}, {hidden.pk, old.pk})

    def test_the_whole_feature_moves_no_money_stock_or_message(self):
        counts = lambda: (SalesInvoice.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count(), OutboxMessage.objects.count())  # noqa: E731
        before = counts()
        item = self.add(kind="online", starts_at="2026-11-20T18:30")
        content.set_published(item, False, self.owner)
        content.set_published(item, True, self.owner)
        content.remove(item, self.owner)
        self.assertEqual(counts(), before)


class ScreenTests(StaffSetup):
    def test_the_group_page_has_the_card_for_a_manager_and_the_groups_teacher_only(self):
        item = self.add(note=NOTE_TEXT)
        hidden = self.add(title=HIDDEN)
        content.set_published(hidden, False, self.owner)
        page = self.get("group", self.a.pk)
        html = page.content.decode()
        card = re.search(r"<section class=\"op-detail-meta\" data-content-card>.*?</section>", html, re.S).group(0)
        self.assertIn("محتوى المجموعة", card)
        self.assertIn('data-new-content', card)
        self.assertEqual(re.findall(r'<tr data-content="(\d+)"', card), [str(hidden.pk), str(item.pk)])  # newest first
        self.assertIn('data-published="1"', self.row(card, item))
        self.assertIn('data-published="0"', self.row(card, hidden))
        self.assertIn(NOTE_TEXT, self.row(card, item))
        self.assertContains(self.get("group", self.a.pk, user=self.teacher_user), "data-content-card")
        for user in (self.other_user, self.cashier):  # they may open the page; the card is not drawn for them
            with self.subTest(user=user.username):
                page = self.get("group", self.a.pk, user=user)
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, "data-content-card")
                self.assertNotContains(page, "data-new-content")

    def test_todays_classes_link_to_the_content_of_the_teachers_own_groups(self):
        page = self.get("today", user=self.teacher_user)
        self.assertContains(page, f'data-content="{self.a.pk}"')
        self.assertNotContains(page, f'data-content="{self.b.pk}"')
        self.assertNotContains(self.get("today", user=self.cashier), "data-content=")

    def test_a_row_reads_kind_date_status_and_every_address_opens_safely(self):
        online = self.add(kind="online", title=ONLINE_A, url="https://zoom.us/j/1", starts_at="2026-11-20T18:30")
        html = self.get("content", self.a.pk).content.decode()
        row = self.row(html, online)
        self.assertIn("حصة أونلاين", row)
        self.assertIn("18:30", row)
        self.assertIn("20 نوفمبر 2026", row)
        self.assertRegex(row, r'<a href="https://zoom.us/j/1" target="_blank" rel="noopener noreferrer" data-content-link>')
        self.assertIn("ظاهر لأولياء الأمور", row)

    def test_a_title_note_and_address_are_escaped_never_trusted(self):
        item = self.add(title="<script>alert(1)</script>", note="<img src=x onerror=alert(2)>", url='https://example.com/?q="onmouseover="x&r=<b>')
        row = self.row(self.get("content", self.a.pk).content.decode(), item)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", row)
        self.assertNotIn("<script>", row)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", row)
        self.assertNotIn("<img", row)
        self.assertIn("?q=&quot;onmouseover=&quot;x&amp;r=&lt;b&gt;", row)
        self.assertNotIn('onmouseover="x', row)

    def test_hide_show_and_remove_work_from_the_screen_and_come_back_to_the_list(self):
        item = self.add()
        done = self.post("content_hide", item.pk)
        self.assertEqual((done.status_code, done["Location"]), (302, reverse("education:content", args=[self.a.pk]) + "?lang=ar"))
        self.assertFalse(GroupContent.objects.get(pk=item.pk).published)
        self.assertEqual(self.post("content_show", item.pk, user=self.teacher_user).status_code, 302)
        self.assertTrue(GroupContent.objects.get(pk=item.pk).published)
        self.assertEqual(self.post("content_remove", item.pk).status_code, 302)
        self.assertFalse(GroupContent.objects.get(pk=item.pk).active)
        listing = self.get("content", self.a.pk).content.decode()
        self.assertEqual(re.findall(r'<tr data-content="(\d+)"', listing), [])  # no row left in the list
        self.assertEqual(self.get("content_edit", item.pk).status_code, 404)  # a removed row is not edited
        again = self.post("content_hide", item.pk)  # a hide on a removed row says so and changes nothing
        self.assertEqual(again.status_code, 302)
        self.assertTrue(any("اتشال" in str(message) for message in get_messages(again.wsgi_request)))
        self.assertTrue(GroupContent.objects.get(pk=item.pk).published)

    def test_the_add_form_saves_and_keeps_what_was_typed_when_the_address_is_refused(self):
        form = {"kind": "online", "title": ONLINE_A, "url": "http://example.com/live", "note": NOTE_TEXT, "starts_at": "2026-11-20T18:30"}
        page = self.post("content_new", self.a.pk, **form)
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        error = re.search(r'data-form-error>(.*?)</div>', html, re.S).group(1)
        self.assertIn("https://", error)
        self.assertRegex(html, r'<input id="ct-url"[^>]*value="http://example.com/live"')
        self.assertRegex(html, rf'<input id="ct-title"[^>]*value="{ONLINE_A}"')
        self.assertRegex(html, r'<input id="ct-start"[^>]*value="2026-11-20T18:30"')
        self.assertFalse(GroupContent.objects.exists())
        done = self.post("content_new", self.a.pk, **{**form, "url": "https://zoom.us/j/55"})
        self.assertEqual(done.status_code, 302)
        item = GroupContent.objects.get()
        self.assertEqual((item.kind, item.url, item.note, item.published, item.active), ("online", "https://zoom.us/j/55", NOTE_TEXT, True, True))
        edit = self.get("content_edit", item.pk).content.decode()
        self.assertRegex(edit, r'<input id="ct-start"[^>]*value="2026-11-20T18:30"')
        self.assertRegex(edit, r'<input id="ct-url"[^>]*value="https://zoom.us/j/55"')
        self.assertEqual(self.post("content_edit", item.pk, kind="file", title="الملف", url=GOOD).status_code, 302)
        item.refresh_from_db()
        self.assertEqual((item.kind, item.title, item.starts_at), ("file", "الملف", None))

    def test_every_screen_speaks_english_on_request(self):
        item = self.add()
        card = self.get("group", self.a.pk, lang="en").content.decode()
        self.assertIn("Group content", card)
        listing = self.get("content", self.a.pk, lang="en").content.decode()
        row = self.row(listing, item)
        for word in ("Recorded video", "Shown to guardians", "Hide", "Edit"):
            self.assertIn(word, row)
        self.assertIn("Add content", listing)
        self.assertIn("The centre keeps only the title and the link", listing)
        form = self.get("content_new", self.a.pk, lang="en").content.decode()
        for word in ("Title", "Link", "Class time", "Online class", "Save"):
            self.assertIn(word, form)
        refused = self.post("content_new", self.a.pk, lang="en", kind="video", title="x", url="http://example.com").content.decode()
        self.assertIn("The link must start with https://", refused)
        self.assertEqual(set(content.MESSAGES["ar"]), set(content.MESSAGES["en"]))
        from . import views_content

        self.assertEqual(set(views_content.WORDS["ar"]), set(views_content.WORDS["en"]))
        self.assertEqual(set(views_content.WORDS["ar"]["kinds"]), set(views_content.WORDS["en"]["kinds"]))


class GuardTests(StaffSetup):
    """The exam screens' order: not an education install (404), the record in the entity being worked in (404), then the permission (403)."""

    def urls(self, item):
        """Every screen of one group's content (and of one row), with the method that changes something."""

        return [("get", reverse("education:content", args=[self.a.pk])), ("get", reverse("education:content_new", args=[self.a.pk])),
                ("post", reverse("education:content_new", args=[self.a.pk])), ("get", reverse("education:content_edit", args=[item.pk])),
                ("post", reverse("education:content_edit", args=[item.pk])), ("post", reverse("education:content_hide", args=[item.pk])),
                ("post", reverse("education:content_show", args=[item.pk])), ("post", reverse("education:content_remove", args=[item.pk]))]

    def call(self, client, method, url):
        form = {"lang": "ar", "kind": "video", "title": "x", "url": GOOD}
        return client.get(url) if method == "get" else client.post(url, form)

    def test_not_an_education_install_is_a_404_before_anything_else(self):
        from settings_core.models import ClientProfile

        item = self.add()
        profile = ClientProfile.get_active()
        profile.activity_slug = "commercial"
        profile.save()
        for user in (self.owner, self.cashier):  # the cashier would be a 403 on an education install
            client = Client()
            client.force_login(user)
            for method, url in self.urls(item):
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.call(client, method, url).status_code, 404)
        self.assertEqual(GroupContent.objects.count(), 1)

    def test_a_group_or_row_of_another_entity_is_a_404_even_for_who_would_get_a_403(self):
        branch = self.branch()
        theirs = self.other_entity_group(branch)
        foreign = self.add(theirs)
        mine = self.add()
        for user in (self.owner, self.cashier):
            client = Client()
            client.force_login(user)
            session = client.session
            session["hesba_entity"] = branch.pk
            session.save()
            for method, url in self.urls(mine):  # from the branch, the main entity's group and row do not exist
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.call(client, method, url).status_code, 404)
        # and from the main entity, the branch's group and row do not exist either
        from entities.services import main_entity

        for user in (self.owner, self.cashier):
            client = Client()
            client.force_login(user)
            session = client.session
            session["hesba_entity"] = main_entity().pk
            session.save()
            for name, args in (("content", [theirs.pk]), ("content_new", [theirs.pk]), ("content_edit", [foreign.pk])):
                with self.subTest(user=user.username, url=name):
                    self.assertEqual(client.get(reverse(f"education:{name}", args=args)).status_code, 404)
                    self.assertEqual(client.post(reverse(f"education:{name}", args=args), {"title": "x", "kind": "video", "url": GOOD}).status_code, 404)
            for name in ("content_hide", "content_show", "content_remove"):
                self.assertEqual(client.post(reverse(f"education:{name}", args=[foreign.pk]), {"lang": "ar"}).status_code, 404, name)
            self.assertEqual(client.get(reverse("education:group", args=[theirs.pk])).status_code, 404)  # and the group page carries no card for it
        foreign.refresh_from_db()
        self.assertEqual((foreign.published, foreign.active, GroupContent.objects.count()), (True, True, 2))

    def test_a_person_who_may_not_is_a_403_on_every_screen_and_nothing_changes(self):
        item = self.add()
        for user in (self.cashier, self.other_user):  # a cashier; the teacher of another group
            client = Client()
            client.force_login(user)
            for method, url in self.urls(item):
                with self.subTest(user=user.username, url=url, method=method):
                    self.assertEqual(self.call(client, method, url).status_code, 403)
        item.refresh_from_db()
        self.assertEqual((item.title, item.published, item.active, GroupContent.objects.count()), (VIDEO_A, True, True, 1))

    def test_a_teacher_with_no_student_file_permission_works_from_todays_classes(self):
        from hesba_testing.factories import make_role, make_user, make_user_profile

        bare = make_user(username="content_bare_teacher")
        make_user_profile(user=bare, role=make_role(code="EMPTY-TEACHER-E9"))
        self.teacher.user = bare
        self.teacher.save()
        self.assertFalse(services.can_view(bare))
        client = Client()
        client.force_login(bare)
        listing = client.get(reverse("education:content", args=[self.a.pk]) + "?lang=ar")
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, f'href="{reverse("education:today")}?lang=ar"')  # back goes to today, not to a page they cannot open
        self.assertEqual(client.post(reverse("education:content_new", args=[self.a.pk]), {"lang": "ar", "kind": "video", "title": "x", "url": GOOD}).status_code, 302)
        self.assertEqual(client.get(reverse("education:content", args=[self.b.pk])).status_code, 403)
        self.assertEqual(client.get(reverse("education:group", args=[self.a.pk])).status_code, 403)

    def test_the_three_changes_are_post_only(self):
        item = self.add()
        for name in ("content_hide", "content_show", "content_remove"):
            url = reverse(f"education:{name}", args=[item.pk])
            for verb in ("get", "put", "delete", "patch"):
                with self.subTest(name=name, verb=verb):
                    self.assertEqual(getattr(self.client, verb)(url).status_code, 405)
        item.refresh_from_db()
        self.assertEqual((item.published, item.active), (True, True))

    def test_every_change_is_csrf_protected(self):
        item = self.add()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for name, args in (("content_hide", [item.pk]), ("content_show", [item.pk]), ("content_remove", [item.pk]), ("content_new", [self.a.pk]), ("content_edit", [item.pk])):
            with self.subTest(name=name):
                self.assertEqual(client.post(reverse(f"education:{name}", args=args), {"lang": "ar", "kind": "video", "title": "x", "url": GOOD}).status_code, 403)
        item.refresh_from_db()
        self.assertEqual((item.title, item.published, item.active, GroupContent.objects.count()), (VIDEO_A, True, True, 1))

    def test_an_unknown_row_is_a_404(self):
        for name in ("content_edit",):
            self.assertEqual(self.client.get(reverse(f"education:{name}", args=[999999])).status_code, 404)
        for name in ("content_hide", "content_show", "content_remove"):
            self.assertEqual(self.client.post(reverse(f"education:{name}", args=[999999]), {"lang": "ar"}).status_code, 404)


# ---- what a guardian reads ----

class ContentPortalSetup(PortalSetup):
    """Yousef is in group A, Mariam in group B (the same payer), a stranger of another payer is in A."""

    def setUp(self):
        super().setUp()
        self.found = None

    def put(self, group, title, kind="video", url=GOOD, **extra):
        return content.save_content(group, {"title": title, "kind": kind, "url": url, **extra}, self.owner)

    def stamp(self, delta):
        """A start time for the form's box, ``delta`` from the frozen moment, in the centre's own time."""

        return timezone.localtime(FIXED + delta).strftime("%Y-%m-%dT%H:%M")

    def read(self, lang="ar"):
        """``{child name: [titles]}`` straight from ``portal.content`` for a fresh link."""

        token = self.token()
        found = portal.read_token(token)
        sheet = portal.content(found, lang)
        return {block["student"].name: [row["title"] for row in block["content"]] for block in sheet["children"]}

    def sections(self, html):
        """``{child name: that child's content section}``: every assertion below reads its own element."""

        out = {}
        for article in re.findall(r'<article class="gp-child".*?</article>', html, re.S):
            name = re.search(r'<h2 class="gp-name"[^>]*><bdi>(.*?)</bdi>', article, re.S).group(1)
            out[name] = re.search(r'<section class="cl-card" data-section="content">.*?</section>', article, re.S).group(0)
        return out


class WhoSeesWhatTests(ContentPortalSetup):
    def test_each_child_reads_only_published_active_items_of_the_groups_they_are_in(self):
        self.put(self.group_a, VIDEO_A)
        self.put(self.group_a, FILE_A, kind="file", url="https://drive.google.com/file/d/abc/view")
        self.put(self.group_b, VIDEO_B)
        hidden = self.put(self.group_a, HIDDEN)
        content.set_published(hidden, False, self.owner)
        gone = self.put(self.group_a, GONE)
        content.remove(gone, self.owner)
        shown = self.read()
        self.assertEqual(shown[YOUSEF], [FILE_A, VIDEO_A])  # newest first
        self.assertEqual(shown[MARIAM], [VIDEO_B])  # her sibling's group content is not hers

    def test_an_enrolment_that_was_stopped_shows_nothing_and_a_new_one_shows_it(self):
        self.put(self.group_a, VIDEO_A)
        enrolment = services.enroll(self.mariam, self.group_a, self.owner, start_date=day(5))
        self.assertEqual(self.read()[MARIAM], [VIDEO_A])
        services.stop(enrolment, self.owner, end_date=day(1))
        self.assertEqual(self.read()[MARIAM], [])
        self.assertEqual(self.read()[YOUSEF], [VIDEO_A])  # the child who is still in the group keeps it

    def test_a_closed_group_shows_nothing_as_the_groups_card_lists_it_no_more(self):
        self.put(self.group_a, VIDEO_A)
        self.group_a.active = False
        self.group_a.save()
        self.assertEqual(self.read()[YOUSEF], [])

    def test_another_entitys_group_and_content_are_not_shown(self):
        branch = Entity.objects.create(code="E-CNT-P", name_ar="فرع الباب", activity_slug="education", sub_activity_slug="tutoring_center")
        theirs = StudyGroup.objects.create(code="G-BR", course=self.course_a, name="مجموعة الفرع", entity=branch)
        Enrollment.objects.create(student=self.yousef, group=theirs, start_date=day(3), created_by=self.owner)  # even an enrolment of the same child
        GroupContent.objects.create(group=theirs, title=BRANCH_ITEM, url=GOOD, created_by=self.owner)
        self.put(self.group_a, VIDEO_A)
        self.assertEqual(self.read()[YOUSEF], [VIDEO_A])

    def test_a_row_whose_address_breaks_the_rules_is_not_shown_whatever_put_it_there(self):
        for index, bad in enumerate(("javascript:alert(1)", "http://example.com/a", "https://user:pw@example.com/a", "data:text/html,x")):
            GroupContent.objects.create(group=self.group_a, title=f"عنوان فيه رابط مرفوض رقم {index}", url=bad, created_by=self.owner)
        self.put(self.group_a, VIDEO_A)
        self.assertEqual(self.read()[YOUSEF], [VIDEO_A])

    def test_an_online_class_stays_for_a_day_after_it_starts_and_then_goes_but_staff_still_see_it(self):
        old = self.put(self.group_a, OLD, kind="online", url="https://zoom.us/j/1", starts_at=self.stamp(timedelta(hours=-25)))
        self.put(self.group_a, NEAR, kind="online", url="https://zoom.us/j/2", starts_at=self.stamp(timedelta(hours=-23)))
        self.put(self.group_a, ONLINE_A, kind="online", url="https://zoom.us/j/3", starts_at=self.stamp(timedelta(hours=3)))
        self.put(self.group_a, NOTIME, kind="online", url="https://zoom.us/j/4")  # no time yet: still shown
        self.put(self.group_a, VIDEO_A)  # an old video is never "stale"
        shown = self.read()[YOUSEF]
        self.assertEqual(shown, [NEAR, ONLINE_A, NOTIME, VIDEO_A])  # online first (soonest first, no time last), then the rest
        self.assertNotIn(OLD, shown)
        self.assertIn(old, content.group_items(self.group_a))  # staff still see it

    def test_the_cut_is_exactly_a_day_after_the_start(self):
        self.put(self.group_a, OLD, kind="online", url="https://zoom.us/j/1", starts_at=self.stamp(timedelta(hours=-24)))
        token = self.token()

        def titles():
            sheet = portal.content(portal.read_token(token), "ar")
            return next([row["title"] for row in block["content"]] for block in sheet["children"] if block["student"].name == YOUSEF)

        self.assertEqual(titles(), [OLD])  # exactly a day: still shown
        with self.clock(minutes=2):
            self.assertEqual(titles(), [])


class PageTests(ContentPortalSetup):
    def setUp(self):
        super().setUp()
        self.put(self.group_a, VIDEO_A, note=NOTE_TEXT)
        self.put(self.group_a, FILE_A, kind="file", url="https://drive.google.com/file/d/abc/view")
        self.online = self.put(self.group_a, ONLINE_A, kind="online", url="https://zoom.us/j/99?pwd=x", starts_at=self.stamp(timedelta(hours=2)))
        self.put(self.group_b, VIDEO_B)

    def test_each_childs_section_lists_their_items_with_the_right_words_and_buttons(self):
        client, token, page = self.open_portal()
        sections = self.sections(page.content.decode())
        mine, hers = sections[YOUSEF], sections[MARIAM]
        self.assertEqual(re.findall(r'data-kind="(\w+)"', mine), ["online", "file", "video"])
        self.assertIn(ONLINE_A, mine)
        self.assertIn("حصة أونلاين", re.search(r'<span class="gp-chip gp-chip--online" data-online-label>(.*?)</span>', mine).group(1))
        when = re.search(r'data-content-when>(.*?)</div>', mine, re.S).group(1)
        self.assertIn("ميعاد الحصة", when)
        self.assertIn("11 نوفمبر 2026", when)
        self.assertIn("14:00", when)  # the frozen noon plus two hours, in the centre's own time
        buttons = re.findall(r'<a class="cl-btn cl-btn--primary gp-open" href="([^"]*)" target="_blank" rel="noopener noreferrer" data-content-open>(.*?)</a>', mine)
        self.assertEqual(buttons, [("https://zoom.us/j/99?pwd=x", "ادخل الحصة"), ("https://drive.google.com/file/d/abc/view", "افتح الملف"), (GOOD, "شاهد الفيديو")])
        self.assertIn(NOTE_TEXT, re.search(r'data-content-note><bdi>(.*?)</bdi>', mine).group(1))
        self.assertEqual(re.findall(r'data-content-item', hers), ["data-content-item"])
        self.assertIn(VIDEO_B, hers)
        self.assertNotIn(VIDEO_A, hers)
        self.assertNotIn(ONLINE_A, hers)

    def test_every_link_leaves_in_a_new_tab_with_no_opener_and_no_referrer(self):
        client, token, page = self.open_portal()
        for section in self.sections(page.content.decode()).values():
            anchors = re.findall(r"<a [^>]*data-content-open[^>]*>", section)
            self.assertTrue(anchors)
            for anchor in anchors:
                self.assertIn('rel="noopener noreferrer"', anchor)
                self.assertIn('target="_blank"', anchor)
                self.assertRegex(anchor, r'href="https://')

    def test_a_title_with_a_script_tag_is_escaped_on_the_portal(self):
        GroupContent.objects.create(group=self.group_b, title="<script>alert(1)</script>", note="<b onmouseover=x>", created_by=self.owner,
                                    url='https://example.com/?q="onmouseover="x&r=<i>')
        client, token, page = self.open_portal()
        hers = self.sections(page.content.decode())[MARIAM]
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", hers)
        self.assertNotIn("<script>", hers)
        self.assertNotIn("<b onmouseover", hers)
        self.assertIn("?q=&quot;onmouseover=&quot;x&amp;r=&lt;i&gt;", hers)
        self.assertNotIn('onmouseover="x', hers)

    def test_a_child_with_nothing_shared_says_so(self):
        content.remove(self.online, self.owner)
        for item in GroupContent.objects.filter(group=self.group_b):
            content.remove(item, self.owner)
        client, token, page = self.open_portal()
        hers = self.sections(page.content.decode())[MARIAM]
        self.assertIn("لسه مفيش محتوى مشترك", hers)
        self.assertNotIn("data-content-item", hers)

    def test_nothing_of_the_content_is_on_the_question_page_or_the_dead_page_before_the_factor(self):
        client, token = self.anon(), self.token()
        for page in (client.get(self.url(token)), client.get(self.url(token, "en")), self.anon().get(self.url("forged-token")),
                     self.post(client, token, "1/1/2000")):
            body = normalized(page, token)
            for marker in (VIDEO_A, FILE_A, ONLINE_A, VIDEO_B, NOTE_TEXT):
                self.assertNotIn(marker, body)
            self.assertNotIn("data-content-item", body)
            self.assertNotIn('data-section="content"', body)
        self.assertEqual(self.post(client, token, "17/3/2011").status_code, 303)
        self.assertIn('data-section="content"', client.get(self.url(token)).content.decode())

    def test_english_renders(self):
        client, token, page = self.open_portal(lang="en")
        mine = self.sections(page.content.decode())[YOUSEF]
        self.assertIn("Group content", mine)
        self.assertIn("Online class", re.search(r'data-online-label>(.*?)</span>', mine).group(1))
        self.assertEqual([text for _, text in re.findall(r'href="([^"]*)" target="_blank" rel="noopener noreferrer" data-content-open>(.*?)</a>', mine)],
                         ["Join the class", "Open the file", "Watch the video"])
        self.assertIn("Class time", mine)
        self.assertIn("11 November 2026", mine)

    def test_the_new_words_read_the_same_for_a_boy_and_a_girl_and_follow_the_voice_rules(self):
        from reports.tests_customer_voice import _problems

        def flat(value):
            return [text for part in (value.values() if isinstance(value, dict) else [value]) for text in flat(part)] if isinstance(value, dict) else [value]

        for lang in ("ar", "en"):
            for key in ("content_h", "content_note", "no_content", "content_kinds", "watch", "open_file", "join", "class_at"):
                for text in flat(portal.WORDS[lang][key]):
                    if lang == "ar":
                        self.assertEqual(_problems(text), [], text)
                        for gendered in ("الابن", "ابنك", "البنت", "بنتك", "ابنكم", "ابنة"):
                            self.assertNotIn(gendered, text, key)
                        self.assertNotRegex(re.sub(r"[\s.،:؛!؟?()«»\-–—]+", " ", text).strip()[:1], r"[A-Za-z]", text)
                        self.assertNotRegex(re.sub(r"[\s.،:؛!؟?()«»\-–—]+", " ", text).strip()[-1:], r"[A-Za-z]", text)
                    else:
                        self.assertNotRegex(text, r"\b(son|daughter)\b")
        self.assertEqual(set(portal.WORDS["ar"]["content_kinds"]), set(portal.WORDS["en"]["content_kinds"]))
        self.assertIn("حصة أونلاين", portal.WORDS["ar"]["content_kinds"]["online"])
        self.assertIn("ادخل الحصة", portal.WORDS["ar"]["join"])

    def test_the_visit_still_writes_exactly_two_rows_and_reads_the_content_in_one_query(self):
        client, token, page = self.open_portal()
        client.get(self.url(token))
        with CaptureQueriesContext(connection) as queries:
            self.assertContains(client.get(self.url(token)), "data-content-item")
        statements = [q["sql"] for q in queries]
        writes = [sql for sql in statements if sql.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]
        self.assertEqual(len(writes), 2, writes)  # last_opened_at and the audit row, as before: group content adds reads only
        self.assertFalse(any("groupcontent" in sql for sql in writes))
        reads = [sql for sql in statements if "education_groupcontent" in sql]
        self.assertEqual(len(reads), 1, reads)  # one read for both children, however many items they have

    def test_the_content_never_adds_a_row_to_anything_else(self):
        before = self.counts()
        outbox = OutboxMessage.objects.count()
        client, token, page = self.open_portal()
        client.get(self.url(token))
        self.assertEqual(self.counts(), before)
        self.assertEqual(OutboxMessage.objects.count(), outbox)


class StylesheetTests(SimpleTestCase):
    def test_the_portal_stylesheet_takes_its_colours_from_the_tokens_and_its_tag_moved_with_it(self):
        css = pathlib.Path(settings.BASE_DIR, "static", "hesba", "css", "education_portal.css").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), [])
        template = pathlib.Path(settings.BASE_DIR, "templates", "education", "portal.html").read_text(encoding="utf-8")
        self.assertIn("education_portal.css' %}?v=e9-1", template)
        self.assertIn(".gp-chip--online", css)
        self.assertIn(".gp-content .gp-open", css)


# ---- the table ----

class SchemaTests(StaffSetup):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_makemigrations_has_nothing_to_add(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)

    def test_the_table_has_exactly_the_planned_columns_and_nothing_is_cascaded(self):
        self.assertEqual(sorted(field.name for field in GroupContent._meta.get_fields()),
                         sorted(["id", "group", "kind", "title", "url", "note", "starts_at", "published", "active", "created_by", "created_at", "updated_at"]))
        self.assertEqual(GroupContent._meta.get_field("group").remote_field.on_delete.__name__, "PROTECT")
        self.assertEqual(GroupContent._meta.get_field("url").max_length, content.URL_MAX)
        self.assertEqual([index.name for index in GroupContent._meta.indexes], ["edu_content_group_idx"])
        self.assertEqual(set(ContentKind.values), {"video", "file", "online"})


class MigrationTests(TransactionTestCase):
    """0011 only creates the one table, after 0010; the groups that already exist are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0010_creates_one_table_and_leaves_existing_data_alone(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("education", "0011_group_content")]
        self.assertIn(("education", "0010_guardian_links"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["CreateModel"])
        executor.migrate([("education", "0010_guardian_links")])
        self.assertNotIn("education_groupcontent", connection.introspection.table_names())
        leaves = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "education"] + [("education", "0010_guardian_links")]
        old = executor.loader.project_state(leaves).apps
        user = old.get_model("auth", "User").objects.create(username="mig_e9")
        item = old.get_model("master_data", "Item").objects.create(item_code="I-E9", item_name="خدمة قديمة", is_stock_tracked=False)
        course = old.get_model("education", "Course").objects.create(name="مادة قديمة", fee="100", item=item)
        group = old.get_model("education", "StudyGroup").objects.create(code="G-OLD", course=course, name="مجموعة قديمة")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("education", "0011_group_content")])
        self.assertIn("education_groupcontent", connection.introspection.table_names())
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "education_groupcontent")
        self.assertIn("edu_content_group_idx", constraints)
        self.assertEqual(constraints["edu_content_group_idx"]["columns"], ["group_id", "active", "published"])
        self.assertEqual(StudyGroup.objects.get(pk=group.pk).name, "مجموعة قديمة")
        row = GroupContent.objects.create(group_id=group.pk, title="أول محتوى", url=GOOD, created_by_id=user.pk)
        self.assertEqual((row.kind, row.published, row.active), ("video", True, True))
