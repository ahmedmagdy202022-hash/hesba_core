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
in the background after the server is up). A template that is still missing when
a visitor asks for it is built on demand in a background thread while they see a
short "بنجهّز نسختك…" page that refreshes, so no request ever runs the seeding.

Budget (measured locally, 9 Nov 2026): one populated template is ~8–12 s to seed
and ~5 MB on disk; all eight extra ones are about 40 MB and under two minutes in
one process. The emptied demo is shared by every activity: «ابدأ فاضي» copies it
and runs the activity's setup presets on the copy (milliseconds), which spares
eight more 4.5 MB files and eight 30 s rebuilds.
"""

import logging
import os
import threading
import time
from pathlib import Path

from django.conf import settings

log = logging.getLogger("hesba.demo")

#: A `.building` file older than this is a crashed build and may be redone.
STALE_BUILD_SECONDS = 15 * 60


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


def building_marker(target):
    return Path(f"{target}.building")


def is_building(activity, sub_activity, now=None):
    marker = building_marker(populated_template(activity, sub_activity))
    try:
        age = (now or time.time()) - marker.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < STALE_BUILD_SECONDS


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
    """The owner's shift is open too, so the till works on the first tap (AN4 needs an open shift per cashier)."""

    from decimal import Decimal

    from django.contrib.auth import get_user_model

    from cashboxes.models import Cashbox, CashboxKind
    from settings_core.setup_services import usable_modules
    from shifts.services import open_shift, open_shift_for

    if "cashboxes" not in set(usable_modules()):
        return None
    owner = get_user_model().objects.filter(username="owner").first()
    drawer = Cashbox.objects.filter(active=True).exclude(kind=CashboxKind.BANK).order_by("pk").first()
    if owner is None or drawer is None or open_shift_for(owner) is not None:
        return None
    return open_shift(owner, drawer, Decimal("300"))


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

def build_template(activity, sub_activity, force=False):
    """Build the populated template for (activity, sub) from the emptied demo. Returns its path.

    Runs in the calling thread against the thread's own default connection (``_use``), the way
    ``prepare_demo_fresh`` does; the base demo and other visitors' copies are never touched."""

    from django.db import connections

    from .demo_sandbox import _use, copy_database

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
    partial = building_marker(target)
    copy_database(source, partial)
    original = connections["default"].settings_dict["NAME"]
    try:
        _use(str(partial))
        complete_demo_setup(activity, sub_activity)
        seed_sample_business()
        with connections["default"].cursor() as cursor:
            cursor.execute("VACUUM")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        connections["default"].close()
        _use(original)
    os.replace(partial, target)
    return target


def missing_templates():
    from .demo import DEMO_ACTIVITIES

    return [(activity, sub) for activity, sub in DEMO_ACTIVITIES if not populated_template(activity, sub).is_file()]


class BackgroundBuilder:
    """Builds a missing template in a thread of its own, once, while the visitor's page refreshes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.threads = {}

    def request(self, activity, sub_activity):
        """Start building unless it exists, is being built here, or another process is building it."""

        key = (activity, sub_activity)
        if populated_template(*key).is_file():
            return False
        with self.lock:
            thread = self.threads.get(key)
            if thread is not None and thread.is_alive():
                return False
            if is_building(*key):
                return False  # start_demo.sh's builder, or another worker, is on it
            thread = threading.Thread(target=self._run, args=key, name=f"demo-template-{activity}-{sub_activity}", daemon=True)
            self.threads[key] = thread
            thread.start()
            return True

    @staticmethod
    def _run(activity, sub_activity):
        try:
            build_template(activity, sub_activity)
        except Exception:
            log.exception("DEMO-FIRST-RUN: building the %s:%s template failed.", activity, sub_activity)


builder = BackgroundBuilder()
