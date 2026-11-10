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

from django.core.management import call_command
from django.core.management.base import CommandError
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
        self.assertEqual((self.row("n:" + hashlib.sha1("فابلودول اقراص".encode()).hexdigest()[:24], "cp1256").form), "tablet")

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
        self.assertEqual(first.source_ref, "n:" + hashlib.sha1("hesbacillin 500mg caps 20's".encode()).hexdigest()[:24])
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
        line = self.run_import(self.write_csv("ten.csv", rows[:11]), "hesba-sample", "2026-12", "--deactivate-missing")
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
        line = self.run_import(self.write_csv("dry.csv", rows), "hesba-sample", "2027", "--dry-run", "--deactivate-missing")
        self.assertIn("created 0, updated 5, deactivated 25", line)
        self.assertEqual(list(DrugReference.objects.order_by("pk").values_list()), before)


class CommandRefusalTests(FileCase):
    def test_refusals(self):
        with self.assertRaisesMessage(CommandError, "File not found"):
            self.run_import(self.folder / "missing.csv")
        with self.assertRaisesMessage(CommandError, "must be .csv or .xlsx"):
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

    def snapshot(self):
        state = {table.__name__: list(table.objects.order_by("pk").values_list()) for table in self.TABLES}
        digest = hashlib.sha256(repr(sorted(state.items())).encode()).hexdigest()
        return state, digest, fingerprint()

    def test_import_reimport_deactivate_and_dry_run_leave_everything_else_alone(self):
        before = self.snapshot()
        rows = self.fixture_rows()
        rows.append(["HS-PAN", "بنادول 500 مجم", "", "Paracetamol", "500 mg", "tablet", "20 tabs", "", "99.00", "", "2000000000039", "0", ""])
        path = self.write_csv("with-panadol.csv", rows)
        self.run_import(path)
        self.run_import(path, "hesba-sample", "2026-12")
        self.run_import(self.write_csv("ten.csv", rows[:11]), "hesba-sample", "2027", "--deactivate-missing")
        self.run_import(path, "hesba-sample", "2027", "--dry-run")
        self.run_import(self.write_csv("simple.csv", SimpleLayoutTests.ROWS), "community")
        self.assertEqual(DrugReference.objects.count(), 36)
        after = self.snapshot()
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], before[2])
        self.panadol.refresh_from_db()
        self.assertEqual((self.panadol.default_sale_price, self.panadol.barcode), (D("42.00"), "2000000000039"))
        self.assertEqual(Item.objects.filter(barcode="2000000000039").count(), 1)
