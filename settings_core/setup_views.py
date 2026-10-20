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
        "step_modules": "الأقسام",
        "step_review": "المراجعة",
        "title": "راجع إعدادات نشاطك",
        "subtitle": "راجع اختياراتك قبل ما تنهي الإعداد.",
        "activity_summary": "ملخص النشاط",
        "general_activity": "النشاط العام",
        "sub_activity_title": "النشاط الفرعي",
        "selected_modules_title": "الأقسام المختارة",
        "settings_note": "ملاحظة الإعدادات",
        "important_note": "تقدر تغيّر الأقسام بعدين من الإعدادات، ومفيش بيانات بتتمسح لما توقف قسم.",
        "capabilities_title": "خصائص مقترحة لنشاطك",
        "capabilities_lead": "علّمنا على اللي بيناسب نشاطك. تقدر تغيّرها بعدين من الإعدادات.",
        "suggested_badge": "مقترح",
        "soon_badge": "قريبًا",
        "empty_modules": "لم يتم اختيار أقسام بعد.",
        "back": "الرجوع إلى اختيار الأقسام",
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
        "capabilities_title": "Capabilities suggested for your business",
        "capabilities_lead": "We ticked what suits your activity. You can change it later from Settings.",
        "suggested_badge": "Suggested",
        "soon_badge": "Coming soon",
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
        "step_modules": "الأقسام",
        "step_review": "المراجعة",
        "title": "تم إنهاء الإعداد",
        "subtitle": "اتحفظت إعدادات نشاطك، وحِسبة جاهزة.",
        "message": "اتسجل نشاطك والأقسام اللي اخترتها. تقدر تغيّرهم بعدين من الإعدادات من غير ما بيانات تتمسح.",
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


#: CONTRACT-002: what the activity's own module does, shown on the review step.
ACTIVITY_FEATURES = {
    catalog.CONTRACTING: {
        "ar": ("المقايسة: بنود الأعمال بالكمية والفئة، وإجماليها هو قيمة العقد.",
               "المستخلصات بالكميات المنفذة حتى تاريخه (السابق والحالي والإجمالي)، وطباعة المستخلص.",
               "ضمان الأعمال بيتحجز من كل مستخلص لحد الاستلام، والإفراج عنه بعدها.",
               "الدفعة المقدمة من صاحب المشروع، وبتتخصم من كل مستخلص بنسبة متفق عليها.",
               "مقاولو الباطن: إسناد الأعمال، مستخلصاتهم كفواتير شراء، وضمان الأعمال المحتجز منهم.",
               "موازنة كل مشروع (خامات، مقاولو باطن، عمالة، معدات) قصاد التكلفة الفعلية."),
        "en": ("Bill of quantities: each item by quantity and rate; its total is the contract value.",
               "Progress certificates by quantity done to date (previous, current, cumulative), printable.",
               "Retention held from each certificate until handover, then released.",
               "The owner's advance, recovered from each certificate at the agreed rate.",
               "Subcontractors: work given, their bills as purchase invoices, the retention held from them.",
               "Each project's budget (materials, subcontractors, labour, equipment) against actual cost."),
    },
}


def _review_features(activity, sub_activity, modules_param, lang):
    """ACT-FIT: what comes with the activity in the chosen modules; a contractor's projects say what they do in detail."""

    from . import activity_fit

    chosen = set(modules_param.split(",")) | set(catalog.required_modules(catalog.closest(activity, sub_activity)[0]))
    detail = ACTIVITY_FEATURES.get(activity, {}).get(lang, ()) if "projects" in chosen else ()
    lines = [f"{feature['label']}: {feature['about']}" for feature in activity_fit.features(activity, sub_activity, lang)
             if feature["module"] in chosen and not (detail and feature["module"] == "projects")]
    return lines + list(detail)


def _fit_rows(activity, sub_activity):
    """ACT-FIT: the modules offered to this activity, with both languages' words for the page's language switch."""

    from . import activity_fit

    from .operational_views import MODULE_ICONS
    from .setup_services import module_needs

    arabic, english = activity_fit.module_rows(activity, sub_activity, "ar"), activity_fit.module_rows(activity, sub_activity, "en")
    names = {ar["slug"]: (ar["label"], en["label"]) for ar, en in zip(arabic, english)}
    rows = []
    for ar, en in zip(arabic, english):
        # SETUP-WIZARD: what this module needs and what needs it, among the modules offered here (the page switches them together).
        needs = [slug for slug in module_needs(ar["slug"]) if slug in names]
        needed_by = [slug for slug in names if ar["slug"] in module_needs(slug)]
        rows.append({"slug": ar["slug"], "preset": ar["preset"], "label_ar": ar["label"], "label_en": en["label"], "about_ar": ar["about"],
                     "about_en": en["about"], "features": [{"label_ar": a["label"], "label_en": e["label"]} for a, e in zip(ar["features"], en["features"])],
                     "icon": MODULE_ICONS.get(ar["slug"], "modules"), "needs": ",".join(needs), "needed_by": ",".join(needed_by),
                     "needs_names": [{"ar": names[slug][0], "en": names[slug][1]} for slug in needs],
                     "needed_by_names": [{"ar": names[slug][0], "en": names[slug][1]} for slug in needed_by]})
    features = [{"slug": a["slug"], "label_ar": a["label"], "label_en": e["label"], "about_ar": a["about"], "about_en": e["about"]}
                for a, e in zip(activity_fit.features(activity, sub_activity, "ar"), activity_fit.features(activity, sub_activity, "en"))]
    return rows, features


def setup_modules(request):
    """Step 3: the modules that fit the chosen activity and sub-activity, most needed first."""

    activity = request.GET.get("activity", "")
    sub_activity = request.GET.get("sub_activity", "")
    if not catalog.is_valid_activity(activity):
        activity, sub_activity = "", ""
    rows, features = _fit_rows(activity or catalog.COMMERCIAL, sub_activity)
    groups = [{"key": key, "rows": [row for row in rows if row["preset"] == key]} for key in ("required", "suggested", "optional", "soon")]
    label_ar = catalog.sub_activity_label(activity, sub_activity, "ar") if catalog.is_valid_sub_activity(activity, sub_activity) else catalog.activity_label(activity or catalog.COMMERCIAL, "ar")
    label_en = catalog.sub_activity_label(activity, sub_activity, "en") if catalog.is_valid_sub_activity(activity, sub_activity) else catalog.activity_label(activity or catalog.COMMERCIAL, "en")
    return render(request, "setup/modules_selection.html", {"module_rows": rows, "module_groups": [group for group in groups if group["rows"]], "fit_features": features, "fit_activity": activity, "fit_sub": sub_activity,
                                                             "fit_label_ar": label_ar, "fit_label_en": label_en})


def _capability_rows(activity, sub_activity, lang):
    """CAP-001: the review step's capability list, suggestions ticked."""

    from . import activity_fit, capabilities

    activity, sub_activity = catalog.closest(activity, sub_activity)  # OTHER-001: offered what the closest activity is offered
    rows = []
    for slug in capabilities.CAPABILITY_SLUGS:
        entry = capabilities.CAPABILITIES[slug]
        if not capabilities.fits(activity, slug, sub_activity):  # AUDIT-3 + SETTINGS-TRACK: only what fits the (sub-)activity
            continue
        is_suggested = capabilities.preset_state(activity, sub_activity, slug) == capabilities.SUGGESTED
        rows.append({
            "slug": slug,
            "label": capabilities.label(slug, lang),
            "about": capabilities.about(activity, slug, lang),
            "available": entry["available"],
            "suggested": is_suggested,
            "checked": entry["available"] and is_suggested,
            # SETUP-WIZARD: the same facts the features screen shows, so the choice is made knowing them.
            "icon": capabilities.ICONS.get(slug, "features"),
            "where": capabilities.WHERE.get(slug, ("", ""))[1 if lang == "en" else 0],
            "group": capabilities.group_of(slug),
            "needs": [{"slug": module, "label": activity_fit.module_text(activity, sub_activity, module, lang)[0]} for module in capabilities.needs(slug)],
            "needs_cap": "pos" if slug == "shift_required" else "",
        })
    return rows


def _capability_groups(rows, lang):
    """SETUP-WIZARD: the review step's features in the features screen's groups."""

    from . import capabilities

    index = 1 if lang == "en" else 0
    return [{"key": key, "title": titles[index], "rows": [row for row in rows if row["group"] == key]}
            for key, titles, _ in capabilities.GROUPS if any(row["group"] == key for row in rows)]


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
        if slug not in catalog.MODULES_WITHOUT_BACKEND
    ]

    capability_rows = _capability_rows(activity, sub_activity, lang)
    if "caps" in request.GET:  # SETUP-WIZARD: the ticks made before switching language
        ticked = set(request.GET.get("caps", "").split(","))
        for row in capability_rows:
            row["checked"] = row["available"] and row["slug"] in ticked
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
        "capability_rows": capability_rows,
        "capability_groups": _capability_groups(capability_rows, lang),
        "activity_features": _review_features(activity, sub_activity, modules_param, lang),
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
            capabilities_raw=",".join(request.POST.getlist("capability")) if request.POST.get("capabilities_sent") else None,
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
        return redirect(_role_home(request.user))

    return redirect("setup_gate")


#: HG-048: someone whose whole job is one screen starts on it: a waiter on the floor, a site engineer on
#: the projects. Anyone who also sees the sales side (a cashier, a manager) keeps the dashboard.
ROLE_HOMES = (("restaurant.take_orders", "restaurant:board", "tables_orders"), ("projects.view_projects", "projects:list", "projects"))


def _role_home(user):
    from permissions.services import user_has_permission

    from .setup_services import usable_modules

    if not user_has_permission(user, "sales.view_sales_invoices"):
        modules = set(usable_modules())
        for code, url_name, module in ROLE_HOMES:
            if module in modules and user_has_permission(user, code):
                return url_name
    return "dashboard_snapshot"


@login_not_required
def root_redirect(request):
    if request.user.is_authenticated:
        return redirect("after_login")

    return redirect("login")


#: ACT-002: the wording of the sub-activity step for activities that share one template.
SUB_STEP_COPY = {
    catalog.MEDICAL: {
        "ar": {"title": "اختر نوع النشاط الطبي", "subtitle": "اختيار النوع يساعد حِسْبَة في تجهيز المواعيد والكشوفات والفواتير."},
        "en": {"title": "Choose the medical activity type", "subtitle": "Choosing the type helps Hesba prepare appointments, visits and billing."},
    },
    catalog.EDUCATION: {
        "ar": {"title": "اختر نوع النشاط التعليمي", "subtitle": "اختيار النوع يساعد حِسْبَة في تجهيز الطلبة والمواعيد والمصاريف."},
        "en": {"title": "Choose the education activity type", "subtitle": "Choosing the type helps Hesba prepare students, sessions and fees."},
    },
    catalog.CONTRACTING: {
        "ar": {"title": "اختر نوع المقاولات", "subtitle": "اختيار النوع يساعد حِسْبَة في تجهيز المشاريع والمستخلصات والخامات."},
        "en": {"title": "Choose the contracting type", "subtitle": "Choosing the type helps Hesba prepare projects, progress bills and materials."},
    },
    catalog.MANUFACTURING: {
        "ar": {"title": "اختر نوع التصنيع", "subtitle": "اختيار النوع يساعد حِسْبَة في تجهيز الوصفات والخامات والتشغيلات."},
        "en": {"title": "Choose the manufacturing type", "subtitle": "Choosing the type helps Hesba prepare recipes, materials and production runs."},
    },
    catalog.OTHER: {
        "ar": {"title": "شغلك أقرب لإيه؟", "subtitle": "حِسْبَة هتجهّز لك الشاشات والكلمات والتقارير بتاعة أقرب نشاط. وتقدر تغيّر الأقسام بعدين من الإعدادات."},
        "en": {"title": "What is your work closer to?", "subtitle": "Hesba sets up the screens, words and reports of the closest activity. You can change the modules later in settings."},
    },
}
SUB_STEP_COMMON = {
    "ar": {"logout": "تسجيل الخروج", "language": "العربية", "stepGeneral": "النشاط العام", "stepSub": "النشاط الفرعي", "stepModules": "الأقسام",
           "stepReview": "المراجعة", "next": "التالي: اختيار الأقسام", "back": "الرجوع لاختيار النشاط العام", "changeLanguage": "تغيير اللغة"},
    "en": {"logout": "Logout", "language": "English", "stepGeneral": "General activity", "stepSub": "Sub activity", "stepModules": "Modules",
           "stepReview": "Review", "next": "Next: choose modules", "back": "Back to general activity", "changeLanguage": "Change language"},
}


def sub_activity_step(request, activity):
    """The sub-activity cards for an activity, straight from the catalog."""

    copy = {}
    for lang in ("ar", "en"):
        words = dict(SUB_STEP_COMMON[lang], **SUB_STEP_COPY[activity][lang])
        words["pageTitle"] = f"{words['title']} - {'حِسْبَة' if lang == 'ar' else 'Hesba'}"
        words.update({slug: labels[lang] for slug, labels in catalog.SUB_ACTIVITY_LABELS[activity].items()})
        copy[lang] = words
    subs = [{"slug": slug, "ar": labels["ar"]} for slug, labels in catalog.SUB_ACTIVITY_LABELS[activity].items()]
    return render(request, "setup/activity_generic_subactivity.html", {"activity": activity, "subs": subs, "copy": copy})


QUESTION_STRINGS = {
    "ar": {"title": "خلينا نعرف شغلك ماشي إزاي", "lead": "أسئلة بكلام نشاطك، والإجابات متعلّمة باقتراحنا لنشاطك. غيّر اللي مش مظبوط، أو دوس «جهّز حسبة» على طول. وتقدر تغيّر أي حاجة بعدين من الإعدادات.",
           "yes": "أيوه", "no": "لأ", "gives": "هيظهرلك", "summary": "حسبة هتجهّزلك", "always": "وده موجود دايمًا لنشاطك",
           "finish": "جهّز حسبة", "back": "الرجوع لنوع النشاط", "manual": "عايز تختار الأقسام والخصائص بنفسك؟", "suggested": "اقتراحنا لنشاطك",
           "accept": "خليها على اقتراحنا", "count": "{n} من {total}", "next_q": "السؤال اللي بعده", "page_title": "شغلك ماشي إزاي - حِسْبَة"},
    "en": {"title": "Tell us how your business works", "lead": "Questions in your trade's words, already answered with our suggestion for your business. Change what is wrong, or press “Set Hesba up” straight away; anything can change later in Settings.",
           "yes": "Yes", "no": "No", "gives": "You get", "summary": "Hesba will set up", "always": "Always there for your business",
           "finish": "Set Hesba up", "back": "Back to the kind of business", "manual": "Rather choose the modules and features yourself?", "suggested": "Suggested for you",
           "accept": "Keep our suggestions", "count": "{n} of {total}", "next_q": "Next question", "page_title": "How your business works - Hesba"},
}


def setup_questions(request):
    """SETUP-INTERVIEW (ST6): step 3, the business described in its own words (settings_core/setup_interview.py).

    GET asks; POST turns the answers into modules and features and saves the setup as the old
    review step did."""

    from . import setup_interview as interview
    from .activity_fit import module_text

    lang = _lang(request)
    data = request.POST if request.method == "POST" else request.GET
    activity, sub_activity = data.get("activity", ""), data.get("sub_activity", "")
    if not catalog.is_valid_activity(activity) or not catalog.is_valid_sub_activity(activity, sub_activity):
        return redirect(f"{reverse('setup_activity')}?lang={_query_value(lang)}")
    if request.method == "POST":
        profile = ClientProfile.get_active()
        yes = request.POST.getlist("yes") + [name[2:] for name, value in request.POST.items() if name.startswith("q_") and value == "yes"]
        modules, features = interview.resolve(activity, sub_activity, yes)
        if profile is None:
            return redirect(f"{reverse('setup_activity')}?lang={_query_value(lang)}")
        complete_setup(profile, activity=activity, sub_activity=sub_activity, modules_raw=",".join(modules),
                       user=request.user, capabilities_raw=",".join(features))
        return redirect(f"{reverse('setup_complete')}?lang={_query_value(lang)}")
    rows = interview.questions(activity, sub_activity, lang)
    from . import activity_fit

    always = [module_text(activity, sub_activity, row["slug"], lang)[0]
              for row in activity_fit.module_rows(activity, sub_activity, lang) if row["preset"] == catalog.REQUIRED]
    always += [feature["label"] for feature in activity_fit.features(activity, sub_activity, lang)]
    context = {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "questions": rows, "activity": activity, "sub_activity": sub_activity,
        "activity_label": catalog.activity_label(activity, lang), "sub_activity_label": catalog.sub_activity_label(activity, sub_activity, lang),
        "always": always, "back_href": f"/setup/activity/{activity}/?lang={_query_value(lang)}",
        "manual_href": _setup_modules_href(lang, activity, sub_activity, ""), "words": QUESTION_STRINGS[lang],
        "other_lang": "ar" if lang == "en" else "en",
    }
    return render(request, "setup/questions.html", context)
