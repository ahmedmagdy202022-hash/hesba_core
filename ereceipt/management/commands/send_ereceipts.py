"""T8 (live part): send the till's queued e-receipts and follow up the ones sent (run it every few minutes, e.g. cron).

A receipt must reach the authority within 24 hours of its issue. One the authority could not take stays queued
with the reason, for the next run.
"""

from django.core.management.base import BaseCommand

from ereceipt import portal


class Command(BaseCommand):
    help = "Send the queued e-receipts to the Tax Authority and update the ones already sent."

    def handle(self, *args, **options):
        needs = portal.missing()
        if needs:
            self.stdout.write("The receipt portal is not set up (" + ", ".join(needs) + "); nothing sent.")
            return
        result = portal.send()
        checked = portal.follow_up()
        self.stdout.write(f"accepted {result['accepted']}, rejected {result['rejected']}, updated {checked}"
                          + (f"; {result['message']}" if result["message"] else ""))
