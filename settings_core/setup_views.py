"""Views for the tail of the setup wizard, plus the post-login gate.

The earlier wizard steps are static templates that carry their answers in the
query string. Only the last step needs the server: it is where the decision is
written down. This module also owns the gate that decides, right after sign-in,
whether someone still needs to run setup or should go straight to work.
"""

from urllib.parse import quote

from django.contrib.auth.decorators import login_not_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import setup_catalog as catalog
from .models import ClientProfile
from .setup_services import complete_setup, enabled_modules


def _lang(request):
    value = request.POST.get("lang") if request.method == "POST" else request.GET.get("lang")
    return "en" if value == "en" else "ar"


def _query_value(value):
    return quote(value or "", safe=",")


def _setup_modules_href(lang, activity, sub_activity, modules):
    return (
        f"/setup/modules/?lang={_query_value(lang)}"
        f"&activity={_query_value(activity)}"
        f"&sub_activity={_query_value(sub_activity)}"
        f"&modules={_query_value(modules)}"
    )


def _setup_review_href(lang, activity="", sub_activity="", modules=""):
    return (
        f"/setup/review/?lang={_query_value(lang)}"
        f"&activity={_query_value(activity)}"
        f"&sub_activity={_query_value(sub_activity)}"
        f"&modules={_query_value(modules)}"
    )


REVIEW_STRINGS = {
    "ar": {
        "page_title": "راجع إعدادات نشاطك - حِسْبَة",
        "logout": "تسجيل الخروج",
        "language": "العربية",
        "step_general": "النشاط العام",
        "step_sub": "النشاط الفرعي",
        "step_modules": "الموديولات",
        "step_review": "المراجعة",
        "title": "راجع إعدادات نشاطك",
        "subtitle": "تأكد من الاختيارات التالية قبل إنهاء إعداد حِسْبَة لنشاطك.",
        "activity_summary": "ملخص النشاط",
        "general_activity": "النشاط العام",
        "sub_activity_title": "النشاط الفرعي",
        "selected_modules_title": "الموديولات المختارة",
        "settings_note": "ملاحظة الإعدادات",
        "important_note": "يمكنك تعديل الموديولات لاحقًا من الإعدادات، ولن يتم حذف أي بيانات عند تعطيل موديول.",
        "empty_modules": "لم يتم اختيار موديولات بعد.",
        "back": "الرجوع إلى اختيار الموديولات",
        "next": "إنهاء الإعداد",
    },
    "en": {
        "page_title": "Review your setup - Hesba",
        "logout": "Logout",
        "language": "English",
        "step_general": "General activity",
        "step_sub": "Sub activity",
        "step_modules": "Modules",
        "step_review": "Review",
        "title": "Review your setup",
        "subtitle": "Confirm the following choices before finishing your Hesba setup.",
        "activity_summary": "Activity summary",
        "general_activity": "General activity",
        "sub_activity_title": "Sub-activity",
        "selected_modules_title": "Selected modules",
        "settings_note": "Settings note",
        "important_note": "You can adjust modules later from Settings. Disabling a module will not delete any existing data.",
        "empty_modules": "No modules selected yet.",
        "back": "Back to modules selection",
        "next": "Finish setup",
    },
}


COMPLETE_STRINGS = {
    "ar": {
        "page_title": "اكتمال الإعداد - حِسْبَة",
        "logout": "تسجيل الخروج",
        "language": "العربية",
        "step_general": "النشاط العام",
        "step_sub": "النشاط الفرعي",
        "step_modules": "الموديولات",
        "step_review": "المراجعة",
        "title": "تم إنهاء الإعداد",
        "subtitle": "تم حفظ إعدادات نشاطك، وحِسْبَة جاهزة للعمل.",
        "message": "تم تسجيل النشاط والموديولات المختارة. يمكنك تعديلها لاحقًا من الإعدادات بدون حذف أي بيانات.",
        "back": "الرجوع إلى المراجعة",
        "next": "الانتقال إلى لوحة القيادة",
    },
    "en": {
        "page_title": "Setup complete - Hesba",
        "logout": "Logout",
        "language": "English",
        "step_general": "General activity",
        "step_sub": "Sub activity",
        "step_modules": "Modules",
        "step_review": "Review",
        "title": "Setup complete",
        "subtitle": "Your activity settings are saved and Hesba is ready to use.",
        "message": "Your activity and selected modules are recorded. You can adjust them later from Settings without deleting any data.",
        "back": "Back to review",
        "next": "Go to the dashboard",
    },
}


def setup_review(request):
    lang = _lang(request)
    activity = request.GET.get("activity", "")
    sub_activity = request.GET.get("sub_activity", "")
    modules_param = request.GET.get("modules", "")
    selected_modules = [
        {
            "slug": slug,
            "label_ar": catalog.module_label(slug, "ar"),
            "label_en": catalog.module_label(slug, "en"),
            "label": catalog.module_label(slug, lang),
        }
        for slug in catalog.parse_module_slugs(modules_param)
    ]

    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "activity": activity,
        "sub_activity_slug": sub_activity,
        "modules_param": modules_param,
        "selected_modules": selected_modules,
        "activity_label": catalog.activity_label(activity, lang),
        "sub_activity_label": catalog.sub_activity_label(activity, sub_activity, lang),
        "activity_label_ar": catalog.activity_label(activity, "ar"),
        "activity_label_en": catalog.activity_label(activity, "en"),
        "sub_activity_label_ar": catalog.sub_activity_label(activity, sub_activity, "ar"),
        "sub_activity_label_en": catalog.sub_activity_label(activity, sub_activity, "en"),
        "back_href": _setup_modules_href(lang, activity, sub_activity, modules_param),
        "complete_url": reverse("setup_complete"),
        **REVIEW_STRINGS[lang],
    }
    return render(request, "setup/review_setup.html", context)


@require_http_methods(["GET", "POST"])
def setup_complete(request):
    """Write the setup decision down, then confirm it.

    A POST persists the wizard's answers and redirects back here as a GET, so a
    refresh cannot resubmit. The GET is the confirmation step, and unlike the
    placeholder it replaced it offers a way forward to the dashboard.
    """

    lang = _lang(request)

    if request.method == "POST":
        return _save_setup(request, lang)

    profile = ClientProfile.get_active()
    if profile is None or not profile.setup_is_complete:
        # Nothing has been saved, so there is nothing to confirm. Send the
        # visitor back to make the choices rather than showing a hollow page.
        return redirect(_setup_review_href(lang))

    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "review_href": _setup_review_href(
            lang, profile.activity_slug, profile.sub_activity_slug, ",".join(enabled_modules())
        ),
        "dashboard_url": reverse("dashboard_snapshot"),
        **COMPLETE_STRINGS[lang],
    }
    return render(request, "setup/setup_complete_placeholder.html", context)


def _save_setup(request, lang):
    activity = request.POST.get("activity", "")
    sub_activity = request.POST.get("sub_activity", "")
    modules_param = request.POST.get("modules", "")

    profile = ClientProfile.get_active()
    if profile is None:
        # Setup cannot be recorded before the installation exists. Bootstrap
        # creates it, so this only happens on a database nobody prepared.
        return redirect(_setup_review_href(lang, activity, sub_activity, modules_param))

    try:
        complete_setup(
            profile,
            activity=activity,
            sub_activity=sub_activity,
            modules_raw=modules_param,
            user=request.user,
        )
    except ValueError:
        # An unrecognised activity or sub-activity means the wizard was skipped
        # or tampered with. Return to review so the choices can be made properly.
        return redirect(_setup_review_href(lang, activity, sub_activity, modules_param))

    return redirect(f"{reverse('setup_complete')}?lang={_query_value(lang)}")


def after_login(request):
    """Send a signed-in user wherever they actually need to be.

    This is the fix for the wall the wizard used to be: login sent everyone to
    setup and setup had no way out, so a finished installation kept landing back
    at its own first-run screen.
    """

    profile = ClientProfile.get_active()
    if profile is not None and profile.setup_is_complete:
        return redirect("dashboard_snapshot")

    return redirect("setup_gate")


@login_not_required
def root_redirect(request):
    if request.user.is_authenticated:
        return redirect("after_login")

    return redirect("login")
