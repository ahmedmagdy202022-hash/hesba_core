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

from .backup_data import EXCLUDE


class Command(BaseCommand):
    help = "Check a backup_data file: readable, and row counts match the database."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        try:
            with gzip.open(options["path"], "rt", encoding="utf-8") as handle:
                rows = json.load(handle)
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
