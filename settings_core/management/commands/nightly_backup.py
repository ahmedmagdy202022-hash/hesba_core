"""BACKUP-003: make the encrypted backup and send it to the client's Google Drive.

Run once a day from cron:  python manage.py nightly_backup
It does nothing if today's backup is already in Drive (use --force to send another).
"""

from django.core.management import BaseCommand, CommandError

from settings_core.drive_backup import DriveError, run_nightly


class Command(BaseCommand):
    help = "Encrypted backup to the client's Google Drive (skips if today's is already there)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Upload even if today's backup is already there.")

    def handle(self, *args, **options):
        try:
            report = run_nightly(force=options["force"])
        except DriveError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(", ".join(f"{key}: {value}" for key, value in report.items()))
