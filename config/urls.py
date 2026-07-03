from urllib.parse import quote

from django.contrib import admin as django_admin
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import render
from django.urls import path
from django.views.generic import RedirectView, TemplateView

from reports.status_views import status_counts_report
from reports.views import dashboard_snapshot, home, report_hub


MODULE_LABELS = {
    "customers": {"ar": "العملاء", "en": "Customers"},
    "suppliers": {"ar": "الموردون", "en": "Suppliers"},
    "items_services": {"ar": "الأصناف والخدمات", "en": "Items & services"},
    "sales_operations": {"ar": "عمليات البيع", "en": "Sales operations"},
    "purchases": {"ar": "المشتريات", "en": "Purchases"},
    "inventory": {"ar": "المخزون", "en": "Inventory"},
    "cashboxes": {"ar": "الخزن", "en": "Cashboxes"},
    "expenses": {"ar": "المصروفات", "en": "Expenses"},
    "reports": {"ar": "التقارير", "en": "Reports"},
    "pdf_printing": {"ar": "طباعة PDF", "en": "PDF printing"},
    "appointments_visits": {"ar": "المواعيد والزيارات", "en": "Appointments & visits"},
    "employees_technicians": {"ar": "الموظفون والفنيون", "en": "Employees & technicians"},
}

ACTIVITY_LABELS = {
    "commercial": {"ar": "نشاط تجاري", "en": "Commercial"},
    "services": {"ar": "نشاط خدمي", "en": "Services"},
}

SUB_ACTIVITY_LABELS = {
    "commercial": {
        "retail": {"ar": "محل تجزئة", "en": "Retail store"},
        "grocery": {"ar": "سوبر ماركت / بقالة", "en": "Supermarket / Grocery"},
        "fashion": {"ar": "ملابس وأحذية", "en": "Clothing & Shoes"},
        "electronics": {"ar": "موبايلات وإلكترونيات", "en": "Mobiles & Electronics"},
        "pharmacy": {"ar": "صيدلية", "en": "Pharmacy"},
        "wholesale": {"ar": "جملة / مخزن", "en": "Wholesale / Warehouse"},
        "online": {"ar": "بيع أونلاين", "en": "Online selling"},
        "other": {"ar": "نشاط تجاري آخر", "en": "Other commercial"},
    },
    "services": {
        "general": {"ar": "خدمات عامة", "en": "General services"},
        "maintenance": {"ar": "صيانة وإصلاح", "en": "Maintenance & Repair"},
        "clinic": {"ar": "عيادة / مركز طبي", "en": "Clinic / Medical center"},
        "beauty": {"ar": "صالون / مركز تجميل", "en": "Salon / Beauty center"},
        "education": {"ar": "مركز تعليمي / كورسات", "en": "Education / Courses Center"},
        "professional": {"ar": "مكتب مهني", "en": "Professional Office"},
        "digital_marketing": {"ar": "تسويق وتصميم وخدمات رقمية", "en": "Marketing, Design & Digital Services"},
        "other": {"ar": "نشاط خدمي آخر", "en": "Other Service Activity"},
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _query_value(value):
    return quote(value or "", safe=",")


def _setup_review_href(lang, activity, sub_activity, modules):
    return (
        f"/setup/modules/?lang={_query_value(lang)}"
        f"&activity={_query_value(activity)}"
        f"&sub_activity={_query_value(sub_activity)}"
        f"&modules={_query_value(modules)}"
    )


def _setup_complete_href(lang):
    return f"/setup/complete/?lang={_query_value(lang)}"


def _fallback_label(slug):
    return (slug or "—").replace("_", " ").strip() or "—"


def _label(mapping, lang, fallback_slug):
    value = mapping.get(lang) if mapping else None
    return value or _fallback_label(fallback_slug)


def setup_review(request):
    lang = _lang(request)
    activity = request.GET.get("activity", "")
    sub_activity = request.GET.get("sub_activity", "")
    modules_param = request.GET.get("modules", "")
    module_slugs = [slug.strip() for slug in modules_param.split(",") if slug.strip()]
    selected_modules = [
        {
            "slug": slug,
            "label_ar": _label(MODULE_LABELS.get(slug, {}), "ar", slug),
            "label_en": _label(MODULE_LABELS.get(slug, {}), "en", slug),
            "label": _label(MODULE_LABELS.get(slug, {}), lang, slug),
        }
        for slug in module_slugs
    ]

    strings = {
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

    activity_label = _label(ACTIVITY_LABELS.get(activity, {}), lang, activity)
    sub_activity_label = _label(SUB_ACTIVITY_LABELS.get(activity, {}).get(sub_activity, {}), lang, sub_activity)

    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "activity": activity,
        "sub_activity_slug": sub_activity,
        "modules_param": modules_param,
        "selected_modules": selected_modules,
        "activity_label": activity_label,
        "sub_activity_label": sub_activity_label,
        "activity_label_ar": _label(ACTIVITY_LABELS.get(activity, {}), "ar", activity),
        "activity_label_en": _label(ACTIVITY_LABELS.get(activity, {}), "en", activity),
        "sub_activity_label_ar": _label(SUB_ACTIVITY_LABELS.get(activity, {}).get(sub_activity, {}), "ar", sub_activity),
        "sub_activity_label_en": _label(SUB_ACTIVITY_LABELS.get(activity, {}).get(sub_activity, {}), "en", sub_activity),
        "back_href": _setup_review_href(lang, activity, sub_activity, modules_param),
        "complete_href": _setup_complete_href(lang),
        **strings[lang],
    }
    return render(request, "setup/review_setup.html", context)


def setup_complete_placeholder(request):
    lang = _lang(request)
    strings = {
        "ar": {
            "page_title": "اكتمال الإعداد - حِسْبَة",
            "logout": "تسجيل الخروج",
            "language": "العربية",
            "step_general": "النشاط العام",
            "step_sub": "النشاط الفرعي",
            "step_modules": "الموديولات",
            "step_review": "المراجعة",
            "title": "تم إنهاء الإعداد",
            "subtitle": "هذه صفحة مؤقتة آمنة فقط لمسار إنهاء الإعداد.",
            "message": "لم يتم تفعيل أي إعدادات إنتاجية أو حفظ أي قرار نهائي في قاعدة البيانات.",
            "back": "الرجوع إلى المراجعة",
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
            "subtitle": "This is a safe placeholder only for the setup completion route.",
            "message": "No production setup activation or final database decision has been saved.",
            "back": "Back to review",
        },
    }
    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "review_href": f"/setup/review/?lang={_query_value(lang)}",
        **strings[lang],
    }
    return render(request, "setup/setup_complete_placeholder.html", context)


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="login", permanent=False), name="root_redirect"),
    path("login/", LoginView.as_view(template_name="registration/login.html", next_page="/setup/"), name="login"),
    path("logout/", LogoutView.as_view(next_page="login"), name="logout"),
    path("setup/", TemplateView.as_view(template_name="setup/setup_gate.html"), name="setup_gate"),
    path("setup/activity/", TemplateView.as_view(template_name="setup/activity_selection.html"), name="setup_activity"),
    path("setup/activity/commercial/", TemplateView.as_view(template_name="setup/activity_commercial_subactivity.html"), name="setup_activity_commercial"),
    path("setup/activity/services/", TemplateView.as_view(template_name="setup/activity_services_subactivity.html"), name="setup_activity_services"),
    path("setup/activity/service/", TemplateView.as_view(template_name="setup/activity_subactivity_placeholder.html"), name="setup_activity_service"),
    path("setup/modules/", TemplateView.as_view(template_name="setup/modules_selection.html"), name="setup_modules"),
    path("setup/review/", setup_review, name="setup_review"),
    path("setup/complete/", setup_complete_placeholder, name="setup_complete"),
    path("home/", home, name="home"),
    path("dashboard/", dashboard_snapshot, name="dashboard_snapshot"),
    path("reports/", report_hub, name="report_hub"),
    path("status/", status_counts_report, name="status_counts_report"),
    path("admin/", django_admin.site.urls),
]
