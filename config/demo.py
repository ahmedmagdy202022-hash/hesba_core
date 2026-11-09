"""DEMO-001: what templates need to know about a showcase install."""

from django.conf import settings


def demo_context(request):
    if not getattr(settings, "DEMO_MODE", False):
        return {}
    from .demo_sandbox import enabled

    return {"demo_mode": True, "demo_password": settings.DEMO_PASSWORD, "demo_sandboxes": enabled()}


# ACT-PROFILE-002: on a showcase install the owner can flip the activity to see
# how the words, the menu and the dashboard change. Never on a real install.
# DEMO-FIRST-RUN: each entry has a pre-built, populated template (config/demo_templates.py); the landing page
# offers exactly these. One more entry costs one more ~5 MB file and ~10 s of build.
DEMO_ACTIVITIES = (
    ("commercial", "retail"), ("commercial", "pharmacy"), ("commercial", "grocery"), ("commercial", "fashion"), ("commercial", "electronics"),
    ("restaurants", "restaurant"), ("medical", "clinic"),
    ("education", "training"), ("services", "maintenance"), ("services", "beauty"), ("manufacturing", "food"), ("contracting", "general"),
)
#: What a visitor who is not sure opens: a general retail shop.
DEMO_DEFAULT = ("commercial", "retail")

#: DEMO-FIRST-RUN: the landing page's tiles, in order: (activity, Arabic hint, English hint). "other" opens the default shop.
DEMO_TILES = (
    ("commercial", "تجزئة، صيدلية، سوبر ماركت", "Retail, pharmacy, supermarket"),
    ("services", "ورشة صيانة، صالون", "Repair workshop, salon"),
    ("medical", "عيادة بمواعيد وملفات", "A clinic with appointments and files"),
    ("education", "كورسات وطلبة ومصاريف", "Courses, students and fees"),
    ("restaurants", "طاولات ومطبخ وأوردرات", "Tables, kitchen and orders"),
    ("contracting", "مشاريع ومستخلصات", "Projects and progress bills"),
    ("manufacturing", "وصفات وأوامر إنتاج", "Recipes and production orders"),
    ("other", "محل عام ببيع وشرا ومخزن", "A general shop that buys, sells and stocks"),
)
#: One line under each sub-activity on the second screen.
DEMO_HINTS = {
    ("commercial", "retail"): ("أي محل بيبيع قطاعي", "Any shop that sells retail"),
    ("commercial", "pharmacy"): ("صلاحيات وبدائل وروشتات", "Expiry dates, alternatives, prescriptions"),
    ("commercial", "grocery"): ("باركود وميزان وكرتونة", "Barcodes, scale and cartons"),
    ("commercial", "fashion"): ("مقاسات وألوان", "Sizes and colours"),
    ("commercial", "electronics"): ("سيريال وضمان", "Serial numbers and warranty"),
    ("restaurants", "restaurant"): ("طاولات وأوردرات ومطبخ", "Tables, orders and the kitchen"),
    ("medical", "clinic"): ("مواعيد وكشوفات وملفات", "Appointments, visits and patient files"),
    ("education", "training"): ("مجموعات وحضور ومصاريف", "Groups, attendance and fees"),
    ("services", "maintenance"): ("أوامر شغل وفنيين", "Work orders and technicians"),
    ("services", "beauty"): ("مواعيد وباكدجات جلسات", "Appointments and session packages"),
    ("manufacturing", "food"): ("وصفات وتشغيلات وصلاحية", "Recipes, batches and expiry"),
    ("contracting", "general"): ("مقايسات ومستخلصات", "Bills of quantities and progress bills"),
}


def demo_subs(activity):
    """The sub-activities the demo offers for ``activity``, in landing order."""

    return [sub for act, sub in DEMO_ACTIVITIES if act == activity]


#: DEMO-FIRST-RUN: «جرّب دلوقتي», three steps in the trade's own words: (title_ar, title_en, hint_ar, hint_en, url name).
#: Keyed by (activity, sub) with the activity as the fallback; every link is a real screen the owner may open.
DEMO_TOURS = {
    "commercial": (
        ("سجّل بيعة من الكاشير", "Ring up a sale at the till", "اكتب اسم صنف أو امسح الباركود؛ الوردية مفتوحة", "Type an item or scan a barcode; the shift is open", "sales:pos"),
        ("شوف اللي قرب يخلص من المخزن", "See what is running low", "صنف خلص وصنف قرب، وتنبيه عليهم", "One item is out and one is low, with an alert", "reports:inventory"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "pharmacy"): (
        ("سجّل بيعة من الكاشير", "Ring up a sale at the till", "اكتب اسم دوا أو امسح الباركود؛ الوردية مفتوحة", "Type a medicine or scan a barcode; the shift is open", "sales:pos"),
        ("شوف الأدوية اللي قربت تنتهي صلاحيتها", "See the medicines about to expire", "تشغيلات بتاريخ صلاحية، وفيها اللي محتاج قرار", "Batches with expiry dates, and what needs a decision", "batches:index"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "grocery"): (
        ("سجّل بيعة من الكاشير", "Ring up a sale at the till", "امسح الباركود أو اكتب الوزن", "Scan a barcode or type the weight", "sales:pos"),
        ("الأصناف بالكرتونة وبالوزن", "Items by the carton and by weight", "الرف كله بوحداته وصلاحيته", "The whole shelf with its units and expiry", "master_data:items"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "fashion"): (
        ("سجّل بيعة من الكاشير", "Ring up a sale at the till", "اختار المقاس واللون وانت بتبيع", "Pick the size and colour as you sell", "sales:pos"),
        ("المقاسات والألوان في المخزن", "Sizes and colours in stock", "كل موديل بمقاساته وألوانه", "Each style with its sizes and colours", "variants:index"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "electronics"): (
        ("سجّل بيعة بالسيريال", "Sell a device by its serial", "الكاشير بيسأل عن السيريال وبيسجّل الضمان", "The till asks for the serial and records the warranty", "sales:pos"),
        ("الأجهزة اللي عندك بسيريالاتها", "Your devices by serial number", "كل جهاز بسيرياله وضمانه", "Every device with its serial and warranty", "serials:index"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    "restaurants": (
        ("افتح طاولة وخد أوردر", "Open a table and take an order", "الصالة قدامك: دوس على طاولة وضيف الأطباق", "The floor is in front of you: tap a table, add dishes", "restaurant:board"),
        ("شوف تكلفة الطبق", "See what a dish costs", "كل طبق بمكوناته من المطبخ وهامشه", "Each dish with its kitchen ingredients and margin", "restaurant:costing"),
        ("تقرير المطعم النهارده", "Today's restaurant report", "الأطباق الأكتر، ساعات الذروة، والويترز", "Top dishes, peak hours and the waiters", "restaurant:report"),
    ),
    "medical": (
        ("افتح أجندة النهارده وسجّل كشف", "Open today's agenda and record a visit", "مواعيد النهارده، ومن الموعد تسجّل الكشف", "Today's appointments; record the visit from the appointment", "appointments:agenda"),
        ("افتح ملف مريض", "Open a patient file", "التاريخ المرضي والحساسية والكشوفات", "History, allergies and past visits", "medical:patients"),
        ("قائمة الانتظار", "The waiting list", "مين مستني ومين دخل", "Who is waiting and who went in", "medical:queue"),
    ),
    "education": (
        ("الطلبة والمجموعات", "Students and groups", "كل مجموعة بطلبتها ومدرّسها", "Each group with its students and teacher", "education:students"),
        ("حصص النهارده والغياب", "Today's classes and attendance", "علّم الحضور من الموبايل", "Mark attendance from the phone", "education:today"),
        ("المصاريف المستحقة", "Fees due", "مين دفع ومين عليه، وتذكير بالواتساب", "Who paid, who owes, and a WhatsApp reminder", "education:dues"),
    ),
    "services": (
        ("افتح أمر شغل", "Open a work order", "استلم الجهاز، صوّره، وابعت عرض السعر", "Take the device in, photograph it, send a quote", "workorders:list"),
        ("الفنيين وشغلهم", "The technicians and their work", "كل فني وأوامره وعمولته", "Each technician, their orders and commission", "staff:list"),
        ("تقرير أوامر الشغل", "The work orders report", "اللي خلص واللي مستني، وربح كل أمر", "Done and waiting, and each order's margin", "workorders:report"),
    ),
    ("services", "beauty"): (
        ("احجز ميعاد", "Book an appointment", "الأجندة بمواعيد النهارده", "The agenda with today's appointments", "appointments:agenda"),
        ("باكدجات الجلسات", "Session packages", "باكدج مدفوع وجلساته اللي اتخصمت", "A paid package and the sessions used", "agreements:packages"),
        ("سجّل بيعة من الكاشير", "Ring up a sale at the till", "منتجات وخدمات، كاش أو فيزا", "Products and services, cash or card", "sales:pos"),
    ),
    "manufacturing": (
        ("أوامر الإنتاج", "Production orders", "تشغيلة شغالة وتشغيلة خلصت", "A run in progress and a finished one", "manufacturing:orders"),
        ("الوصفات والتكلفة", "Recipes and cost", "كل منتج بخاماته وتكلفة القطعة", "Each product with its materials and unit cost", "manufacturing:home"),
        ("المخزن بعد الإنتاج", "Stock after production", "الخامات اللي اتصرفت والمنتج اللي دخل", "Materials used and product received", "inventory:stock"),
    ),
    "contracting": (
        ("افتح مشروع ومقايسته", "Open a project and its bill of quantities", "بنود الأعمال بالكمية والفئة", "Work items by quantity and rate", "projects:list"),
        ("تقرير المشاريع", "The projects report", "المستخلصات والضمان والدفعة المقدمة", "Progress bills, retention and the advance", "projects:report"),
        ("مشتريات الموقع", "Site purchases", "خامات اتشترت على المشروع", "Materials bought for the project", "purchases:list"),
    ),
}


def demo_tour(activity, sub_activity):
    return DEMO_TOURS.get((activity, sub_activity)) or DEMO_TOURS.get(activity) or DEMO_TOURS["commercial"]


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
        from settings_core import capabilities as caps
        from settings_core.demo_ai import CAPABILITIES as AI_CAPABILITIES
        from settings_core.setup_services import complete_setup

        profile = ClientProfile.get_active()
        # DEMO-FIT: the sub-activity's own suggestions too (a salon starts with appointments on).
        # AI demo (HG-078 dev. 7): the showcase keeps its AI features (on the mock, labelled as samples) on every activity,
        # each only where the activity already has the sections it needs (a clinic gets no purchases for the invoice photo).
        modules = activity_fit.default_modules(activity, sub)
        ai = tuple(slug for slug in AI_CAPABILITIES if set(caps.needs(slug)) <= set(modules))
        chosen = tuple(dict.fromkeys(caps.default_selection(activity, sub) + ai))
        complete_setup(profile, activity, sub, ",".join(modules), user=request.user, capabilities_raw=",".join(chosen))
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
    from .demo_templates import seed_sample_business

    # FEEDBACK-R1: a second press (double click, or back and press again) must
    # not try to add the same month twice; seed_sample_business adds it once.
    seed_sample_business()
    return redirect(f"/dashboard/?lang={lang}")
