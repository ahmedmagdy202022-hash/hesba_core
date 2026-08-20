"""The vocabulary the setup wizard speaks: activities, sub-activities, modules.

Before this module the same slugs and labels lived in four places at once —
``config.urls``, the review template's JavaScript, every wizard step's own
``dict`` object, and the test file. Persisting the setup decision needs one
definition to validate against, so this is it. Callers that need a label or a
preset should read it from here rather than restating it.
"""

from .models import ActivityType


COMMERCIAL = "commercial"
SERVICES = "services"

REQUIRED = "required"
SUGGESTED = "suggested"
OPTIONAL = "optional"

#: A module with no explicit preset for the chosen activity starts off.
DEFAULT_MODULE_STATE = OPTIONAL


ACTIVITY_LABELS = {
    COMMERCIAL: {"ar": "نشاط تجاري", "en": "Commercial"},
    SERVICES: {"ar": "نشاط خدمي", "en": "Services"},
}

#: Which ``ClientProfile.activity_type`` each wizard activity maps onto. The two
#: vocabularies were written independently, so the wizard's ``commercial`` has to
#: be translated rather than stored as-is.
ACTIVITY_TYPE_BY_SLUG = {
    COMMERCIAL: ActivityType.STORE,
    SERVICES: ActivityType.SERVICES,
}

SUB_ACTIVITY_LABELS = {
    COMMERCIAL: {
        "retail": {"ar": "محل تجزئة", "en": "Retail store"},
        "grocery": {"ar": "سوبر ماركت / بقالة", "en": "Supermarket / Grocery"},
        "fashion": {"ar": "ملابس وأحذية", "en": "Clothing & Shoes"},
        "electronics": {"ar": "موبايلات وإلكترونيات", "en": "Mobiles & Electronics"},
        "pharmacy": {"ar": "صيدلية", "en": "Pharmacy"},
        "wholesale": {"ar": "جملة / مخزن", "en": "Wholesale / Warehouse"},
        "online": {"ar": "بيع أونلاين", "en": "Online selling"},
        "other": {"ar": "نشاط تجاري آخر", "en": "Other commercial"},
    },
    SERVICES: {
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

#: Declaration order matters: it is the order the wizard renders module cards in,
#: and therefore the order of the comma-separated list it hands back.
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

MODULE_SLUGS = tuple(MODULE_LABELS)

#: Per-activity starting states, from docs/118_MODULES_SELECTION_PLAN.md. A
#: services install deliberately has no entry for ``sales_operations``, so it
#: falls through to DEFAULT_MODULE_STATE — the wizard's own markup does the same.
MODULE_PRESETS = {
    COMMERCIAL: {
        "customers": SUGGESTED,
        "suppliers": SUGGESTED,
        "items_services": REQUIRED,
        "sales_operations": REQUIRED,
        "purchases": SUGGESTED,
        "inventory": SUGGESTED,
        "cashboxes": REQUIRED,
        "expenses": SUGGESTED,
        "reports": REQUIRED,
        "pdf_printing": SUGGESTED,
        "appointments_visits": OPTIONAL,
        "employees_technicians": OPTIONAL,
    },
    SERVICES: {
        "customers": REQUIRED,
        "suppliers": OPTIONAL,
        "items_services": REQUIRED,
        "purchases": OPTIONAL,
        "inventory": OPTIONAL,
        "cashboxes": REQUIRED,
        "expenses": SUGGESTED,
        "reports": REQUIRED,
        "pdf_printing": SUGGESTED,
        "appointments_visits": OPTIONAL,
        "employees_technicians": OPTIONAL,
    },
}

#: Modules the wizard offers but Hesba cannot serve yet: no model, no service,
#: no screen. The dashboard uses this to avoid advertising empty sections.
MODULES_WITHOUT_BACKEND = frozenset(
    {"expenses", "pdf_printing", "appointments_visits", "employees_technicians"}
)


def fallback_label(slug):
    return (slug or "—").replace("_", " ").strip() or "—"


def _label(mapping, lang, slug):
    return (mapping or {}).get(lang) or fallback_label(slug)


def activity_label(slug, lang="ar"):
    return _label(ACTIVITY_LABELS.get(slug), lang, slug)


def sub_activity_label(activity, slug, lang="ar"):
    return _label(SUB_ACTIVITY_LABELS.get(activity, {}).get(slug), lang, slug)


def module_label(slug, lang="ar"):
    return _label(MODULE_LABELS.get(slug), lang, slug)


def is_valid_activity(slug):
    return slug in ACTIVITY_LABELS


def is_valid_sub_activity(activity, slug):
    return slug in SUB_ACTIVITY_LABELS.get(activity, {})


def preset_state(activity, slug):
    return MODULE_PRESETS.get(activity, {}).get(slug, DEFAULT_MODULE_STATE)


def required_modules(activity):
    """Modules the wizard locks on; they cannot be switched off during setup."""

    return tuple(slug for slug in MODULE_SLUGS if preset_state(activity, slug) == REQUIRED)


def default_modules(activity):
    """Modules that start switched on: everything required or suggested."""

    return tuple(
        slug for slug in MODULE_SLUGS if preset_state(activity, slug) in (REQUIRED, SUGGESTED)
    )


def parse_module_slugs(raw):
    """Read the wizard's comma-separated list, keeping only slugs we know."""

    submitted = {slug.strip() for slug in (raw or "").split(",") if slug.strip()}
    return tuple(slug for slug in MODULE_SLUGS if slug in submitted)


def clean_module_slugs(activity, raw):
    """Normalise a submitted module list into what will actually be stored.

    Unknown slugs are dropped and required modules are added back, so a hand-made
    or stale request cannot switch off something the activity depends on. The
    result follows the wizard's own declaration order.
    """

    chosen = set(parse_module_slugs(raw)) | set(required_modules(activity))
    return tuple(slug for slug in MODULE_SLUGS if slug in chosen)
