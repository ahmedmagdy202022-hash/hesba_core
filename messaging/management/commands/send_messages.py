"""T9 (live part): gather today's customer messages and send the ones with an approved WhatsApp template.

Run it each morning after (or instead of) ``gather_messages``. Without a connected WhatsApp Business account it
only gathers; the messages then go with one click from the outbox.
"""

from django.core.management.base import BaseCommand

from messaging import whatsapp
from messaging.services import build, send_by_api


class Command(BaseCommand):
    help = "Gather today's customer messages, then send those with an approved template through WhatsApp Business."

    def handle(self, *args, **options):
        added = build()
        if whatsapp.missing():
            self.stdout.write(f"{added} messages added; WhatsApp Business is not connected (" + ", ".join(whatsapp.missing()) + "), nothing sent.")
            return
        result = send_by_api()
        self.stdout.write(f"{added} messages added, {result['sent']} sent, {result['failed']} not sent.")  # no request timeout here: every message is tried
