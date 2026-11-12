"""Contracting C2b (HG-095): the daily site report: one per project per day, the head count, the delay mark, the photos, the print,
who may write, and that nothing financial or quantitative moves. "Today" is always ``timezone.localdate()``; no fixed clock."""

import re
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from attachments import services as attachments
from attachments.models import Attachment, Kind, Stage
from attachments.tests import JPEG, PDF, PNG, upload
from audit.models import AuditEventType, AuditLog
from permissions.models import RoleCode

from . import contract, site_reports
from .models import Project, SiteReport, SiteReportRow, SiteReportRowKind
from .tests_contract import person
from .tests_documents import C2Setup, footprint, view_only

TODAY = timezone.localdate


def visible_text(html):
    """What a reader sees: the page without its tags, attributes, scripts and styles."""

    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class ReportSetup(C2Setup):
    def save(self, user=None, project=None, day=None, **over):
        data = {"report_date": day or TODAY(), "weather": "clear", "work_done": "صب سقف الدور الأول", "delayed": "", "materials": "", "visitors": "",
                "crew": [("نجار", "6"), ("حداد", "4"), ("عمالة مساعدة", "10")], "equipment": [("خلاطة", "2")]}
        data.update(over)
        return site_reports.save_report(project or self.project, user or self.engineer, **data)

    def post_data(self, day=None, **over):
        data = {"lang": "ar", "report_date": (day or TODAY()).isoformat(), "weather": "clear", "work_done": "صب سقف الدور الأول", "delayed": "", "materials": "", "visitors": "",
                "crew_label": ["نجار", "حداد", "عمالة مساعدة", ""], "crew_count": ["6", "4", "10", ""], "equipment_label": ["خلاطة", ""], "equipment_count": ["2", ""]}
        data.update(over)
        return data

    def write(self, user=None, project=None, day=None, **over):
        self.client.force_login(user or self.engineer)
        return self.client.post(self.url("diary_new", project=project, lang=over.get("lang", "ar")), self.post_data(day, **over))

    def add_photo(self, report, user=None, raw=JPEG, name="a.jpg", mime="image/jpeg", project=None):
        self.client.force_login(user or self.engineer)
        return self.client.post(self.url("diary_photos", report.pk, project=project), {"file": upload(raw, name, mime), "kind": "photo", "stage": "photo"})


class RuleTests(ReportSetup):
    def test_a_second_save_of_the_same_day_edits_the_report_it_does_not_add_one(self):
        first, created = self.save()
        self.assertTrue(created)
        second, created_again = self.save(work_done="صب سقف الدور التاني", weather="hot", crew=[("نجار", "3")], equipment=[])
        self.assertFalse(created_again)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(SiteReport.objects.filter(project=self.project).count(), 1)
        first.refresh_from_db()
        self.assertEqual((first.work_done, first.weather, first.created_by, first.updated_by), ("صب سقف الدور التاني", "hot", self.engineer, self.engineer))
        self.assertEqual([(row.kind, row.label, row.count) for row in first.rows.all()], [("crew", "نجار", 3)])  # the rows are the ones typed last

    def test_the_second_save_by_someone_else_keeps_the_author_and_records_the_editor(self):
        first, _ = self.save()
        self.save(user=self.owner, work_done="تعديل المكتب")
        first.refresh_from_db()
        self.assertEqual((first.created_by, first.updated_by), (self.engineer, self.owner))

    def test_the_database_itself_refuses_two_reports_for_one_project_and_day(self):
        self.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteReport.objects.create(project=self.project, report_date=TODAY(), created_by=self.owner)
        self.save(project=self.other)  # another project, the same day: fine
        self.assertEqual(SiteReport.objects.count(), 2)

    def test_a_future_date_is_refused_and_today_and_yesterday_are_not(self):
        with self.assertRaises(ValidationError) as caught:
            self.save(day=TODAY() + timedelta(days=1))
        self.assertEqual(caught.exception.messages, [site_reports.MESSAGES["ar"]["future"]])
        self.assertFalse(SiteReport.objects.exists())
        self.save(day=TODAY())
        self.save(day=TODAY() - timedelta(days=1))
        self.assertEqual(SiteReport.objects.count(), 2)

    def test_a_missing_or_garbled_date_is_refused(self):
        for bad in (None, "", "غلط", "2026-13-45", "12/11/2026"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.save(report_date=bad)

    def test_the_head_count_is_the_sum_of_the_crew_rows_and_equipment_is_not_counted_in_it(self):
        report, _ = self.save(crew=[("نجار", "6"), ("حداد", "4"), ("عمالة مساعدة", "10")], equipment=[("خلاطة", "2"), ("ونش", "1")])
        self.assertEqual(site_reports.head_count(report), 20)
        self.assertEqual(site_reports.reports_for(self.project)[0].workers, 20)
        self.assertEqual(report.rows.filter(kind=SiteReportRowKind.EQUIPMENT).count(), 2)

    def test_a_day_with_no_crew_has_a_head_count_of_zero(self):
        report, _ = self.save(crew=[])
        self.assertEqual(site_reports.head_count(report), 0)
        self.assertEqual(site_reports.reports_for(self.project)[0].workers, None)

    def test_blank_rows_are_dropped_and_an_equipment_row_without_a_count_counts_as_one(self):
        report, _ = self.save(crew=[("نجار", "5"), ("", ""), ("  ", " ")], equipment=[("ونش", ""), ("", "")])
        self.assertEqual([(row.kind, row.label, row.count) for row in report.rows.all()], [("crew", "نجار", 5), ("equipment", "ونش", 1)])

    def test_bad_rows_are_refused_with_the_row_number_and_the_section(self):
        cases = ((dict(crew=[("نجار", "")]), "row_count"), (dict(crew=[("", "5")]), "row_label"), (dict(crew=[("نجار", "0")]), "row_count"),
                 (dict(crew=[("نجار", "1000")]), "row_count"), (dict(crew=[("نجار", "abc")]), "row_count"), (dict(crew=[("نجار", "-3")]), "row_count"),
                 (dict(crew=[("نجار", "٣")]), "row_count"), (dict(equipment=[("", "2")]), "row_label"), (dict(crew=[("ن" * 81, "2")]), "label_long"),
                 (dict(crew=[("نجار", "2")] * 16), "rows_many"))
        for over, key in cases:
            with self.subTest(over=str(over)[:40]), self.assertRaises(ValidationError) as caught:
                self.save(**over)
            self.assertIn(site_reports.MESSAGES["ar"][key].split("{")[0].strip(), caught.exception.messages[0])
        with self.assertRaises(ValidationError) as caught:
            self.save(crew=[("نجار", "5"), ("حداد", "x")], lang="en")
        self.assertEqual(caught.exception.messages, [site_reports.MESSAGES["en"]["row_count"].format(n=2, section="Workers", max=999)])
        self.assertFalse(SiteReport.objects.exists())  # a refused save leaves nothing behind

    def test_a_report_must_say_what_was_done_or_what_was_delayed(self):
        with self.assertRaises(ValidationError) as caught:
            self.save(work_done="", delayed="  ")
        self.assertEqual(caught.exception.messages, [site_reports.MESSAGES["ar"]["empty"]])
        report, _ = self.save(work_done="", delayed="المطر أوقف الصب")  # a rained-off day is a report too
        self.assertEqual(report.delayed, "المطر أوقف الصب")

    def test_the_weather_is_one_of_the_list_or_nothing(self):
        for value in ("clear", "hot", "cloudy", "rain", "wind", "dust", "cold", ""):
            self.save(weather=value, day=TODAY() - timedelta(days=len(value)))
        with self.assertRaises(ValidationError):
            self.save(weather="snow")
        self.assertEqual(set(site_reports.WEATHER["ar"]), set(site_reports.WEATHER["en"]))
        self.assertEqual([value for value, label in site_reports.weather_choices("ar")], ["clear", "hot", "cloudy", "rain", "wind", "dust", "cold"])

    def test_a_text_longer_than_the_limit_is_refused(self):
        with self.assertRaises(ValidationError):
            self.save(work_done="ا" * (site_reports.MAX_TEXT + 1))
        self.save(work_done="ا" * site_reports.MAX_TEXT)

    def test_a_closed_or_cancelled_project_takes_no_report(self):
        for status in ("done", "cancelled"):
            Project.objects.filter(pk=self.project.pk).update(status=status)
            with self.subTest(status=status), self.assertRaises(ValidationError) as caught:
                self.save()
            self.assertEqual(caught.exception.messages, [site_reports.MESSAGES["ar"]["not_open"]])
        for status in ("planned", "active", "on_hold"):
            Project.objects.filter(pk=self.project.pk).update(status=status)
            self.save(day=TODAY() - timedelta(days=len(status)))

    def test_only_those_who_record_progress_may_write(self):
        self.assertTrue(self.save(user=self.engineer)[1])
        self.assertFalse(self.save(user=self.owner)[1])  # the office edits the same day
        for user in (self.keeper, self.viewer):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                self.save(user=user, day=TODAY() - timedelta(days=3))
        blind = view_only(self.owner, username="c2_blind_rep", codes=("projects.record_progress",))
        with self.assertRaises(PermissionDenied):
            self.save(user=blind, day=TODAY() - timedelta(days=3))  # a write right without a view right is not enough
        self.assertEqual(SiteReport.objects.count(), 1)

    def test_the_audit_row_keeps_the_figures_and_never_the_text(self):
        report, _ = self.save(delayed="تأخر الحديد", work_done="سري جداً")
        self.save(work_done="سري جداً", delayed="", crew=[("نجار", "2")])
        rows = list(AuditLog.objects.filter(action="save_site_report").order_by("pk"))
        self.assertEqual([row.event_type for row in rows], [AuditEventType.CREATE, AuditEventType.UPDATE])
        self.assertEqual(rows[0].after_data["workers"], 20)
        self.assertTrue(rows[0].after_data["has_delay"])
        self.assertEqual((rows[1].before_data["workers"], rows[1].after_data["workers"], rows[1].after_data["has_delay"]), (20, 2, False))
        self.assertEqual({row.object_id for row in rows}, {str(report.pk)})
        self.assertNotIn("سري", str([row.before_data for row in rows] + [row.after_data for row in rows]))

    def test_the_list_is_newest_day_first_and_carries_the_delay_flag(self):
        for n in (3, 1, 2):
            self.save(day=TODAY() - timedelta(days=n), delayed="تأخير" if n == 2 else "")
        rows = site_reports.reports_for(self.project)
        self.assertEqual([row.report_date for row in rows], [TODAY() - timedelta(days=n) for n in (1, 2, 3)])
        self.assertEqual([bool(row.delayed) for row in rows], [False, True, False])

    def test_a_report_is_looked_up_through_its_project(self):
        report, _ = self.save()
        self.assertEqual(site_reports.get_report(self.project, report.pk), report)
        self.assertIsNone(site_reports.get_report(self.other, report.pk))
        self.assertIsNone(site_reports.get_report(self.project, "abc"))


class ScreenFlowTests(ReportSetup):
    def test_an_engineer_writes_todays_report_and_lands_on_it_with_a_reminder_to_add_photos(self):
        response = self.write()
        report = SiteReport.objects.get()
        self.assertRedirects(response, self.url("diary_day", report.pk, lang="ar"), fetch_redirect_response=False)
        page = self.client.get(response["Location"])
        self.assertContains(page, "data-site-report-page")
        self.assertContains(page, "ضيف صور اليوم من تحت")
        self.assertContains(page, "صب سقف الدور الأول")
        self.assertContains(page, "كتبه")
        self.assertContains(page, "c2_engineer")
        self.assertEqual(site_reports.head_count(report), 20)
        self.assertContains(page, 'data-workers>20<')
        self.assertContains(page, "data-report-photos")  # the photo card is right there

    def test_saving_the_same_day_again_edits_it_and_says_so(self):
        self.write()
        response = self.write(work_done="النهارده اتغير", weather="rain")
        self.assertEqual(SiteReport.objects.count(), 1)
        page = self.client.get(response["Location"])
        self.assertContains(page, "اتعدّل تقرير يوم")
        self.assertContains(page, "النهارده اتغير")
        self.assertEqual(SiteReport.objects.get().weather, "rain")

    def test_the_new_form_for_a_day_that_has_a_report_opens_that_report_for_editing(self):
        self.write()
        report = SiteReport.objects.get()
        self.client.force_login(self.engineer)
        response = self.client.get(self.url("diary_new", lang="ar"))
        self.assertRedirects(response, self.url("diary_edit", report.pk, lang="ar"), fetch_redirect_response=False)
        form = self.client.get(response["Location"])
        self.assertContains(form, 'value="6"')
        self.assertContains(form, "صب سقف الدور الأول")
        self.assertNotContains(form, 'name="report_date"')  # an edit keeps its day

    def test_the_form_offers_a_few_blank_rows_and_never_a_future_day(self):
        self.client.force_login(self.engineer)
        page = self.client.get(self.url("diary_new", lang="ar"))
        self.assertEqual(page.content.decode().count('name="crew_label"'), 3 + 1)  # three blank rows and the template for «+ صف»
        self.assertContains(page, f'max="{TODAY().isoformat()}"')
        future = self.client.get(self.url("diary_new", lang="ar") + "&date=" + (TODAY() + timedelta(days=5)).isoformat())
        self.assertContains(future, f'value="{TODAY().isoformat()}"')  # a future ?date= falls back to today

    def test_a_future_day_posted_by_hand_is_refused_with_the_message_and_what_was_typed_is_kept(self):
        response = self.write(day=TODAY() + timedelta(days=1), work_done="كلام اتكتب")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, site_reports.MESSAGES["ar"]["future"])
        self.assertContains(response, "كلام اتكتب")
        self.assertContains(response, 'value="نجار"')
        self.assertFalse(SiteReport.objects.exists())

    def test_the_form_edit_cannot_move_a_report_to_another_day(self):
        self.write()
        report = SiteReport.objects.get()
        self.client.force_login(self.engineer)
        yesterday = TODAY() - timedelta(days=1)
        self.client.post(self.url("diary_edit", report.pk), self.post_data(day=yesterday, work_done="تعديل"))
        report.refresh_from_db()
        self.assertEqual((report.report_date, report.work_done, SiteReport.objects.count()), (TODAY(), "تعديل", 1))

    def test_every_post_needs_the_right_to_write_and_a_refusal_changes_nothing(self):
        self.write()
        report = SiteReport.objects.get()
        before = (SiteReport.objects.count(), SiteReportRow.objects.count(), Attachment.objects.count())
        for user in (self.keeper, self.viewer):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.post(self.url("diary_new"), self.post_data(day=TODAY() - timedelta(days=2))).status_code, 403)
                self.assertEqual(self.client.post(self.url("diary_edit", report.pk), self.post_data(work_done="تخريب")).status_code, 403)
                self.assertEqual(self.client.get(self.url("diary_new")).status_code, 403)
                self.assertEqual(self.client.get(self.url("diary_edit", report.pk)).status_code, 403)
                refused = self.client.post(self.url("diary_photos", report.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "kind": "photo", "stage": "photo"})
                self.assertEqual(refused.status_code, 403)
        self.assertEqual((SiteReport.objects.count(), SiteReportRow.objects.count(), Attachment.objects.count()), before)
        self.assertEqual(SiteReport.objects.get().work_done, "صب سقف الدور الأول")

    def test_reading_needs_the_project_right_and_a_reader_may_read_but_not_edit(self):
        self.write()
        report = SiteReport.objects.get()
        self.client.force_login(self.keeper)
        for name, args in (("diary", ()), ("diary_day", (report.pk,)), ("diary_print", (report.pk,))):
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 403, name)
        self.client.force_login(self.viewer)
        for name, args in (("diary", ()), ("diary_day", (report.pk,)), ("diary_print", (report.pk,))):
            self.assertEqual(self.client.get(self.url(name, *args)).status_code, 200, name)
        detail = self.client.get(self.url("diary_day", report.pk))
        self.assertNotContains(detail, "data-report-edit")
        self.assertContains(detail, "data-att-blocked")
        self.assertNotContains(self.client.get(self.url("diary")), "data-today-report")

    def test_another_projects_report_id_is_refused_on_every_screen(self):
        self.write(project=self.other)
        theirs = SiteReport.objects.get(project=self.other)
        self.add_photo(theirs, project=self.other)
        photo = Attachment.objects.get(content_type=ContentType.objects.get_for_model(SiteReport))
        self.client.force_login(self.owner)
        for name in ("diary_day", "diary_edit", "diary_print"):
            self.assertEqual(self.client.get(self.url(name, theirs.pk)).status_code, 404, name)  # asked through THIS project
        self.assertEqual(self.client.post(self.url("diary_edit", theirs.pk), self.post_data(work_done="تخريب")).status_code, 404)
        self.assertEqual(self.client.post(self.url("diary_photos", theirs.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "kind": "photo", "stage": "photo"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("file", photo.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("file_delete", photo.pk)).status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.work_done, "صب سقف الدور الأول")
        self.assertEqual(Attachment.objects.filter(pk=photo.pk).count(), 1)
        self.assertEqual(self.client.get(self.url("diary_day", theirs.pk, project=self.other)).status_code, 200)  # through its own project it opens

    def test_a_closed_project_shows_its_reports_read_only(self):
        self.write()
        report = SiteReport.objects.get()
        Project.objects.filter(pk=self.project.pk).update(status="done")
        self.client.force_login(self.engineer)
        self.assertContains(self.client.get(self.url("diary_new"), follow=True), "data-project-closed")
        refused = self.client.post(self.url("diary_edit", report.pk), self.post_data(work_done="بعد القفل"))
        self.assertContains(refused, site_reports.MESSAGES["ar"]["not_open"])
        self.assertEqual(SiteReport.objects.get().work_done, "صب سقف الدور الأول")
        detail = self.client.get(self.url("diary_day", report.pk))
        self.assertNotContains(detail, "data-report-edit")
        self.assertNotContains(self.client.get(self.url("diary")), "data-today-report")
        refused_photo = self.add_photo(report)
        self.assertEqual(refused_photo.status_code, 400)
        self.assertEqual(refused_photo.json()["error"], attachments.MESSAGES["ar"]["report_closed"])

    def test_the_list_shows_the_day_in_words_the_head_count_and_a_mark_only_where_something_was_delayed(self):
        self.write(day=TODAY() - timedelta(days=2), delayed="تأخر توريد الحديد يومين")
        self.write(day=TODAY() - timedelta(days=1))
        self.write(day=TODAY())
        self.client.force_login(self.viewer)
        page = self.client.get(self.url("diary", lang="ar"))
        body = page.content.decode()
        self.assertEqual(body.count("data-delay-mark"), 1)
        rows = re.findall(r'<tr data-site-report="([\d-]+)"', body)
        self.assertEqual(rows, [(TODAY() - timedelta(days=n)).isoformat() for n in (0, 1, 2)])  # newest first
        delayed_row = body.split(f'data-site-report="{(TODAY() - timedelta(days=2)).isoformat()}"')[1].split("</tr>")[0]
        self.assertIn("data-delay-mark", delayed_row)
        self.assertEqual(body.count("data-workers"), 3)
        self.assertIn("20 فرد", visible_text(body))
        self.assertNotRegex(visible_text(body), r"\d{4}-\d{2}-\d{2}")  # days are written in words
        from settings_core.templatetags.hesba_format import day_text

        self.assertIn(day_text(TODAY(), "ar"), visible_text(body))

    def test_the_list_is_empty_with_a_clear_message_and_the_primary_action(self):
        self.client.force_login(self.engineer)
        page = self.client.get(self.url("diary"))
        self.assertContains(page, "data-empty-none")
        self.assertContains(page, "تقرير النهارده")
        self.assertContains(page, self.url("diary_new", lang="ar"))

    def test_the_primary_action_is_on_the_overview_the_list_and_nowhere_for_those_who_cannot_write(self):
        for user, shown in ((self.engineer, True), (self.owner, True), (self.viewer, False)):
            self.client.force_login(user)
            for name in ("detail", "diary"):
                page = self.client.get(self.url(name, lang="ar"))
                with self.subTest(user=user.username, screen=name):
                    self.assertEqual("data-today-report" in page.content.decode(), shown)
        self.client.force_login(self.engineer)
        english = self.client.get(self.url("detail", lang="en"))
        self.assertContains(english, "Today&#x27;s report")

    def test_the_project_pages_link_to_the_two_new_tabs(self):
        self.client.force_login(self.engineer)
        page = self.client.get(self.url("detail"))
        self.assertContains(page, 'data-tab="reports"')
        self.assertContains(page, 'data-tab="documents"')
        for tab, name in (("reports", "diary"), ("documents", "documents")):
            self.assertContains(self.client.get(self.url(name)), f'data-tab="{tab}"')
            self.assertRegex(self.client.get(self.url(name)).content.decode(), rf'aria-current="page" data-tab="{tab}"')


class PhotoTests(ReportSetup):
    def setUp(self):
        super().setUp()
        self.report, _ = self.save()
        self.ct = ContentType.objects.get_for_model(SiteReport)

    def test_photos_attach_to_the_report_and_show_on_it_and_in_the_list_count(self):
        for n in range(3):
            self.assertEqual(self.add_photo(self.report, name=f"p{n}.jpg").status_code, 201)
        rows = Attachment.objects.filter(content_type=self.ct, object_id=self.report.pk)
        self.assertEqual((rows.count(), {row.kind for row in rows}, {row.stage for row in rows}, {row.uploaded_by_id for row in rows}), (3, {Kind.PHOTO}, {Stage.PHOTO}, {self.engineer.pk}))
        self.client.force_login(self.viewer)
        detail = self.client.get(self.url("diary_day", self.report.pk))
        self.assertEqual(detail.content.decode().count("data-attachment="), 3)
        self.assertContains(self.client.get(self.url("diary")), "3 صورة")
        self.assertEqual(Attachment.objects.filter(content_type=ContentType.objects.get_for_model(Project)).count(), 0)  # not on the project

    def test_a_report_takes_photos_only_not_pdf_and_not_a_clinic_stage(self):
        refused = self.add_photo(self.report, raw=PDF, name="a.pdf", mime="application/pdf")
        self.assertEqual(refused.status_code, 400)
        self.client.force_login(self.engineer)
        wrong_stage = self.client.post(self.url("diary_photos", self.report.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "kind": "photo", "stage": "scan"})
        self.assertEqual((wrong_stage.status_code, wrong_stage.json()["error"]), (400, attachments.MESSAGES["ar"]["report_stage"]))
        as_document = self.client.post(self.url("diary_photos", self.report.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "kind": "document", "stage": "photo"})
        self.assertEqual(as_document.status_code, 400)
        self.assertFalse(Attachment.objects.filter(content_type=self.ct).exists())

    def test_the_cap_on_photos_per_report_refuses_the_next_with_a_clear_message(self):
        self.assertEqual(attachments.owner_limits(self.report), (12, 12 * 1024 * 1024))
        for n in range(12):
            attachments.add_attachment(self.report, PNG, self.engineer, kind=Kind.PHOTO, stage=Stage.PHOTO)
        refused = self.add_photo(self.report)
        self.assertEqual((refused.status_code, refused.json()["error"]), (400, attachments.MESSAGES["ar"]["max_count"].format(count=12)))
        self.assertEqual(Attachment.objects.filter(content_type=self.ct).count(), 12)

    def test_those_who_write_reports_may_delete_a_photo_and_a_reader_may_not(self):
        self.add_photo(self.report)
        photo = Attachment.objects.get(content_type=self.ct)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url("file_delete", photo.pk)).status_code, 403)
        self.client.force_login(self.engineer)
        self.assertEqual(self.client.post(self.url("file_delete", photo.pk)).status_code, 200)
        self.assertFalse(Attachment.objects.filter(pk=photo.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_attachment", before_data__owner="projects.sitereport").exists())

    def test_deleting_a_report_photo_does_not_touch_the_projects_documents(self):
        attachments.add_attachment(self.project, PDF, self.owner, kind=Kind.DOCUMENT, stage=Stage.SCAN)
        self.add_photo(self.report)
        self.client.force_login(self.engineer)
        self.client.post(self.url("file_delete", Attachment.objects.get(content_type=self.ct).pk))
        self.assertEqual(Attachment.objects.filter(content_type=ContentType.objects.get_for_model(Project)).count(), 1)


class PrintTests(ReportSetup):
    def setUp(self):
        super().setUp()
        self.report, _ = self.save(weather="hot", delayed="تأخر توريد الحديد يومين", materials="٥٠ شكارة أسمنت", visitors="زيارة الاستشاري", crew=[("نجار", "6"), ("حداد", "4"), ("عمالة مساعدة", "10")],
                                   equipment=[("خلاطة", "2"), ("ونش", "1")])
        for n in range(3):
            self.add_photo(self.report, name=f"p{n}.jpg")

    def page(self, lang="ar", user=None):
        self.client.force_login(user or self.viewer)
        return self.client.get(self.url("diary_print", self.report.pk, lang=lang))

    def test_the_print_carries_every_field_in_arabic(self):
        page = self.page("ar")
        self.assertEqual(page.status_code, 200)
        text = visible_text(page.content.decode())
        from settings_core.templatetags.hesba_format import day_text

        for expected in ("تقرير يومي للموقع", day_text(TODAY(), "ar"), "عمارة النور", "شركة النور للتطوير", "حر", "نجار", "حداد", "عمالة مساعدة", "خلاطة", "ونش", "تأخر توريد الحديد يومين",
                         "٥٠ شكارة أسمنت", "زيارة الاستشاري", "صب سقف الدور الأول", "c2_engineer", "مهندس الموقع", "الاستشاري", "المقاول", "إجمالي الفريق"):
            self.assertIn(expected, text)
        self.assertContains(page, 'dir="rtl"')
        self.assertContains(page, 'data-print-workers>20<')

    def test_the_print_in_english_has_the_english_words_and_ltr(self):
        page = self.page("en")
        text = visible_text(page.content.decode())
        for expected in ("Daily site report", "Hot", "Total workers", "Site engineer", "Consultant", "Contractor", "Equipment", "Workers by trade"):
            self.assertIn(expected, text)
        self.assertContains(page, 'dir="ltr"')
        self.assertNotIn("تقرير يومي للموقع", text)

    def test_the_photos_are_thumbnails_and_open_through_the_project_url(self):
        page = self.page("ar")
        body = page.content.decode()
        self.assertEqual(body.count('<img src="/projects/'), 3)  # the three photos, as thumbnails
        photos = list(Attachment.objects.filter(content_type=ContentType.objects.get_for_model(SiteReport)))
        for photo in photos:
            self.assertIn(self.url("file", photo.pk, lang="ar"), body.replace("&amp;", "&"))

    def test_no_internal_id_is_printed_and_empty_sections_are_left_out(self):
        text = visible_text(self.page("ar").content.decode())
        for pk in (self.report.pk, self.project.pk):
            self.assertNotRegex(text, rf"(?:#|\bid\b|\bpk\b|رقم داخلي)\s*[:=]?\s*{pk}\b")
        for forbidden in ("None", "created_by", "updated_by", "report_date", "csrf", "<"):
            self.assertNotIn(forbidden, text)
        bare, _ = self.save(day=TODAY() - timedelta(days=4), delayed="", materials="", visitors="", crew=[], equipment=[])
        self.client.force_login(self.viewer)
        body = self.client.get(self.url("diary_print", bare.pk)).content.decode()
        for section in ("delayed", "materials", "visitors"):
            self.assertNotIn(f'data-print-text="{section}"', body)
        self.assertNotIn("data-print-photos", body)

    def test_the_print_needs_the_project_right(self):
        self.client.force_login(self.keeper)
        self.assertEqual(self.client.get(self.url("diary_print", self.report.pk)).status_code, 403)

    def test_the_print_is_a_page_and_a_a4_sheet(self):
        body = self.page("ar").content.decode()
        self.assertIn('class="pr-body pr-a4"', body)
        self.assertIn("data-print", body)


class NothingFinancialMovesTests(ReportSetup):
    def lines(self):
        return [(line.pk, line.quantity, contract.certified_quantity(line)) for line in self.project.boq.order_by("pk")]

    def test_writing_editing_photographing_deleting_listing_and_printing_move_no_money_stock_or_quantity(self):
        certificate = contract.create_certificate(self.project, self.owner, certificate_date=TODAY(), description="مستخلص 1",
                                                  quantities={self.concrete.pk: D("10"), self.masonry.pk: D("20")})
        before, position, quantities = footprint(), contract.position(self.project), self.lines()
        gross = certificate.gross
        self.write()
        self.write(work_done="تعديل")
        self.write(day=TODAY() - timedelta(days=1), delayed="تأخير")
        report = SiteReport.objects.get(report_date=TODAY())
        self.add_photo(report)
        self.client.force_login(self.owner)
        self.client.get(self.url("diary"))
        self.client.get(self.url("diary_day", report.pk))
        self.client.get(self.url("diary_print", report.pk))
        self.client.post(self.url("file_delete", Attachment.objects.get(content_type=ContentType.objects.get_for_model(SiteReport)).pk))
        self.assertEqual(SiteReport.objects.count(), 2)
        self.assertEqual(footprint(), before)
        self.assertEqual(contract.position(self.project), position)
        self.assertEqual(self.lines(), quantities)
        certificate.refresh_from_db()
        self.assertEqual((certificate.gross, certificate.number), (gross, 1))

    def test_the_quantities_executed_on_a_certificate_are_not_taken_from_a_report(self):
        """The report says «صب 40 م³ النهارده» in words; the certificate still bills exactly what was typed on it."""

        self.write(work_done="صب 40 م³ خرسانة")
        certificate = contract.create_certificate(self.project, self.owner, certificate_date=TODAY(), description="مستخلص 1", quantities={self.concrete.pk: D("12")})
        self.assertEqual(certificate.gross, D("12000.00"))  # 12 m3 x 1,000 from the certificate's own input, nothing from the report

    def test_the_report_modules_never_call_posting_stock_cashbox_or_ledger_code_nor_read_the_contract(self):
        from pathlib import Path

        from django.conf import settings

        for name in ("site_reports.py", "views_reports.py"):
            source = Path(settings.BASE_DIR, "projects", name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                for forbidden in ("post_sales_invoice", "post_purchase_invoice", "StockMovement", "CashboxMovement", "JournalEntry", "LedgerEntry", "average_cost", "adjust_stock",
                                  "from . import contract", "from .contract import", "BoqLine", "create_certificate"):
                    self.assertNotIn(forbidden, source)
