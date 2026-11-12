"""E1: the scan screen and its JSON endpoint, the student's card sheet, the nursery's scan box, and the new stylesheets' house rules."""

import re
from datetime import date, datetime, time

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from unittest import mock

from audit.models import AuditLog
from permissions.models import RoleCode

from . import nursery, scan, services
from .models import Attendance, DayRecord, Enrollment, Student
from .tests import person
from .tests_nursery import NurserySetup
from .tests_scan import TODAY, ScanSetup, at


class ScanScreenTests(ScanSetup):
    def test_the_endpoint_answers_in_json_in_both_languages(self):
        url = reverse("education:scan_mark")
        # the real clock decides present or late here, so only what does not depend on it is asserted
        ar = self.client.post(url, {"code": "ST-99999", "lang": "ar"})
        self.assertEqual((ar.status_code, ar.json()["status"]), (404, "not_found"))
        self.assertIn("مش معروف", ar.json()["message"])
        en = self.client.post(url, {"code": "ST-99999", "lang": "en"})
        self.assertIn("not known", en.json()["message"])
        self.assertEqual(self.client.get(url).status_code, 405)
        ok = self.client.post(url, {"code": self.a.code, "group": self.review.pk, "lang": "en"})
        data = ok.json()
        self.assertEqual((ok.status_code, data["status"], data["student"]["name"], data["group"]["name"]), (200, "ok", "آدم", "مراجعة يومية"))
        self.assertIn(data["presence_label"], ("Present", "Late"))
        self.assertRegex(data["time"], r"^\d\d:\d\d$")
        arabic = self.client.post(url, {"code": self.b.code, "group": self.review.pk, "lang": "ar"}).json()
        self.assertIn(arabic["presence_label"], ("حضور", "تأخير"))
        self.assertEqual(self.client.post(url, {"code": self.b.code, "group": 999999}).status_code, 403)

    def test_who_sees_the_entries_and_the_screen(self):
        self.assertContains(self.client.get(reverse("education:today")), "data-scan-entry")
        page = self.client.get(reverse("education:scan"))
        self.assertContains(page, "data-scan-tab")
        self.assertContains(page, "data-camera-scan")
        self.assertContains(page, "امسح الحضور")
        self.assertContains(self.client.get(reverse("education:scan") + "?lang=en"), "Scan attendance")
        # A cashier who may look at students but not take attendance gets no entry, and the screen and endpoint refuse.
        cashier = person(RoleCode.CASHIER, "edu_scan_cashier2")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("education:scan")).status_code, 403)
        self.assertEqual(self.client.post(reverse("education:scan_mark"), {"code": self.a.code}).status_code, 403)
        today = self.client.get(reverse("education:today"))
        if today.status_code == 200:
            self.assertNotContains(today, "data-scan-tab")
            self.assertNotContains(today, "data-scan-entry")
        # A teacher sees it.
        teacher = person(RoleCode.CASHIER, "edu_scan_teacher2")
        self.teacher.user = teacher
        self.teacher.save()
        self.client.force_login(teacher)
        self.assertContains(self.client.get(reverse("education:scan")), "data-scan-tab")

    def test_a_card_scanned_while_another_is_on_its_way_is_kept_in_line(self):
        """The script sends one code at a time and queues the rest (checked in a browser with a slow server: three cards before
        the first answer go out in order, and one scanned while a «which class?» choice waits goes after the choice). Pinned here:
        the page carries the waiting line and its words, and the script no longer drops a code because a request is in flight."""

        from pathlib import Path

        from django.conf import settings

        self.client.force_login(self.owner)
        for lang, words in (("ar", "كروت مستنية دورها: {n}"), ("en", "Cards waiting their turn: {n}")):
            page = self.client.get(reverse("education:scan") + f"?lang={lang}")
            self.assertContains(page, "data-scan-waiting")
            self.assertContains(page, f'data-t-waiting="{words}"')
            self.assertContains(page, "edu_scan.js?v=e1-2")
        script = (Path(settings.BASE_DIR) / "static/hesba/js/edu_scan.js").read_text(encoding="utf-8")
        self.assertIn("queue.push(code)", script)
        self.assertNotIn("if (!code || busy) { return; }", script)
        self.assertNotIn("busy = false; box.value = ''", script)  # an answer never wipes what is being typed for the next card

    def test_scanning_never_touches_money(self):
        from cashboxes.models import CashboxMovement
        from ledger.models import JournalEntry
        from sales.models import SalesInvoice

        def counts():
            return (SalesInvoice.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count())

        before = counts()
        for minute in (1, 2, 20):
            for student in (self.a, self.b, self.c):
                scan.mark(student.code, self.owner, at(TODAY, 16, minute))
        self.assertEqual(Attendance.objects.count(), 3)
        self.assertEqual(counts(), before)
        self.close_from_the_sheet(TODAY)
        self.assertEqual(counts(), before)


class CardTests(ScanSetup):
    def setUp(self):
        super().setUp()
        Student.objects.filter(pk=self.a.pk).update(phone="01299988877", date_of_birth=date(2012, 3, 4), stage="تالتة إعدادي")
        self.a.refresh_from_db()

    def test_the_card_has_the_name_code_stage_group_and_nothing_private(self):
        page = self.client.get(reverse("education:cards") + f"?student={self.a.pk}")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        for shown in ("آدم", "ST-00001", "تالتة إعدادي", "مراجعة يومية"):
            self.assertIn(shown, html)
        # no phone (the child's or the parent's), no date of birth, no payer
        for private in ("01299988877", "01010000001", "2012", "03-04", "ولي أمر آدم", "ولي أمر"):
            self.assertNotIn(private, html)
        self.assertEqual(re.findall(r'data-qr-text="([^"]*)"', html), ["ST-00001"])  # the QR holds the code alone
        self.assertContains(page, "qrcode-1.4.4.js")
        self.assertContains(page, 'data-cards="1"')

    def test_a_group_prints_a_sheet_of_its_active_students_ten_to_a_page(self):
        for n in range(10):
            self.kid(f"طالب {n:02d}", f"0102000{n:04d}")
        html = self.client.get(reverse("education:cards") + f"?group={self.review.pk}").content.decode()
        codes = re.findall(r'data-qr-text="([^"]*)"', html)
        self.assertEqual(len(codes), 13)
        self.assertEqual(sorted(codes), sorted(Student.objects.values_list("code", flat=True)))
        self.assertEqual(html.count('class="ec-page"'), 2)
        for private in ("ولي أمر", "0102000"):
            self.assertNotIn(private, html)
        # a stopped student has no card
        services.stop(Enrollment.objects.get(student=self.b), self.owner, end_date=TODAY)
        self.assertEqual(len(re.findall(r'data-qr-text="([^"]*)"', self.client.get(reverse("education:cards") + f"?group={self.review.pk}").content.decode())), 12)

    def test_english_and_who_may_print_and_other_entities(self):
        self.assertContains(self.client.get(reverse("education:cards") + f"?student={self.a.pk}&lang=en"), "Print")
        self.assertContains(self.client.get(reverse("education:cards") + f"?student={self.a.pk}&lang=en"), 'dir="ltr"')
        self.assertContains(self.client.get(reverse("education:cards") + f"?student={self.a.pk}"), 'dir="rtl"')
        self.assertEqual(self.client.get(reverse("education:cards")).status_code, 404)
        self.assertEqual(self.client.get(reverse("education:cards") + "?student=abc").status_code, 404)
        self.assertEqual(self.client.get(reverse("education:cards") + "?student=99999").status_code, 404)
        cashier = person(RoleCode.CASHIER, "edu_card_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("education:cards") + f"?student={self.a.pk}").status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("education:student", args=[self.a.pk])), "data-print-card")
        self.assertContains(self.client.get(reverse("education:student", args=[self.a.pk]) + "?lang=en"), "Print the card")
        self.assertContains(self.client.get(reverse("education:group", args=[self.review.pk])), "data-print-cards")
        self.assertContains(self.client.get(reverse("education:group", args=[self.review.pk]) + "?lang=en"), "Print the group")
        self.client.force_login(cashier)
        student_page = self.client.get(reverse("education:student", args=[self.a.pk]))
        if student_page.status_code == 200:
            self.assertNotContains(student_page, "data-print-card")


class ScanAssetsTests(SimpleTestCase):
    def read(self, path):
        from django.contrib.staticfiles import finders

        with open(finders.find(path), encoding="utf-8") as handle:
            return handle.read()

    def test_the_new_stylesheets_use_defined_tokens_and_no_colour_literals(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        for sheet in ("hesba/css/education_scan.css", "hesba/css/education_cards.css"):
            with self.subTest(sheet=sheet):
                css = self.read(sheet)
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), [])
                used = set(re.findall(r"var\((--hs-[\w-]+)", css))
                self.assertTrue(used)
                self.assertEqual(sorted(used - defined), [])
                self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_the_templates_carry_no_cell_labels_and_no_literal_colours(self):
        import pathlib

        from django.conf import settings

        root = pathlib.Path(settings.BASE_DIR) / "templates/education"
        for name in ("scan.html", "cards.html", "nursery.html", "attendance.html", "today.html"):
            text = (root / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(text, r"<td[^>]*data-label=")
                self.assertNotRegex(text, r"#[0-9a-fA-F]{6}\b|style=\"[^\"]*(color|background)")
        cards = (root / "cards.html").read_text(encoding="utf-8")
        self.assertIn("partials/back_arrow.html", cards)
        self.assertNotIn("← ", cards)


class NurseryScanTests(NurserySetup):
    """The same box on the nursery board: a scan checks a child in; a child already inside goes to the existing pick-up choice."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.url = reverse("education:nursery")

    def test_a_scan_checks_the_child_in_through_check_in_and_a_second_scan_opens_the_pickup_choice(self):
        with mock.patch.object(nursery, "check_in", wraps=nursery.check_in) as check_in:
            response = self.client.post(self.url, {"action": "scan", "code": self.child.code, "lang": "ar"})
        self.assertEqual(check_in.call_count, 1)
        self.assertRedirects(response, f"{self.url}?lang=ar&scanned={self.child.pk}", fetch_redirect_response=False)
        record = DayRecord.objects.get()
        self.assertEqual((record.student, record.day, record.left_at), (self.child, timezone.localdate(), None))
        self.assertTrue(AuditLog.objects.filter(action="nursery_check_in").exists())
        board = self.client.get(response["Location"])
        self.assertContains(board, "data-scanned")
        again = self.client.post(self.url, {"action": "scan", "code": self.child.code, "lang": "ar"})
        self.assertRedirects(again, f"{self.url}?lang=ar&pick={self.child.pk}", fetch_redirect_response=False)
        self.assertEqual(DayRecord.objects.count(), 1)  # no second morning
        picking = self.client.get(again["Location"])
        self.assertContains(picking, "data-pick-focus")  # the existing pick-up select is focused
        self.assertContains(picking, "منى حسن")
        # the pick-up rule is the old one: only someone on the list, through nursery.check_out
        out = self.client.post(self.url, {"action": "out", "student": self.child.pk, "person": self.mother.pk, "lang": "ar"})
        self.assertEqual(out.status_code, 302)
        self.assertIsNotNone(DayRecord.objects.get().left_at)
        gone = self.client.post(self.url, {"action": "scan", "code": self.child.code, "lang": "ar"})
        self.assertContains(gone, "مشي بالفعل")

    def test_unknown_codes_the_service_and_who_sees_the_box(self):
        self.assertContains(self.client.post(self.url, {"action": "scan", "code": "ST-77777", "lang": "ar"}), "مش معروف")
        self.assertContains(self.client.post(self.url, {"action": "scan", "code": "ST-77777", "lang": "en"}), "not known")
        self.assertFalse(DayRecord.objects.exists())
        morning = timezone.make_aware(datetime.combine(timezone.localdate(), time(8, 15)))
        result = scan.nursery_mark(self.child.code.lower(), self.owner, morning, lang="en")
        self.assertEqual((result.status, result.at, result.message), ("checked_in", "08:15", f"{self.child.name} arrived at 08:15."))
        self.assertContains(self.client.get(self.url), "data-nursery-scan")
        self.assertContains(self.client.get(self.url + "?lang=en"), "Check in with the card")
        cashier = person(RoleCode.CASHIER, "nur_scan_cashier")
        self.client.force_login(cashier)
        page = self.client.get(self.url)
        if page.status_code == 200:
            self.assertNotContains(page, "data-nursery-scan")
        self.assertEqual(self.client.post(self.url, {"action": "scan", "code": self.child.code}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            scan.nursery_mark(self.child.code, cashier)
