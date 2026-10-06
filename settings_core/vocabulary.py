"""ACT-PROFILE-001: the words each activity uses for the same things.

A clinic has patients and visits, a school has students and fees, a kitchen
has a menu and orders, a contractor has project owners and progress bills.
The records underneath are the same (Customer, Item, SalesInvoice), so this is
display only: one dictionary of terms per activity, with sub-activity
overrides, read by the navigation, the dashboard and the master-data screens.

``terms(lang)`` returns the active installation's vocabulary; ``term(key, lang)``
one word. Keys missing from an activity fall back to the general words, so a
new key never shows blank.
"""

from functools import lru_cache

BASE = {
    "customers": {"ar": "العملاء", "en": "Customers"},
    "customer": {"ar": "عميل", "en": "Customer"},
    "the_customer": {"ar": "العميل", "en": "Customer"},
    "new_customer": {"ar": "عميل جديد", "en": "New customer"},
    "collect": {"ar": "تحصيل من عميل", "en": "Collect from a customer"},
    "customer_dues": {"ar": "مديونيات العملاء", "en": "Customer dues"},
    "suppliers": {"ar": "الموردون", "en": "Suppliers"},
    "supplier": {"ar": "مورد", "en": "Supplier"},
    "new_supplier": {"ar": "مورد جديد", "en": "New supplier"},
    "items": {"ar": "الأصناف والخدمات", "en": "Items & services"},
    "items_short": {"ar": "الأصناف", "en": "Items"},
    "item": {"ar": "صنف / خدمة", "en": "Item / service"},
    "new_item": {"ar": "صنف / خدمة جديدة", "en": "New item or service"},
    "operations": {"ar": "عمليات البيع", "en": "Sales operations"},
    "operations_short": {"ar": "البيع", "en": "Sales"},
    "record_sale": {"ar": "تسجيل عملية بيع", "en": "Record a sale"},
    "staff": {"ar": "الموظفون", "en": "Employees"},
    "appointments": {"ar": "المواعيد", "en": "Appointments"},
}

# Activity-wide words. Only what differs from BASE is listed.
ACTIVITY = {
    "medical": {
        "customers": {"ar": "المرضى", "en": "Patients"},
        "customer": {"ar": "مريض", "en": "Patient"},
        "the_customer": {"ar": "المريض", "en": "Patient"},
        "new_customer": {"ar": "مريض جديد", "en": "New patient"},
        "collect": {"ar": "تحصيل من مريض", "en": "Collect from a patient"},
        "customer_dues": {"ar": "مستحقات على المرضى", "en": "Patient balances"},
        "items": {"ar": "الخدمات الطبية والمستلزمات", "en": "Medical services & supplies"},
        "items_short": {"ar": "الخدمات", "en": "Services"},
        "item": {"ar": "خدمة / مستلزم", "en": "Service / supply"},
        "new_item": {"ar": "خدمة طبية جديدة", "en": "New medical service"},
        "operations": {"ar": "الكشوفات والفواتير", "en": "Visits & bills"},
        "operations_short": {"ar": "الكشوفات", "en": "Visits"},
        "record_sale": {"ar": "تسجيل كشف", "en": "Record a visit"},
        "staff": {"ar": "الأطباء والطاقم", "en": "Doctors & staff"},
        "appointments": {"ar": "الحجوزات", "en": "Bookings"},
    },
    "education": {
        "customers": {"ar": "الطلاب", "en": "Students"},
        "customer": {"ar": "طالب", "en": "Student"},
        "the_customer": {"ar": "الطالب", "en": "Student"},
        "new_customer": {"ar": "طالب جديد", "en": "New student"},
        "collect": {"ar": "تحصيل مصروفات", "en": "Collect fees"},
        "customer_dues": {"ar": "مصروفات متأخرة", "en": "Fees outstanding"},
        "items": {"ar": "الكورسات والمواد", "en": "Courses & materials"},
        "items_short": {"ar": "الكورسات", "en": "Courses"},
        "item": {"ar": "كورس / مادة", "en": "Course / material"},
        "new_item": {"ar": "كورس جديد", "en": "New course"},
        "operations": {"ar": "الاشتراكات والمصروفات", "en": "Enrolments & fees"},
        "operations_short": {"ar": "الاشتراكات", "en": "Fees"},
        "record_sale": {"ar": "تسجيل اشتراك", "en": "Record an enrolment"},
        "staff": {"ar": "المدرّسون", "en": "Teachers"},
        "appointments": {"ar": "الحصص والمواعيد", "en": "Classes"},
    },
    "restaurants": {
        "items": {"ar": "المنيو", "en": "Menu"},
        "items_short": {"ar": "المنيو", "en": "Menu"},
        "item": {"ar": "صنف في المنيو", "en": "Menu item"},
        "new_item": {"ar": "صنف جديد في المنيو", "en": "New menu item"},
        "operations": {"ar": "الطلبات والفواتير", "en": "Orders & bills"},
        "operations_short": {"ar": "الطلبات", "en": "Orders"},
        "record_sale": {"ar": "تسجيل طلب", "en": "Record an order"},
        "staff": {"ar": "الطاقم", "en": "Crew"},
    },
    "manufacturing": {
        "customers": {"ar": "العملاء والموزعون", "en": "Customers & distributors"},
        "items": {"ar": "الخامات والمنتجات", "en": "Materials & products"},
        "items_short": {"ar": "الأصناف", "en": "Items"},
        "item": {"ar": "خامة / منتج", "en": "Material / product"},
        "new_item": {"ar": "خامة أو منتج جديد", "en": "New material or product"},
        "suppliers": {"ar": "موردو الخامات", "en": "Material suppliers"},
        "staff": {"ar": "العمال والفنيون", "en": "Workers & technicians"},
    },
    "contracting": {
        "customers": {"ar": "أصحاب المشاريع", "en": "Project owners"},
        "customer": {"ar": "صاحب مشروع", "en": "Project owner"},
        "the_customer": {"ar": "صاحب المشروع", "en": "Project owner"},
        "new_customer": {"ar": "صاحب مشروع جديد", "en": "New project owner"},
        "collect": {"ar": "تحصيل دفعة", "en": "Collect a payment"},
        "customer_dues": {"ar": "مستحقات على أصحاب المشاريع", "en": "Owner balances"},
        "suppliers": {"ar": "الموردون ومقاولو الباطن", "en": "Suppliers & subcontractors"},
        "new_supplier": {"ar": "مورد / مقاول باطن جديد", "en": "New supplier or subcontractor"},
        "items": {"ar": "المواد والبنود", "en": "Materials & work items"},
        "items_short": {"ar": "المواد", "en": "Materials"},
        "item": {"ar": "مادة / بند", "en": "Material / work item"},
        "new_item": {"ar": "مادة أو بند جديد", "en": "New material or work item"},
        "operations": {"ar": "المستخلصات والفواتير", "en": "Progress bills & invoices"},
        "operations_short": {"ar": "المستخلصات", "en": "Bills"},
        "record_sale": {"ar": "تسجيل مستخلص / فاتورة", "en": "Record a bill"},
        "staff": {"ar": "المهندسون والعمال", "en": "Engineers & crew"},
    },
    "services": {
        "items": {"ar": "الخدمات", "en": "Services"},
        "items_short": {"ar": "الخدمات", "en": "Services"},
        "item": {"ar": "خدمة", "en": "Service"},
        "new_item": {"ar": "خدمة جديدة", "en": "New service"},
        "operations": {"ar": "الفواتير", "en": "Invoices"},
        "operations_short": {"ar": "الفواتير", "en": "Invoices"},
        "record_sale": {"ar": "تسجيل فاتورة خدمة", "en": "Record a service invoice"},
        "staff": {"ar": "الفنيون والموظفون", "en": "Technicians & staff"},
    },
}

# Sub-activity refinements, layered over the activity's words.
SUB_ACTIVITY = {
    ("commercial", "pharmacy"): {
        "items": {"ar": "الأدوية والأصناف", "en": "Medicines & items"},
        "item": {"ar": "دواء / صنف", "en": "Medicine / item"},
        "new_item": {"ar": "دواء أو صنف جديد", "en": "New medicine or item"},
    },
    ("commercial", "wholesale"): {
        "customers": {"ar": "العملاء والتجار", "en": "Customers & traders"},
    },
    ("services", "maintenance"): {
        "operations": {"ar": "أوامر الشغل والفواتير", "en": "Work orders & invoices"},
        "operations_short": {"ar": "أوامر الشغل", "en": "Jobs"},
        "record_sale": {"ar": "تسجيل أمر شغل", "en": "Record a job"},
    },
    ("services", "clinic"): ACTIVITY["medical"],
    ("services", "education"): ACTIVITY["education"],
    ("services", "beauty"): {
        "staff": {"ar": "الطاقم والخبيرات", "en": "Stylists & staff"},
        "appointments": {"ar": "الحجوزات", "en": "Bookings"},
    },
    ("services", "professional"): {
        "customers": {"ar": "الموكّلون والعملاء", "en": "Clients"},
        "items": {"ar": "الخدمات والأتعاب", "en": "Services & fees"},
    },
    ("medical", "vet"): {
        "customers": {"ar": "أصحاب الحيوانات", "en": "Pet owners"},
        "customer": {"ar": "صاحب حيوان", "en": "Pet owner"},
        "the_customer": {"ar": "صاحب الحيوان", "en": "Pet owner"},
        "new_customer": {"ar": "صاحب حيوان جديد", "en": "New pet owner"},
        "collect": {"ar": "تحصيل من عميل", "en": "Collect from a client"},
    },
    ("medical", "lab"): {
        "operations": {"ar": "طلبات التحاليل والفواتير", "en": "Test orders & bills"},
        "operations_short": {"ar": "التحاليل", "en": "Tests"},
        "record_sale": {"ar": "تسجيل طلب تحليل", "en": "Record a test order"},
    },
    ("education", "nursery"): {
        "customers": {"ar": "الأطفال", "en": "Children"},
        "customer": {"ar": "طفل", "en": "Child"},
        "the_customer": {"ar": "الطفل", "en": "Child"},
        "new_customer": {"ar": "طفل جديد", "en": "New child"},
        "staff": {"ar": "المشرفات والطاقم", "en": "Carers & staff"},
    },
}


@lru_cache(maxsize=64)
def vocabulary(activity, sub_activity):
    """The merged term table for one (activity, sub-activity)."""

    merged = {key: dict(value) for key, value in BASE.items()}
    for layer in (ACTIVITY.get(activity, {}), SUB_ACTIVITY.get((activity, sub_activity), {})):
        for key, value in layer.items():
            merged[key] = dict(value)
    return merged


def active_vocabulary():
    # ENT-002: the words of the entity being worked in, else the installation's.
    from entities.current import effective_activity

    activity, sub_activity = effective_activity()
    return vocabulary(activity or "", sub_activity or "")


def terms(lang, table=None):
    """{key: word} in one language."""

    lang = "en" if lang == "en" else "ar"
    table = table or active_vocabulary()
    return {key: value[lang] for key, value in table.items()}


def term(key, lang, table=None):
    table = table or active_vocabulary()
    entry = table.get(key) or BASE.get(key)
    if entry is None:
        return key
    return entry["en" if lang == "en" else "ar"]
