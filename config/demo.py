"""DEMO-001: what templates need to know about a showcase install."""

from django.conf import settings


def demo_context(request):
    if not getattr(settings, "DEMO_MODE", False):
        return {}
    return {"demo_mode": True, "demo_password": settings.DEMO_PASSWORD}


# ACT-PROFILE-002: on a showcase install the owner can flip the activity to see
# how the words, the menu and the dashboard change. Never on a real install.
DEMO_ACTIVITIES = (
    ("commercial", "retail"), ("commercial", "pharmacy"), ("restaurants", "restaurant"), ("medical", "clinic"),
    ("education", "training"), ("services", "maintenance"), ("manufacturing", "food"), ("contracting", "general"),
)


def demo_activity_choices(lang):
    from settings_core import setup_catalog as catalog

    return [(f"{a}:{s}", f"{catalog.activity_label(a, lang)} · {catalog.sub_activity_label(a, s, lang)}") for a, s in DEMO_ACTIVITIES]


def switch_activity(request):
    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    from django.shortcuts import redirect

    from permissions.services import user_has_permission
    from settings_core.models import ClientProfile

    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")
    if request.method != "POST":
        return redirect("dashboard_snapshot")
    if not user_has_permission(request.user, "settings.view_settings"):
        raise PermissionDenied("Owner only.")
    activity, _, sub = (request.POST.get("activity") or "").partition(":")
    if (activity, sub) in DEMO_ACTIVITIES:
        ClientProfile.objects.filter(is_active=True).update(activity_slug=activity, sub_activity_slug=sub)
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    return redirect(f"/dashboard/?lang={lang}")
