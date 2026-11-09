"""DEMO-FIRST-RUN: build the ready, populated shop of every demo activity.

Run after prepare_demo and prepare_demo_fresh on a showcase install with
private demo copies (DEMO-SANDBOX). Each entry of DEMO_ACTIVITIES gets its own
SQLite template (demo.<activity>.<sub>.sqlite3): a copy of the emptied demo,
set up with the activity's presets and filled with a month of its trade, so a
visitor who taps that activity only waits for a file copy. Existing templates
are kept unless --force; --only builds some of them (activity:sub, repeatable);
--top-up also brings the existing ones up to today (start_demo.sh, on every
wake: the server's disk goes back to the build image). A template another
builder holds the lock of is skipped. One activity failing does not stop the
others; the command reports it and exits non-zero at the end.
"""

import time

from django.conf import settings
from django.core.management import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Build the populated demo template of every demo activity (showcase installs only)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Rebuild templates that exist.")
        parser.add_argument("--top-up", action="store_true", dest="top_up",
                            help="Also bring existing templates up to today (today's trade, the owner's shift).")
        parser.add_argument("--only", action="append", default=[], metavar="ACTIVITY:SUB",
                            help="Build only these (repeatable), e.g. --only commercial:pharmacy.")

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("prepare_demo_templates only runs with DEMO_MODE=True.")
        if not settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
            self.stdout.write("Not a SQLite demo; nothing to build.")
            return
        from config.demo import DEMO_ACTIVITIES
        from config.demo_templates import base_template, build_template, clean_leftovers, fresh_template, populated_template, top_up_template

        wanted = list(DEMO_ACTIVITIES)
        if options["only"]:
            wanted = []
            for raw in options["only"]:
                activity, _, sub = raw.partition(":")
                if (activity, sub) not in DEMO_ACTIVITIES:
                    raise CommandError(f"Not a demo activity: {raw}")
                wanted.append((activity, sub))
        if not fresh_template().is_file():
            raise CommandError("The emptied demo is missing; run prepare_demo_fresh first.")
        clean_leftovers()
        built = kept = topped = skipped = 0
        failed = []
        for activity, sub in wanted:
            target = populated_template(activity, sub)
            if target == base_template():
                kept += 1  # the retail shop is the base demo: prepare_demo builds and tops it up
                continue
            started = time.monotonic()
            try:
                if target.is_file() and not options["force"]:
                    if not options["top_up"]:
                        kept += 1
                        continue
                    if top_up_template(activity, sub) is None:
                        skipped += 1
                        self.stdout.write(f"{activity}:{sub} is locked by another builder; skipped.")
                        continue
                    topped += 1
                    self.stdout.write(f"{activity}:{sub} topped up ({time.monotonic() - started:.1f} s)")
                    continue
                if build_template(activity, sub, force=options["force"]) is None:
                    skipped += 1
                    self.stdout.write(f"{activity}:{sub} is being built by another builder; skipped.")
                    continue
            except Exception as error:  # one activity's seeding must not cost the others theirs
                failed.append(f"{activity}:{sub}")
                self.stderr.write(f"WARNING: {activity}:{sub} failed: {error!r}")
                continue
            built += 1
            size = target.stat().st_size / 1_048_576
            self.stdout.write(f"{activity}:{sub} -> {target.name} ({size:.1f} MB, {time.monotonic() - started:.1f} s)")
        summary = f"Demo templates ready: {built} built, {topped} topped up, {kept} kept, {skipped} skipped."
        if failed:
            raise CommandError(f"{summary} Failed: {', '.join(failed)}.")
        self.stdout.write(self.style.SUCCESS(summary))
