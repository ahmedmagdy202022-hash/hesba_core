"""ACT-FIT: what each activity and sub-activity is offered, in its own words.

One place answers, for a chosen (activity, sub-activity):

- which modules are offered at all, and whether each is required, suggested or
  optional for it (``module_state``). A module that has nothing to do with the
  activity (tables and orders for a clinic, projects for a shop) is not offered;
  one that is already switched on still shows, so it can be switched off;
- what each module is called and does there (``module_rows``): a clinic's
  customers are its patients, a school's sales are its fee bills;
- the activity's own screens that come with it (``features``): a dental chart,
  a lab's result sheet, a kitchen's tickets, a contractor's progress bills.

The setup wizard and the Settings screens both read from here, so what is
offered is the same before and after setup. The required modules stay the
activity's (``setup_catalog.MODULE_PRESETS``); a sub-activity only moves a
module between suggested and optional, so nothing already locked changes.
Display and suggestion only: no flag, gate or permission is decided here.
"""

from django.urls import NoReverseMatch, reverse

from . import setup_catalog as catalog

REQUIRED, SUGGESTED, OPTIONAL = catalog.REQUIRED, catalog.SUGGESTED, catalog.OPTIONAL
ORDER = (REQUIRED, SUGGESTED, OPTIONAL)

#: What each module does, in general words.
ABOUT = {
    "customers": ("إدارة أرصدة العملاء وبيانات التواصل.", "Manage customer balances and contact details."),
    "suppliers": ("حسابات الموردين والمشتريات عند الحاجة.", "Supplier accounts and purchases when needed."),
    "items_services": ("تعريف الخدمات أو الأصناف التي يتم بيعها.", "Define the services or items you sell."),
    "sales_operations": ("تسجيل البيع والتحصيل والآجل.", "Record sales, paid amounts, and credit sales."),
    "purchases": ("تسجيل فواتير الشراء وتكلفة الأصناف.", "Record purchase invoices and item cost."),
    "inventory": ("مناسب للأصناف وقطع الغيار والمستهلكات.", "Useful for items, spare parts, and consumables."),
    "cashboxes": ("متابعة الخزنة والتحويلات والأرصدة.", "Track cashboxes, transfers, and balances."),
    "expenses": ("تسجيل مصروفات النشاط اليومية.", "Record daily business expenses."),
    "reports": ("تقارير التشغيل والحسابات الأساسية.", "Core operational and account reports."),
    "pdf_printing": ("إيصالات وكشوفات جاهزة للطباعة والمشاركة.", "Receipts and statements ready to print or share."),
    "appointments_visits": ("تنظيم المواعيد أو زيارات العملاء.", "Organize appointments or customer visits."),
    "employees_technicians": ("ربط العمليات بفريق العمل عند الحاجة.", "Connect operations to team members when needed."),
    "tables_orders": ("طلبات الصالة والتيك أواي والدليفري، وتذكرة المطبخ، والحساب.", "Dine-in, takeaway and delivery orders, the kitchen ticket and the bill."),
    "projects": ("مشاريع بعقد وعميل: مستخلصات، خامات، مصروفات، وربح كل مشروع.", "Contract jobs per customer: progress bills, materials, expenses and each project's profit."),
    "manufacturing": ("وصفات المنتجات، وتشغيلات بتصرف الخامات وتدخّل المنتج بتكلفته.", "Product recipes, and runs that use the materials and bring the product in at their cost."),
}

#: The module's name is the activity's own word for it (settings_core.vocabulary).
VOCABULARY_KEY = {"customers": "customers", "suppliers": "suppliers", "items_services": "items", "sales_operations": "operations",
                  "employees_technicians": "staff", "appointments_visits": "appointments"}

#: What a module does in an activity, where the general words do not fit. A
#: ``(activity, sub-activity)`` key wins over the activity's.
ABOUT_FOR = {
    catalog.MEDICAL: {
        "customers": ("ملف لكل مريض: بياناته وحسابه وفواتيره وكشوفاته.", "One file per patient: details, account, bills and visits."),
        "items_services": ("الكشف والاستشارة والخدمات الطبية بأسعارها، والمستلزمات.", "Visits, consultations and medical services with their prices, and supplies."),
        "sales_operations": ("فاتورة كل كشف أو خدمة، والتحصيل والآجل.", "The bill of each visit or service, collections and credit."),
        "inventory": ("المستلزمات والأدوية اللي بتتصرف أو بتتباع.", "Supplies and medicines used or sold."),
        "appointments_visits": ("حجز الكشف بالدكتور والميعاد، ومنه فاتورة الكشف.", "Book a visit with a doctor at a time, and bill it from there."),
        "employees_technicians": ("الأطباء والتمريض والاستقبال، وعمولة كل دكتور.", "Doctors, nurses and reception, and each doctor's commission."),
        "purchases": ("شراء المستلزمات والأدوية من الموردين.", "Buying supplies and medicines from suppliers."),
    },
    (catalog.MEDICAL, "lab"): {
        "inventory": ("الكواشف والمستهلكات اللي بتتصرف في كل تحليل.", "Reagents and consumables used in each test."),
    },
    (catalog.MEDICAL, "vet"): {
        "customers": ("ملف لكل صاحب حيوان، وتحته حيواناته وكشوفاتهم وحسابه.", "One file per owner, with their animals, visits and account."),
        "inventory": ("الأدوية والتطعيمات والأكل اللي بتتباع وتتصرف.", "Medicines, vaccines and food sold or used."),
    },
    catalog.EDUCATION: {
        "customers": ("ولي الأمر هو اللي بيدفع: حسابه وفواتيره، وتحته أولاده.", "The parent pays: their account and bills, with their children under it."),
        "items_services": ("الكورسات والمصروفات الشهرية أو بالحصة.", "Courses and fees, monthly or per class."),
        "sales_operations": ("فواتير المصروفات الشهرية والتحصيل من أولياء الأمور.", "Monthly fee bills and collections from parents."),
        "appointments_visits": ("مواعيد الحصص الخاصة أو مقابلات أولياء الأمور.", "Private classes or parent meetings."),
        "employees_technicians": ("المدرسين والطاقم، وأجر كل مدرس بالحصة أو بالنسبة أو شهري.", "Teachers and staff, each paid per class, by a share or monthly."),
    },
    catalog.RESTAURANTS: {
        "customers": ("عملاء الدليفري بعناوينهم، والحسابات الآجلة.", "Delivery customers with their addresses, and credit accounts."),
        "items_services": ("المنيو بالأسعار، والخامات اللي بتدخل في كل طبق.", "The menu with prices, and the ingredients each dish takes."),
        "sales_operations": ("حساب كل طلب على الكاشير، والتحصيل.", "Each order's bill at the till, and collections."),
        "inventory": ("مخزن الخامات، والهالك، وتكلفة الطبق.", "The ingredients store, waste and dish cost."),
        "purchases": ("شراء الخامات من الموردين بتكلفتها.", "Buying ingredients from suppliers at their cost."),
        "employees_technicians": ("الويترز والكابتن والطيارين، ومين خدم كل طلب.", "Waiters, captains and drivers, and who served each order."),
    },
    catalog.SERVICES: {
        "items_services": ("الخدمات اللي بتقدمها بأسعارها.", "The services you provide, with their prices."),
        "employees_technicians": ("الفنيين أو فريق العمل، ومين عمل كل شغلانة.", "Technicians or the team, and who did each job."),
    },
    (catalog.SERVICES, "maintenance"): {
        "items_services": ("الخدمات وقطع الغيار بأسعارها.", "Services and spare parts with their prices."),
        "inventory": ("قطع الغيار اللي بتتركب، ورصيدها.", "Spare parts fitted, and their stock."),
        "employees_technicians": ("الفنيين، وكل أمر شغل باسم الفني اللي عمله.", "Technicians, each work order under the one who did it."),
    },
    (catalog.SERVICES, "beauty"): {
        "appointments_visits": ("حجوزات العميلات بالخدمة والأخصائية والميعاد.", "Bookings by service, specialist and time."),
        "employees_technicians": ("الأخصائيات والكوافيرات، وعمولة كل واحدة.", "Specialists and stylists, and each one's commission."),
        "inventory": ("المنتجات اللي بتتباع أو بتتصرف في الخدمة.", "Products sold or used in a service."),
    },
    (catalog.SERVICES, "professional"): {
        "employees_technicians": ("فريق المكتب، وساعات كل واحد على العملاء.", "The office team, and each one's hours on customers."),
    },
    (catalog.SERVICES, "digital_marketing"): {
        "employees_technicians": ("المصممين والفريق، وساعات كل واحد على العملاء.", "Designers and the team, and each one's hours on customers."),
    },
    catalog.CONTRACTING: {
        "customers": ("أصحاب المشاريع: عقودهم ومستخلصاتهم وتحصيلهم.", "Project owners: their contracts, progress bills and collections."),
        "suppliers": ("موردين الخامات ومقاولين الباطن.", "Material suppliers and subcontractors."),
        "items_services": ("بنود الأعمال والخامات بوحداتها.", "Work items and materials with their units."),
        "sales_operations": ("المستخلصات والفواتير على أصحاب المشاريع.", "Progress bills and invoices to project owners."),
        "inventory": ("مخزن الخامات والصرف على كل مشروع.", "The materials store and what each project used."),
        "employees_technicians": ("المهندسين والمشرفين والعمالة.", "Engineers, supervisors and workers."),
    },
    (catalog.CONTRACTING, "maintenance_contracts"): {
        "appointments_visits": ("زيارات الصيانة الدورية على عقود العملاء.", "Scheduled maintenance visits on customer contracts."),
    },
    catalog.MANUFACTURING: {
        "items_services": ("الخامات والمنتجات التامة بوحداتها وأسعارها.", "Materials and finished products with their units and prices."),
        "inventory": ("مخزن الخامات والمنتج التام، والصرف للتصنيع.", "Materials and finished goods stock, and issues to production."),
        "employees_technicians": ("العمال والمشرفين على كل مرحلة.", "Workers and supervisors on each stage."),
        "purchases": ("شراء الخامات بتكلفتها، اللي بتدخل في تكلفة المنتج.", "Buying materials at a cost that goes into the product's cost."),
    },
}

#: Modules with nothing to do with an activity: not offered (unless already on).
NOT_OFFERED = {
    catalog.COMMERCIAL: {"tables_orders", "projects", "manufacturing"},
    catalog.SERVICES: {"tables_orders", "projects", "manufacturing"},
    catalog.MEDICAL: {"tables_orders", "projects", "manufacturing"},
    catalog.EDUCATION: {"tables_orders", "projects", "manufacturing"},
    catalog.RESTAURANTS: {"projects", "manufacturing"},
    catalog.CONTRACTING: {"tables_orders", "manufacturing"},
    catalog.MANUFACTURING: {"tables_orders"},
    catalog.OTHER: {"tables_orders", "projects", "manufacturing"},
}

#: A sub-activity moves a module between suggested and optional (never to or from required), or offers one its activity does not.
SUB_PRESETS = {
    (catalog.SERVICES, "maintenance"): {"inventory": SUGGESTED, "purchases": SUGGESTED, "employees_technicians": SUGGESTED},
    (catalog.SERVICES, "beauty"): {"appointments_visits": SUGGESTED, "employees_technicians": SUGGESTED},
    (catalog.SERVICES, "clinic"): {"appointments_visits": SUGGESTED},
    (catalog.SERVICES, "education"): {"appointments_visits": SUGGESTED},
    (catalog.SERVICES, "professional"): {"employees_technicians": SUGGESTED},
    (catalog.SERVICES, "digital_marketing"): {"employees_technicians": SUGGESTED},
    (catalog.RESTAURANTS, "cloud_kitchen"): {"customers": SUGGESTED},
    (catalog.RESTAURANTS, "bakery"): {"manufacturing": OPTIONAL},
    (catalog.MEDICAL, "lab"): {"inventory": SUGGESTED, "purchases": SUGGESTED},
    (catalog.MEDICAL, "vet"): {"inventory": SUGGESTED, "purchases": SUGGESTED},
    (catalog.EDUCATION, "private_tutor"): {"employees_technicians": OPTIONAL},
    (catalog.CONTRACTING, "maintenance_contracts"): {"appointments_visits": SUGGESTED},
}


def _feature(slug, ar, en, about_ar, about_en, url, module, where):
    return {"slug": slug, "ar": ar, "en": en, "about_ar": about_ar, "about_en": about_en, "url": url, "module": module, "where": where}


def _services(subs):
    return tuple((catalog.SERVICES, sub) for sub in subs)


def _feature_table():
    """The activity screens, each with the (activity, sub-activity) pairs it comes with (``"*"`` = every sub-activity).

    The service sub-activity lists are the screens' own (``WORK_SUBS``, ``SUB_ACTIVITIES``), so this never drifts from them."""

    from agreements import hours, packages
    from agreements.services import SUB_ACTIVITIES as CONTRACT_SUBS
    from workorders.services import WORK_SUBS

    medical, education, restaurants = ((catalog.MEDICAL, "*"),), ((catalog.EDUCATION, "*"),), ((catalog.RESTAURANTS, "*"),)
    return (
        # medical
        _feature("patient_files", "الملف الطبي", "Patient files", "التاريخ المرضي والحساسية والأمراض المزمنة، وكل كشف بالتشخيص والعلاج، وطباعة الروشتة.",
                 "Medical history, allergies and chronic conditions, every visit with its diagnosis and treatment, and the printed prescription.",
                 "medical:patients", "customers", medical),
        _feature("follow_ups", "المتابعات", "Follow-ups", "مين ميعاد متابعته فات ومجاش، ومين ميعاده جاي ولسه ماحجزش، مع تذكير واتساب.",
                 "Who missed a follow-up and who is due but has not booked, with a WhatsApp reminder.", "medical:follow_ups", "appointments_visits", medical),
        _feature("reception_queue", "قايمة الانتظار", "Reception queue", "رقم لكل مريض بترتيب وصوله، والدكتور بينده الدور، وشاشة للصالة.",
                 "A number per patient in arrival order, the doctor calls the next one, and a waiting-room screen.", "medical:queue", "appointments_visits", medical),
        _feature("clinic_report", "تقرير العيادة", "Clinic report", "الكشوفات والمتابعات والمرضى الجداد، والإيراد لكل دكتور وخدمة.",
                 "Visits, follow-ups and new patients, and revenue per doctor and service.", "medical:report", "reports", medical),
        _feature("medical_insurance", "التأمين الطبي", "Medical insurance", "شركات التأمين وعقودها، وكل كشف مؤمَّن عليه بفاتورتين (المريض والشركة)، والمطالبة الشهرية.",
                 "Insurers and their contracts, each insured visit on two invoices (patient and insurer), and the monthly claim.", "medical:insurance",
                 "sales_operations", tuple((catalog.MEDICAL, sub) for sub in catalog.SUB_ACTIVITY_LABELS[catalog.MEDICAL] if sub != "vet")),
        _feature("dental_chart", "خريطة الأسنان وخطة العلاج", "Dental chart & treatment plan", "حالة كل سنة، وخطة العلاج على مراحل، وفوترة اللي خلص منها.",
                 "Each tooth's condition, a treatment plan in stages, and billing the steps done.", "medical:patients", "customers", ((catalog.MEDICAL, "dental"),)),
        _feature("lab_results", "التحاليل ونتايجها", "Lab tests & results", "التحاليل بقيمها المرجعية، والنتيجة بعلامة عالي أو واطي، وطباعة شيت النتايج.",
                 "Tests with their reference ranges, results flagged high or low, and the printed result sheet.", "medical:lab_tests", "items_services",
                 ((catalog.MEDICAL, "lab"),)),
        _feature("physio_packages", "باكدج الجلسات", "Session packages", "باكدج جلسات بيتباع مرة، والجلسات بتتسحب منه واحدة واحدة.",
                 "A package of sessions sold once, with sessions used one by one.", "medical:patients", "sales_operations", ((catalog.MEDICAL, "physio"),)),
        _feature("animal_files", "ملف الحيوان", "Animal files", "صاحب الحيوان عميل، وتحته حيواناته، وكل حيوان بكشوفاته وحساسيته.",
                 "The owner is the customer, with their animals under them, each with its visits and allergies.", "medical:patients", "customers",
                 ((catalog.MEDICAL, "vet"),)),
        # education
        _feature("students_groups", "الطلاب والمجموعات", "Students & groups", "ملف لكل طالب وولي أمره، والمجموعات بمواعيدها ومدرسها، والتسجيل فيها.",
                 "A file per student and parent, groups with their schedule and teacher, and enrolment.", "education:students", "customers", education),
        _feature("attendance", "الحضور والغياب", "Attendance", "حصص النهارده، وتسجيل الحضور، ونسبة الحضور، وواتساب لولي الأمر.",
                 "Today's classes, taking attendance, attendance rates and a WhatsApp to the parent.", "education:today", "appointments_visits", education),
        _feature("monthly_fees", "المصروفات الشهرية", "Monthly fees", "فواتير المصروفات لكل الطلاب بدوسة، وأولياء الأمور اللي عليهم فلوس.",
                 "Fee bills for every student in one click, and the parents who still owe.", "education:fees", "sales_operations", education),
        _feature("teacher_pay", "أجور المدرسين", "Teacher pay", "أجر كل مدرس بالحصة أو بنسبة من المصروفات أو شهري.",
                 "Each teacher's pay per class, as a share of fees, or monthly.", "education:teachers", "employees_technicians", education),
        # restaurants
        _feature("tables_board", "الصالة والطلبات", "Floor & orders", "الطاولات وحالتها، وطلب الصالة والتيك أواي والدليفري، وتذكرة المطبخ والحساب.",
                 "Tables and their state, dine-in, takeaway and delivery orders, the kitchen ticket and the bill.", "restaurant:board", "tables_orders", restaurants),
        _feature("delivery", "الدليفري", "Delivery", "الطيار ووقت الخروج ورسوم التوصيل كسطر في الفاتورة.",
                 "The driver, dispatch time and the delivery fee as its own invoice line.", "restaurant:board", "tables_orders", restaurants),
        _feature("dish_cost", "تكلفة الطبق والهامش", "Dish cost & margin", "خامات كل طبق وتكلفته قصاد سعره في المنيو.",
                 "Each dish's ingredients and cost against its menu price.", "restaurant:costing", "items_services", restaurants),
        _feature("restaurant_report", "تقرير المطعم", "Restaurant report", "البيع بالساعة، وأكتر الأطباق، والطاولات، والويترز، والهالك.",
                 "Sales by hour, best sellers, tables, waiters and waste.", "restaurant:report", "reports", restaurants),
        # services
        _feature("work_orders", "أوامر الشغل", "Work orders", "استلام الجهاز، والتشخيص وعرض السعر، والفني وقطع الغيار، والتسليم بفاتورة وضمان.",
                 "Intake, diagnosis and quote, technician and parts, hand-back with an invoice and a warranty.", "workorders:list", "sales_operations",
                 _services(WORK_SUBS)),
        _feature("service_contracts", "عقود الخدمة الدورية", "Service contracts", "عقد بيتفوتر كل شهر أو 3 شهور أو سنة، وزيارات الفترة.",
                 "A contract billed each month, quarter or year, with the period's visits.", "agreements:list", "sales_operations",
                 _services(CONTRACT_SUBS) + ((catalog.CONTRACTING, "maintenance_contracts"),)),
        _feature("session_packages", "باكدجات الجلسات", "Session packages", "باكدج جلسات بيتباع مرة على فاتورة، والجلسات بتتسحب منه.",
                 "A package of sessions sold once on one invoice, with sessions used from it.", "agreements:packages", "sales_operations",
                 _services(packages.SUB_ACTIVITIES)),
        _feature("billable_hours", "ساعات العمل على العملاء", "Billable hours", "كل واحد يسجّل ساعاته على العميل، وتتفوتر وقت ما تحب.",
                 "Everyone logs their hours per customer, billed whenever you like.", "agreements:hours", "employees_technicians",
                 _services(hours.SUB_ACTIVITIES)),
        # contracting
        _feature("boq_certificates", "المقايسة والمستخلصات", "BOQ & progress bills", "بنود الأعمال بالكمية والفئة، والمستخلصات بالكميات المنفذة، والضمان والدفعة المقدمة.",
                 "Work items by quantity and rate, progress bills by quantity done, retention and the advance.", "projects:list", "projects",
                 ((catalog.CONTRACTING, "*"),)),
        _feature("projects_report", "تقرير المشاريع", "Projects report", "كل مشروع بقيمته والمنفذ والمحصّل، والمستخلصات المتأخرة.",
                 "Each project's value, work done and collected, and overdue progress bills.", "projects:report", "reports", ((catalog.CONTRACTING, "*"),)),
        # manufacturing
        _feature("recipes_runs", "الوصفات والتشغيلات", "Recipes & production runs", "مكونات كل منتج، وتشغيلة بتصرف الخامات وتدخّل المنتج بتكلفته.",
                 "Each product's components, and runs that use the materials and bring the product in at their cost.", "manufacturing:home", "manufacturing",
                 ((catalog.MANUFACTURING, "*"),)),
        _feature("production_orders", "أوامر التشغيل والمراحل", "Production orders & stages", "أمر تشغيل بمراحله، والعمالة والمصاريف على المنتج، والهالك.",
                 "A production order through its stages, labour and overhead on the product, and scrap.", "manufacturing:orders", "manufacturing",
                 ((catalog.MANUFACTURING, "*"),)),
    )


def _layers(activity, sub_activity):
    return (ABOUT_FOR.get(activity, {}), ABOUT_FOR.get((activity, sub_activity), {}))


def module_state(activity, sub_activity, slug):
    """required / suggested / optional for this (activity, sub-activity), or None when the module is not offered to it."""

    activity, sub_activity = catalog.closest(activity, sub_activity)
    base = catalog.preset_state(activity, slug)
    if base == REQUIRED:
        return REQUIRED
    override = SUB_PRESETS.get((activity, sub_activity), {}).get(slug)
    if override:
        return override
    if slug in NOT_OFFERED.get(activity, set()):
        return None
    return base


def module_text(activity, sub_activity, slug, lang="ar"):
    """(name, what it does) for a module in this activity's words."""

    from .vocabulary import term, vocabulary

    activity, sub_activity = catalog.closest(activity, sub_activity)
    index = 1 if lang == "en" else 0
    key = VOCABULARY_KEY.get(slug)
    name = term(key, lang, vocabulary(activity, sub_activity)) if key else catalog.module_label(slug, lang)
    about = ABOUT[slug][index]
    for layer in _layers(activity, sub_activity):
        if slug in layer:
            about = layer[slug][index]
    return name, about


def _applies(where, activity, sub_activity):
    return any(a == activity and s in ("*", sub_activity) for a, s in where)


def features(activity, sub_activity, lang="ar", module=None):
    """The activity's own screens: [{slug, label, about, url, module}], optionally those under one module."""

    activity, sub_activity = catalog.closest(activity, sub_activity)
    rows = []
    for entry in _feature_table():
        if not _applies(entry["where"], activity, sub_activity) or (module and entry["module"] != module):
            continue
        try:
            url = reverse(entry["url"])
        except NoReverseMatch:
            url = ""
        rows.append({"slug": entry["slug"], "label": entry["en" if lang == "en" else "ar"], "about": entry[f"about_{'en' if lang == 'en' else 'ar'}"],
                     "url": url, "module": entry["module"]})
    return rows


def module_rows(activity, sub_activity, lang="ar", enabled=None):
    """Every module offered to the activity (and any already on), most needed first.

    Each row: slug, label, about, preset (required/suggested/optional, or None
    when not offered but on), features (the activity screens under it)."""

    enabled = set(enabled or ())
    under = {}
    for feature in features(activity, sub_activity, lang):
        under.setdefault(feature["module"], []).append(feature)
    rows = []
    for slug in catalog.MODULE_SLUGS:
        preset = module_state(activity, sub_activity, slug)
        if preset is None and slug not in enabled:
            continue
        name, about = module_text(activity, sub_activity, slug, lang)
        rows.append({"slug": slug, "label": name, "about": about, "preset": preset, "features": under.get(slug, [])})
    rank = {state: n for n, state in enumerate(ORDER)}
    rows.sort(key=lambda row: rank.get(row["preset"], len(ORDER)))  # stable: the catalog's order within each group
    return rows


def default_modules(activity, sub_activity):
    """What starts switched on: the required and suggested modules for this sub-activity."""

    return tuple(slug for slug in catalog.MODULE_SLUGS if module_state(activity, sub_activity, slug) in (REQUIRED, SUGGESTED))
