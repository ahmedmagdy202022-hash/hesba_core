"""T9: queue today's customer messages (run it each morning, the same way the daily digest runs)."""

from django.core.management.base import BaseCommand

from messaging.services import build


class Command(BaseCommand):
    help = "Gather today's customer messages into the outbox (amounts due, instalments, tomorrow's appointments, refills)."

    def handle(self, *args, **options):
        self.stdout.write(f"{build()} messages added.")
