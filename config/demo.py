"""DEMO-001: what templates need to know about a showcase install."""

from django.conf import settings


def demo_context(request):
    if not getattr(settings, "DEMO_MODE", False):
        return {}
    from .demo_sandbox import enabled

    return {"demo_mode": True, "demo_password": settings.DEMO_PASSWORD, "demo_sandboxes": enabled()}


# ACT-PROFILE-002: on a showcase install the owner can flip the activity to see
# how the words, the menu and the dashboard change. Never on a real install.
DEMO_ACTIVITIES = (
    ("commercial", "retail"), ("commercial", "pharmacy"), ("restaurants", "restaurant"), ("medical", "clinic"),
    ("education", "training"), ("services", "maintenance"), ("manufacturing", "food"), ("contracting", "general"),
)


# TRACK-ACTS W02: the sample shop is called what it is ("صيدلية" for a pharmacy, not "محل").
DEMO_NAME = "محل حِسبة التجريبي"
DEMO_NAMES = {
    ("commercial", "grocery"): "سوبر ماركت حِسبة التجريبي", ("commercial", "pharmacy"): "صيدلية حِسبة التجريبية",
    ("commercial", "wholesale"): "حِسبة لتجارة الجملة (تجريبي)", ("commercial", "online"): "متجر حِسبة أونلاين (تجريبي)",
    ("restaurants", "cafe"): "كافيه حِسبة التجريبي", ("restaurants", "bakery"): "مخبز حِسبة التجريبي",
    ("medical", "dental"): "عيادة حِسبة للأسنان (تجريبي)", ("medical", "lab"): "معمل حِسبة للتحاليل (تجريبي)",
    ("medical", "medical_center"): "مركز حِسبة الطبي (تجريبي)", ("medical", "physio"): "مركز حِسبة للعلاج الطبيعي (تجريبي)",
    ("medical", "vet"): "عيادة حِسبة البيطرية (تجريبي)", ("education", "nursery"): "حضانة حِسبة (تجريبي)",
    ("services", "beauty"): "صالون حِسبة التجريبي",
    "restaurants": "مطعم حِسبة التجريبي", "medical": "عيادة حِسبة التجريبية", "education": "مركز حِسبة التعليمي (تجريبي)",
    "services": "حِسبة للخدمات (تجريبي)", "manufacturing": "مصنع حِسبة التجريبي", "contracting": "حِسبة للمقاولات (تجريبي)",
}


def demo_name(activity, sub_activity):
    return DEMO_NAMES.get((activity, sub_activity)) or DEMO_NAMES.get(activity) or DEMO_NAME


def _name_the_demo_shop():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    if profile and profile.display_name == DEMO_NAME:  # never a name the visitor typed
        profile.display_name = demo_name(profile.activity_slug, profile.sub_activity_slug)
        profile.save(update_fields=["display_name", "updated_at"])


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
        from settings_core import activity_fit
        from settings_core.setup_services import complete_setup

        profile = ClientProfile.get_active()
        # DEMO-FIT: the sub-activity's own suggestions too (a salon starts with appointments on).
        complete_setup(profile, activity, sub, ",".join(activity_fit.default_modules(activity, sub)), user=request.user)
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
    if getattr(request, "demo_fresh_copy", False):
        # DEMO-FAST: the visitor is already on a copy of the emptied demo.
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.get(username="owner")
    else:
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
    from django.db import DatabaseError, transaction

    from sales.models import SalesInvoice
    from settings_core.management.commands.prepare_demo import Command as Prepare

    # FEEDBACK-R1: a second press (double click, or back and press again) must
    # not try to add the same month twice; that failed on duplicate numbers.
    if not SalesInvoice.objects.filter(status="posted").exists():
        prepare = Prepare()
        try:
            with transaction.atomic():
                prepare._history()
                call_command("seed_demo_business", username="owner", force=True, verbosity=0)
                prepare._spread_sale_times()
                prepare._extras()
                _name_the_demo_shop()
        except CommandError:
            pass
        except DatabaseError:
            # Two presses at once: the other one is filling it in; this one
            # rolled back whole, so nothing is half-written.
            pass
    return redirect(f"/dashboard/?lang={lang}")
