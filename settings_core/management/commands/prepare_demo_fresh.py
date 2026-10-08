"""DEMO-FAST: build the emptied demo that "start over" copies.

Run after prepare_demo on a showcase install with private demo copies
(DEMO-SANDBOX). It copies the demo database next to itself and empties the
copy the way "start over" does, once, so a tester starting over only waits
for a file copy. Without --force an existing one is kept.
"""

import os

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Build the emptied demo database that starting over copies (showcase installs only)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Rebuild it even if it exists.")

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("prepare_demo_fresh only runs with DEMO_MODE=True.")
        database = settings.DATABASES["default"]
        if not database["ENGINE"].endswith("sqlite3"):
            raise CommandError("prepare_demo_fresh needs the SQLite demo database.")
        from config.demo_restart import restart_demo
        from config.demo_sandbox import _use, copy_database, fresh_template

        template = str(database["NAME"])
        target = fresh_template(template)
        if target.is_file() and not options["force"]:
            self.stdout.write("Fresh demo already built.")
            return
        partial = target.with_suffix(".building")
        copy_database(template, partial)
        try:
            _use(str(partial))
            restart_demo()
            # Emptied pages stay in the file until compacted; smaller copies are quicker.
            with connections["default"].cursor() as cursor:
                cursor.execute("VACUUM")
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        finally:
            connections["default"].close()
            _use(template)
        os.replace(partial, target)
        self.stdout.write(self.style.SUCCESS(f"Fresh demo ready: {target.name}"))
