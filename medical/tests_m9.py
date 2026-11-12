"""M9 (HG-092): the lab's sample code and label, and importing results from an analyzer's file.

What these pin: the analyzer code of a test (upper case, unique when set); the sample code (what it holds and what it never
holds, and that its barcode decodes back to it); the label (name, day, tests, nothing private, never M6's link); finding a
sample by its code; every status of an import line; that applying writes only the good lines through the typed path (same flag
as typing) in one transaction, once (the same file again changes nothing), never over a typed result and never for a test that
was not ordered; who may do what; the lab entity; and that nothing financial moves.

"Today" is ``timezone.localdate()`` and every other day is built from it.
"""

import re
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from audit.models import AuditLog
from barcode.symbology import code128_modules
from barcode.tests import decode_code128
from cashboxes.models import CashboxMovement
from hesba_testing.factories import add_sales_line, make_customer, make_draft_sales_invoice, make_item, make_location
from inventory.models import StockMovement
from ledger.models import JournalEntry
from master_data.models import Customer
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice

from . import lab, lab_import, public, samples, services
from .lab_import import BAD_ROW, DIFFERS, DUPLICATE, NOT_NUMBER, NOT_ORDERED, SAME, UNKNOWN_SAMPLE, UNKNOWN_TEST, WRITE
from .models import LabResult, LabTest, PatientFile
from .tests import CLINIC, person
from .tests_inpatient import footprint
from .tests_lab_link import token_of


def csv_file(text, name="results.csv", encoding="utf-8"):
    return SimpleUploadedFile(name, text.encode(encoding) if isinstance(text, str) else text, content_type="text/csv")


class LabBench(TestCase):
    """A lab: three tests with analyzer codes (two of them billed as services, one a word with no range), patients, staff of every kind."""

    def setUp(self):
        prepared_client("medical", "lab", CLINIC)
        self.today = timezone.localdate()
        self.day = self.today - timedelta(days=1)
        self.location = make_location(location_code="LAB", is_default=True, is_selling_location=True)
        self.owner = person(RoleCode.OWNER, "m9_owner")
        self.manager = person(RoleCode.MANAGER, "m9_manager")
        self.doctor = person(RoleCode.DOCTOR, "m9_doctor")
        self.desk = person(RoleCode.CASHIER, "m9_desk")
        self.keeper = person(RoleCode.STOCK_KEEPER, "m9_keeper")
        self.cbc = make_item(item_code="SRV-CBC", item_name="صورة دم كاملة", is_stock_tracked=False, default_sale_price="150.00")
        self.sugar_item = make_item(item_code="SRV-GLU", item_name="سكر صايم", is_stock_tracked=False, default_sale_price="60.00")
        self.hb = lab.save_test({"name": "هيموجلوبين", "code": "hb", "unit": "g/dL", "low": "12", "high": "16", "service": self.cbc}, self.owner)
        self.wbc = lab.save_test({"name": "كرات دم بيضاء", "code": "WBC", "unit": "×10³/µL", "low": "4", "high": "11", "service": self.cbc}, self.owner)
        self.glu = lab.save_test({"name": "سكر صايم", "code": "GLU", "unit": "mg/dL", "low": "70", "high": "100", "service": self.sugar_item}, self.owner)
        self.bg = lab.save_test({"name": "فصيلة الدم", "code": "BG"}, self.owner)  # a word: no range, no service
        self.noshort = lab.save_test({"name": "كوليسترول الدم الكلي", "unit": "mg/dL", "low": "0", "high": "200"}, self.owner)  # no analyzer code
        self.mona = self.patient("M9-1", "منى عبد الله", "01001234567")
        self.sami = self.patient("M9-2", "سامي فريد", "01112223334")
        self.hala = self.patient("M9-3", "هالة نبيل", "")
        self.invoices = 0

    def patient(self, code, name, phone):
        patient = make_customer(customer_code=code, name=name, phone=phone)
        services.file_for(patient)
        return patient

    def order(self, patient, *items, day=None, status="draft", location=None):
        """A sales invoice with the lab services as its lines (the lab's order)."""

        self.invoices += 1
        invoice = make_draft_sales_invoice(customer=patient, location=location or self.location, invoice_number=f"M9-SI-{self.invoices}",
                                           invoice_date=day or self.day)
        for item in items:
            add_sales_line(invoice, item, 1, item.default_sale_price)
        if status != "draft":
            SalesInvoice.objects.filter(pk=invoice.pk).update(status=status)
        return invoice

    def code(self, patient, day=None):
        return samples.sample_code(patient, day or self.day)

    def csv(self, *rows, header=True, sep=","):
        lines = [sep.join(("sample code", "test code", "value"))] if header else []
        lines += [sep.join(row) for row in rows]
        return csv_file("\r\n".join(lines) + "\r\n")


# ---- the analyzer code of a test ----------------------------------------------------------------


class TestCodeTests(LabBench):
    def test_it_is_kept_upper_case_and_trimmed_and_audited(self):
        self.assertEqual((self.hb.code, self.wbc.code, self.noshort.code), ("HB", "WBC", ""))
        test = lab.save_test({"name": "كالسيوم", "code": "  ca-125 "}, self.owner)
        self.assertEqual(LabTest.objects.get(pk=test.pk).code, "CA-125")
        row = AuditLog.objects.filter(action="save_lab_test", object_id=str(test.pk)).get()
        self.assertEqual(row.after_data["code"], "CA-125")

    def test_it_is_unique_among_the_tests_whatever_the_case_and_a_test_keeps_its_own(self):
        with self.assertRaisesMessage(ValidationError, "هيموجلوبين"):
            lab.save_test({"name": "تحليل تاني", "code": "hb"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "already used by “هيموجلوبين”"):
            lab.save_test({"name": "تحليل تاني", "code": "Hb"}, self.owner, lang="en")
        lab.save_test({"name": "هيموجلوبين", "code": "hb", "unit": "g/dL", "low": "11", "high": "16"}, self.owner, test=self.hb)  # itself: fine
        self.assertEqual(LabTest.objects.get(pk=self.hb.pk).low, D("11"))
        self.assertEqual(LabTest.objects.filter(name="تحليل تاني").count(), 0)

    def test_only_letters_digits_and_a_few_signs_are_taken(self):
        for bad in ("a b", "gl,u", "g;lu", "جلوكوز", "x" * 21, "-GLU", "GL\tU"):
            with self.subTest(code=bad), self.assertRaisesMessage(ValidationError, "كود الجهاز"):
                lab.save_test({"name": "x", "code": bad}, self.owner)
        for good in ("T3", "CA-125", "a.b_c/d+e", "X" * 20):
            lab.save_test({"name": f"t-{good}", "code": good}, self.owner)

    def test_a_caller_that_sends_no_code_never_wipes_one_and_a_blank_clears_it(self):
        lab.save_test({"name": "هيموجلوبين", "unit": "g/dL", "low": "12", "high": "16", "service": self.cbc}, self.owner, test=self.hb)  # no "code" key (the demo, old callers)
        self.assertEqual(LabTest.objects.get(pk=self.hb.pk).code, "HB")
        lab.save_test({"name": "هيموجلوبين", "code": "", "unit": "g/dL", "low": "12", "high": "16"}, self.owner, test=self.hb)
        self.assertEqual(LabTest.objects.get(pk=self.hb.pk).code, "")

    def test_the_database_enforces_it_but_many_tests_may_have_none(self):
        self.assertEqual(LabTest.objects.filter(code="").count(), 1)
        LabTest.objects.create(name="بلا كود 1")
        LabTest.objects.create(name="بلا كود 2")  # several empty codes
        with self.assertRaises(IntegrityError), transaction.atomic():
            LabTest.objects.create(name="مكرر", code="GLU")


# ---- the sample code ---------------------------------------------------------------------------


class SampleCodeTests(LabBench):
    def test_it_holds_the_patient_number_and_the_day_and_nothing_else(self):
        code = self.code(self.mona, date(2026, 11, 12))
        self.assertEqual(code, f"L{self.mona.pk}-20261112")
        for private in (self.mona.name, self.mona.phone, self.mona.customer_code):
            self.assertNotIn(private, code)
        self.assertEqual(samples.parse(code), (self.mona.pk, date(2026, 11, 12)))

    def test_what_a_scanner_or_a_keyboard_types_is_read(self):
        pk = self.mona.pk
        arabic = "".join("٠١٢٣٤٥٦٧٨٩"[int(ch)] if ch.isdigit() else ch for ch in f"l{pk}-20261112")
        for typed in (f"L{pk}-20261112", f"l{pk}-20261112", f"  L{pk}-2026 1112 \n", arabic):
            with self.subTest(typed=typed):
                self.assertEqual(samples.parse(typed), (pk, date(2026, 11, 12)))

    def test_anything_else_is_not_a_code(self):
        for bad in ("", "L-20261112", "L12", "L12-2026111", "L12-202611123", "X12-20261112", "L12_20261112", "L12-20261312", "L12-20260230", "12-20261112",
                    "L1234567890-20261112", "L12-20261112; DROP", "../L12-20261112"):
            with self.subTest(code=bad):
                self.assertIsNone(samples.parse(bad))

    def test_it_finds_a_real_patient_on_a_day_that_has_come_and_nobody_else(self):
        self.assertEqual(samples.find(self.code(self.mona)), (self.mona, self.day))
        self.assertEqual(samples.find(self.code(self.mona, self.today)), (self.mona, self.today))
        self.assertIsNone(samples.find(self.code(self.mona, self.today + timedelta(days=1))))
        self.assertIsNone(samples.find(f"L{Customer.objects.order_by('-pk').first().pk + 5}-{self.day:%Y%m%d}"))
        walk_in = make_customer(customer_code="WALK-IN", name="زبون عابر")
        self.assertIsNone(samples.find(self.code(walk_in)))

    def test_its_barcode_is_the_codes_own_code128(self):
        from barcode.symbology import barcode_svg

        for patient in (self.mona, make_customer(customer_code="M9-BIG", name="كبير")):
            code = self.code(patient)
            self.assertEqual(decode_code128(code128_modules(code)), code)
            self.assertEqual(modules_of(barcode_svg(code, module_width=2, height=60, show_text=False)), code128_modules(code))

    def test_the_short_name_is_the_analyzer_code_else_the_start_of_the_name(self):
        self.assertEqual(samples.short_name(self.glu), "GLU")
        self.assertEqual(samples.short_name(self.noshort), "كوليسترول ال")

    def test_ordered_tests_come_from_live_invoices_of_that_day_and_from_results(self):
        self.order(self.mona, self.cbc)
        cancelled = self.order(self.mona, self.sugar_item, status="cancelled")
        self.order(self.mona, self.sugar_item, day=self.today)  # another day
        lab.record_results(self.mona, self.day, {self.bg: ("", "A+")}, self.owner)
        ids = samples.ordered([(self.mona.pk, self.day), (self.sami.pk, self.day)])
        self.assertEqual(ids[(self.mona.pk, self.day)], {self.hb.pk, self.wbc.pk, self.bg.pk})  # one service covers two tests; a cancelled invoice never
        self.assertEqual(ids[(self.sami.pk, self.day)], set())
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual({test.code for test in samples.tests_of(self.mona, self.day)}, {"HB", "WBC", "BG"})

    def test_a_switched_off_test_is_listed_only_when_it_has_a_result(self):
        self.order(self.mona, self.cbc)
        LabTest.objects.filter(pk=self.wbc.pk).update(active=False)
        self.assertEqual({test.code for test in samples.tests_of(self.mona, self.day)}, {"HB"})
        lab.record_results(self.mona, self.day, {self.wbc: ("5", "")}, self.owner)
        self.assertEqual({test.code for test in samples.tests_of(self.mona, self.day)}, {"HB", "WBC"})


def modules_of(svg):
    """The bar/space string a barcode SVG draws (module width 2, 10 quiet modules each side): an independent reader of the picture."""

    rects = [(int(float(x)), int(float(w))) for x, w in re.findall(r'<rect x="([\d.]+)" y="0" width="([\d.]+)"', svg)]
    width = int(re.search(r'viewBox="0 0 (\d+)', svg).group(1))
    bits = ["0"] * (width // 2)
    for x, w in rects:
        for module in range(x // 2, (x + w) // 2):
            bits[module] = "1"
    return "".join(bits)[10:-10]


# ---- reading the file --------------------------------------------------------------------------


class ReadFileTests(LabBench):
    def rows(self, text, **kwargs):
        return [(row.line, row.sample, row.test, row.value, row.bad) for row in lab_import.read_file(csv_file(text, **kwargs))]

    def test_a_header_row_or_none_a_comma_or_a_semicolon(self):
        want = [(2, "L1-20261112", "GLU", "5.4", ""), (3, "L1-20261112", "HB", "13", "")]
        self.assertEqual(self.rows("sample code,test code,value\nL1-20261112,GLU,5.4\nL1-20261112,HB,13\n"), want)
        self.assertEqual(self.rows("Sample_ID;Test;Result\nL1-20261112;GLU;5.4\nL1-20261112;HB;13\n"), want)
        self.assertEqual(self.rows("L1-20261112,GLU,5.4\nL1-20261112,HB,13\n"), [(1, "L1-20261112", "GLU", "5.4", ""), (2, "L1-20261112", "HB", "13", "")])
        self.assertEqual(self.rows("L1-20261112;GLU;5.4\n")[0][1:4], ("L1-20261112", "GLU", "5.4"))
        self.assertEqual(self.rows("كود العينة,التحليل,النتيجة\nL1-20261112,GLU,5.4\n")[0][1:4], ("L1-20261112", "GLU", "5.4"))

    def test_a_semicolon_file_with_decimal_commas_is_not_split_on_the_comma(self):
        rows = self.rows("L5-20261112;GLU;12,5\r\nL5-20261112;HB;13,25\r\n")
        self.assertEqual([(row[1], row[2], row[3]) for row in rows], [("L5-20261112", "GLU", "12,5"), ("L5-20261112", "HB", "13,25")])
        self.assertEqual(self.rows('L5-20261112,GLU,"12,5"\n')[0][3], "12,5")  # a comma file quotes it

    def test_a_headerless_semicolon_file_with_grouped_thousands_is_still_semicolon_separated(self):
        # two semicolons and two commas in the first line: counting punctuation would pick the comma
        rows = self.rows("L12-20261112;PLT;1,250,000\r\nL12-20261112;HB;13,5\r\n")
        self.assertEqual([(row[1], row[2], row[3], row[4]) for row in rows], [("L12-20261112", "PLT", "1,250,000", ""), ("L12-20261112", "HB", "13,5", "")])
        self.assertEqual(self.rows("Sample;Test;Result\r\nL12-20261112;PLT;1,250,000\r\n")[0][1:4], ("L12-20261112", "PLT", "1,250,000"))
        # and a comma file whose text value carries semicolons stays comma-separated
        self.assertEqual(self.rows('L12-20261112,BG,"A+; rechecked; ok"\n')[0][1:4], ("L12-20261112", "BG", "A+; rechecked; ok"))

    def test_a_byte_order_mark_windows_line_ends_blank_lines_and_extra_columns(self):
        text = "﻿sample code,test code,value,unit\r\n\r\nL1-20261112,GLU,5.4,mmol/L\r\n,,\r\nL1-20261112,HB,13,g/dL\r\n"
        self.assertEqual(self.rows(text), [(3, "L1-20261112", "GLU", "5.4", ""), (5, "L1-20261112", "HB", "13", "")])
        self.assertEqual(self.rows("L1-20261112,GLU,5.4\r\n", encoding="utf-8-sig")[0][1], "L1-20261112")  # a BOM and no header: the BOM is not part of the code

    def test_a_windows_arabic_file_is_read(self):
        rows = self.rows("L1-20261112,BG,إيجابي\n", encoding="cp1256")
        self.assertEqual(rows[0][3], "إيجابي")

    def test_a_short_empty_or_long_line_is_kept_as_a_bad_row_not_dropped(self):
        rows = self.rows("L1-20261112,GLU\nL1-20261112,,5\nL1-20261112,GLU," + "9" * 300 + "\n")
        self.assertEqual([row[4] for row in rows], ["columns", "empty", "long"])
        self.assertEqual(len(rows[2][3]), lab_import.VALUE_MAX)

    def test_what_is_not_a_csv_or_is_too_big_or_empty_is_refused_with_a_message(self):
        cases = (
            (SimpleUploadedFile("results.xlsx", b"PK\x03\x04rest"), "CSV"),
            (SimpleUploadedFile("results.txt", b"L1-20261112,GLU,5"), "CSV"),
            (SimpleUploadedFile("results.csv", b"PK\x03\x04\x00\x00binary"), "CSV"),  # a spreadsheet renamed
            (csv_file(""), "فاضي"),
            (csv_file("sample code,test code,value\n"), "فاضي"),
            (csv_file("\n\n,,\n"), "فاضي"),
            (None, "اختار ملف"),
        )
        for upload, text in cases:
            with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                lab_import.read_file(upload)
        with patch.object(lab_import, "MAX_BYTES", 20), self.assertRaisesMessage(ValidationError, "أكبر من 5 ميجا"):
            lab_import.read_file(csv_file("L1-20261112,GLU,5.4\n" * 3))
        with patch.object(lab_import, "MAX_ROWS", 2), self.assertRaisesMessage(ValidationError, "أكتر من 5000"):
            lab_import.read_file(csv_file("L1-20261112,GLU,5.4\n" * 3))
        with self.assertRaisesMessage(ValidationError, "must be a CSV"):
            lab_import.read_file(SimpleUploadedFile("a.xlsx", b"x"), "en")


# ---- what every line would do ------------------------------------------------------------------


class ClassifyTests(LabBench):
    def lines(self, *rows, **kwargs):
        file = lab_import.read_file(self.csv(*rows, **kwargs))
        return lab_import.classify(file)

    def test_every_status_with_a_small_file(self):
        self.order(self.mona, self.cbc, self.sugar_item)  # HB, WBC, GLU ordered; BG and cholesterol not
        self.order(self.sami, self.sugar_item)
        lab.record_results(self.hala, self.day, {self.hb: ("14", ""), self.wbc: ("5", ""), self.bg: ("", "A+")}, self.owner)
        mona, sami, hala = self.code(self.mona), self.code(self.sami), self.code(self.hala)
        future = self.code(self.mona, self.today + timedelta(days=2))
        rows = [
            (mona, "HB", "17.5"),                    # 2  WRITE
            (mona, "glu", "88"),                     # 3  WRITE (a code in lower case)
            ("L9999999-" + f"{self.day:%Y%m%d}", "HB", "13"),  # 4  no such patient
            (mona, "XXX", "5"),                      # 5  unknown test code
            (mona, "WBC", "abc"),                    # 6  numeric test given text
            (sami, "HB", "14"),                      # 7  not ordered for this patient (only sugar was)
            (mona, "BG", "A+"),                      # 8  not ordered (no service)
            (mona, "HB", "17.5"),                    # 9  the same line again
            (hala, "HB", "14.0"),                    # 10 already entered, same value
            (hala, "WBC", "6"),                      # 11 already entered, another value
            (mona, "GLU"),                           # 12 too few columns
            (future, "HB", "13"),                    # 13 a day that has not come
            (sami, "GLU", "120"),                    # 14 WRITE, flagged high later
            ("junk", "HB", "13"),                    # 15 not a sample code
            (hala, "BG", "A+"),                      # 16 already entered text, same
            (self.code(self.hala, self.day - timedelta(days=3)), "HB", "13"),  # 17 nothing ordered or entered that day
        ]
        found = self.lines(*rows)
        statuses = [line.status for line in found]
        self.assertEqual(statuses, [WRITE, WRITE, UNKNOWN_SAMPLE, UNKNOWN_TEST, NOT_NUMBER, NOT_ORDERED, NOT_ORDERED, DUPLICATE, SAME, DIFFERS, BAD_ROW,
                                    UNKNOWN_SAMPLE, WRITE, UNKNOWN_SAMPLE, SAME, NOT_ORDERED])
        self.assertEqual(found[7].other, 2)  # the repeat points at the first line
        self.assertEqual((found[9].existing, found[9].new), ("5", "6"))
        self.assertEqual(found[11].reason, "future")
        self.assertEqual(found[5].reason, "")
        self.assertEqual(found[15].reason, "nothing")  # nothing at all that day
        self.assertEqual((found[0].patient, found[0].test, found[0].day), (self.mona, self.hb, self.day))
        totals = lab_import.counts(found)
        self.assertEqual((totals[WRITE], totals[SAME], totals[DIFFERS], totals[NOT_ORDERED], totals[DUPLICATE]), (3, 2, 1, 3, 1))

    def test_conflicting_repeats_hold_back_every_occurrence_and_identical_ones_only_the_later(self):
        self.order(self.mona, self.cbc)
        mona = self.code(self.mona)
        found = self.lines((mona, "HB", "13"), (mona, "HB", "15"), (mona, "WBC", "5"), (mona, "wbc", "5.0"), (mona, "WBC", "5,0"))
        self.assertEqual([line.status for line in found], [DUPLICATE, DUPLICATE, WRITE, DUPLICATE, DUPLICATE])
        self.assertEqual((found[0].reason, found[0].other, found[1].other), ("conflict", 3, 2))
        self.assertEqual((found[3].reason, found[3].other), ("same", 4))

    def test_numeric_means_a_range_and_a_test_without_one_takes_a_number_or_a_word(self):
        self.order(self.mona, self.cbc)
        lab.record_results(self.mona, self.day, {self.bg: ("", "O+")}, self.owner)
        mona = self.code(self.mona)
        text_test = LabTest.objects.create(name="بول", code="UR")  # no range, never ordered but resulted below
        lab.record_results(self.mona, self.day, {text_test: ("", "سلبي")}, self.owner)
        found = self.lines((mona, "HB", "<0.5"), (mona, "HB", "12.5"), (mona, "BG", "O+"), (mona, "UR", "traces"))
        self.assertEqual([line.status for line in found], [NOT_NUMBER, WRITE, SAME, DIFFERS])
        self.assertEqual(found[1].entry, ("12.5", ""))
        self.assertEqual(found[3].new, "traces")
        self.assertEqual(self.lines((mona, "UR", "7"))[0].status, DIFFERS)  # a number on a test with no range is a number: not the word entered

    def test_a_switched_off_test_and_a_test_without_a_code_are_unknown(self):
        self.order(self.mona, self.cbc)
        LabTest.objects.filter(pk=self.wbc.pk).update(active=False)
        found = self.lines((self.code(self.mona), "WBC", "5"), (self.code(self.mona), "كوليسترول", "150"), (self.code(self.mona), "a b", "1"))
        self.assertEqual([line.status for line in found], [UNKNOWN_TEST] * 3)

    def test_a_text_value_longer_than_the_column_is_a_bad_row(self):
        self.order(self.mona, self.sugar_item)
        found = self.lines((self.code(self.mona), "BG", "ن" * 130))
        self.assertEqual((found[0].status, found[0].reason), (BAD_ROW, "long"))

    def test_an_order_in_another_entity_or_a_cancelled_or_another_days_one_is_not_an_order(self):
        from entities.services import save_entity

        branch = save_entity({"code": "LAB2", "name_ar": "فرع المعادي", "activity_slug": "medical", "sub_activity_slug": "lab"}, self.owner)
        elsewhere = make_location(location_code="LAB2L", is_selling_location=True, entity=branch)
        self.order(self.mona, self.cbc, location=elsewhere)
        self.order(self.sami, self.cbc, status="cancelled")
        self.order(self.hala, self.cbc, day=self.day - timedelta(days=1))
        rows = ((self.code(self.mona), "HB", "13"), (self.code(self.sami), "HB", "13"), (self.code(self.hala), "HB", "13"))
        from entities.current import working_in
        from entities.services import main_entity

        with working_in(main_entity()):
            found = self.lines(*rows)
        self.assertEqual([line.status for line in found], [NOT_ORDERED] * 3)
        self.assertEqual(found[0].reason, "nothing")
        with working_in(branch):  # the branch's own order is an order there
            self.assertEqual([line.status for line in self.lines(*rows)], [WRITE, NOT_ORDERED, NOT_ORDERED])


# ---- applying ------------------------------------------------------------------------------------


class ApplyTests(LabBench):
    def setUp(self):
        super().setUp()
        self.order(self.mona, self.cbc, self.sugar_item)
        self.order(self.sami, self.sugar_item)
        self.mona_code, self.sami_code = self.code(self.mona), self.code(self.sami)
        self.rows = lab_import.read_file(self.csv(
            (self.mona_code, "HB", "17.5"), (self.mona_code, "GLU", "88"), (self.mona_code, "WBC", "abc"), (self.mona_code, "XXX", "1"),
            (self.sami_code, "HB", "14"), (self.sami_code, "GLU", "120"), ("junk", "GLU", "1")))

    def apply(self, rows=None, user=None):
        return lab_import.apply(user or self.owner, "results.csv", rows or self.rows)

    def test_it_writes_exactly_the_good_lines_and_leaves_the_rest(self):
        lines, totals = self.apply()
        self.assertEqual(LabResult.objects.count(), 3)
        self.assertEqual(sorted(LabResult.objects.values_list("patient__customer_code", "test__code", "value")),
                         [("M9-1", "GLU", D("88")), ("M9-1", "HB", D("17.5")), ("M9-2", "GLU", D("120"))])
        self.assertEqual([line.written for line in lines], [True, True, False, False, False, True, False])
        self.assertEqual((totals[WRITE], totals[NOT_NUMBER], totals[UNKNOWN_TEST], totals[NOT_ORDERED], totals[UNKNOWN_SAMPLE]), (3, 1, 1, 1, 1))

    def test_a_value_is_flagged_and_given_its_range_exactly_as_when_typed(self):
        self.apply()
        typed_patient = self.patient("M9-T", "كتابة بإيد", "")
        lab.record_results(typed_patient, self.day, {self.hb: ("17.5", ""), self.glu: ("88", ""), self.bg: ("", "A+")}, self.owner)
        imported = {r.test.code: r for r in LabResult.objects.filter(patient=self.mona)}
        typed = {r.test.code: r for r in LabResult.objects.filter(patient=typed_patient)}
        for code in ("HB", "GLU"):
            one, other = imported[code], typed[code]
            self.assertEqual((one.value, one.text_value, one.unit, one.low, one.high, one.flag, one.taken_on),
                             (other.value, other.text_value, other.unit, other.low, other.high, other.flag, other.taken_on), code)
        self.assertEqual((imported["HB"].flag, typed["HB"].flag), ("high", "high"))  # 17.5 is above 16 in both
        self.assertEqual((imported["GLU"].flag, imported["GLU"].unit, imported["GLU"].entered_by), ("normal", "mg/dL", self.owner))
        self.assertEqual(LabResult.objects.get(patient=self.sami, test=self.glu).flag, "high")

    def test_a_word_result_goes_in_as_text_with_no_flag(self):
        self.order(self.hala, self.cbc)
        LabTest.objects.filter(pk=self.bg.pk).update(service=self.cbc)  # now ordered with the other blood tests
        rows = lab_import.read_file(self.csv((self.code(self.hala), "BG", "AB-")))
        self.apply(rows)
        result = LabResult.objects.get(patient=self.hala, test=self.bg)
        self.assertEqual((result.value, result.text_value, result.flag), (None, "AB-", ""))

    def test_the_same_file_twice_changes_nothing_the_second_time(self):
        self.apply()
        link = self._link()
        before = {r.pk: (r.value, r.text_value, r.flag, r.entered_by_id, r.updated_at) for r in LabResult.objects.all()}
        audit = AuditLog.objects.count()
        lines, totals = self.apply(user=self.manager)
        self.assertEqual(totals[WRITE], 0)
        self.assertEqual(totals[SAME], 3)
        self.assertEqual(before, {r.pk: (r.value, r.text_value, r.flag, r.entered_by_id, r.updated_at) for r in LabResult.objects.all()})
        self.assertEqual(AuditLog.objects.count(), audit)  # not one more row of any kind
        self.assertIsNotNone(public.read_token(link))  # an M6 link made before the second import still opens

    @override_settings(PUBLIC_BASE_URL="https://lab.example.test")
    def _link(self):
        return token_of(public.make_link(self.mona, self.day, self.owner)["url"])

    def test_a_result_already_typed_is_never_overwritten_by_the_file(self):
        lab.record_results(self.mona, self.day, {self.hb: ("13.5", "")}, self.owner, "ar")
        typed = LabResult.objects.get(patient=self.mona, test=self.hb)
        lines, totals = self.apply()
        self.assertEqual(lines[0].status, DIFFERS)
        self.assertEqual((lines[0].existing, lines[0].new), ("13.5", "17.5"))
        typed.refresh_from_db()
        self.assertEqual((typed.value, typed.flag), (D("13.5"), "normal"))
        self.assertEqual(LabResult.objects.filter(patient=self.mona).count(), 2)  # HB typed + GLU imported

    def test_a_result_entered_between_the_judging_and_the_write_is_not_overwritten(self):
        """The race Codex found: ``apply`` judges, a clinician saves, ``apply`` writes. The write is insert-only."""

        judged = lab_import.classify(self.rows)  # before anyone typed: three good lines
        self.assertEqual(lab_import.counts(judged)[WRITE], 3)
        lab.record_results(self.mona, self.day, {self.hb: ("13.5", "")}, self.doctor)  # typed in between, another value
        lab.record_results(self.sami, self.day, {self.glu: ("120", "")}, self.doctor)  # typed in between, the same value
        audit = AuditLog.objects.filter(action="record_lab_result").count()
        with patch.object(lab_import, "classify", return_value=judged):
            lines, totals = self.apply()
        hb, glu = LabResult.objects.get(patient=self.mona, test=self.hb), LabResult.objects.get(patient=self.sami, test=self.glu)
        self.assertEqual((hb.value, hb.flag, hb.entered_by), (D("13.5"), "normal", self.doctor))  # not 17.5, not the importer
        self.assertEqual((glu.value, glu.entered_by), (D("120"), self.doctor))
        # the file's lines: 0 Mona's HB, 1 Mona's sugar, 5 Sami's sugar
        self.assertEqual([(lines[i].status, lines[i].written) for i in (0, 1, 5)], [(DIFFERS, False), (WRITE, True), (SAME, False)])
        self.assertEqual((lines[0].existing, lines[0].new), ("13.5", "17.5"))
        self.assertEqual((totals[WRITE], totals[DIFFERS], totals[SAME]), (1, 1, 1))
        self.assertEqual(AuditLog.objects.filter(action="record_lab_result").count(), audit + 1)  # only Mona's sugar was written
        self.assertEqual(AuditLog.objects.get(action="import_lab_results").after_data["written"], 1)

    def test_only_new_leaves_an_entered_result_exactly_as_it_is(self):
        lab.record_results(self.mona, self.day, {self.hb: ("13.5", "")}, self.doctor)
        before = LabResult.objects.get(patient=self.mona, test=self.hb)
        saved = lab.record_results(self.mona, self.day, {self.hb: ("17.5", ""), self.glu: ("88", "")}, self.owner, only_new=True)
        self.assertEqual([result.test for result in saved], [self.glu])
        after = LabResult.objects.get(patient=self.mona, test=self.hb)
        self.assertEqual((after.value, after.flag, after.entered_by, after.updated_at), (D("13.5"), "normal", self.doctor, before.updated_at))
        self.assertEqual(lab.record_results(self.mona, self.day, {self.hb: ("17.5", "")}, self.owner)[0].value, D("17.5"))  # typing still corrects

    def test_a_stale_preview_is_judged_again_when_applied(self):
        stale = lab_import.classify(self.rows)
        self.assertEqual(lab_import.counts(stale)[WRITE], 3)
        lab.record_results(self.sami, self.day, {self.glu: ("101", "")}, self.owner)  # typed after the preview
        SalesInvoice.objects.filter(customer=self.mona).update(status="cancelled")  # and the order cancelled
        lines, totals = self.apply()
        self.assertEqual(totals[WRITE], 0)
        self.assertEqual(LabResult.objects.get(patient=self.sami, test=self.glu).value, D("101"))
        self.assertEqual(LabResult.objects.filter(patient=self.mona).count(), 0)
        self.assertEqual([line.status for line in lines][:2], [NOT_ORDERED, NOT_ORDERED])

    def test_it_is_one_transaction_a_failure_halfway_writes_nothing(self):
        real = lab.record_results
        calls = []

        def failing(patient, day, entries, user, lang="ar", **kwargs):
            calls.append(patient.pk)
            self.assertEqual(kwargs, {"only_new": True})  # the import always writes insert-only
            if len(calls) == 2:
                raise ValidationError("boom")
            return real(patient, day, entries, user, lang, **kwargs)

        audit = AuditLog.objects.count()
        with patch.object(lab, "record_results", failing), self.assertRaises(ValidationError):
            self.apply()
        self.assertEqual(LabResult.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), audit)

    def test_it_is_audited_without_a_single_value(self):
        audit = AuditLog.objects.count()
        self.apply()
        summary = AuditLog.objects.get(action="import_lab_results")
        self.assertEqual((summary.actor, summary.module, summary.event_type), (self.owner, "medical", "import"))
        self.assertEqual(summary.after_data["written"], 3)
        self.assertEqual(summary.after_data["file"], "results.csv")
        self.assertEqual(summary.after_data["left"], {"not_ordered": 1, "unknown_sample": 1, "unknown_test": 1, "not_number": 1})
        text = str(summary.after_data)
        for value in ("17.5", "88", "120", self.mona.name, self.mona_code):
            self.assertNotIn(value, text)
        # The row's id is a digest of the file's rows: 16 hex characters that hold no value. It is checked by its shape, not
        # searched for "88": a digest holds those two characters by chance about once in seventeen runs (the CI failure on #386).
        self.assertRegex(summary.object_id, r"^[0-9a-f]{16}$")
        rows = AuditLog.objects.filter(action="record_lab_result")
        self.assertEqual(rows.count(), 3)  # the typed path's own row per result
        self.assertEqual(AuditLog.objects.count(), audit + 4)

    def test_only_a_writer_on_a_lab_may_apply(self):
        from django.core.exceptions import PermissionDenied

        for user in (self.desk, self.keeper):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                self.apply(user=user)
        self.assertEqual(LabResult.objects.count(), 0)
        for user in (self.manager, self.doctor):
            lab_import.apply(user, "f.csv", self.rows)  # the first writes, the rest find nothing left
        self.assertEqual(LabResult.objects.count(), 3)

    def test_it_never_makes_a_patient_a_test_an_order_or_any_money(self):
        counts = lambda: (Customer.objects.count(), LabTest.objects.count(), SalesInvoice.objects.count(), PatientFile.objects.count())  # noqa: E731
        before, money = counts(), footprint()
        lab_import.classify(self.rows)
        self.assertEqual((counts(), footprint()), (before, money))
        self.apply()
        self.assertEqual((counts(), footprint()), (before, money))
        self.assertEqual((StockMovement.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count()), (money["stock"], money["cash"], money["journal"]))

    def test_the_file_is_the_only_input_the_payload_carries_and_it_is_signed_and_bound_to_the_user(self):
        token = lab_import.pack(self.owner, "results.csv", self.rows)
        name, rows = lab_import.unpack(self.owner, token)
        self.assertEqual((name, [row.pack() for row in rows]), ("results.csv", [row.pack() for row in self.rows]))
        for bad in ("", "garbage", token[:-3] + "xyz", token + "x"):
            with self.subTest(token=bad[-6:]), self.assertRaisesMessage(ValidationError, "قديمة"):
                lab_import.unpack(self.owner, bad)
        with self.assertRaisesMessage(ValidationError, "out of date"):
            lab_import.unpack(self.manager, token, "en")  # a preview is its maker's
        with patch.object(lab_import, "PAYLOAD_SECONDS", -1), self.assertRaises(ValidationError):
            lab_import.unpack(self.owner, token)


class NoMoneyModuleTests(SimpleTestCase):
    def test_no_module_of_m9_posts_or_moves_stock_cash_or_a_ledger(self):
        from pathlib import Path

        from django.conf import settings

        for name in ("samples.py", "lab_import.py", "views_lab.py"):
            source = Path(settings.BASE_DIR, "medical", name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                for forbidden in ("post_sales_invoice", "StockMovement", "CashboxMovement", "LedgerEntry", "JournalEntry", "average_cost", "post_", "create_sales_draft"):
                    self.assertNotIn(forbidden, source)
