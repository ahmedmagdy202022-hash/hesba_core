"""BACKUP-002: turn an encrypted backup back into a fixture, with the owner's private key.

    python manage.py decrypt_backup hesba-20260928-020000.hesba-backup --key-file my-key.txt
    python manage.py loaddata hesba-20260928-020000.json.gz

Works on any machine with Hesba installed; it needs no database access.
"""

import gzip
from pathlib import Path

from django.core.management import BaseCommand, CommandError

from settings_core.backup_crypto import SUFFIX, BackupKeyError, decrypt, load_private


class Command(BaseCommand):
    help = "Decrypt a .hesba-backup file with the owner's private key into a .json.gz fixture."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--key-file", required=True, help="File holding the owner's private key (hesba-key-1:...).")
        parser.add_argument("--out", default=None, help="Where to write the fixture (default: beside the backup).")

    def handle(self, *args, **options):
        source = Path(options["path"])
        target = Path(options["out"]) if options["out"] else source.with_name(source.name.removesuffix(SUFFIX) + ".json.gz")
        try:
            private_key = load_private(Path(options["key_file"]).read_text(encoding="utf-8"))
            data = decrypt(source.read_bytes(), private_key)
            gzip.decompress(data)  # refuse to write something loaddata cannot read
        except (OSError, BackupKeyError) as exc:
            raise CommandError(f"Backup cannot be opened: {exc}") from exc
        target.write_bytes(data)
        self.stdout.write(f"{target} ({target.stat().st_size:,} bytes). Restore with: python manage.py loaddata {target}")
