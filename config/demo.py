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
        # NAV-ACT: switch the modules and capabilities too, exactly as the setup
        # wizard would for this activity, so the menu shows what it would use.
        from settings_core import setup_catalog as catalog
        from settings_core.setup_services import complete_setup

        profile = ClientProfile.get_active()
        complete_setup(profile, activity, sub, ",".join(catalog.default_modules(activity)), user=request.user)
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    return redirect(f"/dashboard/?lang={lang}")


def restart(request):
    """POST from the login page or the demo bar: start the showcase over."""

    from django.contrib.auth import login
    from django.http import Http404
    from django.shortcuts import redirect

    from .demo_restart import restart_demo

    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    if request.method != "POST":
        return redirect(f"/login/?lang={lang}")
    owner = restart_demo()
    login(request, owner, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(f"/setup/activity/?lang={lang}")


def sample(request):
    """After choosing the activity on a restarted demo: a month of sample trade."""

    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    from django.shortcuts import redirect

    from permissions.services import user_has_permission

    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    if request.method != "POST":
        return redirect(f"/dashboard/?lang={lang}")
    if not user_has_permission(request.user, "settings.view_settings"):
        raise PermissionDenied("Owner only.")
    from django.core.management import call_command
    from django.core.management.base import CommandError

    from settings_core.management.commands.prepare_demo import Command as Prepare

    prepare = Prepare()
    try:
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._spread_sale_times()
        prepare._extras()
    except CommandError:
        pass
    return redirect(f"/dashboard/?lang={lang}")
