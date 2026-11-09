"""A3 (HG-081): delete the invoice photos waiting for review longer than ``PENDING_HOURS``.

Pruning also happens on use (each upload and review) and each time the purchase invoices list opens, whether the feature
is on or not; this command covers a server where neither happens for a while. Safe to run at any time (for example
hourly): it touches nothing but the waiting photos, never a saved draft or its attached photo.
"""

from django.core.management.base import BaseCommand

from ai.invoice_photo import PENDING_HOURS, prune


class Command(BaseCommand):
    help = f"Delete the invoice photos waiting for review longer than {PENDING_HOURS} hours."

    def handle(self, *args, **options):
        deleted = prune()
        self.stdout.write(f"Deleted {deleted} waiting invoice photo(s).")
