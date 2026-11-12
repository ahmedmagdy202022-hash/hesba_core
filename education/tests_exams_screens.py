"""E2 screens: the exams card, the mark sheet, the marks card on a student, and the report card, for every kind of person, in both languages."""

import pathlib
import re
from decimal import Decimal as D

from django.conf import settings
from django.contrib.messages import get_messages
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from django.urls import reverse

from audit.models import AuditLog
from messaging.models import OutboxMessage

from . import exams
from .models import Exam, ExamMark
from .tests_exams import TODAY, ExamSetup, day

VERSION = re.compile(r'name="sheet_version" value="([^"]*)"')
SECRET = "امتحان سري للمدرس التاني"  # a title only B's teacher and a manager may ever see


class ScreenSetup(ExamSetup):
    def setUp(self):
        super().setUp()
        self.quiz = self.exam(self.a, "كويز الأسبوع", held=6, out_of="20")
        self.monthly = self.exam(self.a, "امتحان الشهر", held=3, out_of="10", kind="monthly")
        self.secret = self.exam(self.b, SECRET, held=2, out_of="50")
        exams.record_marks(self.quiz, {self.adam.pk: {"score": "18"}, self.basma.pk: {"score": "14"}}, self.owner)
        exams.record_marks(self.monthly, {self.adam.pk: {"score": "7.5"}, self.gad.pk: {"absent": True}}, self.owner)
        exams.record_marks(self.secret, {self.basma.pk: {"score": "40"}}, self.owner)
        self.client.force_login(self.owner)

    def as_user(self, user):
        self.client.force_login(user)
        return self.client

    def sheet(self, exam=None, lang="ar"):
        exam = exam or self.quiz
        return self.client.get(reverse("education:exam", args=[exam.pk]) + f"?lang={lang}")

    def open_form(self, exam=None):
        html = self.sheet(exam).content.decode()
        return {"lang": "ar", "action": "marks", "sheet_version": VERSION.search(html).group(1)}

    def save(self, form, exam=None):
        return self.client.post(reverse("education:exam", args=[(exam or self.quiz).pk]), form)

    def marks(self, exam=None):
        return {mark.student.name: (mark.score, mark.absent) for mark in (exam or self.quiz).marks.select_related("student")}


class WhoSeesTests(ScreenSetup):
    def test_the_group_page_has_the_exams_card_for_a_manager_and_the_groups_teacher_only(self):
        url = reverse("education:group", args=[self.a.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-exams-card")
        self.assertContains(page, "كويز الأسبوع")
        self.assertContains(page, "data-new-exam")
        self.assertContains(page, "2 من 3")  # Adam and Basma entered, out of the three on the roster
        self.assertNotContains(page, SECRET)
        self.assertContains(self.as_user(self.teacher_user).get(url), "data-exams-card")
        for user in (self.other_user, self.cashier):  # another group's teacher; a cashier: both may open the group page, neither sees marks
            with self.subTest(user=user.username):
                page = self.as_user(user).get(url)
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, "data-exams-card")
                self.assertNotContains(page, "كويز الأسبوع")
                self.assertNotContains(page, "data-new-exam")

    def test_the_student_page_has_the_marks_card_for_a_manager_and_a_teacher_of_the_student_only_for_their_groups(self):
        url = reverse("education:student", args=[self.basma.pk])  # in A and B
        page = self.client.get(url)
        self.assertContains(page, "data-marks-card")
        for title in ("كويز الأسبوع", SECRET):
            self.assertContains(page, title)
        self.assertContains(page, "data-print-report")
        page = self.as_user(self.teacher_user).get(url)
        self.assertContains(page, "data-marks-card")
        self.assertContains(page, "كويز الأسبوع")
        self.assertNotContains(page, SECRET)  # the other group's exam of the same student
        self.assertNotContains(page, "40 / 50")
        cashier = self.as_user(self.cashier).get(url)
        self.assertEqual(cashier.status_code, 200)  # a cashier still sees the student's file...
        for marker in ("data-marks-card", "data-print-report", "كويز الأسبوع", SECRET, "14 / 20", "data-student-marks"):
            self.assertNotContains(cashier, marker)  # ...but not one mark
        other = self.as_user(self.other_user).get(reverse("education:student", args=[self.adam.pk]))  # a teacher of none of Adam's groups
        self.assertNotContains(other, "data-marks-card")
        self.assertNotContains(other, "كويز الأسبوع")

    def test_today_offers_the_exams_link_to_those_who_may_see_them(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("education:today")), f'data-exams="{self.a.pk}"')
        page = self.as_user(self.teacher_user).get(reverse("education:today"))
        self.assertContains(page, f'data-exams="{self.a.pk}"')
        self.assertNotContains(page, f'data-exams="{self.b.pk}"')
        cashier = self.as_user(self.cashier).get(reverse("education:today"))
        self.assertNotContains(cashier, "data-exams=")

    def test_every_page_and_every_post_refuses_who_may_not_see_the_marks(self):
        sheet, listing = reverse("education:exam", args=[self.quiz.pk]), reverse("education:exams", args=[self.a.pk])
        form = {"lang": "ar", "action": "marks", "sheet_version": "x", f"s_{self.adam.pk}": "1"}
        for user in (self.cashier, self.other_user):
            self.as_user(user)
            with self.subTest(user=user.username):
                for url in (sheet, listing, reverse("education:exam_new", args=[self.a.pk]), reverse("education:report_card", args=[self.adam.pk])):
                    self.assertEqual(self.client.get(url).status_code, 403, url)
                for post in (form, {"lang": "ar", "action": "edit", "title": "x", "kind": "quiz", "held_on": day(1).isoformat(), "out_of": "5"},
                             {"lang": "ar", "action": "publish"}, {"lang": "ar", "action": "unpublish"}):
                    self.assertEqual(self.client.post(sheet, post).status_code, 403, post)
                self.assertEqual(self.client.post(reverse("education:exam_new", args=[self.a.pk]), {"title": "x", "held_on": day(1).isoformat(), "out_of": "5"}).status_code, 403)
        self.assertEqual(self.marks(), {"آدم": (D("18.00"), False), "بسمة": (D("14.00"), False)})
        self.assertEqual(Exam.objects.count(), 3)
        self.assertFalse(Exam.objects.get(pk=self.quiz.pk).published)

    def test_the_groups_teacher_works_the_sheet_but_cannot_publish(self):
        self.as_user(self.teacher_user)
        self.assertEqual(self.sheet().status_code, 200)
        self.assertNotContains(self.sheet(), "data-toggle-publish")
        form = self.open_form()
        self.assertEqual(self.save({**form, f"s_{self.gad.pk}": "9"}).status_code, 302)
        self.assertEqual(self.client.post(reverse("education:exam", args=[self.quiz.pk]), {"lang": "ar", "action": "publish"}).status_code, 403)
        self.assertEqual(self.marks()["جاد"], (D("9.00"), False))
        self.assertFalse(Exam.objects.get(pk=self.quiz.pk).published)
        self.assertEqual(self.client.get(reverse("education:report_card", args=[self.adam.pk])).status_code, 200)  # a teacher of Adam's group

    def test_a_teacher_with_no_student_file_permission_works_from_todays_classes(self):
        from hesba_testing.factories import make_role, make_user, make_user_profile

        bare = make_user(username="exam_bare_teacher")
        make_user_profile(user=bare, role=make_role(code="EMPTY-TEACHER"))  # no permission at all: only the employee link
        self.teacher.user = bare
        self.teacher.save()
        self.as_user(bare)
        self.assertFalse(exams.services.can_view(bare))
        today = self.client.get(reverse("education:today"))
        self.assertEqual(today.status_code, 200)
        self.assertContains(today, f'data-exams="{self.a.pk}"')
        self.assertNotContains(today, f'data-exams="{self.b.pk}"')
        listing = self.client.get(reverse("education:exams", args=[self.a.pk]))
        self.assertContains(listing, "كويز الأسبوع")
        self.assertContains(listing, f'href="{reverse("education:today")}?lang=ar"')  # back goes to today, not to a page they cannot open
        self.assertEqual(self.sheet().status_code, 200)
        self.assertEqual(self.save({**self.open_form(), f"s_{self.gad.pk}": "11"}).status_code, 302)
        self.assertEqual(self.marks()["جاد"], (D("11.00"), False))
        self.assertEqual(self.client.get(reverse("education:exam", args=[self.secret.pk])).status_code, 403)  # another group's paper
        for url in (reverse("education:group", args=[self.a.pk]), reverse("education:student", args=[self.adam.pk])):
            self.assertEqual(self.client.get(url).status_code, 403, url)  # the school's lists stay closed to them
        card = self.client.get(reverse("education:report_card", args=[self.adam.pk]))
        self.assertEqual(card.status_code, 200)
        self.assertContains(card, f'href="{reverse("education:today")}?lang=ar"')
        self.assertContains(card, "18 / 20")

    def test_the_student_without_a_group_of_the_viewers_has_no_report_card(self):
        self.as_user(self.other_user)
        self.assertEqual(self.client.get(reverse("education:report_card", args=[self.hana.pk])).status_code, 200)  # B's own student
        self.assertEqual(self.client.get(reverse("education:report_card", args=[self.gad.pk])).status_code, 403)

    def test_another_entity_is_not_found(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="E-EXS", name_ar="فرع", activity_slug="education", sub_activity_slug="tutoring_center")
        session = self.client.session
        session["hesba_entity"] = branch.pk
        session.save()
        for url in (reverse("education:exam", args=[self.quiz.pk]), reverse("education:exams", args=[self.a.pk]), reverse("education:exam_new", args=[self.a.pk]),
                    reverse("education:report_card", args=[self.adam.pk])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(reverse("education:exam", args=[self.quiz.pk]), {"action": "publish"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("education:exam_new", args=[self.a.pk]), {"title": "x"}).status_code, 404)
        self.assertFalse(Exam.objects.get(pk=self.quiz.pk).published)

    def test_an_unknown_id_and_an_unknown_action(self):
        self.assertEqual(self.client.get(reverse("education:exam", args=[999999])).status_code, 404)
        self.assertEqual(self.client.post(reverse("education:exam", args=[self.quiz.pk]), {"action": "delete"}).status_code, 404)


class SheetTests(ScreenSetup):
    def test_the_sheet_is_a_phone_first_row_per_student(self):
        page = self.sheet()
        html = page.content.decode()
        self.assertEqual(re.findall(r'data-mark-row="(\d+)"', html), [str(self.adam.pk), str(self.basma.pk), str(self.gad.pk)])  # the roster, by name
        self.assertRegex(html, rf'<input class="ex-box" id="ex-s-{self.adam.pk}" name="s_{self.adam.pk}" type="text" inputmode="decimal" dir="ltr"[^>]*value="18"')
        self.assertContains(self.sheet(self.monthly), f'name="a_{self.gad.pk}" value="1" checked')  # Gad was absent from the monthly exam
        self.assertContains(page, "غايب")
        self.assertContains(page, "data-save-bar")
        self.assertContains(page, "اتسجّل 2 من 3")
        self.assertContains(page, "education_exams.css")
        self.assertContains(page, "edu_marks.js")
        self.assertNotRegex(html, r'type="number"')
        self.assertRegex(html, VERSION)
        english = self.sheet(lang="en").content.decode()
        self.assertIn("Save marks", english)
        self.assertIn("2 of 3 entered", english)
        self.assertIn("Absent", english)
        self.assertIn("Quiz", english)

    def test_saving_the_sheet_writes_the_marks_and_the_message_holds_no_mark(self):
        form = self.open_form()
        form.update({f"s_{self.adam.pk}": "19", f"s_{self.basma.pk}": "", f"a_{self.basma.pk}": "1", f"s_{self.gad.pk}": "١٢", f"n_{self.gad.pk}": "تحسّن"})
        response = self.save(form)
        self.assertRedirects(response, reverse("education:exam", args=[self.quiz.pk]) + "?lang=ar", fetch_redirect_response=False)
        self.assertEqual(self.marks(), {"آدم": (D("19.00"), False), "بسمة": (None, True), "جاد": (D("12.00"), False)})
        flashed = " ".join(str(message) for message in get_messages(response.wsgi_request))
        self.assertIn("اتحفظت", flashed)
        self.assertFalse(re.search(r"\d", flashed))  # not a single number in the message
        self.assertFalse(OutboxMessage.objects.exists())
        log = AuditLog.objects.filter(action="record_marks").latest("pk")
        self.assertEqual(log.before_data["marks"][self.adam.code]["score"], "18.00")  # the correction keeps both sides
        self.assertEqual(log.after_data["marks"][self.adam.code]["score"], "19.00")

    def test_a_refused_save_writes_nothing_and_comes_back_with_what_was_typed(self):
        form = self.open_form()
        response = self.save({**form, f"s_{self.adam.pk}": "5", f"s_{self.gad.pk}": "21"})  # 21 of 20
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-form-error")
        self.assertContains(response, "من صفر لحد 20")
        self.assertContains(response, 'value="21"')
        self.assertEqual(self.marks(), {"آدم": (D("18.00"), False), "بسمة": (D("14.00"), False)})  # Adam's valid 5 was not saved either
        both = self.save({**form, f"s_{self.gad.pk}": "3", f"a_{self.gad.pk}": "1"})
        self.assertContains(both, "غايب ومكتوب له درجة")
        self.assertEqual(len(self.marks()), 2)
        english = self.client.post(reverse("education:exam", args=[self.quiz.pk]), {**form, "lang": "en", f"s_{self.gad.pk}": "abc"})
        self.assertContains(english, "The mark must be a number from zero up to 20")
        self.assertEqual(len(self.marks()), 2)

    def test_a_student_off_the_roster_or_a_forged_id_is_refused(self):
        form = self.open_form()
        for key in (f"s_{self.hana.pk}", "s_999999"):  # another group's student; nobody
            self.assertContains(self.save({**form, key: "5"}), "مش في المجموعة")
        self.assertEqual(len(self.marks()), 2)

    def test_two_saves_from_the_same_opened_page_the_second_is_refused_and_writes_nothing(self):
        opened = self.open_form()  # both people opened the sheet showing Adam 18 and Basma 14
        first = self.save({**opened, f"s_{self.adam.pk}": "18", f"s_{self.basma.pk}": "16"})  # the first corrects Basma
        self.assertEqual(first.status_code, 302)
        second = self.save({**opened, f"s_{self.adam.pk}": "2", f"s_{self.gad.pk}": "9"})  # the second still holds the old digest
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "حد حفظ قبلك")
        self.assertEqual(self.marks(), {"آدم": (D("18.00"), False), "بسمة": (D("16.00"), False)})  # nothing of the stale page was written
        self.assertContains(second, 'value="16"')  # the page comes back from the database...
        self.assertNotContains(second, 'value="9"')  # ...not from the old boxes
        fresh = VERSION.search(second.content.decode()).group(1)
        self.assertEqual(self.save({**opened, "sheet_version": fresh, f"s_{self.gad.pk}": "9"}).status_code, 302)  # a reload saves
        self.assertEqual(self.marks()["جاد"], (D("9.00"), False))
        forged = {key: value for key, value in opened.items() if key != "sheet_version"}
        self.assertContains(self.save({**forged, f"s_{self.gad.pk}": "1"}), "حد حفظ قبلك")  # no digest at all, with marks present
        self.assertEqual(self.marks()["جاد"], (D("9.00"), False))

    def test_an_exam_still_to_come_has_a_sheet_that_cannot_be_filled(self):
        from datetime import timedelta

        ahead = exams.save_exam(self.a, {"title": "جاي", "kind": "final", "held_on": TODAY + timedelta(days=3), "out_of": "100"}, self.owner)
        page = self.sheet(ahead)
        self.assertContains(page, "data-future")
        self.assertNotContains(page, "data-save-marks")
        self.assertContains(self.save({"lang": "ar", "action": "marks", "sheet_version": "x", f"s_{self.adam.pk}": "5"}, ahead), "لسه ماجاش يومه")

    def test_creating_editing_and_publishing_an_exam(self):
        response = self.client.post(reverse("education:exam_new", args=[self.a.pk]), {"lang": "ar", "title": "كويز جديد", "kind": "midterm", "held_on": day(1).isoformat(),
                                                                                      "out_of": "30", "notes": "الباب 2"})
        exam = Exam.objects.get(title="كويز جديد")
        self.assertRedirects(response, reverse("education:exam", args=[exam.pk]) + "?lang=ar", fetch_redirect_response=False)
        self.assertEqual((exam.group, exam.kind, exam.out_of, exam.published, exam.created_by), (self.a, "midterm", D("30.00"), False, self.owner))
        bad = self.client.post(reverse("education:exam_new", args=[self.a.pk]), {"lang": "ar", "title": "x", "held_on": day(1).isoformat(), "out_of": "0"})
        self.assertContains(bad, "data-form-error")
        self.assertContains(bad, 'value="x"')  # what was typed stays
        self.assertEqual(Exam.objects.count(), 4)
        self.client.post(reverse("education:exam", args=[exam.pk]), {"lang": "ar", "action": "edit", "title": "كويز معدّل", "kind": "quiz", "held_on": day(1).isoformat(), "out_of": "25"})
        exam.refresh_from_db()
        self.assertEqual((exam.title, exam.out_of), ("كويز معدّل", D("25.00")))
        refused = self.client.post(reverse("education:exam", args=[self.quiz.pk]), {"lang": "ar", "action": "edit", "title": "x", "kind": "quiz", "held_on": day(6).isoformat(), "out_of": "10"})
        self.assertContains(refused, "أعلى من الدرجة النهائية الجديدة")
        self.assertContains(refused, "<details class=\"op-form\" open>")
        self.assertEqual(self.client.post(reverse("education:exam", args=[exam.pk]), {"lang": "ar", "action": "publish"}).status_code, 302)
        self.assertTrue(Exam.objects.get(pk=exam.pk).published)
        self.assertContains(self.sheet(exam), "data-toggle-publish")
        self.client.post(reverse("education:exam", args=[exam.pk]), {"lang": "ar", "action": "unpublish"})
        self.assertFalse(Exam.objects.get(pk=exam.pk).published)

    def test_the_list_page_and_the_form_in_both_languages(self):
        listing = self.client.get(reverse("education:exams", args=[self.a.pk]))
        for text in ("كويز الأسبوع", "امتحان الشهر", "امتحان جديد", "غير منشور", "2 من 3"):
            self.assertContains(listing, text)
        english = self.client.get(reverse("education:exams", args=[self.a.pk]) + "?lang=en")
        for text in ("Exams", "New exam", "Not published", "2 of 3", "Monthly exam"):
            self.assertContains(english, text)
        self.assertContains(self.client.get(reverse("education:exam_new", args=[self.a.pk]) + "?lang=en"), "Full mark")
        self.assertContains(self.client.get(reverse("education:exam_new", args=[self.a.pk])), "الدرجة النهائية")


class ReportCardScreenTests(ScreenSetup):
    def url(self, student=None, lang="ar", **query):
        extra = "".join(f"&{key}={value}" for key, value in query.items())
        return reverse("education:report_card", args=[(student or self.adam).pk]) + f"?lang={lang}{extra}"

    def test_the_card_in_arabic_has_the_centre_the_student_the_period_the_marks_and_the_average(self):
        from printing.company import company_details
        from settings_core.templatetags.hesba_format import day_text

        page = self.client.get(self.url(first=day(10).isoformat(), last=TODAY.isoformat()))
        self.assertContains(page, "data-report")
        self.assertContains(page, company_details()["name"])
        self.assertContains(page, "آدم")
        self.assertContains(page, day_text(day(10), "ar"))  # dates in words
        self.assertContains(page, day_text(TODAY, "ar"))
        html = page.content.decode()
        self.assertIn("18 / 20", html)
        self.assertIn("7.5 / 10", html)
        self.assertIn("90.0%", html)
        self.assertIn("75.0%", html)
        self.assertRegex(html, r'(?s)data-report-average>.*?82\.5%')  # (90 + 75) / 2
        self.assertNotIn("امتحان سري", html)
        self.assertNotIn("40 / 50", html)  # Basma's, and group B's
        self.assertNotIn("14 / 20", html)  # another student's mark in the same exam
        self.assertContains(page, "data-print")
        self.assertContains(page, "اطبع")

    def test_the_card_in_english_and_a_range_with_nothing_in_it(self):
        page = self.client.get(self.url(lang="en", first=day(10).isoformat(), last=TODAY.isoformat()))
        for text in ("Student report", "Attendance", "Exams", "Average", "Period", "Print"):
            self.assertContains(page, text)
        self.assertContains(page, 'dir="ltr"')
        empty = self.client.get(self.url(first=day(100).isoformat(), last=day(90).isoformat()))
        self.assertContains(empty, "data-no-exams")
        self.assertContains(empty, "data-no-classes")
        self.assertContains(empty, "—")

    def test_a_reversed_period_falls_back_with_a_message(self):
        page = self.client.get(self.url(first=TODAY.isoformat(), last=day(5).isoformat()))
        self.assertContains(page, "data-form-error")
        self.assertContains(page, "data-report-period")
        self.assertEqual(self.client.get(self.url(first="nonsense", last="x")).status_code, 200)  # the last 30 days

    def test_absent_and_not_entered_exams_are_listed_and_left_out_of_the_average(self):
        page = self.client.get(self.url(self.gad, first=day(10).isoformat(), last=TODAY.isoformat()))
        html = page.content.decode()
        self.assertRegex(html, rf'data-report-exam="{self.monthly.pk}" data-status="absent"')
        self.assertRegex(html, rf'data-report-exam="{self.quiz.pk}" data-status="pending"')
        self.assertRegex(html, r'data-report-average>\s*<span>[^<]*</span><strong>—')  # he sat nothing: no average
        self.assertContains(page, "لسه ماتسجّلتش")

    def test_it_never_offers_to_send_a_mark_and_never_shows_a_rank(self):
        for lang in ("ar", "en"):
            html = self.client.get(self.url(lang=lang)).content.decode()
            for banned in ("wa.me", "whatsapp", "mailto:", "sms:", "data-send", "الترتيب", "rank", "الأول على"):
                self.assertNotIn(banned, html.lower() if banned.isascii() else html)
        for page in (self.client.get(reverse("education:student", args=[self.adam.pk])), self.sheet(), self.client.get(reverse("education:exams", args=[self.a.pk]))):
            self.assertNotIn("wa.me", page.content.decode())

    def test_the_card_of_a_teacher_is_their_groups_only(self):
        self.as_user(self.teacher_user)
        page = self.client.get(self.url(self.basma, first=day(10).isoformat(), last=TODAY.isoformat()))
        html = page.content.decode()
        self.assertIn("14 / 20", html)
        self.assertNotIn("40 / 50", html)
        self.assertRegex(html, r'(?s)data-report-average>.*?70\.0%')  # Basma's 14 of 20 alone


class AssetTests(SimpleTestCase):
    def read(self, path):
        with open(finders.find(path), encoding="utf-8") as handle:
            return handle.read()

    def test_the_new_stylesheets_use_defined_tokens_and_no_colour_literals(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        for sheet in ("hesba/css/education_exams.css", "hesba/css/education_report.css"):
            with self.subTest(sheet=sheet):
                css = self.read(sheet)
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), [])
                used = set(re.findall(r"var\((--hs-[\w-]+)", css))
                self.assertTrue(used)
                self.assertEqual(sorted(used - defined), [])
                self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_the_templates_carry_no_cell_labels_no_literal_colours_and_no_iso_dates(self):
        root = pathlib.Path(settings.BASE_DIR) / "templates/education"
        for name in ("exams.html", "exam_form.html", "marks.html", "exam_report.html", "_exam_card.html", "_exam_list.html", "_exam_fields.html", "_marks_card.html"):
            text = (root / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(text, r"<td[^>]*data-label=")
                self.assertNotRegex(text, r"#[0-9a-fA-F]{6}\b|style=\"[^\"]*(color|background)")
                self.assertNotIn('|date:"Y-m-d"', text)  # a date shown to a person is in words (day_text); the ISO form is only a date box's value
        self.assertIn("partials/back_arrow.html", (root / "exam_report.html").read_text(encoding="utf-8"))
