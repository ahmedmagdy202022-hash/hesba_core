"""T13 (live part): pull the connected stores' recent orders into draft invoices (run it every hour or so, e.g. cron)."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from master_data.models import Location
from onlinestore import connect


class Command(BaseCommand):
    help = "Pull the last days' orders from the connected Shopify / WooCommerce store as draft sales invoices."

    def add_arguments(self, parser):
        parser.add_argument("--location", required=True, help="The code of the selling location the orders leave from.")
        parser.add_argument("--user", help="The username the drafts are made under (default: the first active superuser).")
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        stores = connect.connected()
        if not stores:
            self.stdout.write("No store account is connected; nothing pulled.")
            return
        location = Location.objects.filter(location_code=options["location"], active=True, is_selling_location=True).first()
        if location is None:
            raise CommandError("No active selling location with that code.")
        users = get_user_model().objects.filter(is_active=True)
        user = users.filter(username=options["user"]).first() if options.get("user") else users.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise CommandError("No user to make the drafts under; pass --user.")
        for store in stores:
            try:
                result = connect.pull(store, location, user, days=options["days"], max_pages=None)  # every page, no request timeout here
            except ValidationError as exc:
                self.stdout.write(f"{store}: {' '.join(exc.messages)}")
                continue
            self.stdout.write(f"{store}: {len(result['made'])} drafts, {len(result['again'])} already in, {len(result['skipped'])} not imported.")
