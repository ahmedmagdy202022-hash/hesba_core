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
RESTAURANTS = "restaurants"
MEDICAL = "medical"
EDUCATION = "education"
OTHER = "other"
CONTRACTING = "contracting"
MANUFACTURING = "manufacturing"

REQUIRED = "required"
SUGGESTED = "suggested"
OPTIONAL = "optional"

#: A module with no explicit preset for the chosen activity starts off.
DEFAULT_MODULE_STATE = OPTIONAL


ACTIVITY_LABELS = {
    COMMERCIAL: {"ar": "نشاط تجاري", "en": "Commercial"},
    SERVICES: {"ar": "نشاط خدمي", "en": "Services"},
    RESTAURANTS: {"ar": "مطاعم وكافيهات", "en": "Restaurants & cafés"},
    MEDICAL: {"ar": "نشاط طبي", "en": "Medical"},
    EDUCATION: {"ar": "نشاط تعليمي", "en": "Education"},
    OTHER: {"ar": "نشاط آخر", "en": "Other activity"},
    CONTRACTING: {"ar": "مقاولات", "en": "Contracting"},
    MANUFACTURING: {"ar": "نشاط تصنيعي", "en": "Manufacturing"},
}

#: Which ``ClientProfile.activity_type`` each wizard activity maps onto. The two
#: vocabularies were written independently, so the wizard's ``commercial`` has to
#: be translated rather than stored as-is.
ACTIVITY_TYPE_BY_SLUG = {
    COMMERCIAL: ActivityType.STORE,
    SERVICES: ActivityType.SERVICES,
    # RESTO-001: a restaurant sells from stock like a shop; the wizard slug kept
    # on the profile tells the two apart, so no new choice (and no migration).
    RESTAURANTS: ActivityType.STORE,
    # ACT-002: clinics and schools sell services; "other" can be both.
    MEDICAL: ActivityType.SERVICES,
    EDUCATION: ActivityType.SERVICES,
    OTHER: ActivityType.MIXED,
    CONTRACTING: ActivityType.CONTRACTING,
    # MFG-001: a workshop makes, stocks and sells products, like a shop does.
    MANUFACTURING: ActivityType.STORE,
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
    RESTAURANTS: {
        "restaurant": {"ar": "مطعم", "en": "Restaurant"},
        "cafe": {"ar": "كافيه / كوفي شوب", "en": "Café / Coffee shop"},
        "fast_food": {"ar": "وجبات سريعة", "en": "Fast food"},
        "bakery": {"ar": "مخبز / حلواني", "en": "Bakery / Sweets"},
        "cloud_kitchen": {"ar": "مطبخ أونلاين / دليفري", "en": "Cloud kitchen / Delivery"},
        "other": {"ar": "نشاط أكل ومشروبات آخر", "en": "Other food & drinks"},
    },
    MEDICAL: {
        "clinic": {"ar": "عيادة", "en": "Clinic"},
        "dental": {"ar": "عيادة أسنان", "en": "Dental clinic"},
        "medical_center": {"ar": "مركز طبي / بولي كلينك", "en": "Medical center / Polyclinic"},
        "lab": {"ar": "معمل تحاليل / أشعة", "en": "Lab / Radiology"},
        "physio": {"ar": "علاج طبيعي", "en": "Physiotherapy"},
        "vet": {"ar": "عيادة بيطري", "en": "Veterinary clinic"},
        "other": {"ar": "نشاط طبي آخر", "en": "Other medical"},
    },
    EDUCATION: {
        "tutoring_center": {"ar": "سنتر دروس", "en": "Tutoring center"},
        "training": {"ar": "كورسات وتدريب", "en": "Courses & training"},
        "languages": {"ar": "مركز لغات", "en": "Language center"},
        "nursery": {"ar": "حضانة", "en": "Nursery"},
        "private_tutor": {"ar": "مدرس خصوصي", "en": "Private tutor"},
        "other": {"ar": "نشاط تعليمي آخر", "en": "Other education"},
    },
    OTHER: {
        "mixed": {"ar": "بيع وخدمات مع بعض", "en": "Selling and services together"},
        "general": {"ar": "نشاط عام", "en": "General activity"},
        "other": {"ar": "حاجة تانية", "en": "Something else"},
    },
    CONTRACTING: {
        "general": {"ar": "مقاولات عامة / مباني", "en": "General building"},
        "finishing": {"ar": "تشطيبات وديكور", "en": "Finishing & interiors"},
        "electromechanical": {"ar": "كهروميكانيك (كهرباء، سباكة، تكييف)", "en": "MEP (electrical, plumbing, HVAC)"},
        "infrastructure": {"ar": "طرق وبنية تحتية", "en": "Roads & infrastructure"},
        "maintenance_contracts": {"ar": "عقود صيانة", "en": "Maintenance contracts"},
        "other": {"ar": "مقاولات أخرى", "en": "Other contracting"},
    },
    MANUFACTURING: {
        "food": {"ar": "أغذية وحلويات", "en": "Food & sweets"},
        "garments": {"ar": "ملابس ومفروشات", "en": "Garments & textiles"},
        "furniture": {"ar": "أثاث ونجارة", "en": "Furniture & carpentry"},
        "printing": {"ar": "طباعة وتغليف", "en": "Printing & packaging"},
        "chemicals": {"ar": "منظفات وكيماويات", "en": "Detergents & chemicals"},
        "workshop": {"ar": "ورشة / حرفة", "en": "Workshop / Craft"},
        "other": {"ar": "تصنيع آخر", "en": "Other manufacturing"},
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
    "tables_orders": {"ar": "الطاولات والطلبات", "en": "Tables & orders"},
    "projects": {"ar": "المشاريع", "en": "Projects"},
    "manufacturing": {"ar": "التصنيع", "en": "Manufacturing"},
}

MODULE_SLUGS = tuple(MODULE_LABELS)

#: Per-activity starting states, from docs/118_MODULES_SELECTION_PLAN.md.
#: AUDIT-2: services now require ``sales_operations`` too; without it a
#: services business had no way to issue an invoice (medical and education,
#: also services, already required it).
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
        "sales_operations": REQUIRED,
        "purchases": OPTIONAL,
        "inventory": OPTIONAL,
        "cashboxes": REQUIRED,
        "expenses": SUGGESTED,
        "reports": REQUIRED,
        "pdf_printing": SUGGESTED,
        "appointments_visits": OPTIONAL,
        "employees_technicians": OPTIONAL,
    },
    RESTAURANTS: {
        "customers": OPTIONAL,
        "suppliers": SUGGESTED,
        "items_services": REQUIRED,
        "sales_operations": REQUIRED,
        "purchases": SUGGESTED,
        "inventory": SUGGESTED,
        "cashboxes": REQUIRED,
        "expenses": SUGGESTED,
        "reports": REQUIRED,
        "pdf_printing": OPTIONAL,
        "appointments_visits": OPTIONAL,
        "employees_technicians": SUGGESTED,
        "tables_orders": REQUIRED,
    },
    # ACT-002: a visit is billed through sales, so a clinic needs sales operations.
    MEDICAL: {
        "customers": REQUIRED,
        "suppliers": OPTIONAL,
        "items_services": REQUIRED,
        "sales_operations": REQUIRED,
        "purchases": OPTIONAL,
        "inventory": OPTIONAL,
        "cashboxes": REQUIRED,
        "expenses": SUGGESTED,
        "reports": REQUIRED,
        "pdf_printing": SUGGESTED,
        "appointments_visits": REQUIRED,
        "employees_technicians": SUGGESTED,
        "tables_orders": OPTIONAL,
    },
    EDUCATION: {
        "customers": REQUIRED,
        "suppliers": OPTIONAL,
        "items_services": REQUIRED,
        "sales_operations": REQUIRED,
        "purchases": OPTIONAL,
        "inventory": OPTIONAL,
        "cashboxes": REQUIRED,
        "expenses": SUGGESTED,
        "reports": REQUIRED,
        "pdf_printing": SUGGESTED,
        "appointments_visits": SUGGESTED,
        "employees_technicians": SUGGESTED,
        "tables_orders": OPTIONAL,
    },
    OTHER: {
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
        "tables_orders": OPTIONAL,
    },
    # CONTRACT-001: a contractor lives on projects; materials and costs come from stock and expenses.
    CONTRACTING: {
        "customers": REQUIRED,
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
        "employees_technicians": SUGGESTED,
        "tables_orders": OPTIONAL,
        "projects": REQUIRED,
    },
    MANUFACTURING: {
        "customers": SUGGESTED,
        "suppliers": SUGGESTED,
        "items_services": REQUIRED,
        "sales_operations": REQUIRED,
        "purchases": REQUIRED,
        "inventory": REQUIRED,
        "cashboxes": REQUIRED,
        "expenses": SUGGESTED,
        "reports": REQUIRED,
        "pdf_printing": SUGGESTED,
        "appointments_visits": OPTIONAL,
        "employees_technicians": SUGGESTED,
        "tables_orders": OPTIONAL,
        "projects": OPTIONAL,
        "manufacturing": REQUIRED,
    },
}

#: Modules the wizard offers but Hesba cannot serve yet: no model, no service,
#: no screen. The dashboard uses this to avoid advertising empty sections.
MODULES_WITHOUT_BACKEND = frozenset()


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

    Unknown slugs and modules with no backend yet are dropped and required
    modules are added back, so a hand-made or stale request cannot switch off
    something the activity depends on. The result follows the wizard's own
    declaration order.
    """

    chosen = set(parse_module_slugs(raw)) | set(required_modules(activity))
    # CATALOG-003: a module with no backend cannot be switched on yet; storing it
    # would show "on" for something with no screens behind it.
    return tuple(slug for slug in MODULE_SLUGS if slug in chosen and slug not in MODULES_WITHOUT_BACKEND)
