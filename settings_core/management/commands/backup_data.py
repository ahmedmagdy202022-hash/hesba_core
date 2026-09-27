"""OPS-001: a full, restorable backup of the business data.

Writes every row (not sessions, permissions cache or admin log) as gzipped
Django fixtures, with natural keys so it loads into a fresh database, and
keeps the newest BACKUP_KEEP files. For SQLite it also takes a consistent copy
of the database file with SQLite's own online-backup API.

Run it daily (cron / a scheduled task):  python manage.py backup_data
Restore:  python manage.py migrate && python manage.py loaddata <file>.json.gz
"""

import gzip
import io
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.db import connection
from django.utils import timezone


EXCLUDE = ("contenttypes", "auth.permission", "sessions", "admin.logentry")


class Command(BaseCommand):
    help = "Back up all business data to BACKUP_DIR and keep the newest BACKUP_KEEP files."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=None, help="Override BACKUP_DIR.")
        parser.add_argument("--keep", type=int, default=None, help="Override BACKUP_KEEP.")

    def handle(self, *args, **options):
        target = Path(options["dir"] or settings.BACKUP_DIR)
        keep = options["keep"] or settings.BACKUP_KEEP
        target.mkdir(parents=True, exist_ok=True)
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        buffer = io.StringIO()
        call_command("dumpdata", exclude=list(EXCLUDE), natural_foreign=True, natural_primary=True, indent=None, stdout=buffer)
        fixture = target / f"hesba-{stamp}.json.gz"
        with gzip.open(fixture, "wt", encoding="utf-8") as handle:
            handle.write(buffer.getvalue())
        written = [fixture]
        if connection.vendor == "sqlite":
            source_path = settings.DATABASES["default"]["NAME"]
            if source_path and source_path != ":memory:" and Path(str(source_path)).exists():
                copy = target / f"hesba-{stamp}.sqlite3"
                with sqlite3.connect(str(source_path)) as source, sqlite3.connect(str(copy)) as dest:
                    source.backup(dest)
                written.append(copy)
        self._rotate(target, keep)
        for path in written:
            self.stdout.write(f"{path} ({path.stat().st_size:,} bytes)")

    def _rotate(self, target, keep):
        for pattern in ("hesba-*.json.gz", "hesba-*.sqlite3"):
            files = sorted(target.glob(pattern))
            for old in files[:-keep] if keep > 0 else []:
                old.unlink()
