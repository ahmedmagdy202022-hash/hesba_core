"""DIGEST-002: send the daily follow-up email.

    python manage.py send_daily_email            # whatever is due now (for a real cron)
    python manage.py send_daily_email --slot morning --force   # send one slot now
"""

from django.core.management.base import BaseCommand, CommandError

from settings_core.daily_email import SLOTS, email_ready, run_due, send_slot


class Command(BaseCommand):
    help = "Send the daily follow-up email that is due (or one slot with --force)."

    def add_arguments(self, parser):
        parser.add_argument("--slot", choices=SLOTS)
        parser.add_argument("--force", action="store_true", help="Send the slot now, even if it was already sent today.")

    def handle(self, *args, **options):
        if not email_ready():
            raise CommandError("Email is not configured: set EMAIL_HOST and the other EMAIL_* variables.")
        if options["force"]:
            if not options["slot"]:
                raise CommandError("--force needs --slot.")
            result = send_slot(options["slot"])
            self.stdout.write(f"{options['slot']}: sent {result['sent']}, failed {len(result['failed'])}")
            return
        report = run_due()
        self.stdout.write(f"{report['status']}: " + ", ".join(f"{slot} {r['sent']}" for slot, r in report["slots"].items()))
