"""R1 (HG-082): the reference drug list and its importer.

The importer writes ``DrugReference`` only; the guard tests prove items, drug data, stock, the cashbox, the ledgers and
the journal are identical before and after an import."""

import csv
import hashlib
import io
import shutil
import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, router
from django.test import SimpleTestCase, TestCase

from cashboxes.models import CashboxDirection, CashboxMovement
from hesba_testing.factories import make_cashbox_movement, purchase_ready
from inventory.models import StockMovement
from ledger.models import JournalEntry, JournalLine
from ledger.projector import ensure_fresh, fingerprint
from master_data.models import Item
from purchases.models import SupplierLedgerEntry
from purchases.services import post_purchase_invoice
from sales.models import CustomerLedgerEntry

from . import reference, services
from .models import DrugInfo, DrugReference
from .tests import PharmacySetup

FIXTURE = Path(__file__).resolve().parent / "tests_fixtures" / "drug_reference_sample.csv"
PRICE_DATE = date(2026, 11, 1)


def derived_id(name_key, strength_key="", form="", pack=""):
    """The id a row without a source_ref gets: its folded name, strength, form and pack."""

    return "n:" + hashlib.sha1("|".join((name_key, strength_key, form, pack)).encode()).hexdigest()[:24]


class FileCase(TestCase):
    """Writes files into a temporary folder and runs the command on them."""

    def setUp(self):
        super().setUp()
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def write_csv(self, name, rows, encoding="utf-8"):
        path = self.folder / name
        with path.open("w", newline="", encoding=encoding) as handle:
            csv.writer(handle).writerows(rows)
        return path

    def fixture_rows(self):
        with FIXTURE.open(encoding="utf-8") as handle:
            return list(csv.reader(handle))

    def run_import(self, path=FIXTURE, source="hesba-sample", version="2026-11", *extra):
        out = io.StringIO()
        call_command("import_drug_reference", "--file", str(path), "--source", source, "--source-version", version, *extra, stdout=out)
        return out.getvalue().strip()

    def row(self, ref, source="hesba-sample"):
        return DrugReference.objects.get(source=source, source_ref=ref)


class BarcodeAndPriceTests(SimpleTestCase):
    def test_gs1_check_digits(self):
        self.assertTrue(reference.gtin_ok("2000000000015"))  # EAN-13
        self.assertTrue(reference.gtin_ok("4006381333931"))  # EAN-13, the GS1 textbook example
        self.assertTrue(reference.gtin_ok("036000291452"))   # UPC-A
        self.assertTrue(reference.gtin_ok("96385074"))       # EAN-8
        self.assertFalse(reference.gtin_ok("2000000000016"))
        self.assertFalse(reference.gtin_ok("036000291453"))
        self.assertFalse(reference.gtin_ok("96385075"))
        self.assertFalse(reference.gtin_ok("12345"))
        self.assertFalse(reference.gtin_ok("２０００００００００01５"))  # full-width digits are not ASCII

    def test_barcode_cleaning(self):
        self.assertEqual(reference.barcode_or_blank("200 0000 000015"), ("2000000000015", False))
        self.assertEqual(reference.barcode_or_blank("٢٠٠٠٠٠٠٠٠٠٠١٥"), ("2000000000015", False))
        self.assertEqual(reference.barcode_or_blank(2000000000015), ("2000000000015", False))
        self.assertEqual(reference.barcode_or_blank(2000000000015.0), ("2000000000015", False))
        self.assertEqual(reference.barcode_or_blank(""), ("", False))
        self.assertEqual(reference.barcode_or_blank("2000000000016"), ("", True))
        self.assertEqual(reference.barcode_or_blank("ABC123"), ("", True))

    def test_prices_are_read_strictly(self):
        self.assertEqual(reference.price_or_none("62.5"), (D("62.50"), False))
        self.assertEqual(reference.price_or_none("٦٢٫٥"), (D("62.50"), False))
        self.assertEqual(reference.price_or_none("1,250.75"), (D("1250.75"), False))
        self.assertEqual(reference.price_or_none("EGP 30"), (D("30.00"), False))
        self.assertEqual(reference.price_or_none("30 جنيه"), (D("30.00"), False))
        self.assertEqual(reference.price_or_none(12.345), (D("12.35"), False))
        for empty in ("", "0", "0.00", None):
            self.assertEqual(reference.price_or_none(empty), (None, False), empty)
        for bad in ("-5", "1e3", "NaN", "abc", "12,5", "99999999999999", "1.2.3"):
            self.assertEqual(reference.price_or_none(bad), (None, True), bad)

    def test_yes_no_leaves_unknown_empty(self):
        self.assertIs(reference.yes_no("1"), True)
        self.assertIs(reference.yes_no("نعم"), True)
        self.assertIs(reference.yes_no("No"), False)
        self.assertIsNone(reference.yes_no(""))
        self.assertIsNone(reference.yes_no("maybe"))


class NameParsingTests(SimpleTestCase):
    def test_clear_names(self):
        cases = {
            "Hesbacillin 500mg caps 20's": ("500mg", "capsule", "20's"),
            "Hesbacillin 250mg/5ml susp 100ml": ("250mg/5ml", "suspension", "100ml"),
            "FABLODOL 500 MG 20 F.C.TABS.": ("500 mg", "tablet", "20 f.c.tabs"),
            "Clavohesba 875/125mg tab": ("875/125mg", "tablet", ""),
            "Hydrofab 1% cream 15gm": ("1%", "cream", "15gm"),
            "Ibufable syrup 120 ML": ("", "syrup", "120 ml"),
            "Diclofab 75mg amp 3's": ("75mg", "injection", "3's"),
            "هسباسيلين ٥٠٠ مجم كبسول": ("500 مجم", "capsule", ""),
            "Vitafab D3 1000iu caps 30's": ("1000iu", "capsule", "30's"),
        }
        for name, (strength, form, pack) in cases.items():
            self.assertEqual(reference.parse_name(name), {"strength": strength, "form": form, "pack": pack}, name)

    def test_unclear_names_stay_empty(self):
        self.assertEqual(reference.parse_name("Mixfab 500mg 250mg tab")["strength"], "")   # two strengths
        self.assertEqual(reference.parse_name("Fizzfab tab sachet")["form"], "")            # two forms
        self.assertEqual(reference.parse_name("Packfab 10's 20's")["pack"], "")              # two packs
        self.assertEqual(reference.parse_name("Hesba Baby Lotion"), {"strength": "", "form": "", "pack": ""})
        # A grouped number is not understood: the whole strength stays blank, never the suffix after the comma.
        self.assertEqual(reference.parse_name("Vitafab 1,000 IU caps")["strength"], "")
        self.assertEqual(reference.parse_name("Drugfab 2,500mg tab")["strength"], "")
        self.assertEqual(reference.parse_name("Packfab 1,000's")["pack"], "")
        self.assertEqual(reference.parse_name("Hesbatab"), {"strength": "", "form": "", "pack": ""})  # a form word inside a name


class KeysMatchDrugInfoTests(FileCase, PharmacySetup):
    def test_reference_rows_match_drug_data_spelt_differently(self):
        combo = self.drug("CLV-B", "كلافوهسبا 1 جم", "118.00", "0", "Amoxicillin + Clavulanic acid", "1 جم", "أقراص")
        self.run_import()
        for item, ref in ((self.generic, "HS-001"), (combo, "HS-005"), (self.panadol, "HS-003")):
            drug, row = DrugInfo.objects.get(item=item), self.row(ref)
            self.assertEqual((row.ingredient_key, row.strength_key, row.form), (drug.ingredient_key, drug.strength_key, drug.form), ref)
        # HS-005 is written "clavulanic acid/amoxicillin", "1 g", "tab"; the drug data "Amoxicillin + Clavulanic acid", "1 جم", "أقراص".
        self.assertEqual(self.row("HS-005").ingredient_key, services.ingredient_key("Amoxicillin + Clavulanic acid"))
        self.assertEqual(self.row("HS-005").strength_key, "1g")
        self.assertEqual(DrugReference.objects.filter(ingredient_key=self.generic.drug.ingredient_key).count(), 3)


class TemplateImportTests(FileCase):
    def test_the_sample_csv(self):
        line = self.run_import(FIXTURE, "hesba-sample", "2026-11", "--price-date", "2026-11-01")
        self.assertIn("اتضاف 30، اتحدّث 0، اتقفل 0، اتساب 0، باركود غلط اتشال 2، سعر غلط اتشال 1", line)
        self.assertIn("created 30, updated 0, deactivated 0, skipped 0, bad barcodes 2, bad prices 1", line)
        self.assertEqual(DrugReference.objects.count(), 30)
        first = self.row("HS-001")
        self.assertEqual((first.name_en, first.name_ar, first.name_key), ("Hesbacillin 500mg caps 20's", "هسباسيلين 500 مجم كبسول", "hesbacillin 500mg caps 20's"))
        self.assertEqual((first.active_ingredient, first.ingredient_key, first.strength, first.strength_key, first.form, first.pack),
                         ("Amoxicillin", "amoxicillin", "500 mg", "500mg", "capsule", "20 caps"))
        self.assertEqual((first.manufacturer, first.public_price, first.price_date, first.barcode), ("Hesba Demo Pharma", D("62.50"), date(2026, 10, 1), "2000000000015"))
        self.assertEqual((first.source_version, first.prescription_only, first.is_drug, first.is_active), ("2026-11", True, True, True))
        self.assertEqual(self.row("HS-002").form, "suspension")          # "معلق"
        self.assertEqual(self.row("HS-003").form, "tablet")              # "أقراص"
        self.assertEqual(self.row("HS-003").strength_key, "500mg")       # "500 مجم"
        self.assertIs(self.row("HS-003").prescription_only, False)
        self.assertIsNone(self.row("HS-006").prescription_only)          # empty = unknown
        self.assertEqual(self.row("HS-006").price_date, PRICE_DATE)      # no date in the row: --price-date
        self.assertEqual(self.row("HS-011").barcode, "20000110")          # EAN-8
        self.assertEqual(self.row("HS-023").name_key, "زنكوفاب شراب")     # Arabic name only
        self.assertIsNone(self.row("HS-023").public_price)
        self.assertIs(self.row("HS-026").is_drug, False)
        self.assertEqual(self.row("HS-027").barcode, "")                 # wrong EAN-13 check digit
        self.assertEqual(self.row("HS-028").barcode, "")                 # wrong EAN-8 check digit
        self.assertIsNone(self.row("HS-029").public_price)               # "not set"

    def test_xlsx_with_the_same_columns_gives_the_same_rows(self):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        rows = self.fixture_rows()
        sheet.append(rows[0])
        for row in rows[1:]:
            cells = list(row)
            cells[8] = float(cells[8]) if cells[8].replace(".", "").isdigit() else cells[8]   # a number cell, as Excel keeps it
            cells[9] = date.fromisoformat(cells[9]) if cells[9] else None                       # a date cell
            cells[10] = int(cells[10]) if cells[10] else None                                   # a barcode typed as a number
            sheet.append(cells)
        path = self.folder / "sample.xlsx"
        book.save(path)
        self.run_import(FIXTURE, "from-csv")
        line = self.run_import(path, "from-xlsx")
        self.assertIn("created 30, updated 0, deactivated 0, skipped 0, bad barcodes 2, bad prices 1", line)
        fields = list(reference.DATA_FIELDS)
        csv_rows = list(DrugReference.objects.filter(source="from-csv").order_by("source_ref").values_list("source_ref", *fields))
        xlsx_rows = list(DrugReference.objects.filter(source="from-xlsx").order_by("source_ref").values_list("source_ref", *fields))
        self.assertEqual(xlsx_rows, csv_rows)

    def test_windows_arabic_csv_and_semicolons(self):
        path = self.folder / "cp1256.csv"
        path.write_bytes("name_ar;public_price;form\nفابلودول أقراص;35;أقراص\n".encode("cp1256"))
        line = self.run_import(path, "cp1256")
        self.assertIn("created 1,", line)
        self.assertEqual(self.row(derived_id("فابلودول اقراص", form="tablet"), "cp1256").form, "tablet")

    def test_over_long_text_and_an_absurd_price_fit_the_table(self):
        path = self.write_csv("long.csv", [["source_ref", "name_en", "manufacturer", "strength", "pack", "public_price", "barcode"],
                                           ["L-1", "N" * 400, "M" * 300, "5" * 80, "P" * 90, "99999999999999", "1" * 20],
                                           ["L-2", "Okfab", "", "", "", "9999999999.99", ""]])
        line = self.run_import(path, "long")
        self.assertIn("created 2,", line)
        self.assertIn("bad barcodes 1, bad prices 1", line)
        row = self.row("L-1", "long")
        self.assertEqual((len(row.name_en), len(row.name_key), len(row.manufacturer), len(row.strength), len(row.pack)), (255, 255, 120, 60, 60))
        self.assertIsNone(row.public_price)
        self.assertEqual(row.barcode, "")
        self.assertEqual(self.row("L-2", "long").public_price, D("9999999999.99"))

    def test_rows_without_a_name_and_repeated_refs_are_skipped(self):
        path = self.write_csv("skips.csv", [["source_ref", "name_en", "name_ar"], ["A", "Okfab", ""], ["B", "", ""], ["A", "Okfab again", ""]])
        line = self.run_import(path, "skips")
        self.assertIn("created 1, updated 0, deactivated 0, skipped 2", line)
        self.assertEqual(self.row("A", "skips").name_en, "Okfab")


class SimpleLayoutTests(FileCase):
    ROWS = [["name", "price", "barcode"],
            ["Hesbacillin 500mg caps 20's", "62.50", "2000000000015"],
            ["Ibufable 100mg/5ml syrup 120ml", "33", "2000000000077"],
            ["Hydrofab 1% cream 15gm", "26.5", "2000000000176"],
            ["FABLODOL 500 MG 20 F.C.TABS.", "35", "2000000000039"],
            ["Mixfab 500mg 250mg tab sachet", "", "2000000000038"]]

    def test_name_price_barcode(self):
        line = self.run_import(self.write_csv("simple.csv", self.ROWS), "community")
        self.assertIn("created 5, updated 0, deactivated 0, skipped 0, bad barcodes 1, bad prices 0", line)
        rows = {row.name_en: row for row in DrugReference.objects.filter(source="community")}
        first = rows["Hesbacillin 500mg caps 20's"]
        self.assertEqual((first.strength, first.strength_key, first.form, first.pack, first.public_price, first.barcode),
                         ("500mg", "500mg", "capsule", "20's", D("62.50"), "2000000000015"))
        self.assertEqual(first.source_ref, derived_id("hesbacillin 500mg caps 20's", "500mg", "capsule", "20's"))
        self.assertEqual((first.active_ingredient, first.ingredient_key, first.name_ar), ("", "", ""))
        self.assertIsNone(first.prescription_only)
        syrup = rows["Ibufable 100mg/5ml syrup 120ml"]
        self.assertEqual((syrup.strength, syrup.form, syrup.pack), ("100mg/5ml", "syrup", "120ml"))
        cream = rows["Hydrofab 1% cream 15gm"]
        self.assertEqual((cream.strength, cream.form, cream.pack), ("1%", "cream", "15gm"))
        tabs = rows["FABLODOL 500 MG 20 F.C.TABS."]
        self.assertEqual((tabs.strength, tabs.strength_key, tabs.form, tabs.pack), ("500 mg", "500mg", "tablet", "20 f.c.tabs"))
        unclear = rows["Mixfab 500mg 250mg tab sachet"]
        self.assertEqual((unclear.strength, unclear.form, unclear.pack, unclear.public_price, unclear.barcode), ("", "", "", None, ""))
        line = self.run_import(self.write_csv("simple.csv", self.ROWS), "community")
        self.assertIn("created 0, updated 5", line)

    def test_arabic_headers_and_extra_columns(self):
        path = self.write_csv("arabic.csv", [["الاسم", "السعر", "الباركود", "المادة الفعالة", "الشركة"],
                                             ["فابلودول 500 مجم أقراص", "٣٥", "", "باراسيتامول", "Imaginary Labs"]])
        self.run_import(path, "arabic-file")
        row = DrugReference.objects.get(source="arabic-file")
        self.assertEqual((row.name_en, row.name_ar, row.name_key), ("", "فابلودول 500 مجم أقراص", "فابلودول 500 مجم اقراص"))
        self.assertEqual((row.strength, row.strength_key, row.form, row.public_price), ("500 مجم", "500mg", "tablet", D("35.00")))
        self.assertEqual((row.active_ingredient, row.ingredient_key, row.manufacturer), ("باراسيتامول", "باراسيتامول", "Imaginary Labs"))


    def test_with_two_columns_for_one_field_the_first_listed_wins(self):
        rows = [["new_price", "name", "ean", "price", "barcode", "id", "source_ref"],
                ["70", "Okfab 5mg tab", "2000000000077", "62.50", "2000000000015", "ID-9", "REF-1"]]
        for _attempt in range(2):
            self.run_import(self.write_csv("two.csv", rows), "two-columns")
        row = DrugReference.objects.get(source="two-columns")
        self.assertEqual((row.source_ref, row.public_price, row.barcode), ("REF-1", D("62.50"), "2000000000015"))


class ReImportTests(FileCase):
    def test_a_second_import_creates_nothing_and_updates_every_row(self):
        self.run_import()
        first = {row.source_ref: row.pk for row in DrugReference.objects.all()}
        line = self.run_import(FIXTURE, "hesba-sample", "2026-12")
        self.assertIn("created 0, updated 30, deactivated 0, skipped 0", line)
        self.assertEqual({row.source_ref: row.pk for row in DrugReference.objects.all()}, first)
        self.assertEqual(set(DrugReference.objects.values_list("source_version", flat=True)), {"2026-12"})

    def test_a_changed_price_is_updated(self):
        self.run_import()
        rows = self.fixture_rows()
        rows[1][8] = "70.00"
        self.run_import(self.write_csv("changed.csv", rows))
        self.assertEqual(self.row("HS-001").public_price, D("70.00"))
        self.assertEqual(self.row("HS-002").public_price, D("48.00"))

    def test_deactivate_missing_touches_only_its_own_source(self):
        self.run_import(FIXTURE, "other-list")
        self.run_import()
        rows = self.fixture_rows()
        line = self.run_import(self.write_csv("ten.csv", rows[:11]), "hesba-sample", "2026-12", "--deactivate-missing", "--force-deactivate")
        self.assertIn("created 0, updated 10, deactivated 20", line)
        self.assertEqual(DrugReference.objects.filter(source="hesba-sample", is_active=True).count(), 10)
        self.assertFalse(self.row("HS-011").is_active)
        self.assertEqual(DrugReference.objects.filter(source="other-list", is_active=True).count(), 30)
        line = self.run_import(FIXTURE, "hesba-sample", "2027-01", "--deactivate-missing")
        self.assertIn("created 0, updated 30, deactivated 0", line)
        self.assertEqual(DrugReference.objects.filter(source="hesba-sample", is_active=True).count(), 30)

    def test_without_the_flag_missing_rows_stay_active(self):
        self.run_import()
        self.run_import(self.write_csv("ten.csv", self.fixture_rows()[:11]))
        self.assertEqual(DrugReference.objects.filter(is_active=True).count(), 30)

    def test_a_file_with_no_usable_row_cannot_deactivate_a_source(self):
        self.run_import()
        for rows in ([["name_en", "name_ar"], ["", ""]], [["name_en"]]):
            with self.assertRaises(CommandError):
                self.run_import(self.write_csv("empty.csv", rows), "hesba-sample", "x", "--deactivate-missing")
        self.assertEqual(DrugReference.objects.filter(is_active=True).count(), 30)


class DryRunTests(FileCase):
    def test_a_dry_run_writes_nothing(self):
        line = self.run_import(FIXTURE, "hesba-sample", "2026-11", "--dry-run")
        self.assertIn("(dry run, nothing saved): created 30, updated 0", line)
        self.assertIn("تجربة بس", line)
        self.assertEqual(DrugReference.objects.count(), 0)

    def test_a_dry_run_after_an_import_changes_nothing(self):
        self.run_import()
        before = list(DrugReference.objects.order_by("pk").values_list())
        rows = self.fixture_rows()[:6]
        rows[1][8] = "999.00"
        line = self.run_import(self.write_csv("dry.csv", rows), "hesba-sample", "2027", "--dry-run", "--deactivate-missing", "--force-deactivate")
        self.assertIn("created 0, updated 5, deactivated 25", line)
        self.assertEqual(list(DrugReference.objects.order_by("pk").values_list()), before)


class CommandRefusalTests(FileCase):
    def test_refusals(self):
        with self.assertRaisesMessage(CommandError, "File not found"):
            self.run_import(self.folder / "missing.csv")
        with self.assertRaisesMessage(CommandError, "must be .csv, .txt or .xlsx"):
            self.run_import(self.write_csv("list.ods", [["name"], ["x"]]))
        with self.assertRaisesMessage(CommandError, "No name column"):
            self.run_import(self.write_csv("noname.csv", [["price", "barcode"], ["5", ""]]))
        with self.assertRaisesMessage(CommandError, "The source is letters"):
            self.run_import(FIXTURE, "bad source!")
        with self.assertRaisesMessage(CommandError, "--price-date must be YYYY-MM-DD"):
            self.run_import(FIXTURE, "hesba-sample", "v", "--price-date", "1/11/2026")
        with self.assertRaisesMessage(CommandError, "The file is empty"):
            self.run_import(self.write_csv("blank.csv", [[""]]))
        self.assertEqual(DrugReference.objects.count(), 0)


class ImportTouchesNothingElseTests(FileCase, PharmacySetup):
    """The importer writes the reference table only: every protected table is identical before and after."""

    TABLES = (Item, DrugInfo, StockMovement, CashboxMovement, CustomerLedgerEntry, SupplierLedgerEntry, JournalEntry, JournalLine)

    def setUp(self):
        super().setUp()
        self.dispense(self.prescription(customer=self.customer))                     # a posted sale on credit: stock out, customer ledger
        invoice, *_rest = purchase_ready(paid_now="0.00")
        post_purchase_invoice(invoice.pk, self.owner)                                # a posted purchase on credit: stock in, supplier ledger
        make_cashbox_movement(self.cashbox, CashboxDirection.IN, "125.00")           # a cash receipt
        ensure_fresh()                                                               # the projected journal
        # One reference row shares an existing item's name and barcode with another price.
        self.panadol.barcode = "2000000000039"
        self.panadol.save(update_fields=["barcode"])
        for table in self.TABLES:
            self.assertGreater(table.objects.count(), 0, table.__name__)

    #: Not checked: the reference table itself, and Django's own session and content-type bookkeeping.
    SKIPPED = ("pharmacy.drugreference", "sessions.session", "contenttypes.contenttype")

    @classmethod
    def every_model(cls):
        """Every concrete table in the default database except :attr:`SKIPPED`, so a stray write anywhere is caught."""

        return [model for model in apps.get_models()
                if model._meta.managed and not model._meta.proxy and not model._meta.swapped
                and model._meta.label_lower not in cls.SKIPPED and router.db_for_read(model) == "default"]

    def snapshot(self):
        state = {table.__name__: list(table.objects.order_by("pk").values_list()) for table in self.TABLES}
        digest = hashlib.sha256(repr(sorted(state.items())).encode()).hexdigest()
        everything = {model._meta.label: (model._default_manager.count(),
                                          hashlib.sha256(repr(list(model._default_manager.order_by("pk").values_list())).encode()).hexdigest())
                      for model in self.every_model()}
        return state, digest, fingerprint(), everything

    def test_import_reimport_deactivate_and_dry_run_leave_everything_else_alone(self):
        before = self.snapshot()
        rows = self.fixture_rows()
        rows.append(["HS-PAN", "بنادول 500 مجم", "", "Paracetamol", "500 mg", "tablet", "20 tabs", "", "99.00", "", "2000000000039", "0", ""])
        path = self.write_csv("with-panadol.csv", rows)
        self.run_import(path)
        self.run_import(path, "hesba-sample", "2026-12")
        self.run_import(self.write_csv("ten.csv", rows[:11]), "hesba-sample", "2027", "--deactivate-missing", "--force-deactivate")
        self.run_import(path, "hesba-sample", "2027", "--dry-run")
        self.run_import(self.write_csv("simple.csv", SimpleLayoutTests.ROWS), "community")
        self.assertEqual(DrugReference.objects.count(), 36)
        after = self.snapshot()
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], before[2])
        self.assertGreater(len(after[3]), 50)                       # the loop really covers the whole schema
        changed = sorted(label for label in before[3] if after[3][label] != before[3][label])
        self.assertEqual(changed, [])
        self.panadol.refresh_from_db()
        self.assertEqual((self.panadol.default_sale_price, self.panadol.barcode), (D("42.00"), "2000000000039"))
        self.assertEqual(Item.objects.filter(barcode="2000000000039").count(), 1)


# ---- review fixes (HG-082) ----

class DerivedIdTests(FileCase):
    """H1: a row without a source_ref is identified by its folded name, strength, form and pack, not the name alone."""

    def test_same_name_with_different_strengths_and_forms_are_separate_rows(self):
        path = self.write_csv("samename.csv", [["name_en", "strength", "form", "public_price"],
                                               ["Samefab", "500 mg", "tab", "10"],
                                               ["Samefab", "1000 mg", "tab", "18"],
                                               ["Samefab", "250 mg/5 ml", "syrup", "12"]])
        line = self.run_import(path, "samename")
        self.assertIn("created 3, updated 0, deactivated 0, skipped 0", line)
        rows = {row.source_ref: row for row in DrugReference.objects.filter(source="samename")}
        self.assertEqual(set(rows), {derived_id("samefab", "500mg", "tablet"), derived_id("samefab", "1000mg", "tablet"),
                                     derived_id("samefab", "250mg/5ml", "syrup")})
        self.assertEqual(rows[derived_id("samefab", "1000mg", "tablet")].public_price, D("18.00"))
        self.assertIn("created 0, updated 3", self.run_import(path, "samename"))

    def test_a_true_duplicate_is_skipped_with_its_line_number(self):
        path = self.write_csv("dupes.csv", [["name_en", "strength", "form", "public_price"],
                                            ["Samefab", "500 mg", "tab", "10"],
                                            ["", "", "", "3"],
                                            ["SAMEFAB", "500mg", "Tablets", "11"],
                                            ["Otherfab", "", "", "4"],
                                            ["samefab ", "500 MG", "TAB", "12"]])
        out = self.run_import(path, "dupes")
        self.assertIn("created 2, updated 0, deactivated 0, skipped 3", out)
        self.assertIn("Skipped rows (line in the file): duplicate 2 (4, 6), no name 1 (3)", out)
        self.assertIn("متكرر 2 (4, 6)", out)
        self.assertEqual(DrugReference.objects.get(source="dupes", name_key="samefab").public_price, D("10.00"))  # the first row wins

    def test_xlsx_lines_are_the_excel_row_numbers(self):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        for cell, value in (("A3", "source_ref"), ("B3", "name_en"), ("A4", "A"), ("B4", "Okfab"), ("A6", "A"), ("B6", "Okfab again")):
            sheet[cell] = value
        path = self.folder / "start3.xlsx"
        book.save(path)
        out = self.run_import(path, "start3")
        self.assertIn("created 1,", out)
        self.assertIn("duplicate 1 (6)", out)

    def test_only_the_first_three_lines_are_listed(self):
        path = self.write_csv("many.csv", [["source_ref", "name_en"]] + [["A", "Okfab"]] * 6)
        out = self.run_import(path, "many")
        self.assertIn("skipped 5", out)
        self.assertIn("duplicate 5 (3, 4, 5…)", out)

    def test_harakat_and_tatweel_fold_into_the_same_name(self):
        self.assertEqual(reference.fold("بَانادولـفاب"), reference.fold("بانادولفاب"))
        self.assertEqual(reference.fold("دواءٌ"), "دواء")
        self.assertEqual(services._plain("بَ"), "بَ")  # DrugInfo's keys are built with _plain, which is unchanged


class EncodingTests(FileCase):
    """M1 / L2: UTF-8, UTF-16 with a byte-order mark and Windows Arabic; a damaged UTF-8 file is refused, not garbled."""

    def test_utf8_with_one_bad_byte_is_refused_with_its_offset(self):
        data = "name,price\nعربيفاب,10\n".encode() + b"x\xe9fab,5\n"
        offset = data.index(b"\xe9")
        path = self.folder / "mixed.csv"
        path.write_bytes(data)
        with self.assertRaisesMessage(CommandError, f"invalid byte at offset {offset}"):
            self.run_import(path, "mixed")
        bom = self.folder / "bom.csv"
        bom.write_bytes(b"\xef\xbb\xbfname,price\nx\xe9fab,5\n")  # a UTF-8 byte-order mark: always UTF-8
        with self.assertRaisesMessage(CommandError, "invalid byte at offset"):
            self.run_import(bom, "mixed")
        self.assertEqual(DrugReference.objects.count(), 0)

    def test_windows_arabic_with_pairs_that_look_like_utf8_is_still_read(self):
        # In cp1256 «ب،» and «ك»» are byte pairs that are also valid UTF-8; the file is still Windows Arabic.
        text = "name_ar;active_ingredient;public_price\n«كافيفاب» أقراص;باراسيتامول،كافيين;20\n"
        path = self.folder / "cp1256.csv"
        path.write_bytes(text.encode("cp1256"))
        self.assertIn("created 1,", self.run_import(path, "cp1256"))
        row = DrugReference.objects.get(source="cp1256")
        self.assertEqual((row.name_ar, row.active_ingredient, row.form), ("«كافيفاب» أقراص", "باراسيتامول،كافيين", "tablet"))

    def test_utf16_with_a_byte_order_mark(self):
        for encoding, bom in (("utf-16-le", "﻿"), ("utf-16-be", "﻿")):
            path = self.folder / f"{encoding}.txt"
            path.write_bytes((bom + "name\tprice\nيونيفاب\t10\nUnifab 5mg tab\t12\n").encode(encoding))
            self.assertIn("created 2,", self.run_import(path, encoding))
            self.assertEqual(set(DrugReference.objects.filter(source=encoding).values_list("name_ar", "name_en", "public_price")),
                             {("يونيفاب", "", D("10.00")), ("", "Unifab 5mg tab", D("12.00"))})


class HeaderTests(FileCase):
    """M2 / L7 / L12: the same header aliases in both layouts, the ignored columns named, and repeated headers refused."""

    def test_the_template_reads_the_aliases_too(self):
        path = self.write_csv("alias.csv", [["Name-EN", "Price (EGP)", "EAN", "Notes"], ["Aliasfab 5mg tab", "25", "2000000000015", "x"]])
        out = self.run_import(path, "alias")
        row = DrugReference.objects.get(source="alias")
        self.assertEqual((row.name_en, row.public_price, row.barcode, row.strength, row.form), ("Aliasfab 5mg tab", D("25.00"), "2000000000015", "5mg", "tablet"))
        self.assertIn("أعمدة اتجاهلت / ignored columns: Notes", out)

    def test_arabic_header_spellings_fold(self):
        path = self.write_csv("arabic.csv", [["إسم الدواء", "السعر"], ["فابلودول", "35"]])
        self.run_import(path, "arabic")
        self.assertEqual(DrugReference.objects.get(source="arabic").name_ar, "فابلودول")

    def test_a_template_column_beside_a_name_alias_makes_it_the_template(self):
        headers = ["name", "strength", "source_ref", "price"]
        self.assertEqual(reference.detect_layout([reference._header(name) for name in headers]), "hesba")
        self.assertEqual(reference.detect_layout(["name", "price"]), "simple")
        path = self.write_csv("l7.csv", [headers, ["Templfab 5mg caps 20's", "10 mg", "T-1", "9"]])
        out = self.run_import(path, "l7")
        row = self.row("T-1", "l7")
        # The strength column wins over the name; form and pack have no column, so they are read from the name.
        self.assertEqual((row.name_en, row.strength, row.form, row.pack, row.public_price), ("Templfab 5mg caps 20's", "10 mg", "capsule", "20's", D("9.00")))
        self.assertNotIn("ignored columns", out)

    def test_an_empty_template_cell_is_not_filled_from_the_name(self):
        path = self.write_csv("empty-cell.csv", [["source_ref", "name_en", "strength", "form", "pack"], ["E-1", "Emptyfab 5mg tab 20's", "", "", ""]])
        self.run_import(path, "empty-cell")
        row = self.row("E-1", "empty-cell")
        self.assertEqual((row.strength, row.form, row.pack), ("", "", ""))

    def test_a_simple_file_names_the_columns_it_does_not_read(self):
        path = self.write_csv("simple.csv", [["name", "price", "price_date"], ["Okfab", "5", "2026-10-01"]])
        out = self.run_import(path, "simple", "v", "--layout", "simple")
        self.assertIn("ignored columns: price_date", out)

    def test_two_columns_with_the_same_header_are_refused(self):
        with self.assertRaisesMessage(CommandError, "Two columns have the same header: price"):
            self.run_import(self.write_csv("twice.csv", [["name", "Price", "price "], ["Okfab", "5", "6"]]), "twice")
        self.run_import(self.write_csv("blank-headers.csv", [["name", "", ""], ["Okfab", "a", "b"]]), "blank")  # empty headers are fine
        self.assertEqual(DrugReference.objects.filter(source="twice").count(), 0)
        self.assertEqual(DrugReference.objects.filter(source="blank").count(), 1)


class NumberCellTests(FileCase):
    """M3 / M4 / M5: an ambiguous price, Excel float noise and barcodes that lost their leading zeros."""

    def test_a_three_digit_decimal_in_text_is_ambiguous(self):
        for ambiguous in ("1.250", "12.500", "999.999"):
            self.assertEqual(reference.price_or_none(ambiguous), (None, True), ambiguous)
        self.assertEqual(reference.price_or_none("1,250"), (D("1250.00"), False))
        self.assertEqual(reference.price_or_none("1.25"), (D("1.25"), False))
        self.assertEqual(reference.price_or_none("0.125"), (D("0.13"), False))  # never a thousands group
        self.assertEqual(reference.price_or_none(1.255), (D("1.26"), False))    # a number cell is not ambiguous

    def test_float_noise_is_cut(self):
        self.assertEqual(reference._cell(27.500000000000004), "27.5")
        self.assertEqual(reference._cell(0.1 + 0.2), "0.3")
        self.assertEqual(reference._cell(12.0), "12")
        self.assertEqual(reference._cell(7), "7")
        self.assertEqual(reference.price_or_none(0.1 + 0.2), (D("0.30"), False))

    def test_an_xlsx_price_with_float_noise(self):
        import zipfile

        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.append(["source_ref", "name_en", "public_price", "barcode"])
        sheet.append(["X-1", "Xlfab 20mg tab", 27.500000000000004, 36000291452])   # a UPC-A typed as a number: 11 digits left
        sheet.append(["X-2", "Xlfab 5mg tab", 25, 1234565])                        # an EAN-8 that lost its leading 0
        plain = self.folder / "plain.xlsx"
        book.save(plain)
        # openpyxl writes 16 significant digits; Excel writes 17, so the noise is put into the sheet's XML by hand.
        path = self.folder / "noise.xlsx"
        with zipfile.ZipFile(plain) as source, zipfile.ZipFile(path, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    self.assertIn(b"<v>27.5</v>", data)
                    data = data.replace(b"<v>27.5</v>", b"<v>27.500000000000004</v>")
                target.writestr(item, data)
        self.assertEqual(reference.read_table(path)[2][0][1]["public_price"], 27.500000000000004)  # the file really holds the noise
        out = self.run_import(path, "noise")
        self.assertIn("bad barcodes 0, bad prices 0", out)
        self.assertEqual((self.row("X-1", "noise").public_price, self.row("X-1", "noise").barcode), (D("27.50"), "0036000291452"))
        self.assertEqual(self.row("X-2", "noise").barcode, "01234565")

    def test_upc_a_is_stored_as_ean13(self):
        for value in (36000291452, 36000291452.0, "036000291452", "0036000291452", 36000291452 * 1):
            self.assertEqual(reference.barcode_or_blank(value), ("0036000291452", False), value)
        self.assertEqual(reference.barcode_or_blank("36000291452"), ("", True))   # text is never padded
        self.assertEqual(reference.barcode_or_blank(36000291453), ("", True))     # padded, the check digit still fails
        self.assertEqual(reference.barcode_or_blank(True), ("", True))


class ReImportSemanticsTests(FileCase):
    """Item 7: what a newer file does to a stored price, price date and prescription flag."""

    HEADER = ["source_ref", "name_en", "public_price", "price_date", "prescription_only"]

    def first_import(self):
        self.run_import(self.write_csv("v1.csv", [self.HEADER, ["R-1", "Rfab", "10", "", "1"], ["R-2", "Rfab2", "20", "2026-10-01", ""]]),
                        "v", "1", "--price-date", "2026-11-01")
        self.assertEqual((self.row("R-1", "v").price_date, self.row("R-2", "v").price_date), (PRICE_DATE, date(2026, 10, 1)))

    def test_a_bad_price_keeps_the_stored_price_and_date(self):
        self.first_import()
        out = self.run_import(self.write_csv("v2.csv", [self.HEADER, ["R-1", "Rfab", "10", "", "1"], ["R-2", "Rfab2", "abc", "", ""]]),
                              "v", "2", "--price-date", "2026-12-01")
        self.assertIn("bad prices 1", out)
        self.assertEqual((self.row("R-2", "v").public_price, self.row("R-2", "v").price_date), (D("20.00"), date(2026, 10, 1)))
        self.assertEqual(self.row("R-1", "v").price_date, date(2026, 12, 1))   # a row with a price takes the new date

    def test_without_a_date_the_stored_date_is_kept(self):
        self.first_import()
        self.run_import(self.write_csv("v2.csv", [self.HEADER, ["R-1", "Rfab", "11", "", "1"], ["R-2", "Rfab2", "21", "2026/12/15", ""]]), "v", "2")
        self.assertEqual((self.row("R-1", "v").public_price, self.row("R-1", "v").price_date), (D("11.00"), PRICE_DATE))
        self.assertEqual(self.row("R-2", "v").price_date, date(2026, 12, 15))  # the row's own date, written YYYY/MM/DD

    def test_an_unreadable_date_is_counted_and_changes_nothing(self):
        self.first_import()
        out = self.run_import(self.write_csv("v2.csv", [self.HEADER, ["R-1", "Rfab", "10", "", "1"], ["R-2", "Rfab2", "22", "soon", ""],
                                                        ["R-3", "Rfab3", "30", "31/02/2026", ""]]), "v", "2", "--price-date", "2026-12-01")
        self.assertIn("bad prices 0, bad dates 2", out)
        self.assertIn("تاريخ غلط اتساب 2", out)
        self.assertEqual((self.row("R-2", "v").public_price, self.row("R-2", "v").price_date), (D("22.00"), date(2026, 10, 1)))
        self.assertIsNone(self.row("R-3", "v").price_date)  # a new row: not given --price-date in place of its own unreadable date

    def test_a_date_is_written_only_with_a_price(self):
        path = self.write_csv("noprice.csv", [self.HEADER, ["P-1", "Pfab", "", "", ""], ["P-2", "Pfab2", "x", "", ""], ["P-3", "Pfab3", "5", "", ""]])
        self.run_import(path, "p", "1", "--price-date", "2026-11-01")
        self.assertEqual([self.row(ref, "p").price_date for ref in ("P-1", "P-2", "P-3")], [None, None, PRICE_DATE])

    def test_an_empty_prescription_cell_resets_to_unknown(self):
        self.first_import()
        self.assertIs(self.row("R-1", "v").prescription_only, True)
        self.run_import(self.write_csv("v2.csv", [self.HEADER, ["R-1", "Rfab", "10", "", ""]]), "v", "2")
        self.assertIsNone(self.row("R-1", "v").prescription_only)


class DeactivateGuardTests(FileCase):
    """L1: --deactivate-missing refuses to switch off more than half of a source unless --force-deactivate is given."""

    def test_more_than_half_is_refused_without_the_force_flag(self):
        self.run_import()
        ten = self.write_csv("ten.csv", self.fixture_rows()[:11])
        before = list(DrugReference.objects.order_by("pk").values_list())
        for extra in ((), ("--dry-run",)):
            with self.assertRaisesMessage(CommandError, "would deactivate 20 of the 30 active rows"):
                self.run_import(ten, "hesba-sample", "2027", "--deactivate-missing", *extra)
        self.assertEqual(list(DrugReference.objects.order_by("pk").values_list()), before)
        self.assertIn("deactivated 20", self.run_import(ten, "hesba-sample", "2027", "--deactivate-missing", "--force-deactivate"))

    def test_exactly_half_is_allowed(self):
        self.run_import()
        line = self.run_import(self.write_csv("fifteen.csv", self.fixture_rows()[:16]), "hesba-sample", "2027", "--deactivate-missing")
        self.assertIn("updated 15, deactivated 15", line)

    def test_inactive_rows_do_not_count(self):
        self.run_import()
        self.run_import(self.write_csv("fifteen.csv", self.fixture_rows()[:16]), "hesba-sample", "2027", "--deactivate-missing")
        # 15 active left: a file with 8 of them switches off 7, under half of the active rows.
        line = self.run_import(self.write_csv("eight.csv", self.fixture_rows()[:9]), "hesba-sample", "2028", "--deactivate-missing")
        self.assertIn("deactivated 7", line)
        line = self.run_import(self.write_csv("other.csv", self.fixture_rows()[:2]), "new-source", "1", "--deactivate-missing")
        self.assertIn("created 1, updated 0, deactivated 0", line)  # a new source has nothing to switch off


class FormAndWriteTests(FileCase):
    """L5 / L9: film-coated tablets in a form cell; a failed write is a clear refusal."""

    def test_film_coated_tablets(self):
        for text in ("F.C.Tabs", "f.c.tabs.", "FC TABS", "fc tabs", "F.C. Tablets", "tab", "أقراص"):
            self.assertEqual(reference.form_or_blank(text), "tablet", text)
        self.assertEqual(reference.form_or_blank("F.C."), "")
        self.assertEqual(services.form_from_text("f.c.tabs"), "")  # the shared table used by save_drug is unchanged

    def test_an_integrity_error_is_a_clear_refusal(self):
        with mock.patch.object(DrugReference.objects, "bulk_create", side_effect=IntegrityError("duplicate key")):
            with self.assertRaisesMessage(CommandError, "nothing was saved, try again"):
                self.run_import()
        self.assertEqual(DrugReference.objects.count(), 0)
