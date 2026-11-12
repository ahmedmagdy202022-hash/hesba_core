"""M9 (HG-092): the sample screens, in Arabic and English, who may open and post what, the lab entity, the template scanners,
and the migration on a database that already holds lab tests.
"""

import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog
from barcode.symbology import code128_modules
from barcode.tests import decode_code128
from hesba_testing.factories import grant, make_customer, make_role, make_user, make_user_profile
from permissions.models import Permission
from sales.models import SalesInvoice
from settings_core.templatetags.hesba_format import day_text

from . import lab, lab_import, public, samples, services
from .models import LabResult, LabTest
from .tests_inpatient import footprint
from .tests_m9 import LabBench, modules_of

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
PRE_LABELLED = re.compile(r"<t[dh]\b[^>]*\bdata-label\b")
BASE = "https://lab.example.test"


def strip_inputs(html):
    """The words on the page: no tags at all (a date in a link or a form control is ISO by design; the text a person reads must not be)."""

    return re.sub(r"<[^>]+>", " ", html)


class Screens(LabBench):
    def setUp(self):
        super().setUp()
        self.viewer = make_user(username="m9_viewer")
        role = make_role(code="M9-READER")
        grant(role, Permission.objects.get(code=services.VIEW))  # may read patient records, not record anything
        make_user_profile(user=self.viewer, role=role)
        self.nobody = make_user(username="m9_nobody")  # a login with no role at all
        self.client.force_login(self.owner)

    def url(self, name, *args, lang="ar", **query):
        extra = "".join(f"&{key}={value}" for key, value in query.items())
        return f"{reverse(name, args=args)}?lang={lang}{extra}"

    def post(self, name, data=None, user=None, lang="ar", args=()):
        if user is not None:
            self.client.force_login(user)
        return self.client.post(self.url(name, *args, lang=lang), {"lang": lang, **(data or {})})

    def readers(self):
        return (self.owner, self.manager, self.doctor, self.viewer)

    def writers(self):
        return (self.owner, self.manager, self.doctor)

    def refused(self):
        return (self.desk, self.keeper, self.nobody)

    def file_post(self, text, user=None, lang="ar", name="results.csv"):
        from .tests_m9 import csv_file

        if user is not None:
            self.client.force_login(user)
        return self.client.post(self.url("medical:lab_import", lang=lang), {"lang": lang, "action": "preview", "file": csv_file(text, name=name)})

    def payload(self, response):
        return re.search(r'name="payload" value="([^"]+)"', response.content.decode()).group(1)

    def good_file(self):
        """Two good lines for Mona, one for Sami (not ordered), one unknown code."""

        self.order(self.mona, self.cbc, self.sugar_item)
        mona = self.code(self.mona)
        return f"sample code,test code,value\n{mona},HB,17.5\n{mona},GLU,88\n{self.code(self.sami)},HB,14\n{mona},XXX,1\n"


# ---- the label --------------------------------------------------------------------------------


class LabelTests(Screens):
    def page(self, patient=None, day=None, lang="ar", **query):
        return self.client.get(self.url("medical:lab_label_day", (patient or self.mona).pk, (day or self.day).isoformat(), lang=lang, **query))

    def test_it_holds_the_barcode_the_name_the_day_and_the_tests_short_names(self):
        self.order(self.mona, self.cbc, self.sugar_item)
        lab.record_results(self.mona, self.day, {self.noshort: ("150", "")}, self.owner)
        page = self.page()
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        code = self.code(self.mona)
        self.assertContains(page, 'data-sample-label', count=2)  # two copies by default on the roll
        self.assertContains(page, f'data-sample-code>{code}<', count=2)
        self.assertContains(page, self.mona.name)
        self.assertContains(page, day_text(self.day, "ar"))
        tests = re.search(r"data-sample-tests>(.*?)</p>", body, re.S).group(1)
        for short in ("HB", "WBC", "GLU", "كوليسترول ال"):
            self.assertIn(f"<bdi>{short}</bdi>", tests)
        self.assertNotIn("BG", tests)  # not ordered, no result

    def test_its_bars_are_the_sample_codes_own_code128_and_read_back_as_the_code(self):
        svg = re.search(r'<svg[^>]*class="hs-barcode".*?</svg>', self.page().content.decode(), re.S).group(0)
        code = self.code(self.mona)
        self.assertEqual(modules_of(svg), code128_modules(code))
        self.assertEqual(decode_code128(modules_of(svg)), code)
        self.assertIn(f'data-symbology="code128"', svg)

    def test_there_is_no_phone_no_file_number_no_diagnosis_no_note_and_never_the_results_link(self):
        file = services.file_for(self.mona)
        services.save_profile(file, {"allergies": "ZZALLERGY", "chronic_conditions": "ZZCHRONIC", "current_medications": "ZZMEDICATION", "notes": "ZZFILENOTE"}, self.owner)
        services.save_visit(self.mona, {"complaint": "ZZCOMPLAINT", "diagnosis": "ZZDIAGNOSIS", "treatment": "ZZTREATMENT", "visit_date": self.day.isoformat()}, self.owner)
        lab.record_results(self.mona, self.day, {self.hb: ("17.5", ""), self.bg: ("", "ZZRESULT")}, self.owner)
        self.order(self.mona, self.cbc)
        with override_settings(PUBLIC_BASE_URL=BASE):
            link = public.make_link(self.mona, self.day, self.owner)
        token = link["url"].split("/results/", 1)[1].split("/", 1)[0]
        for lang in ("ar", "en"):
            body = self.page(lang=lang).content.decode()
            for private in (self.mona.phone, "01001234567", file.file_number, "P-0", "ZZALLERGY", "ZZCHRONIC", "ZZMEDICATION", "ZZFILENOTE", "ZZCOMPLAINT",
                            "ZZDIAGNOSIS", "ZZTREATMENT", "ZZRESULT", "17.5", token, token[:20], "/results/", "wa.me", "hs_lab_", BASE, self.mona.customer_code):
                with self.subTest(lang=lang, marker=private):
                    self.assertNotIn(private, body)
            self.assertNotIn("Set-Cookie", str(self.page(lang=lang).headers))

    def test_it_prints_before_any_result_exists_and_a_cancelled_order_is_not_on_it(self):
        self.order(self.mona, self.cbc)
        self.order(self.mona, self.sugar_item, status="cancelled")
        self.assertEqual(LabResult.objects.count(), 0)
        page = self.page()
        self.assertContains(page, "<bdi>HB</bdi>")
        self.assertNotContains(page, "GLU")
        empty = self.page(patient=self.hala)
        self.assertEqual(empty.status_code, 200)  # nothing recorded yet: the label still prints, with its own words
        self.assertContains(empty, "مفيش تحاليل مسجّلة")

    def test_many_tests_are_cut_with_a_count(self):
        for index in range(9):
            test = lab.save_test({"name": f"تحليل {index}", "code": f"T{index}"}, self.owner)
            lab.record_results(self.mona, self.day, {test: ("", "x")}, self.owner)
        page = self.page(copies=1)
        self.assertContains(page, "<bdi>+3</bdi>")  # six shown, three more

    def test_the_two_sizes_and_the_copies(self):
        roll = self.page().content.decode()
        self.assertIn('class="ls-body ls-roll"', roll)
        a4 = self.page(size="a4").content.decode()
        self.assertIn('class="ls-body ls-a4"', a4)
        self.assertEqual(a4.count("data-sample-label"), 8)
        self.assertEqual(self.page(copies=3).content.decode().count("data-sample-label"), 3)
        self.assertEqual(self.page(copies=999).content.decode().count("data-sample-label"), 40)
        self.assertEqual(self.page(copies="x").content.decode().count("data-sample-label"), 2)
        self.assertEqual(self.page(copies=0).content.decode().count("data-sample-label"), 1)

    def test_the_label_has_print_rules_for_both_sizes_in_its_stylesheet(self):
        css = Path(settings.BASE_DIR, "static/hesba/css/lab_label.css").read_text(encoding="utf-8")
        self.assertIn("@page roll{size:50mm 25mm;margin:0}", css)
        self.assertIn("width:50mm;height:25mm", css)
        self.assertIn("grid-template-columns:repeat(4,50mm)", css)
        self.assertIn("@media print", css)

    def test_the_day_may_come_from_the_query_for_a_day_with_no_results_yet(self):
        page = self.client.get(self.url("medical:lab_label", self.mona.pk, day=self.today.isoformat()))
        self.assertContains(page, self.code(self.mona, self.today))
        default = self.client.get(self.url("medical:lab_label", self.mona.pk))
        self.assertContains(default, self.code(self.mona, self.today))

    def test_english(self):
        self.order(self.mona, self.cbc)
        page = self.page(lang="en")
        self.assertContains(page, 'lang="en" dir="ltr"')
        self.assertContains(page, day_text(self.day, "en"))
        self.assertContains(page, "50×25 mm label")
        self.assertContains(page, "Print")
        self.assertNotRegex(strip_inputs(page.content.decode()), ISO)

    def test_a_bad_day_a_future_day_or_a_stranger_is_not_found(self):
        for day in ("2026-02-30", "x", "yesterday"):
            self.assertEqual(self.client.get(self.url("medical:lab_label_day", self.mona.pk, day)).status_code, 404, day)
        self.assertEqual(self.client.get(self.url("medical:lab_label_day", self.mona.pk, (self.today + timedelta(days=1)).isoformat())).status_code, 404)
        self.assertEqual(self.client.get(self.url("medical:lab_label", 999999)).status_code, 404)
        walk_in = make_customer(customer_code="WALK-IN", name="زبون عابر")
        self.assertEqual(self.client.get(self.url("medical:lab_label", walk_in.pk)).status_code, 404)

    def test_only_readers_of_lab_results_print_it_and_only_in_a_lab(self):
        url = self.url("medical:lab_label", self.mona.pk)
        for user in self.readers():
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 200, user.username)
        for user in self.refused():
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403, user.username)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        from settings_core.models import ClientProfile

        self.client.force_login(self.owner)
        ClientProfile.objects.update(sub_activity_slug="clinic")
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_printing_and_scanning_move_no_money(self):
        before = footprint()
        self.order(self.mona, self.cbc)
        invoices = SalesInvoice.objects.count()
        self.page()
        self.page(size="a4")
        self.client.get(self.url("medical:lab_sample", code=self.code(self.mona)), follow=True)  # the scan, and the page it opens
        self.assertEqual((footprint(), SalesInvoice.objects.count(), AuditLog.objects.filter(module="medical", action__icontains="label").count()), (before, invoices, 0))


# ---- finding a sample by its code ---------------------------------------------------------------


class SampleBoxTests(Screens):
    def open(self, code, **kwargs):
        return self.client.get(self.url("medical:lab_sample", code=code, **kwargs))

    def test_a_typed_or_scanned_code_opens_the_patients_results_of_that_day(self):
        lab.record_results(self.mona, self.day, {self.hb: ("17.5", "")}, self.owner)
        code = self.code(self.mona)
        for typed in (code, code.lower(), f" {code} ", code.replace("-", " - ")):
            response = self.open(typed.replace(" ", "%20"))
            self.assertEqual(response.status_code, 302, typed)
            self.assertEqual(response["Location"], f"{reverse('medical:lab', args=[self.mona.pk])}?lang=ar&day={self.day.isoformat()}#lab-day-{self.day.isoformat()}")
        page = self.client.get(self.open(code)["Location"])
        self.assertContains(page, f'data-lab-sample-opened="{code}"')
        self.assertContains(page, f"عيّنة \u2066{code}\u2069 · {day_text(self.day, 'ar')}")  # an Arabic sentence that starts in Arabic
        self.assertContains(page, f'name="taken_on" value="{self.day.isoformat()}"')  # the entry form is on that day
        self.assertContains(page, "data-lab-focus")
        self.assertNotContains(page, "مفيش نتايج متسجّلة في اليوم ده")

    def test_a_sample_with_no_results_yet_says_so_and_still_opens(self):
        page = self.client.get(self.open(self.code(self.hala, self.today))["Location"])
        self.assertContains(page, "مفيش نتايج متسجّلة في اليوم ده لسه")
        self.assertContains(page, f'name="taken_on" value="{self.today.isoformat()}"')

    def test_english_keeps_the_language(self):
        response = self.open(self.code(self.mona), lang="en")
        self.assertIn("lang=en", response["Location"])
        self.assertContains(self.client.get(response["Location"]), "No results are recorded for this day yet.")

    def test_a_code_that_names_nobody_is_not_found_and_shows_nothing_of_anyone(self):
        lab.record_results(self.mona, self.day, {self.hb: ("13.375", "")}, self.owner)
        bad = ("junk", "L999999-" + self.day.strftime("%Y%m%d"), self.code(self.mona, self.today + timedelta(days=3)), "L1-20261399", "Lx-20261112", "L12")
        for code in bad:
            with self.subTest(code=code):
                response = self.open(code)
                self.assertEqual(response.status_code, 404)
                self.assertContains(response, "data-lab-sample-error", status_code=404)
                self.assertContains(response, "مفيش عيّنة بالكود ده", status_code=404)
                self.assertNotContains(response, self.mona.name, status_code=404)
                self.assertNotContains(response, "13.375", status_code=404)

    def test_the_box_page_has_the_box_the_help_and_the_tabs(self):
        page = self.open("")
        self.assertEqual(page.status_code, 200)
        for marker in ("data-lab-sample-box", "data-camera-scan", "data-tab-lab-sample", "data-tab-lab-import", "data-lab-import-link", "lab_sample.js"):
            self.assertContains(page, marker)
        self.assertContains(page, "كود العيّنة")
        english = self.open("", lang="en")
        self.assertContains(english, "Sample code")
        self.assertContains(english, "Scan or type the code")
        self.assertNotRegex(strip_inputs(page.content.decode()), ISO)

    def test_who_may_open_it(self):
        for user in self.readers():
            self.client.force_login(user)
            self.assertEqual(self.open("").status_code, 200, user.username)
            self.assertEqual(self.open(self.code(self.mona)).status_code, 302, user.username)
        self.client.force_login(self.viewer)
        page = self.open("")
        self.assertNotContains(page, "data-lab-import-link")  # may read, may not import
        self.assertNotContains(page, "data-tab-lab-import")
        for user in self.refused():
            self.client.force_login(user)
            self.assertEqual(self.open(self.code(self.mona)).status_code, 403, user.username)
        self.client.logout()
        self.assertEqual(self.open("").status_code, 302)

    def test_another_entitys_lab_data_is_not_opened_from_a_non_lab_entity(self):
        from entities.current import SESSION_KEY
        from entities.services import save_entity

        branch = save_entity({"code": "CL2", "name_ar": "عيادة المعادي", "activity_slug": "medical", "sub_activity_slug": "clinic"}, self.owner)
        lab.record_results(self.mona, self.day, {self.hb: ("17.5", "")}, self.owner)
        session = self.client.session
        session[SESSION_KEY] = branch.pk
        session.save()
        code = self.code(self.mona)
        self.assertEqual(self.open(code).status_code, 404)
        self.assertEqual(self.client.get(self.url("medical:lab_label", self.mona.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("medical:lab_import")).status_code, 404)
        self.assertEqual(self.file_post(self.good_file()).status_code, 404)
        self.assertEqual(self.post("medical:lab_import", {"action": "apply", "payload": "x"}).status_code, 404)
        self.assertEqual(LabResult.objects.count(), 1)


# ---- the label and the box on the patient's page and the tests set-up -------------------------------


class PatientPageTests(Screens):
    def test_the_results_page_offers_the_label_for_a_day_and_for_each_day_with_results(self):
        lab.record_results(self.mona, self.day, {self.hb: ("17.5", "")}, self.owner)
        page = self.client.get(self.url("medical:lab", self.mona.pk))
        self.assertContains(page, "data-lab-label-card")
        self.assertContains(page, f'action="{reverse("medical:lab_label", args=[self.mona.pk])}"')
        self.assertContains(page, f'value="{self.today.isoformat()}" max="{self.today.isoformat()}"')
        self.assertContains(page, "اطبع ملصق العيّنة")
        self.assertContains(page, reverse("medical:lab_label_day", args=[self.mona.pk, self.day.isoformat()]))
        self.assertContains(page, "lab_sample.css")
        english = self.client.get(self.url("medical:lab", self.mona.pk, lang="en"))
        self.assertContains(english, "Print the sample label")
        self.assertContains(english, "Sample label")

    def test_a_reader_who_cannot_write_still_prints_the_label(self):
        self.client.force_login(self.viewer)
        page = self.client.get(self.url("medical:lab", self.mona.pk))
        self.assertContains(page, "data-lab-label-card")
        self.assertNotContains(page, "data-lab-entry")

    def test_the_tests_set_up_has_the_code_column_and_field_and_saves_it(self):
        page = self.client.get(self.url("medical:lab_tests"))
        self.assertContains(page, 'name="code"')
        self.assertContains(page, 'data-test-code')
        self.assertContains(page, "كود الجهاز")
        self.assertContains(page, "data-tab-lab-sample")
        response = self.post("medical:lab_tests", {"name": "كرياتينين", "code": "crea", "unit": "mg/dL", "low": "0.6", "high": "1.3", "active": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LabTest.objects.get(name="كرياتينين").code, "CREA")
        edit = self.client.get(self.url("medical:lab_tests", edit=LabTest.objects.get(name="كرياتينين").pk))
        self.assertContains(edit, 'value="CREA"')

    def test_a_taken_code_is_refused_with_the_other_tests_name_and_the_typed_values_stay(self):
        response = self.post("medical:lab_tests", {"name": "تحليل جديد", "code": "glu", "unit": "UNITX", "active": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "سكر صايم")
        self.assertContains(response, 'value="UNITX"')
        self.assertFalse(LabTest.objects.filter(name="تحليل جديد").exists())
        english = self.post("medical:lab_tests", {"name": "تحليل جديد", "code": "glu", "active": "1"}, lang="en")
        self.assertContains(english, "already used by")
        self.assertContains(self.client.get(self.url("medical:lab_tests", lang="en")), "Analyzer code")

    def test_a_reader_cannot_save_a_test_code(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.post("medical:lab_tests", {"name": "x", "code": "ZZ"}).status_code, 403)
        self.assertFalse(LabTest.objects.filter(code="ZZ").exists())


# ---- the import -----------------------------------------------------------------------------------


class ImportScreenTests(Screens):
    def test_the_upload_page_explains_the_file_in_both_languages(self):
        page = self.client.get(self.url("medical:lab_import"))
        self.assertEqual(page.status_code, 200)
        for marker in ("data-lab-import-form", 'type="file"', 'accept=".csv', "L12-20261112,GLU,5.4", "سطر العناوين اختياري", "data-tab-lab-import"):
            self.assertContains(page, marker)
        english = self.client.get(self.url("medical:lab_import", lang="en"))
        self.assertContains(english, "Three columns in this order")
        self.assertContains(english, "Show the preview")

    def test_the_preview_writes_nothing_and_shows_each_line_with_its_status(self):
        text = self.good_file()
        before = LabResult.objects.count()
        page = self.file_post(text)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(LabResult.objects.count(), before)
        self.assertFalse(AuditLog.objects.filter(action="import_lab_results").exists())
        for status, count in (("write", 2), ("not_ordered", 1), ("unknown_test", 1)):
            self.assertContains(page, f'data-lab-status-count="{status}"')
            self.assertEqual(len(re.findall(rf'data-lab-line-status="{status}"', page.content.decode())), count, status)
        self.assertContains(page, self.mona.name)
        self.assertContains(page, "هيتكتب")
        self.assertContains(page, "مفيش تحاليل مطلوبة للمريض في اليوم ده")
        self.assertContains(page, "data-lab-import-apply")
        self.assertContains(page, "اكتب 2 نتيجة")
        self.assertContains(page, "results.csv")
        self.assertNotRegex(strip_inputs(page.content.decode()), ISO)

    def test_the_preview_in_english_and_each_remaining_status_has_its_words(self):
        self.order(self.mona, self.cbc)
        mona = self.code(self.mona)
        lab.record_results(self.hala, self.day, {self.hb: ("14", ""), self.wbc: ("5", "")}, self.owner)
        hala = self.code(self.hala)
        text = (f"{mona},HB,13\n{mona},HB,13\n{mona},WBC,abc\n{hala},HB,14\n{hala},WBC,6\nL999999-{self.day:%Y%m%d},HB,1\n{mona},WBC\n"
                f"{self.code(self.mona, self.today + timedelta(days=1))},HB,1\n")
        body = self.file_post(text, lang="en").content.decode()
        for words in ("Will be written", "Duplicate line", "Value is not a number", "Already entered, same value", "Already entered, different value", "Unknown sample",
                      "Incomplete line", "Entered: 5; the file has 6. It will not be changed.", "Same as line 1.", "The sample date has not come yet.",
                      "This test has a normal range, so its value must be a number."):
            with self.subTest(words=words):
                self.assertIn(words, body)

    def test_apply_writes_the_good_lines_reports_them_and_a_second_apply_writes_nothing(self):
        page = self.file_post(self.good_file())
        token = self.payload(page)
        money = footprint()
        result = self.post("medical:lab_import", {"action": "apply", "payload": token})
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, 'data-lab-import-written="2"')
        self.assertContains(result, "اتكتبت 2 نتيجة")
        self.assertContains(result, "اتكتب")
        self.assertContains(result, "واتساب 2 سطر من غير ما يتكتب")
        self.assertEqual(sorted(LabResult.objects.values_list("test__code", "value", "flag")), [("GLU", 88, "normal"), ("HB", 17.5, "high")])
        self.assertEqual(footprint(), money)
        self.assertEqual(AuditLog.objects.filter(action="import_lab_results").count(), 1)
        again = self.post("medical:lab_import", {"action": "apply", "payload": token})
        self.assertEqual(again.status_code, 200)
        self.assertContains(again, "ماتكتبتش أي نتيجة")
        self.assertEqual(LabResult.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="import_lab_results").count(), 1)
        self.assertContains(again, "متسجّل بنفس القيمة")

    def test_the_result_in_english(self):
        token = self.payload(self.file_post(self.good_file(), lang="en"))
        result = self.post("medical:lab_import", {"action": "apply", "payload": token}, lang="en")
        self.assertContains(result, "2 results written")
        self.assertContains(result, "stops working")
        self.assertContains(result, "Upload another file")

    def test_a_preview_with_nothing_good_offers_no_apply(self):
        page = self.file_post(f"{self.code(self.mona)},XXX,1\n")
        self.assertContains(page, "data-lab-import-nothing")
        self.assertNotContains(page, "data-lab-import-apply")

    def test_a_long_file_lists_the_problems_first_up_to_the_cap_but_counts_it_all(self):
        self.order(self.mona, self.cbc)
        mona = self.code(self.mona)
        text = f"{mona},HB,13\n{mona},WBC,5\n{self.code(self.sami)},HB,1\n"
        with patch.object(lab_import, "SHOWN_ROWS", 2):
            page = self.file_post(text)
        body = page.content.decode()
        self.assertContains(page, "data-lab-import-capped")
        self.assertEqual(body.count("data-lab-line-status="), 2)
        self.assertIn('data-lab-line-status="not_ordered"', body)  # the problem is listed before the good lines
        self.assertContains(page, 'data-lab-status-count="write"')

    def test_a_file_that_cannot_be_read_gives_a_clear_message_and_changes_nothing(self):
        cases = (("a,b,c\n", "results.xlsx", "CSV"), ("", "results.csv", "فاضي"), ("sample code,test code,value\n", "results.csv", "فاضي"))
        for text, name, message in cases:
            with self.subTest(name=name, message=message):
                page = self.file_post(text, name=name)
                self.assertEqual(page.status_code, 400)
                self.assertContains(page, "data-lab-import-error", status_code=400)
                self.assertContains(page, message, status_code=400)
        with patch.object(lab_import, "MAX_BYTES", 10):
            page = self.file_post("L1-20261112,GLU,5.4\n" * 2)
        self.assertContains(page, "أكبر من 5 ميجا", status_code=400)
        none = self.client.post(self.url("medical:lab_import"), {"lang": "ar", "action": "preview"})
        self.assertContains(none, "اختار ملف", status_code=400)
        self.assertEqual(LabResult.objects.count(), 0)

    def test_a_preview_that_is_forged_stale_or_someone_elses_writes_nothing(self):
        token = self.payload(self.file_post(self.good_file()))
        for bad in ("", "garbage", token[:-4] + "AAAA"):
            with self.subTest(token=bad[-6:]):
                response = self.post("medical:lab_import", {"action": "apply", "payload": bad})
                self.assertContains(response, "قديمة أو مش بتاعتك", status_code=400)
        self.client.force_login(self.manager)
        self.assertContains(self.post("medical:lab_import", {"action": "apply", "payload": token}), "قديمة", status_code=400)
        with patch.object(lab_import, "PAYLOAD_SECONDS", -1):
            self.client.force_login(self.owner)
            self.assertContains(self.post("medical:lab_import", {"action": "apply", "payload": token}), "قديمة", status_code=400)
        self.assertEqual(LabResult.objects.count(), 0)

    def test_a_signed_preview_cannot_be_used_after_the_makers_permission_is_gone(self):
        token = self.payload(self.file_post(self.good_file(), user=self.doctor))
        for user in self.refused() + (self.viewer,):
            self.client.force_login(user)
            self.assertEqual(self.post("medical:lab_import", {"action": "apply", "payload": token}).status_code, 403, user.username)
        self.assertEqual(LabResult.objects.count(), 0)

    def test_who_may_open_preview_and_apply(self):
        url = self.url("medical:lab_import")
        for user in self.writers():
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 200, user.username)
        for user in self.refused() + (self.viewer,):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403, user.username)
            self.assertEqual(self.file_post(self.good_file()).status_code, 403, user.username)
            self.assertEqual(self.post("medical:lab_import", {"action": "apply", "payload": "x"}).status_code, 403, user.username)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.file_post(self.good_file()).status_code, 302)
        self.assertEqual(LabResult.objects.count(), 0)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.put(url).status_code, 405)

    def test_a_non_lab_business_has_none_of_it(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(sub_activity_slug="clinic")
        self.assertEqual(self.client.get(self.url("medical:lab_import")).status_code, 404)
        self.assertEqual(self.client.get(self.url("medical:lab_sample")).status_code, 404)
        self.assertNotContains(self.client.get(self.url("medical:patients")), "data-tab-lab-sample")
        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.assertEqual(self.client.get(self.url("medical:lab_import")).status_code, 404)

    def test_an_order_made_in_another_entity_does_not_count_here(self):
        from entities.current import SESSION_KEY
        from entities.services import main_entity, save_entity
        from hesba_testing.factories import make_location

        branch = save_entity({"code": "LAB2", "name_ar": "فرع المعادي", "activity_slug": "medical", "sub_activity_slug": "lab"}, self.owner)
        elsewhere = make_location(location_code="LAB2L", is_selling_location=True, entity=branch)
        self.order(self.mona, self.cbc, location=elsewhere)
        text = f"{self.code(self.mona)},HB,13\n"
        session = self.client.session
        session[SESSION_KEY] = main_entity().pk
        session.save()
        page = self.file_post(text)
        self.assertContains(page, 'data-lab-line-status="not_ordered"')
        self.assertNotContains(page, "data-lab-import-apply")
        session[SESSION_KEY] = branch.pk
        session.save()
        self.assertContains(self.file_post(text), 'data-lab-line-status="write"')

    def test_preview_and_apply_through_the_screens_move_no_money(self):
        money, invoices = footprint(), SalesInvoice.objects.count()
        text = self.good_file()
        invoices += 1  # the order the file's patient was billed on
        token = self.payload(self.file_post(text))
        self.post("medical:lab_import", {"action": "apply", "payload": token})
        self.assertEqual((footprint(), SalesInvoice.objects.count()), (money, invoices))


# ---- the scanners ---------------------------------------------------------------------------------------


class ScannerTests(TestCase):
    TEMPLATES = ("lab_sample.html", "lab_import.html", "lab_label.html", "lab_results.html", "lab_tests.html")

    def test_no_template_pre_fills_a_data_label(self):
        for name in self.TEMPLATES:
            source = Path(settings.BASE_DIR, "templates/medical", name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(source, PRE_LABELLED)
                self.assertNotIn("data-label=", source)

    def test_the_import_list_uses_the_shared_row_roles_and_the_stylesheets_are_versioned(self):
        source = Path(settings.BASE_DIR, "templates/medical/lab_import.html").read_text(encoding="utf-8")
        self.assertIn("data-list-rows", source)
        for role in ("title", "sub", "meta", "empty"):
            self.assertIn(f'data-row="{role}"', source)
        for name, sheet in (("lab_import.html", "lab_sample.css"), ("lab_sample.html", "lab_sample.css"), ("lab_results.html", "lab_sample.css"), ("lab_label.html", "lab_label.css")):
            self.assertIn(f"{sheet}' %}}?v=m9-1", Path(settings.BASE_DIR, "templates/medical", name).read_text(encoding="utf-8"), name)

    def test_the_stylesheets_use_tokens_only_and_every_token_is_defined(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        for sheet in ("lab_sample.css", "lab_label.css"):
            css = Path(settings.BASE_DIR, "static/hesba/css", sheet).read_text(encoding="utf-8")
            with self.subTest(sheet=sheet):
                self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
                self.assertNotRegex(css, r"rgba?\(")
                used = set(re.findall(r"var\((--hs-[\w-]+)", css))
                self.assertTrue(used)
                self.assertEqual(sorted(used - defined), [])
                self.assertNotRegex(css, r"font-weight:\s*[89]00")


# ---- the migration on a database that already holds lab tests -------------------------------------------------


class MigrationTests(TransactionTestCase):
    """0015 only adds the code column and its uniqueness, after 0014; the tests and results that exist are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0014_and_adds_one_column_and_one_constraint_without_touching_data(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("medical", "0015_lab_test_code")]
        self.assertIn(("medical", "0014_vaccinations"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["AddField", "AddConstraint"])
        executor.migrate([("medical", "0014_vaccinations")])
        old = executor.loader.project_state([n for n in executor.loader.graph.leaf_nodes() if n[0] != "medical"] + [("medical", "0014_vaccinations")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_m9")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="منى")
        test = old.get_model("medical", "LabTest").objects.create(name="سكر صايم", unit="mg/dL", low=70, high=100)
        old.get_model("medical", "LabTest").objects.create(name="فصيلة الدم")
        old.get_model("medical", "LabResult").objects.create(patient=customer, test=test, taken_on=timezone_today(), value=88, flag="normal", entered_by=user)
        columns = [column.name for column in connection.introspection.get_table_description(connection.cursor(), "medical_labtest")]
        self.assertNotIn("code", columns)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("medical", "0015_lab_test_code")])
        self.assertEqual(sorted(LabTest.objects.values_list("name", "code", "unit")), [("سكر صايم", "", "mg/dL"), ("فصيلة الدم", "", "")])  # every old test: no code
        self.assertEqual(LabResult.objects.get().value, 88)
        LabTest.objects.create(name="بلا كود جديد")  # many empty codes
        LabTest.objects.create(name="GLU", code="GLU")
        with self.assertRaises(IntegrityError), transaction.atomic():
            LabTest.objects.create(name="GLU 2", code="GLU")


def timezone_today():
    from django.utils import timezone

    return timezone.localdate()


class MakeMigrationsCleanTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_the_code_column_is_the_only_change_to_the_schema(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)


class DemoSeedTests(TestCase):
    def test_the_lab_demo_has_two_coded_tests_and_a_printable_label(self):
        from django.contrib.auth import get_user_model

        from settings_core.tests_demo_clinic import DemoClinicBookingTests

        DemoClinicBookingTests("fill").fill("medical", "lab")
        self.assertEqual(sorted(LabTest.objects.exclude(code="").values_list("name", "code")), [("سكر صايم", "GLU"), ("هيموجلوبين", "HB")])
        owner = get_user_model().objects.get(username="owner")
        result = LabResult.objects.order_by("-taken_on").first()
        self.client.force_login(owner)
        page = self.client.get(reverse("medical:lab_label_day", args=[result.patient_id, result.taken_on.isoformat()]) + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<bdi>HB</bdi>")
        self.assertContains(page, "<bdi>GLU</bdi>")
        self.assertContains(page, samples.sample_code(result.patient, result.taken_on))
        self.assertEqual(self.client.get(reverse("medical:lab_sample") + "?lang=ar&code=" + samples.sample_code(result.patient, result.taken_on)).status_code, 302)
