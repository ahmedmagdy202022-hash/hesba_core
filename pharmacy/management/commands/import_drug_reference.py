"""R1 (HG-082): load a reference drug list from a CSV or .xlsx file into ``DrugReference``.

The work is in :mod:`pharmacy.reference`; this command only reads the options and prints the summary. It writes the
reference table only, never an item, drug data, a price, stock or a ledger. See ``docs/DRUG_REFERENCE_IMPORT.md``.
"""

from argparse import ArgumentTypeError
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from pharmacy.reference import LAYOUTS, SIMPLE_COLUMNS, TEMPLATE_COLUMNS, ReferenceImportError, import_file


def _date(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as error:
        raise ArgumentTypeError("--price-date لازم يكون YYYY-MM-DD. | --price-date must be YYYY-MM-DD.") from error


class Command(BaseCommand):
    help = (
        "Load a reference drug list (CSV or .xlsx) into the pharmacy reference table, upserting by source + source_ref. "
        f"Hesba template columns: {', '.join(TEMPLATE_COLUMNS)} (only name_en or name_ar is required). "
        f"Or a simple list with a name column ({', '.join(SIMPLE_COLUMNS['name'][:3])}, ...), a price and a barcode; "
        "strength, form and pack are then read from the name when clear. Never touches items, prices, stock or ledgers."
    )

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="The CSV (UTF-8 or Windows Arabic) or .xlsx file.")
        parser.add_argument("--source", required=True, help="A short id for the list, e.g. ahmed-file-2026-11 (letters, digits, . _ -).")
        parser.add_argument("--source-version", required=True, help="The list's version or date as its publisher gives it.")
        parser.add_argument("--price-date", type=_date, default=None, help="YYYY-MM-DD: the date the prices are from (a price_date column wins).")
        parser.add_argument("--layout", choices=LAYOUTS, default="auto", help="hesba, simple, or auto (from the header).")
        parser.add_argument("--dry-run", action="store_true", help="Read and count only; save nothing.")
        parser.add_argument("--deactivate-missing", action="store_true",
                            help="Switch off this source's rows that are not in the file (other sources are untouched).")

    def handle(self, *args, **options):
        try:
            summary = import_file(options["file"], source=options["source"], source_version=options["source_version"],
                                  price_date=options["price_date"], layout=options["layout"], dry_run=options["dry_run"],
                                  deactivate_missing=options["deactivate_missing"])
        except ReferenceImportError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(summary.line())
