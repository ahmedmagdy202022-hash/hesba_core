"""OPS-001: prove a backup file is readable and complete before you need it.

Reads the gzipped fixture, counts rows per model and compares them with the
live database. Exit status is non-zero when the file is unreadable or a model
is missing rows, so it can run right after backup_data in the same cron job.
"""

import gzip
import json
from collections import Counter

from django.apps import apps
from django.core.management import BaseCommand, CommandError

from settings_core.backup_crypto import BackupKeyError, decrypt, load_private

from .backup_data import EXCLUDE


def read_rows(path, key_file=None):
    """Rows of a plain ``.json.gz`` backup, or of an encrypted one given the private key file."""

    if key_file:
        try:
            with open(key_file, encoding="utf-8") as handle:
                private_key = load_private(handle.read())
            with open(path, "rb") as handle:
                data = gzip.decompress(decrypt(handle.read(), private_key))
        except (OSError, BackupKeyError) as exc:
            raise CommandError(f"Backup cannot be opened: {exc}") from exc
        return json.loads(data.decode("utf-8"))
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


class Command(BaseCommand):
    help = "Check a backup_data file: readable, and row counts match the database."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--key-file", default=None, help="The owner's private key file, for an encrypted backup.")

    def handle(self, *args, **options):
        try:
            rows = read_rows(options["path"], options["key_file"])
        except (OSError, ValueError) as exc:
            raise CommandError(f"Backup is not readable: {exc}") from exc
        counts = Counter(row["model"] for row in rows)
        problems = []
        for model in apps.get_models():
            label = model._meta.label_lower
            if label.split(".")[0] in EXCLUDE or label in EXCLUDE or not model._meta.managed or model._meta.proxy:
                continue
            live = model._default_manager.count()
            if counts.get(label, 0) != live:
                problems.append(f"{label}: backup {counts.get(label, 0)}, database {live}")
        self.stdout.write(f"{sum(counts.values()):,} rows across {len(counts)} tables.")
        if problems:
            raise CommandError("Backup does not match the database:\n" + "\n".join(problems))
        self.stdout.write("Backup OK.")
