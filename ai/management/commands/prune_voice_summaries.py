"""A1 (HG-079): delete the voice summary's cached audio older than seven Cairo days.

Pruning also happens on access (each new voicing prunes first), so this command is only needed on a server where nobody
presses play for a while. Safe to run at any time; it touches nothing but the audio cache.
"""

from django.core.management.base import BaseCommand

from ai.voice_summary import KEEP_DAYS, prune


class Command(BaseCommand):
    help = f"Delete the voice summary audio older than {KEEP_DAYS} Cairo days."

    def handle(self, *args, **options):
        deleted = prune()
        self.stdout.write(f"Deleted {deleted} cached voice summary audio row(s).")
