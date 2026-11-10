"""DEMO-FIRST-RUN: a ready, populated shop per activity, built once and copied per visitor.

The public demo opens on one tap: the visitor picks the kind of business and
lands in a shop of that kind that already has a month of trade, an open shift
and an owner with a name. Nothing is asked and nothing is seeded while they
wait, because every (activity, sub-activity) in ``DEMO_ACTIVITIES`` has its own
pre-built SQLite template next to the base demo database:

    demo.sqlite3                          the base shop (commercial · retail, built by prepare_demo)
    demo.fresh.sqlite3                    the emptied demo (prepare_demo_fresh): «ابدأ فاضي» copies this
    demo.<activity>.<sub>.sqlite3         one populated shop per other DEMO_ACTIVITIES entry

``prepare_demo_templates`` builds the populated ones (build.sh, and start_demo.sh
in the background after the server is up, where ``--top-up`` also brings the
existing ones up to today). A template that is still missing when a visitor asks
for it is built on demand in a background thread while they see a short
"بنجهّز نسختك…" page that refreshes, so no request ever runs the seeding.

Every build and top-up holds ``<template>.lock`` (created with O_EXCL, so one
holder across processes and threads), works in a file named for its own process
and thread, and appears with ``os.replace``: a visitor never copies half a shop.

Budget (measured locally, 9 Nov 2026): one populated template is ~8–12 s to seed
and ~5 MB on disk; all eight extra ones are about 40 MB and under two minutes in
one process. The emptied demo is shared by every activity: «ابدأ فاضي» copies it
and runs the activity's setup presets on the copy (milliseconds), which spares
eight more 4.5 MB files and eight 30 s rebuilds.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

from django.conf import settings

from .demo_sandbox import copy_database

log = logging.getLogger("hesba.demo")

#: A lock older than this belongs to a build that crashed or was killed; it may be taken over.
STALE_BUILD_SECONDS = 15 * 60
#: The preparing page stops waiting after this many failed builds of one template...
MAX_ATTEMPTS = 3
#: ...or once builds of it have been failing for this long (counted from the first failure, not from the
#: first request: a healthy build on the showcase's small server can itself take most of a minute).
FAILURE_WINDOW_SECONDS = 60
#: A failure older than this is forgotten, so a later visitor may try again.
FORGET_FAILURE_SECONDS = 10 * 60
#: However the build is going, the preparing page gives up after this long.
MAX_WAIT_SECONDS = 5 * 60


def is_known(activity, sub_activity):
    from .demo import DEMO_ACTIVITIES

    return (activity, sub_activity) in DEMO_ACTIVITIES


def base_template():
    return Path(settings.DATABASES["default"]["NAME"])


def fresh_template():
    from .demo_sandbox import fresh_template as fresh

    return fresh(base_template())


def populated_template(activity, sub_activity):
    """Where the ready shop for this kind of business lives. The base demo *is* the retail shop."""

    base = base_template()
    if (activity, sub_activity) == ("commercial", "retail"):
        return base
    return base.with_name(f"{base.stem}.{activity}.{sub_activity}{base.suffix}")


def lock_path(target):
    """Held (created with O_EXCL) by whoever is building or topping up ``target``, in any process."""

    return Path(f"{target}.lock")


def failed_stamp(target):
    return Path(f"{target}.failed")


def _age(path, now=None):
    try:
        return (now or time.time()) - path.stat().st_mtime
    except FileNotFoundError:
        return None


def is_building(activity, sub_activity, now=None):
    age = _age(lock_path(populated_template(activity, sub_activity)), now)
    return age is not None and age < STALE_BUILD_SECONDS


def _acquire(lock):
    """Take the lock file, or return False if someone else holds it. A stale lock (a crashed build) is taken over once."""

    for attempt in (1, 2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            age = _age(lock)
            if attempt == 1 and (age is None or age >= STALE_BUILD_SECONDS):
                lock.unlink(missing_ok=True)  # gone already, or left by a build that died: try once more
                continue
            return False
        with os.fdopen(fd, "w") as handle:
            handle.write(f"{os.getpid()} {threading.get_ident()} {time.time():.0f}\n")
        return True
    return False


def _work_file(target, kind):
    """A name of this builder's own (process and thread), so two builders never share a half-written file."""

    return Path(f"{target}.{os.getpid()}-{threading.get_ident()}.{kind}")


def _discard(path):
    for leftover in (path, path.with_suffix(".partial"), Path(f"{path}-journal")):
        leftover.unlink(missing_ok=True)


def clean_leftovers(now=None):
    """Remove what crashed builds left next to the base demo: half copies, work files, dead locks, old failure stamps.

    Only files older than STALE_BUILD_SECONDS go: a web worker may be building one right now."""

    base = base_template()
    removed = 0
    if not base.parent.is_dir():
        return removed
    for entry in base.parent.glob(f"{base.stem}.*"):
        # Never a journal: next to the base demo it could be the hot journal SQLite needs to recover it.
        if not entry.name.endswith((".partial", ".building", ".topup", ".lock", ".failed")):
            continue
        age = _age(entry, now)
        if age is not None and age >= STALE_BUILD_SECONDS:
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


# ---- what a populated shop is made of ----------------------------------------------------------

#: The owner's name per kind of business: the dashboard greets a person, not a role.
DEMO_OWNERS = {
    ("commercial", "retail"): "أستاذ محمود الشربيني", ("commercial", "pharmacy"): "د. أحمد الشاذلي",
    ("commercial", "grocery"): "الحاج سعيد عبد العال", ("commercial", "fashion"): "مدام نهى فؤاد",
    ("commercial", "electronics"): "أستاذ كريم حمدي", ("commercial", "wholesale"): "الحاج رمضان عبد الحميد",
    ("restaurants", "restaurant"): "الشيف كريم عادل", ("restaurants", "cafe"): "أستاذ عمر فاروق",
    ("medical", "clinic"): "د. سارة حسين", ("medical", "dental"): "د. هشام عبد الله", ("medical", "vet"): "د. كريم البيطري",
    ("education", "training"): "أ. منى عبد الرحمن", ("education", "nursery"): "ميس دينا شوقي",
    ("services", "maintenance"): "الأسطى محمد رمضان", ("services", "beauty"): "مدام هالة سمير",
    ("manufacturing", "food"): "الحاج سيد عبد الفتاح", ("contracting", "general"): "م. طارق الجندي",
    "commercial": "أستاذ محمود الشربيني", "restaurants": "الشيف كريم عادل", "medical": "د. سارة حسين", "education": "أ. منى عبد الرحمن",
    "services": "الأسطى محمد رمضان", "manufacturing": "الحاج سيد عبد الفتاح", "contracting": "م. طارق الجندي",
}
ROLE_NAME = "المالك"  # what seed_demo_users calls the owner; only that is ever replaced


def owner_name(activity, sub_activity):
    return DEMO_OWNERS.get((activity, sub_activity)) or DEMO_OWNERS.get(activity) or DEMO_OWNERS["commercial"]


def name_the_demo_owner(activity=None, sub_activity=None):
    """Give the demo owner a realistic name for this kind of business (never a name a visitor typed)."""

    from accounts.models import UserProfile
    from settings_core.models import ClientProfile

    if activity is None:
        profile = ClientProfile.get_active()
        activity, sub_activity = (profile.activity_slug, profile.sub_activity_slug) if profile else ("", "")
    owner = UserProfile.objects.filter(user__username="owner").first()
    if owner is not None and owner.display_name in ("", ROLE_NAME, *DEMO_OWNERS.values()):
        owner.display_name = owner_name(activity, sub_activity)
        owner.save(update_fields=["display_name", "updated_at"])


def demo_capabilities(activity, sub_activity, features):
    """The features a demo of this business switches on: the interview's suggestions, plus the AI showcase
    wherever its sections exist (HG-078 dev. 7), plus what prepare_demo turns on for the retail shop."""

    from settings_core import capabilities as caps

    chosen = list(features)
    if (activity, sub_activity) == ("commercial", "retail"):  # DEMO-CAPS: the base shop shows these too
        chosen += ["promotions", "loyalty", "shipping", "online_store", "quotations", "e_invoice", "e_receipt"]
    return tuple(dict.fromkeys(slug for slug in chosen if caps.is_available(slug)))


def complete_demo_setup(activity, sub_activity, user=None):
    """Finish setup for a demo copy the way the interview would with every suggestion accepted: no question asked."""

    from settings_core import capabilities as caps
    from settings_core import setup_interview as interview
    from settings_core.demo_ai import CAPABILITIES as AI_CAPABILITIES
    from settings_core.models import ClientProfile
    from settings_core.setup_services import complete_setup

    from .demo import _name_the_demo_shop

    modules, features = interview.resolve(activity, sub_activity, interview.suggested_answers(activity, sub_activity))
    ai = [slug for slug in AI_CAPABILITIES if set(caps.needs(slug)) <= set(modules)]
    profile = ClientProfile.get_active()
    complete_setup(profile, activity, sub_activity, ",".join(modules), user=user,
                   capabilities_raw=",".join(demo_capabilities(activity, sub_activity, list(features) + ai)))
    _name_the_demo_shop()
    name_the_demo_owner(activity, sub_activity)
    return profile


def open_owner_shift():
    """The owner's shift is open too, so the till works on the first tap (AN4 needs an open shift per cashier).

    A shift left open from an earlier day (a template built days ago) is closed at exactly the cash it expects, so
    there is no difference and nothing is posted, and today's is opened in its place."""

    from decimal import Decimal

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from cashboxes.models import Cashbox, CashboxKind
    from settings_core.setup_services import usable_modules
    from shifts.services import close_shift, open_shift, open_shift_for, summary

    if "cashboxes" not in set(usable_modules()):
        return None
    owner = get_user_model().objects.filter(username="owner").first()
    if owner is None:
        return None
    current = open_shift_for(owner)
    if current is not None:
        if timezone.localtime(current.opened_at).date() >= timezone.localdate():
            return None
        drawer = current.cashbox
        close_shift(current, summary(current)["expected"], owner)
    else:
        drawer = Cashbox.objects.filter(active=True).exclude(kind=CashboxKind.BANK).order_by("pk").first()
    if drawer is None:
        return None
    return open_shift(owner, drawer, Decimal("300"))


def top_up_current_db():
    """Bring a filled demo up to today: today's shift for the owner, then today's trade (both idempotent)."""

    from django.core.management import call_command

    open_owner_shift()
    call_command("seed_demo_business", username="owner", force=True, verbosity=0)


def seed_sample_business():
    """A month of sample trade for the active profile's business, through the real posting services.

    Shared by the old «املاها ببيانات تجريبية» button and the template builder. Idempotent: a shop that
    already has posted sales is left alone (FEEDBACK-R1)."""

    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import DatabaseError, transaction

    from sales.models import SalesInvoice
    from settings_core.management.commands.prepare_demo import Command as Prepare

    from .demo import _name_the_demo_shop

    if SalesInvoice.objects.filter(status="posted").exists():
        return False
    prepare = Prepare()
    try:
        with transaction.atomic():
            prepare._history()
            call_command("seed_demo_business", username="owner", force=True, verbosity=0)
            prepare._spread_sale_times()
            prepare._extras()
            _name_the_demo_shop()
            name_the_demo_owner()
            open_owner_shift()
    except CommandError:
        return False
    except DatabaseError:
        # Two presses at once: the other one is filling it in; this one rolled back whole.
        return False
    return True


# ---- building a template file ------------------------------------------------------------------

def _with_db(path, work):
    """Run ``work()`` with this thread's default connection on the SQLite file at ``path``, then put it back.

    The way ``prepare_demo_fresh`` does it; the base demo and other visitors' copies are never touched."""

    from django.db import connections

    from .demo_sandbox import _use

    if connections["default"].vendor != "sqlite":
        raise RuntimeError("Demo templates are SQLite files; this database is not SQLite.")
    original = connections["default"].settings_dict["NAME"]
    try:
        _use(str(path))
        work()
    finally:
        connections["default"].close()
        _use(original)


def demo_reference_list(activity, sub_activity):
    """R1: the pharmacy demo's reference drug list (illustrative prices), loaded into its template only. Returns the
    importer's summary, or None for every other business."""

    if (activity, sub_activity) != ("commercial", "pharmacy"):
        return None
    from pharmacy.demo import load_demo_reference

    return load_demo_reference()


def _populate(activity, sub_activity):
    from django.db import connections

    complete_demo_setup(activity, sub_activity)
    seed_sample_business()
    demo_reference_list(activity, sub_activity)
    with connections["default"].cursor() as cursor:
        cursor.execute("VACUUM")


def build_template(activity, sub_activity, force=False):
    """Build the populated template for (activity, sub) from the emptied demo.

    Returns its path once it exists (built here, or already there), or None when another builder (a web worker's
    thread, or prepare_demo_templates in another process) holds its lock: the caller skips it, never waits on it.
    The file appears all at once (os.replace), so nobody ever copies a half-built shop."""

    if not is_known(activity, sub_activity):
        raise ValueError(f"Not a demo activity: {activity}:{sub_activity}")
    target = populated_template(activity, sub_activity)
    if target == base_template():
        return target  # the retail shop is the base demo itself
    if target.is_file() and not force:
        return target
    source = fresh_template()
    if not source.is_file():
        raise FileNotFoundError(f"The emptied demo is missing: {source.name} (run prepare_demo_fresh first).")
    lock = lock_path(target)
    if not _acquire(lock):
        return None
    work = _work_file(target, "building")
    try:
        if target.is_file() and not force:
            return target  # built by whoever held the lock just before us
        copy_database(source, work)
        _with_db(work, lambda: _populate(activity, sub_activity))
        os.replace(work, target)
        failed_stamp(target).unlink(missing_ok=True)
        return target
    finally:
        _discard(work)
        lock.unlink(missing_ok=True)


def top_up_template(activity, sub_activity):
    """Bring a populated template up to today (today's trade, the owner's shift dated today), under its lock.

    The server's disk goes back to the build image every time it wakes, so a template built on deploy day would
    otherwise open N days later with nothing «today» and a shift from deploy day. Returns the path, or None when
    there is nothing to top up (the base demo, which prepare_demo tops up itself; a missing file) or it is locked."""

    target = populated_template(activity, sub_activity)
    if target == base_template() or not target.is_file():
        return None
    lock = lock_path(target)
    if not _acquire(lock):
        return None
    work = _work_file(target, "topup")
    try:
        if not target.is_file():
            return None
        copy_database(target, work)
        _with_db(work, top_up_current_db)
        os.replace(work, target)
        return target
    finally:
        _discard(work)
        lock.unlink(missing_ok=True)


def missing_templates():
    from .demo import DEMO_ACTIVITIES

    return [(activity, sub) for activity, sub in DEMO_ACTIVITIES if not populated_template(activity, sub).is_file()]


class BackgroundBuilder:
    """Builds a missing template in a thread of its own, once, while the visitor's page refreshes.

    Failures are remembered per template, here and in a ``.failed`` stamp next to it (so the other web worker sees
    them too): after MAX_ATTEMPTS, or once it has been failing for FAILURE_WINDOW_SECONDS, nobody starts it again
    until the failure is FORGET_FAILURE_SECONDS old, and the preparing page stops refreshing."""

    def __init__(self):
        self.lock = threading.Lock()
        self.threads = {}
        self.failures = {}  # (activity, sub) -> (count, first failure, last failure)

    def request(self, activity, sub_activity):
        """Start building unless it exists, is being built here or elsewhere, or keeps failing."""

        key = (activity, sub_activity)
        if populated_template(*key).is_file():
            return False
        with self.lock:
            thread = self.threads.get(key)
            if thread is not None and thread.is_alive():
                return False
            if is_building(*key) or self.gave_up(*key):
                return False  # start_demo.sh's builder or another worker is on it; or it keeps failing
            thread = threading.Thread(target=self._run, args=key, name=f"demo-template-{activity}-{sub_activity}", daemon=True)
            self.threads[key] = thread
            thread.start()
            return True

    def _run(self, activity, sub_activity):
        try:
            build_template(activity, sub_activity)
        except Exception:
            log.exception("DEMO-FIRST-RUN: building the %s:%s template failed.", activity, sub_activity)
            self.record_failure(activity, sub_activity)
        else:
            with self.lock:
                self.failures.pop((activity, sub_activity), None)

    def record_failure(self, activity, sub_activity, now=None):
        now = now or time.time()
        key = (activity, sub_activity)
        count, first, _ = self.failure(activity, sub_activity, now) or (0, now, now)
        with self.lock:
            self.failures[key] = (count + 1, first, now)
        try:
            failed_stamp(populated_template(*key)).write_text(json.dumps({"count": count + 1, "first": first, "last": now}))
        except OSError:
            pass  # remembered in this process at least

    def failure(self, activity, sub_activity, now=None):
        """(count, first, last) of the recent failures of this template, or None. The stamp counts for other processes."""

        now = now or time.time()
        known = [self.failures.get((activity, sub_activity))]
        try:
            stamp = json.loads(failed_stamp(populated_template(activity, sub_activity)).read_text())
            known.append((int(stamp["count"]), float(stamp["first"]), float(stamp["last"])))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        known = [row for row in known if row and now - row[2] < FORGET_FAILURE_SECONDS]
        return max(known) if known else None

    def gave_up(self, activity, sub_activity, now=None):
        now = now or time.time()
        row = self.failure(activity, sub_activity, now)
        return bool(row) and (row[0] >= MAX_ATTEMPTS or now - row[1] >= FAILURE_WINDOW_SECONDS)


builder = BackgroundBuilder()
