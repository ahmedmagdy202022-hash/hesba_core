"""Turning the setup wizard's answers into rows.

Until now the wizard carried its answers in the query string and threw them away
at the end. These services write them down: the activity and sub-activity land on
the client profile, and each module becomes a feature flag so it can be switched
later from Settings without touching data.
"""

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import setup_catalog as catalog
from .models import FeatureFlag


MODULE_FLAG_PREFIX = "module."


def module_flag_code(slug):
    return f"{MODULE_FLAG_PREFIX}{slug}"


def enabled_modules():
    """Slugs switched on for this installation, in the wizard's own order."""

    enabled = set(
        FeatureFlag.objects.filter(
            code__startswith=MODULE_FLAG_PREFIX, enabled=True
        ).values_list("code", flat=True)
    )
    return tuple(
        slug for slug in catalog.MODULE_SLUGS if module_flag_code(slug) in enabled
    )


def module_is_enabled(slug):
    return FeatureFlag.objects.filter(code=module_flag_code(slug), enabled=True).exists()


def usable_modules():
    """Enabled modules that Hesba can actually serve today.

    A module can be switched on in setup and still have no model behind it —
    expenses and appointments are offered but unimplemented. Screens should ask
    for this list so they never advertise a section that cannot show anything.
    """

    return tuple(
        slug for slug in enabled_modules() if slug not in catalog.MODULES_WITHOUT_BACKEND
    )


def _write_module_flags(chosen):
    chosen = set(chosen)
    for slug in catalog.MODULE_SLUGS:
        FeatureFlag.objects.update_or_create(
            code=module_flag_code(slug),
            defaults={
                "name": catalog.module_label(slug, "en"),
                "description": catalog.module_label(slug, "ar"),
                "enabled": slug in chosen,
            },
        )


@transaction.atomic
def complete_setup(profile, activity, sub_activity, modules_raw, user=None):
    """Record the setup decision and mark the installation ready.

    ``modules_raw`` is the wizard's comma-separated list. It is normalised through
    the catalog, so unknown slugs are dropped and required modules are added back
    even if the request left them out.

    Safe to call more than once: a resubmitted wizard overwrites the same rows and
    keeps the original completion time.
    """

    if not catalog.is_valid_activity(activity):
        raise ValueError(f"Unknown activity: {activity!r}")
    if not catalog.is_valid_sub_activity(activity, sub_activity):
        raise ValueError(f"Unknown sub-activity for {activity}: {sub_activity!r}")

    chosen = catalog.clean_module_slugs(activity, modules_raw)

    before = {
        "activity_slug": profile.activity_slug,
        "sub_activity_slug": profile.sub_activity_slug,
        "modules": list(enabled_modules()),
        "setup_completed_at": profile.setup_completed_at.isoformat()
        if profile.setup_completed_at
        else None,
    }

    profile.activity_slug = activity
    profile.sub_activity_slug = sub_activity
    profile.activity_type = catalog.ACTIVITY_TYPE_BY_SLUG[activity]
    profile.setup_completed_at = profile.setup_completed_at or timezone.now()
    profile.save()

    _write_module_flags(chosen)

    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user if user is not None and user.is_authenticated else None,
        module="settings",
        action="complete_setup",
        object_type="settings_core.ClientProfile",
        object_id=str(profile.pk),
        before_data=before,
        after_data={
            "activity_slug": activity,
            "sub_activity_slug": sub_activity,
            "activity_type": profile.activity_type,
            "modules": list(chosen),
            "setup_completed_at": profile.setup_completed_at.isoformat(),
        },
        reason="Initial setup completed from the setup wizard.",
    )

    return profile
