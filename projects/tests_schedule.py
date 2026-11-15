"""Contracting C3 (HG-124): the schedule (الجدول الزمني) and its delay alerts: the figures, the rules, the report and the screens.

Every figure is read on a fixed day, never on the day the suite runs:

* the school (three tasks typed at 10% / 30% / 60%): excavation 1–15 January 2026 (15 days), foundations 16 January – 15
  February (31 days), structure 16 February – 30 June (135 days). On 20 February: planned = 10 + 30 + 60 × 5/135 = 42.2%;
  with excavation done, foundations at 70% and structure at 0, done = 10 + 21 + 0 = 31.0%, 11.2 points behind; foundations
  are 5 days past their end, and structure should have started while foundations is not finished.
* the reference contract of ``tests_variation_baseline`` (item A 400 m³ × 1,500.00) for progress read from certificates.
"""

import re
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_cashbox, make_customer, make_location
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import insights, schedule, services
from .models import Project, ProjectTask, VariationOrder, VariationStatus
from .tests_c2_ui import PRE_LABELLED, sentences
from .tests_contract import MODULES, person
from .tests_documents import view_only
from .tests_variation_baseline import DAY, ReferenceContract

#: Friday 20 February 2026, 10:00 in Cairo (08:00 UTC).
FEB20 = date(2026, 2, 20)
FEB20_NOW = datetime(2026, 2, 20, 8, 0, tzinfo=dt_timezone.utc)
BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")
LATIN_START = re.compile(r"^[A-Za-z]")
ARABIC = re.compile(r"[؀-ۿ]")


def task(project, user, name, start, end, **extra):
    data = {"name": name, "start_date": start.isoformat(), "end_date": end.isoformat()}
    data.update(extra)
    return schedule.save_task(project, user, data)


class SchoolSetup(TestCase):
    """The three-task school on 20 February 2026, with the office, the site engineer, a reader and a stock keeper."""

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=FEB20_NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.profile = prepared_client(activity="contracting", sub_activity="general", modules=MODULES)
        self.owner = person(RoleCode.OWNER, "c3_owner")
        self.engineer = person(RoleCode.SITE_ENGINEER, "c3_engineer")
        self.keeper = person(RoleCode.STOCK_KEEPER, "c3_keeper")
        make_location(location_code="STORE", is_default=True)
        make_cashbox(cashbox_code="MAIN", is_default=True)
        self.customer = make_customer(customer_code="C-SCHOOL", name="مدارس الأمل الخاصة")
        self.project = services.save_project({"name": "مدرسة الأمل", "customer": self.customer, "start_date": date(2026, 1, 1),
                                              "end_date": date(2026, 6, 30)}, self.owner)
        self.excavation = task(self.project, self.owner, "أعمال الحفر", date(2026, 1, 1), date(2026, 1, 15), weight="10")
        self.foundations = task(self.project, self.owner, "الأساسات", date(2026, 1, 16), date(2026, 2, 15), weight="30", predecessor=str(self.excavation.pk))
        self.structure = task(self.project, self.owner, "الهيكل الخرساني", date(2026, 2, 16), date(2026, 6, 30), weight="60", predecessor=str(self.foundations.pk))
        schedule.set_progress(self.project, self.excavation, self.owner, "100")
        schedule.set_progress(self.project, self.foundations, self.owner, "70")

    def url(self, name, *args, project=None, lang=None):
        link = reverse(f"projects:{name}", args=[(project or self.project).pk, *args])
        return f"{link}?lang={lang}" if lang else link

    def row(self, data, task_obj):
        return next(row for row in data["rows"] if row["task"].pk == task_obj.pk)


# ---- the figures ----

class FigureTests(SchoolSetup):
    def test_twentieth_of_february_planned_done_gap_and_the_alerts(self):
        data = schedule.snapshot(self.project, FEB20)
        self.assertEqual((data["planned"], data["actual"], data["gap"]), (D("42.2"), D("31.0"), D("11.2")))
        self.assertEqual([(self.row(data, t)["planned"], self.row(data, t)["actual"], self.row(data, t)["share"]) for t in (self.excavation, self.foundations, self.structure)],
                         [(D("100.0"), D("100.0"), D("10.0")), (D("100.0"), D("70.0"), D("30.0")), (D("3.7"), D("0.0"), D("60.0"))])
        self.assertEqual([schedule.duration(t) for t in (self.excavation, self.foundations, self.structure)], [15, 31, 135])
        kinds = [(alert["kind"], alert["task"].name) for alert in data["alerts"]]
        self.assertEqual(kinds, [("late", "الأساسات"), ("waiting", "الهيكل الخرساني")])
        self.assertEqual(self.row(data, self.foundations)["late_days"], 5)
        self.assertEqual(self.row(data, self.structure)["blocked_by"], self.foundations)
        # 11.2 points is not more than BEHIND_POINTS (15): the gap alone raises nothing; the late task does
        self.assertEqual(insights.BEHIND_POINTS, 15)
        self.assertNotIn("behind", [alert["kind"] for alert in data["alerts"]])
        self.assertTrue(data["behind"])

    def test_fifteenth_of_march_the_gap_passes_the_threshold(self):
        data = schedule.snapshot(self.project, date(2026, 3, 15))  # structure: 28 of 135 days → 60 × 28/135 = 12.44
        self.assertEqual((data["planned"], data["actual"], data["gap"]), (D("52.4"), D("31.0"), D("21.4")))
        self.assertEqual(data["alerts"][0], {"kind": "behind", "points": D("21.4")})
        self.assertEqual(self.row(data, self.foundations)["late_days"], 28)

    def test_a_task_counts_its_own_days_and_a_finished_one_raises_nothing(self):
        self.assertEqual([schedule.planned_share(self.structure, day) for day in (date(2026, 2, 15), date(2026, 2, 16), date(2026, 6, 30), date(2026, 7, 9))],
                         [D("0"), D("100") / D("135"), D("100"), D("100")])
        schedule.set_progress(self.project, self.foundations, self.owner, "100")
        data = schedule.snapshot(self.project, FEB20)
        self.assertEqual((data["actual"], data["alerts"], data["behind"]), (D("40.0"), [], False))  # 10 + 30 + 0, structure may start

    def test_with_no_weight_a_task_weighs_its_days(self):
        other = services.save_project({"name": "سور المدرسة", "customer": self.customer}, self.owner)
        first = task(other, self.owner, "حفر السور", date(2026, 2, 1), date(2026, 2, 10))
        task(other, self.owner, "مباني السور", date(2026, 2, 21), date(2026, 3, 22), predecessor=str(first.pk))
        schedule.set_progress(other, first, self.owner, "100")
        data = schedule.snapshot(other, FEB20)  # 10 days done, 30 days not started: planned 10/40, done 10/40
        self.assertEqual([row["weight"] for row in data["rows"]], [D("10"), D("30")])
        self.assertEqual((data["planned"], data["actual"], data["alerts"]), (D("25.0"), D("25.0"), []))

    def test_planned_end_is_the_last_task_plus_approved_extensions(self):
        self.assertEqual(schedule.planned_end(self.project), date(2026, 6, 30))
        # Extensions are counted from approved orders only (C1's ``extension_days``); a draft and a rejected order extend nothing.
        for number, status in ((1, VariationStatus.DRAFT), (2, VariationStatus.REJECTED)):
            VariationOrder.objects.create(project=self.project, number=number, order_date=FEB20, title="تمديد", status=status, extension_days=30, created_by=self.owner)
        self.assertEqual(schedule.planned_end(self.project), date(2026, 6, 30))
        VariationOrder.objects.create(project=self.project, number=3, order_date=FEB20, title="تمديد معتمد", status=VariationStatus.APPROVED, extension_days=30, created_by=self.owner)
        self.assertEqual(schedule.planned_end(self.project), date(2026, 7, 30))
        self.assertEqual(schedule.snapshot(self.project, FEB20)["planned_end"], date(2026, 7, 30))
        empty = services.save_project({"name": "بدون جدول", "customer": self.customer}, self.owner)
        self.assertIsNone(schedule.planned_end(empty))


# ---- the rules ----

class RuleTests(SchoolSetup):
    def test_a_predecessor_loop_is_refused_and_so_are_itself_and_another_projects_task(self):
        with self.assertRaisesMessage(ValidationError, "دايرة"):
            schedule.save_task(self.project, self.owner, {"name": "أعمال الحفر", "start_date": "2026-01-01", "end_date": "2026-01-15",
                                                           "predecessor": str(self.structure.pk)}, self.excavation)
        with self.assertRaisesMessage(ValidationError, "لنفسه"):
            schedule.save_task(self.project, self.owner, {"name": "الأساسات", "start_date": "2026-01-16", "end_date": "2026-02-15",
                                                           "predecessor": str(self.foundations.pk)}, self.foundations)
        other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)
        stranger = task(other, self.owner, "بند غريب", date(2026, 1, 1), date(2026, 1, 2))
        with self.assertRaisesMessage(ValidationError, "نفس المشروع"):
            task(self.project, self.owner, "تشطيبات", date(2026, 7, 1), date(2026, 8, 1), predecessor=str(stranger.pk))
        self.excavation.refresh_from_db()
        self.assertIsNone(self.excavation.predecessor_id)

    def test_refusals_in_words_in_both_languages(self):
        cases = ((dict(name=""), "اكتب اسم البند", "Type the task's name"),
                 (dict(end_date="2025-12-31"), "تاريخ النهاية لازم", "The end date must be"),
                 (dict(start_date="امبارح"), "تاريخ البداية", "start date"),
                 (dict(weight="-3"), "الوزن لازم", "The weight must"),
                 (dict(weight="abc"), "الوزن لازم", "The weight must"),
                 (dict(name="س" * 161), "أطول من المسموح", "longer than allowed"))
        for over, arabic, english in cases:
            data = {"name": "تشطيبات", "start_date": "2026-01-01", "end_date": "2026-01-05"}
            data.update(over)
            with self.subTest(over=over):
                with self.assertRaisesMessage(ValidationError, arabic):
                    schedule.save_task(self.project, self.owner, data)
                with self.assertRaisesMessage(ValidationError, english):
                    schedule.save_task(self.project, self.owner, data, lang="en")
        for bad in ("101", "-1", "سبعين", ""):
            with self.subTest(progress=bad), self.assertRaisesMessage(ValidationError, "من 0 لـ 100"):
                schedule.set_progress(self.project, self.structure, self.owner, bad)
        self.assertEqual(ProjectTask.objects.filter(project=self.project).count(), 3)

    def test_the_database_refuses_impossible_rows(self):
        for over in (dict(end_date=date(2025, 1, 1)), dict(progress=101), dict(weight=D("0"))):
            with self.subTest(over=over), self.assertRaises(IntegrityError), transaction.atomic():
                values = dict(project=self.project, name="غلط", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), created_by=self.owner)
                values.update(over)
                ProjectTask.objects.create(**values)

    def test_a_task_others_depend_on_is_not_deleted_a_last_one_is_and_both_are_audited(self):
        with self.assertRaisesMessage(ValidationError, "معتمدة عليه"):
            schedule.delete_task(self.project, self.foundations, self.owner)
        schedule.delete_task(self.project, self.structure, self.owner)
        self.assertEqual(list(ProjectTask.objects.filter(project=self.project).values_list("name", flat=True)), ["أعمال الحفر", "الأساسات"])
        actions = list(AuditLog.objects.filter(object_type="projects.ProjectTask").values_list("action", flat=True))
        self.assertEqual(actions.count("save_project_task"), 3)
        self.assertEqual(actions.count("project_task_progress"), 2)
        self.assertEqual(actions.count("delete_project_task"), 1)

    def test_a_closed_project_takes_no_change(self):
        Project.objects.filter(pk=self.project.pk).update(status="done")
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            task(self.project, self.owner, "تشطيبات", date(2026, 7, 1), date(2026, 8, 1))
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            schedule.set_progress(self.project, self.structure, self.owner, "10")

    def test_a_new_task_goes_last_and_the_order_can_be_typed(self):
        finishing = task(self.project, self.owner, "التشطيبات", date(2026, 7, 1), date(2026, 8, 31))
        self.assertEqual(finishing.sort, 4)
        schedule.save_task(self.project, self.owner, {"name": "التشطيبات", "start_date": "2026-07-01", "end_date": "2026-08-31", "sort": "0"}, finishing)
        self.assertEqual([t.name for t in schedule.tasks_for(self.project)][0], "التشطيبات")


class BoqProgressTests(ReferenceContract):
    """Progress read from the certificates of the reference contract's items (A: 400 m³ × 1,500.00 = 600,000.00)."""

    def setUp(self):
        super().setUp()
        self.concrete = schedule.save_task(self.project, self.owner, {"name": "الخرسانة المسلحة", "start_date": (DAY - timedelta(days=30)).isoformat(),
                                                                       "end_date": (DAY + timedelta(days=30)).isoformat(), "boq_lines": [self.a.pk]})

    def test_150_of_400_certified_is_37_5_and_the_task_weighs_the_items_value(self):
        self.certify(a="150")
        actual, weight, source = schedule.task_figures(ProjectTask.objects.get(pk=self.concrete.pk))
        self.assertEqual((actual, weight, source), (D("37.5"), D("600000.00"), "boq"))
        self.assertEqual(schedule.snapshot(self.project, DAY)["actual"], D("37.5"))

    def test_a_draft_certificate_moves_nothing_until_it_is_posted(self):
        from sales.services import post_sales_invoice

        draft = self.certify(post=False, a="150")
        self.assertEqual(schedule.task_figures(ProjectTask.objects.get(pk=self.concrete.pk))[0], D("0"))
        post_sales_invoice(draft.invoice_id, self.owner)
        self.assertEqual(schedule.task_figures(ProjectTask.objects.get(pk=self.concrete.pk))[0], D("37.5"))

    def test_a_posted_sales_return_takes_its_quantity_back(self):
        from sales.services import create_sales_return

        first = self.certify(a="150")
        create_sales_return("SR-C3", DAY, first.invoice_id, [{"source_line": first.invoice.lines.get(), "quantity": D("50")}], "خطأ في الكميات", self.owner)
        self.assertEqual(schedule.task_figures(ProjectTask.objects.get(pk=self.concrete.pk))[0], D("25"))  # 100 of 400 m³

    def test_the_percent_is_not_typed_on_a_linked_task(self):
        with self.assertRaisesMessage(ValidationError, "بتتحسب من المستخلصات"):
            schedule.set_progress(self.project, self.concrete, self.owner, "80")
        self.assertEqual(ProjectTask.objects.get(pk=self.concrete.pk).progress, 0)

    def test_an_item_is_linked_to_one_task_and_only_of_its_project(self):
        with self.assertRaisesMessage(ValidationError, "مربوط بالفعل"):
            schedule.save_task(self.project, self.owner, {"name": "خرسانة الدور التاني", "start_date": DAY.isoformat(), "end_date": DAY.isoformat(), "boq_lines": [self.a.pk]})
        other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)
        with self.assertRaisesMessage(ValidationError, "من نفس المشروع"):
            schedule.save_task(other, self.owner, {"name": "خرسانة", "start_date": DAY.isoformat(), "end_date": DAY.isoformat(), "boq_lines": [self.b.pk]})
        both = schedule.save_task(self.project, self.owner, {"name": "المباني", "start_date": DAY.isoformat(), "end_date": DAY.isoformat(), "boq_lines": [self.b.pk]})
        self.assertEqual(list(both.boq_lines.values_list("code", flat=True)), ["B"])

    def test_nothing_here_writes_money(self):
        from .tests_variation_baseline import money_footprint

        self.certify(a="150")
        before = money_footprint()
        schedule.snapshot(self.project, DAY)
        schedule.save_task(self.project, self.owner, {"name": "المباني", "start_date": DAY.isoformat(), "end_date": DAY.isoformat(), "weight": "5"})
        self.assertEqual(money_footprint(), before)


# ---- the portfolio, the report and the dashboard ----

class PortfolioTests(ReferenceContract):
    """The reference project after certificates 1 and 2: billed 45% (450,000.00 of 1,000,000.00), 100 of 200 days gone on DAY."""

    def setUp(self):
        super().setUp()
        self.two_certificates()

    def row(self, today):
        rows, totals = insights.portfolio(today=today)
        return next(row for row in rows if row["project"].pk == self.project.pk), totals

    def test_a_project_without_tasks_keeps_the_billing_based_flag(self):
        row, totals = self.row(DAY + timedelta(days=60))
        self.assertEqual((row["time_pct"], row["figures"]["progress"], row["behind"], row["schedule"], totals["behind"]), (80, 45, True, None, 1))  # 45 + 15 < 80
        row, totals = self.row(DAY)
        self.assertEqual((row["time_pct"], row["behind"], totals["behind"]), (50, False, 0))  # 45 + 15 is not below 50

    def test_a_schedule_on_track_replaces_a_billing_lag(self):
        on_track = task(self.project, self.owner, "الهيكل", DAY - timedelta(days=100), DAY + timedelta(days=200))
        schedule.set_progress(self.project, on_track, self.owner, "60")
        row, totals = self.row(DAY + timedelta(days=60))  # billing says behind (45 + 15 < 80); the schedule (planned 53.5%, done 60%) does not
        self.assertEqual((row["schedule"]["planned"], row["schedule"]["actual"]), (D("53.5"), D("60.0")))
        self.assertEqual((row["behind"], totals["behind"]), (False, 0))

    def test_a_late_task_flags_a_project_billing_on_time(self):
        late = task(self.project, self.owner, "الأساسات", DAY - timedelta(days=60), DAY - timedelta(days=5))
        schedule.set_progress(self.project, late, self.owner, "50")
        row, totals = self.row(DAY)  # billing alone says on time (45 + 15 ≥ 50)
        self.assertEqual((row["behind"], totals["behind"]), (True, 1))
        # planned 100% (its end passed), done 50%: 50 points behind, and the task is 5 days late
        self.assertEqual([alert["kind"] for alert in row["schedule"]["alerts"]], ["behind", "late"])

    def test_the_report_shows_the_flag_and_says_where_it_comes_from(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("projects:report")).content.decode()
        self.assertNotIn("data-behind-by", page)  # on DAY the billing rule is quiet
        late = task(self.project, self.owner, "الأساسات", DAY - timedelta(days=60), DAY - timedelta(days=5))
        schedule.set_progress(self.project, late, self.owner, "50")
        page = self.client.get(reverse("projects:report")).content.decode()
        self.assertIn(f'data-portfolio-row="{self.project.code}" data-behind', page)
        self.assertIn('data-behind-by="schedule"', page)
        self.assertIn("الجدول: المخطط 100% والمنفّذ 50%", page)
        english = self.client.get(reverse("projects:report") + "?lang=en").content.decode()
        self.assertIn("Schedule: planned 100%, done 50%", english)

    def test_the_report_keeps_the_billing_flag_for_a_project_without_tasks(self):
        Project.objects.filter(pk=self.project.pk).update(start_date=DAY - timedelta(days=160))  # 160 of 260 days gone: 62%, and 45 + 15 < 62
        self.client.force_login(self.owner)
        page = self.client.get(reverse("projects:report")).content.decode()
        self.assertIn('data-behind-by="billing"', page)
        self.assertNotIn("data-plan-figures", page)

    def test_the_dashboard_counts_the_projects_behind_schedule(self):
        from reports.activity_panel import WORDS, _projects

        def count(today):
            tiles, _ = _projects(WORDS["ar"], today)
            tile = next(t for t in tiles if t["key"] == "behind_schedule")
            return tile["value"], tile["sub"]

        self.assertEqual(count(DAY), (0, ""))
        self.assertEqual(count(DAY + timedelta(days=60)), (1, "برج النور الإداري"))  # billing-based: no tasks yet
        late = task(self.project, self.owner, "الأساسات", DAY - timedelta(days=60), DAY - timedelta(days=5))
        schedule.set_progress(self.project, late, self.owner, "50")
        self.assertEqual(count(DAY), (1, "برج النور الإداري"))  # schedule-based


# ---- the screens ----

class CodexFindingsTests(SchoolSetup):
    """Codex on #429: a weight that rounds to zero, a plan save that wrote back a stale percent, and the table's headers."""

    def test_a_weight_that_rounds_to_zero_is_refused_with_a_message(self):
        with self.assertRaises(ValidationError):
            task(self.project, self.owner, "تشطيبات", date(2026, 7, 1), date(2026, 8, 1), weight="0.001")
        self.client.force_login(self.owner)
        page = self.client.post(self.url("schedule"), {"lang": "ar", "action": "add", "name": "تشطيبات", "start_date": "2026-07-01",
                                                       "end_date": "2026-08-01", "weight": "0.004"})
        self.assertLess(page.status_code, 500)
        self.assertFalse(ProjectTask.objects.filter(name="تشطيبات").exists())

    def test_a_plan_save_from_a_task_read_before_the_engineers_update_keeps_the_new_percent(self):
        stale = ProjectTask.objects.get(pk=self.foundations.pk)  # the office's page loaded it at 70%
        schedule.set_progress(self.project, self.foundations, self.engineer, "90")
        schedule.save_task(self.project, self.owner, {"name": "الأساسات المسلحة", "start_date": "2026-01-16", "end_date": "2026-02-15",
                                                      "weight": "30", "predecessor": str(self.excavation.pk)}, stale)
        self.foundations.refresh_from_db()
        self.assertEqual((self.foundations.name, self.foundations.progress), ("الأساسات المسلحة", 90))

    def test_the_task_tables_headers_match_its_cells(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.url("schedule")).content.decode()
        head = re.search(r"data-schedule-tasks><thead><tr>(.*?)</tr></thead>", html).group(1)
        headers = re.findall(r"<th[^>]*>(.*?)</th>", head)
        self.assertEqual(headers[2], "الإنجاز")  # the third cell is the percent done
        self.assertEqual(len(headers), 8)


class ScreenTests(SchoolSetup):
    def test_the_tab_shows_the_summary_the_alerts_the_chart_and_the_tasks(self):
        self.client.force_login(self.owner)
        page = self.client.get(self.url("schedule"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        for marker, text in (('data-summary="planned"', "42.2%"), ('data-summary="actual"', "31%"), ('data-summary="gap"', "11.2 نقطة"),
                             ('data-summary="planned_end"', "30 يونيو 2026")):
            with self.subTest(marker=marker):
                self.assertRegex(html, re.escape(marker) + r"[^>]*>" + re.escape(text) + "<")
        self.assertIn("«الأساسات» عدّى ميعاد نهايته بـ 5 يوم وإنجازه 70%.", html)
        self.assertIn("«الهيكل الخرساني» كان المفروض يبدأ، واللي قبله «الأساسات» لسه ما خلصش.", html)
        self.assertEqual(len(re.findall(r"data-gantt-row=", html)), 3)
        self.assertIn("data-gantt-today", html)
        self.assertIn('data-schedule-task="%d" data-late' % self.foundations.pk, html)
        self.assertIn("data-task-form", html)

    def test_the_chart_scrolls_inside_its_own_box_and_never_widens_the_page(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.url("schedule")).content.decode()
        scroller = re.search(r'<div class="sch-gantt" data-gantt-scroll tabindex="0" role="region" aria-label="[^"]+">\s*<div class="sch-canvas" style="--sch-width:(\d+)px" data-gantt>', html)
        self.assertIsNotNone(scroller)
        self.assertEqual(int(scroller.group(1)), 640)  # 181 days × 3 px is less than the 640 px floor
        css = Path(settings.BASE_DIR, "static/hesba/css/projects_schedule.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.sch-gantt\{[^}]*max-inline-size:100%;overflow-x:auto")
        widths = re.findall(r"([.\w-]+)\{[^}]*min-inline-size:var\(--sch-width", css)
        self.assertEqual(widths, [".sch-canvas"])  # the only wide box is the one inside the scroller
        self.assertNotRegex(css, r"(?<![-\w])(left|right)\s*:")  # logical properties only, so Arabic mirrors the bars
        # every inline style of the chart carries custom properties only, written with a Latin point whatever the page's language
        chart = html[html.index("data-gantt-scroll"):html.index("sch-legend")]
        for style in re.findall(r'style="([^"]*)"', chart):
            with self.subTest(style=style):
                self.assertRegex(style, r"^(--sch-[a-z]+:[0-9.]+(px)?;?)+$")
        bar = re.search(r'data-gantt-row="%d"><span class="sch-name">[^<]+</span><span class="sch-track"><span class="sch-bar" style="([^"]+)"' % self.structure.pk, html)
        self.assertEqual(bar.group(1), "--sch-from:0.2541;--sch-span:0.7459;--sch-done:0.0000")  # 46 of 181 days in, 135 long

    def test_arabic_reads_right_to_left_and_english_left_to_right(self):
        from printing.company import company_details

        from .tests_site_reports import visible_text

        self.client.force_login(self.owner)
        for name in ("schedule", "schedule_task"):
            args = (self.foundations.pk,) if name == "schedule_task" else ()
            arabic = self.client.get(self.url(name, *args)).content.decode()
            english = self.client.get(self.url(name, *args, lang="en")).content.decode()
            self.assertRegex(arabic, r'<html[^>]*dir="rtl"')
            self.assertRegex(english, r'<html[^>]*dir="ltr"')
            # the shell's language switch, the project's code and the company's name are data, not this page's words
            data = (self.project.code, "English", company_details()["name"])
            for text in sentences(re.sub(r"<(script|style)\b.*?</\1>", " ", arabic, flags=re.S)):
                if text.startswith(data):
                    continue
                with self.subTest(screen=name, arabic=text[:40]):
                    self.assertFalse(LATIN_START.match(text), text)
            visible = visible_text(re.sub(r"<bdi>.*?</bdi>", " ", english, flags=re.S))
            for words in ("الجدول الزمني", "متأخر عن الجدول", "مخطط التنفيذ", "نسبة الإنجاز", "تاريخ البداية", "النهارده"):
                with self.subTest(screen=name, words=words):
                    self.assertNotIn(words, visible)
        english = self.client.get(self.url("schedule", lang="en")).content.decode()
        self.assertIn("«الأساسات» passed its end by 5 days at 70% done.", english)
        self.assertIn("15 February 2026", english)  # dates in words, in the page's language

    def test_every_project_page_links_to_the_tab_and_the_header_carries_the_alert(self):
        self.client.force_login(self.owner)
        for name in ("detail", "boq", "certificates", "variations", "schedule"):
            with self.subTest(page=name):
                html = self.client.get(self.url(name)).content.decode()
                self.assertIn('data-tab="schedule"', html)
                self.assertIn("data-schedule-alert", html)
                self.assertIn("1 بند عدّى ميعاده · 1 بند مستني اللي قبله", html)
        self.assertIn("data-schedule-alert-link", self.client.get(self.url("detail")).content.decode())
        self.assertNotIn("data-schedule-alert-link", self.client.get(self.url("schedule")).content.decode())
        quiet = services.save_project({"name": "بدون جدول", "customer": self.customer}, self.owner)
        self.assertNotIn("data-schedule-alert", self.client.get(self.url("detail", project=quiet)).content.decode())

    def test_the_office_adds_edits_and_deletes_a_task_from_the_screens(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url("schedule"), {"lang": "ar", "name": "التشطيبات", "start_date": "2026-07-01", "end_date": "2026-08-31",
                                                           "predecessor": str(self.structure.pk), "weight": "20"})
        self.assertEqual(response.status_code, 302)
        finishing = ProjectTask.objects.get(name="التشطيبات")
        self.assertEqual((finishing.predecessor, finishing.weight, finishing.sort), (self.structure, D("20.00"), 4))
        response = self.client.post(self.url("schedule_task", finishing.pk), {"lang": "ar", "action": "save", "name": "التشطيبات والدهانات",
                                                                             "start_date": "2026-07-05", "end_date": "2026-09-05", "predecessor": str(self.structure.pk)})
        self.assertEqual(response.status_code, 302)
        finishing.refresh_from_db()
        self.assertEqual((finishing.name, finishing.start_date, finishing.weight), ("التشطيبات والدهانات", date(2026, 7, 5), None))
        self.assertEqual(self.client.post(self.url("schedule_task", finishing.pk), {"lang": "ar", "action": "delete"}).status_code, 302)
        self.assertFalse(ProjectTask.objects.filter(pk=finishing.pk).exists())

    def test_a_refusal_is_shown_in_words_with_what_was_typed(self):
        self.client.force_login(self.owner)
        page = self.client.post(self.url("schedule"), {"lang": "ar", "name": "التشطيبات", "start_date": "2026-07-01", "end_date": "2026-06-01"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-form-error")
        self.assertContains(page, "تاريخ النهاية لازم يكون في يوم البداية أو بعده.")
        self.assertContains(page, 'value="التشطيبات"')
        page = self.client.post(self.url("schedule_task", self.excavation.pk), {"lang": "ar", "action": "save", "name": "أعمال الحفر", "start_date": "2026-01-01",
                                                                               "end_date": "2026-01-15", "predecessor": str(self.structure.pk)})
        self.assertContains(page, "بيعمل دايرة")

    def test_the_engineer_records_progress_but_never_the_plan(self):
        self.client.force_login(self.engineer)
        self.assertEqual(self.client.get(self.url("schedule")).status_code, 200)
        page = self.client.get(self.url("schedule_task", self.foundations.pk)).content.decode()
        self.assertIn("data-progress-form", page)
        self.assertNotIn("data-task-form", page)
        self.assertNotIn("data-task-delete", page)
        response = self.client.post(self.url("schedule_task", self.foundations.pk), {"lang": "ar", "action": "progress", "progress": "100"})
        self.assertEqual(response.status_code, 302)
        self.foundations.refresh_from_db()
        self.assertEqual(self.foundations.progress, 100)
        refused = self.client.post(self.url("schedule_task", self.foundations.pk), {"lang": "ar", "action": "save", "name": "الأساسات",
                                                                                   "start_date": "2026-01-16", "end_date": "2026-03-31"})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.client.post(self.url("schedule_task", self.foundations.pk), {"lang": "ar", "action": "delete"}).status_code, 403)
        self.assertEqual(self.client.post(self.url("schedule"), {"lang": "ar", "name": "بند", "start_date": "2026-07-01", "end_date": "2026-07-02"}).status_code, 403)
        self.foundations.refresh_from_db()
        self.assertEqual((self.foundations.start_date, self.foundations.end_date), (date(2026, 1, 16), date(2026, 2, 15)))
        self.assertEqual(ProjectTask.objects.filter(project=self.project).count(), 3)
        self.assertIn("data-plan-only", self.client.get(self.url("schedule")).content.decode())

    def test_a_reader_sees_and_a_stock_keeper_is_refused(self):
        reader = view_only(self.owner, "c3_reader")
        self.client.force_login(reader)
        page = self.client.get(self.url("schedule")).content.decode()
        self.assertIn("data-view-only", page)
        self.assertNotIn("data-task-form", page)
        self.assertEqual(self.client.post(self.url("schedule_task", self.structure.pk), {"action": "progress", "progress": "5"}).status_code, 403)
        self.client.force_login(self.keeper)
        for url in (self.url("schedule"), self.url("schedule_task", self.structure.pk)):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {"action": "progress", "progress": "5"}).status_code, 403)

    def test_another_projects_task_and_an_unknown_one_are_404(self):
        other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("schedule_task", self.structure.pk, project=other)).status_code, 404)
        self.assertEqual(self.client.post(self.url("schedule_task", self.structure.pk, project=other), {"action": "progress", "progress": "5"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("schedule_task", 10 ** 9)).status_code, 404)
        self.assertEqual(self.client.post(self.url("schedule_task", self.structure.pk), {"action": "nonsense"}).status_code, 404)
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.progress, 0)

    def test_a_closed_project_is_read_only(self):
        Project.objects.filter(pk=self.project.pk).update(status="done")
        self.client.force_login(self.owner)
        page = self.client.get(self.url("schedule")).content.decode()
        self.assertIn("data-project-closed", page)
        self.assertNotIn("data-task-form", page)
        self.assertNotIn("data-progress-form", self.client.get(self.url("schedule_task", self.structure.pk)).content.decode())

    def test_an_empty_schedule_says_so(self):
        empty = services.save_project({"name": "بدون جدول", "customer": self.customer}, self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(self.url("schedule", project=empty)).content.decode()
        self.assertIn("لسه مفيش بنود في الجدول الزمني للمشروع ده.", page)
        self.assertNotIn("data-gantt-scroll", page)
        self.assertNotIn("data-schedule-summary", page)

    def test_the_engineer_cannot_type_the_percent_of_a_linked_task_on_the_screen(self):
        from . import contract

        line = contract.save_boq_line(self.project, self.owner, {"code": "A", "description": "خرسانة", "unit": "م³", "quantity": "10", "rate": "1000"})
        schedule.save_task(self.project, self.owner, {"name": "الهيكل الخرساني", "start_date": "2026-02-16", "end_date": "2026-06-30",
                                                       "predecessor": str(self.foundations.pk), "weight": "60", "boq_lines": [line.pk]}, self.structure)
        self.client.force_login(self.engineer)
        page = self.client.get(self.url("schedule_task", self.structure.pk)).content.decode()
        self.assertIn("data-progress-boq", page)
        self.assertNotIn("data-progress-form", page)
        refused = self.client.post(self.url("schedule_task", self.structure.pk), {"lang": "ar", "action": "progress", "progress": "40"})
        self.assertContains(refused, "بتتحسب من المستخلصات")
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.progress, 0)


class EntityTests(ReferenceContract):
    def test_another_entity_sees_none_of_the_schedule_and_changes_nothing(self):
        from entities.current import working_in
        from entities.models import Entity, EntityMembership

        self.two_certificates()  # the project's money is now in the main entity
        concrete = schedule.save_task(self.project, self.owner, {"name": "الخرسانة", "start_date": (DAY - timedelta(days=60)).isoformat(),
                                                                  "end_date": (DAY - timedelta(days=5)).isoformat()})
        branch = Entity.objects.create(code="E9", name_ar="فرع الإسماعيلية")
        make_location(location_code="E9-LOC", entity=branch)
        make_cashbox(cashbox_code="E9-BOX", entity=branch)
        manager = person(RoleCode.MANAGER, "c3_branch_mgr")
        EntityMembership.objects.create(user=manager, entity=branch, is_default=True)
        with working_in(branch):
            self.assertIsNone(schedule.get_task(self.project, concrete.pk))
            self.assertIsNone(schedule.header_alerts(self.project, DAY))
            with self.assertRaisesMessage(ValidationError, "كيان تاني"):
                schedule.save_task(self.project, self.owner, {"name": "بند", "start_date": DAY.isoformat(), "end_date": DAY.isoformat()})
            with self.assertRaisesMessage(ValidationError, "كيان تاني"):
                schedule.set_progress(self.project, concrete, self.owner, "50")
        self.client.force_login(manager)
        tab = self.client.get(reverse("projects:schedule", args=[self.project.pk]))
        self.assertContains(tab, "data-other-entity")
        for hidden in ("data-schedule-summary", "data-gantt-scroll", "data-schedule-task=", "data-task-form", "data-schedule-alert"):
            with self.subTest(hidden=hidden):
                self.assertNotContains(tab, hidden)
        url = reverse("projects:schedule_task", args=[self.project.pk, concrete.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"action": "progress", "progress": "50"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("projects:schedule", args=[self.project.pk]), {"name": "بند", "start_date": DAY.isoformat(), "end_date": DAY.isoformat()}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("projects:detail", args=[self.project.pk])), "data-schedule-alert")
        self.assertEqual(ProjectTask.objects.filter(project=self.project).count(), 1)
        self.assertEqual(ProjectTask.objects.get(pk=concrete.pk).progress, 0)


# ---- the scanners ----

class ScannerTests(TestCase):
    TEMPLATES = ("schedule.html", "schedule_task.html", "_task_fields.html")

    def test_the_stylesheet_uses_tokens_only_and_every_token_is_defined(self):
        css = Path(settings.BASE_DIR, "static/hesba/css/projects_schedule.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(css, r"rgba?\(")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(used - defined, set())

    def test_no_template_pre_fills_a_data_label_and_each_pins_its_stylesheets(self):
        folder = Path(settings.BASE_DIR, "templates/projects")
        for name in self.TEMPLATES:
            source = (folder / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(source, PRE_LABELLED)
                if name != "_task_fields.html":
                    self.assertIn("projects.css' %}?v=c1-vo1", source)
                    self.assertIn("projects_schedule.css' %}?v=c3-1", source)

    def test_the_words_never_use_the_banned_terms(self):
        from .views_schedule import WORDS

        texts = [Path(settings.BASE_DIR, "templates/projects", name).read_text(encoding="utf-8") for name in self.TEMPLATES]
        texts.append(Path(settings.BASE_DIR, "projects/schedule.py").read_text(encoding="utf-8"))
        texts += [str(WORDS["ar"]), str(WORDS["en"])]
        for word in BANNED:
            for text in texts:
                with self.subTest(word=word):
                    self.assertNotIn(word, text)


class MigrationTests(TransactionTestCase):
    """0005 adds one table after 0004; a project and its bill of quantities are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_follows_0004_and_adds_one_table_without_touching_data(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("projects", "0005_schedule")]
        self.assertIn(("projects", "0004_variation_orders"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["CreateModel"])  # no data migration, no column on an existing table
        others = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "projects"]
        executor.migrate([("projects", "0004_variation_orders")])
        old = executor.loader.project_state(others + [("projects", "0004_variation_orders")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_c3")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="مدارس الأمل الخاصة")
        project = old.get_model("projects", "Project").objects.create(code="P-0001", name="مدرسة الأمل", customer=customer, created_by=user, contract_value=1000000)
        old.get_model("projects", "BoqLine").objects.create(project=project, line_number=1, code="A", description="خرسانة مسلحة", unit="م³", quantity="400", rate="1500", created_by=user)
        self.assertNotIn("projects_projecttask", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("projects", "0005_schedule")])
        self.assertIn("projects_projecttask", connection.introspection.table_names())
        migrated = Project.objects.get()
        self.assertEqual((migrated.code, migrated.contract_value), ("P-0001", D("1000000.00")))
        self.assertEqual(list(migrated.boq.values_list("code", "quantity", "rate")), [("A", D("400.000"), D("1500.00"))])
        self.assertEqual(ProjectTask.objects.count(), 0)
        self.assertFalse(schedule.has_tasks(migrated))
