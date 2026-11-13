"""Contracting C2a / C2b (HG-095): the screens in Arabic and English, the template and stylesheet scanners, the migration on a
database that already holds data, and the demo's documents and reports."""

import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from attachments import services as attachments
from attachments.models import Attachment
from attachments.tests import JPEG, PDF
from permissions.models import RoleCode
from settings_core.templatetags.hesba_format import day_text

from . import demo, documents, site_reports
from .models import Project, SiteReport
from .tests_contract import person
from .tests_documents import C2Setup, footprint
from .tests_site_reports import ReportSetup, visible_text

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
#: A cell with its label already written (the table-cards script writes it at load; a template never does).
PRE_LABELLED = re.compile(r"<t[dh]\b[^>]*\bdata-label\b")
LATIN_START = re.compile(r"^[A-Za-z]")
TEMPLATES = ("documents.html", "reports.html", "report_form.html", "report_day.html", "report_print.html", "_file_row.html", "_site_cta.html", "_tabs.html", "detail.html")


def strip_inputs(html):
    """The page without its form controls: a date input carries an ISO value by design; the text around it must not."""

    return re.sub(r"<(input|select|option|textarea)\b[^>]*>", "", html)


def sentences(html):
    """The text of the paragraphs, small print, legends, labels, headings and messages: what a reader reads as a sentence."""

    found = []
    for tag in ("p", "small", "legend", "label", "h1", "h2", "h3", "button"):
        for body in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, flags=re.S):
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
            if text:
                found.append(text)
    return found


class ScreenTests(ReportSetup):
    def setUp(self):
        super().setUp()
        self.add_document(self.engineer, title="رسم الدور الأول", stage=documents.KIND_STAGE["drawing"])
        self.add_document(self.engineer, raw=JPEG, filename="a.jpg", mime="image/jpeg", stage="photo", title="صورة الأساسات")
        self.report, _ = self.save(weather="rain", delayed="المطر أوقف الصب", materials="أسمنت", visitors="الاستشاري", equipment=[("خلاطة", "2")])
        self.add_photo(self.report)
        self.client.force_login(self.engineer)

    def screens(self, lang):
        return {"documents": self.url("documents", lang=lang), "reports": self.url("diary", lang=lang), "form": self.url("diary_new", lang=lang) + "&date=" + (TODAY_MINUS(3)).isoformat(),
                "edit": self.url("diary_edit", self.report.pk, lang=lang), "report": self.url("diary_day", self.report.pk, lang=lang), "print": self.url("diary_print", self.report.pk, lang=lang)}

    def test_every_screen_opens_in_arabic_rtl_and_in_english_ltr(self):
        for lang, direction, marker in (("ar", "rtl", "المستندات"), ("en", "ltr", "Documents")):
            for name, url in self.screens(lang).items():
                with self.subTest(lang=lang, screen=name):
                    page = self.client.get(url)
                    self.assertEqual(page.status_code, 200)
                    self.assertContains(page, f'dir="{direction}"')
                    if name != "print":
                        self.assertContains(page, marker)  # the tab, in the page's language

    def test_the_arabic_pages_carry_no_english_sentence_and_no_arabic_leaks_into_the_english_ones(self):
        for name, url in self.screens("ar").items():
            html = self.client.get(url).content.decode()
            html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
            from printing.company import company_details

            # the shell's language switch, the project's code (a code, not a sentence) and the company's name are data, not this page's words
            data = (self.project.code, "English", company_details()["name"])
            for text in sentences(html):
                if text.startswith(data):
                    continue
                with self.subTest(screen=name, text=text[:40]):
                    self.assertFalse(LATIN_START.match(text), text)  # never an Arabic sentence that opens with an English word
        for name, url in self.screens("en").items():
            visible = visible_text(re.sub(r"<bdi>.*?</bdi>", " ", self.client.get(url).content.decode(), flags=re.S))
            with self.subTest(screen=name):
                own = re.sub(r"[ء-ي]+", "", visible)  # user-typed Arabic (names, texts) is data; the page's own words must be English
                self.assertEqual(own.count("المستندات"), 0)
                self.assertNotIn("اليوميات", visible)
                self.assertNotIn("تقرير النهارده", visible)

    def test_dates_are_in_words_not_iso_on_the_documents_the_list_the_report_and_the_print(self):
        for name in ("documents", "reports", "report", "print"):
            html = self.client.get(self.screens("ar")[name]).content.decode()
            with self.subTest(screen=name):
                self.assertNotRegex(visible_text(strip_inputs(html)), ISO)
        self.assertIn(day_text(timezone.localdate(), "ar"), visible_text(self.client.get(self.screens("ar")["report"]).content.decode()))
        self.assertIn(day_text(timezone.localdate(), "en"), visible_text(self.client.get(self.screens("en")["report"]).content.decode()))

    def test_controls_carry_the_shared_row_roles_and_no_pre_written_labels(self):
        for name in ("reports", "documents", "report"):
            html = self.client.get(self.screens("ar")[name]).content.decode()
            self.assertNotRegex(html, PRE_LABELLED)
        listing = self.client.get(self.screens("ar")["reports"]).content.decode()
        self.assertIn("data-list-rows", listing)
        for role in ("title", "sub", "meta", "action"):
            self.assertIn(f'data-row="{role}"', listing)

    def test_the_form_error_is_announced_and_the_fields_have_labels(self):
        response = self.client.post(self.url("diary_new"), self.post_data(day=timezone.localdate() - timedelta(days=9), work_done="", delayed=""))
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, site_reports.MESSAGES["ar"]["empty"])
        html = response.content.decode()
        for field in ("sr-date", "sr-work", "sr-delayed", "sr-materials", "sr-visitors"):
            self.assertIn(f'for="{field}"', html)
            self.assertIn(f'id="{field}"', html)

    def test_the_phone_layout_hooks_exist_thumb_bar_sticky_save_and_one_row_of_tabs(self):
        overview = self.client.get(self.url("detail")).content.decode()
        self.assertIn('data-site-cta="bar"', overview)
        self.assertIn("data-project-tabs", overview)
        form = self.client.get(self.screens("ar")["form"]).content.decode()
        self.assertIn("hs-sticky-save", form)  # the save row sticks above the floating tab bar (form_controls.css)
        css = Path(settings.BASE_DIR, "static/hesba/css/projects.css").read_text(encoding="utf-8")
        self.assertIn("--hs-tabbar-clear", css)
        self.assertIn("[data-project-tabs]", css)

    def test_the_documents_card_names_every_control_for_a_phone(self):
        html = self.client.get(self.screens("ar")["documents"]).content.decode()
        self.assertIn('capture="environment"', html)  # the phone's camera
        self.assertIn('accept="image/*,application/pdf"', html)  # its files
        self.assertEqual(html.count('name="stage"'), 6)  # the six kinds
        self.assertIn('name="title"', html)
        self.assertIn("data-doc-space", html)


def TODAY_MINUS(days):
    return timezone.localdate() - timedelta(days=days)


class ScannerTests(TestCase):
    def test_no_template_pre_fills_a_data_label(self):
        for name in TEMPLATES:
            source = Path(settings.BASE_DIR, "templates/projects", name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(source, PRE_LABELLED)
                self.assertNotIn("data-label", source)  # not even in a comment: the table-cards script writes them

    def test_the_stylesheet_uses_tokens_only_and_every_token_is_defined(self):
        css = Path(settings.BASE_DIR, "static/hesba/css/projects.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(css, r"rgba?\(")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
        self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_the_templates_are_versioned_and_load_the_shared_layers(self):
        for name, needles in (("documents.html", ("projects.css' %}?v=c2-1", "attachments.css' %}?v=c2-1", "attachments.js' %}?v=c2-1")),
                              ("reports.html", ("projects.css' %}?v=c2-1",)), ("report_form.html", ("projects.css' %}?v=c2-1", "attachments.css' %}?v=c2-1", "site_report.js' %}?v=c2-1")),
                              ("report_day.html", ("projects.css' %}?v=c2-1", "attachments.js' %}?v=c2-1")), ("report_print.html", ("print.css' %}?v=print-004", "projects.css' %}?v=c2-1"))):
            source = Path(settings.BASE_DIR, "templates/projects", name).read_text(encoding="utf-8")
            for needle in needles:
                with self.subTest(template=name, needle=needle):
                    self.assertIn(needle, source)

    def test_the_new_scripts_use_no_inline_colour(self):
        for name in ("site_report.js",):
            self.assertNotRegex(Path(settings.BASE_DIR, "static/hesba/js", name).read_text(encoding="utf-8"), r"#[0-9a-fA-F]{3,8}\b")


class MigrationTests(TransactionTestCase):
    """0003 only adds two tables, after 0002; projects and a bill of quantities that exist are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0002_and_adds_two_tables_without_touching_data(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("projects", "0003_site_reports")]
        self.assertIn(("projects", "0002_contract_boq_certificates"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["CreateModel", "CreateModel", "AddConstraint"])
        executor.migrate([("projects", "0002_contract_boq_certificates")])
        old = executor.loader.project_state([n for n in executor.loader.graph.leaf_nodes() if n[0] != "projects"] + [("projects", "0002_contract_boq_certificates")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_c2")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="شركة النور")
        project = old.get_model("projects", "Project").objects.create(code="P-0001", name="عمارة", customer=customer, created_by=user, contract_value=100000)
        old.get_model("projects", "BoqLine").objects.create(project=project, line_number=1, description="خرسانة", unit="م³", quantity="10", rate="1000", created_by=user)
        names = connection.introspection.table_names()
        self.assertNotIn("projects_sitereport", names)
        self.assertNotIn("projects_sitereportrow", names)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("projects", "0003_site_reports")])
        names = connection.introspection.table_names()
        self.assertIn("projects_sitereport", names)
        self.assertIn("projects_sitereportrow", names)
        self.assertEqual(list(Project.objects.values_list("code", "name")), [("P-0001", "عمارة")])
        self.assertEqual([(line.line_number, str(line.quantity), str(line.rate)) for line in Project.objects.get().boq.all()], [(1, "10.000", "1000.00")])
        self.assertEqual(SiteReport.objects.count(), 0)  # nothing is seeded by the migration


class MakeMigrationsCleanTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_the_two_tables_are_the_only_change_to_the_schema_and_attachments_needs_none(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)  # no pending change anywhere, attachments included
        self.assertEqual(sorted(p.name for p in Path(settings.BASE_DIR, "attachments/migrations").glob("0*.py")), ["0001_initial.py", "0002_medical_categories.py"])


class DemoTests(TestCase):
    """The contracting demo built the way ``prepare_demo`` builds it: each project has three documents and four days of reports."""

    @classmethod
    def build(cls):
        from reports.tests_demo_catalogs import SampleBusinessPerActivityTests

        SampleBusinessPerActivityTests("fill").fill("contracting", "general")

    def setUp(self):
        self.build()
        from django.contrib.auth import get_user_model

        self.owner = get_user_model().objects.get(username="owner")

    def test_each_project_has_three_documents_and_four_days_of_reports_one_of_them_delayed(self):
        today = timezone.localdate()
        self.assertEqual(Project.objects.count(), 2)  # the demo's count is unchanged
        for project in Project.objects.all():
            rows = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(Project), object_id=project.pk)
            self.assertEqual(sorted(documents.STAGE_KIND[row.stage] for row in rows), ["contract", "drawing", "photo"])
            reports = site_reports.reports_for(project)
            self.assertEqual(len(reports), 4)
            self.assertEqual([report.report_date for report in reports], [today - timedelta(days=n) for n in (1, 2, 3, 4)])  # newest first, none today: today's is still to write
            self.assertEqual([bool(report.delayed) for report in reports].count(True), 1)
            self.assertTrue(all(report.workers for report in reports))
            photos = site_reports.photo_counts(reports)
            self.assertEqual(sum(photos.values()), 3)

    def test_the_demo_files_are_real_files_the_attachments_checks_accept(self):
        self.assertEqual(attachments.sniff(demo._pdf("x")), "application/pdf")
        self.assertTrue(attachments._valid_png(demo._scene(3)))
        self.assertLess(len(demo._scene(3)), 60 * 1024)
        for row in Attachment.objects.defer("data"):
            self.assertIn(row.content_type_name, ("application/pdf", "image/png"))
            self.assertLess(row.size, 60 * 1024)

    def test_seeding_again_adds_nothing_and_moves_no_money(self):
        before, count = footprint(), Attachment.objects.count()
        self.assertEqual(demo.seed(list(Project.objects.all()), self.owner), 0)
        self.assertEqual((footprint(), Attachment.objects.count()), (before, count))
        from ledger.reports import reconciliation

        result = reconciliation()
        self.assertTrue(result["balanced"] and result["ok"], result["rows"])

    def test_the_demos_tabs_open_for_the_owner_and_for_the_site_engineer_in_both_languages(self):
        project = Project.objects.get(name__startswith="عمارة")
        report = SiteReport.objects.filter(project=project).exclude(delayed="").get()
        engineer = get_demo_engineer()
        for user in (self.owner, engineer):
            self.client.force_login(user)
            for lang in ("ar", "en"):
                for name, args in (("documents", ()), ("diary", ()), ("diary_day", (report.pk,)), ("diary_print", (report.pk,)), ("detail", ())):
                    with self.subTest(user=user.username, lang=lang, screen=name):
                        page = self.client.get(f"{reverse(f'projects:{name}', args=[project.pk, *args])}?lang={lang}")
                        self.assertEqual(page.status_code, 200)
        listing = self.client.get(reverse("projects:diary", args=[project.pk]) + "?lang=ar")
        self.assertContains(listing, "data-delay-mark", count=1)
        self.assertContains(listing, "data-today-report")  # today's report is still to write


def get_demo_engineer():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(username="engineer").first() or person(RoleCode.SITE_ENGINEER, "demo_engineer")
