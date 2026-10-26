"""T7 (HG-059): send the credit notes waiting in the queue (run it every few minutes, e.g. from cron).

A return never waits for the authority: it is posted at once, and its credit note goes out here once the original
invoice is accepted. One that fails stays in the queue for the next run; a rejected one waits for a manual send
after its data is corrected.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from einvoice import portal
from einvoice.services import send_due_credit_notes


class Command(BaseCommand):
    help = "Send the e-invoice credit notes of posted returns whose original invoice the authority accepted."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="The username the sendings are recorded under (default: the first active superuser).")

    def handle(self, *args, **options):
        if portal.missing():
            self.stdout.write("The portal connection is not set up; nothing sent.")
            return
        users = get_user_model().objects.filter(is_active=True)
        user = users.filter(username=options["user"]).first() if options.get("user") else users.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise CommandError("No user to record the sendings under; pass --user.")
        sent, failed = send_due_credit_notes(user)
        for submission in sent:
            self.stdout.write(f"{submission.sales_return.return_number}: {submission.status}")
        for sales_return, reason in failed:
            self.stdout.write(f"{sales_return.return_number}: still waiting — {reason}")
        self.stdout.write(f"{len(sent)} sent, {len(failed)} still waiting.")
