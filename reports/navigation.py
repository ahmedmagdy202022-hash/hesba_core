"""The one list of sections every signed-in screen can navigate to.

The dashboard and the app shell (sidebar, phone tab bar) both read it, so a
section a user cannot open — module switched off, permission not held — is
hidden the same way everywhere. Hiding is only a courtesy: each view still
enforces its own permission.
"""

from django.urls import reverse
from django.utils.module_loading import import_string

from permissions.services import user_has_permission

from settings_core.capabilities import capability_enabled
from settings_core.vocabulary import active_vocabulary, term

# ACT-PROFILE-001: sections whose name follows the activity's own words.
NAV_TERMS = {"operations": "operations", "customers": "customers", "suppliers": "suppliers", "items": "items", "staff": "staff", "appointments": "appointments"}
TAB_TERMS = {"operations": "operations_short", "customers": "customers", "items": "items_short", "appointments": "appointments"}

# ACT-PROFILE-002: the section an activity works from comes right after the
# dashboard, in the sidebar and on the phone tab bar.
ACTIVITY_LEAD = {
    "restaurants": ("restaurant", "pos"),
    "medical": ("appointments", "medical"),
    "education": ("education", "classes", "appointments"),
    "services": ("workorders", "appointments", "packages", "hours", "contracts"),
    "manufacturing": ("manufacturing",),
    "contracting": ("projects",),
}


def _activity():
    from entities.current import effective_activity

    return effective_activity()[0] or ""


#: ENT-002: sections that belong to one activity. While working in an entity
#: that has its own activity, the others' sections step aside (the whole-group
#: view keeps every enabled section).
ACTIVITY_OWNED = {"manufacturing": "manufacturing", "projects": "contracting", "restaurant": "restaurants"}


def _hidden_for_entity(key):
    from entities.current import current_entity

    entity = current_entity()
    owner = ACTIVITY_OWNED.get(key)
    return bool(owner and entity is not None and entity.activity_slug and entity.activity_slug != owner)


def _lead_first(keys, activity):
    lead = [key for key in ACTIVITY_LEAD.get(activity, ()) if key in keys]
    head = [key for key in keys if key == "dashboard"]
    return head + lead + [key for key in keys if key not in lead and key != "dashboard"]


NAV_ITEMS = (
    {"key": "dashboard", "ar": "لوحة القيادة", "en": "Dashboard", "url_name": "dashboard_snapshot", "module": None},
    {"key": "operations", "ar": "عمليات البيع", "en": "Sales operations", "url_name": "sales:list", "module": "sales_operations", "permission": "sales.view_sales_invoices"},
    # T1: quotations that become sales orders, invoiced on the ordinary invoice form.
    {"key": "quotes", "ar": "عروض الأسعار والأوامر", "en": "Quotations & orders", "url_name": "orders:list", "module": "sales_operations", "capability": "quotations",
     "permission": "sales.view_sales_invoices"},
    # T5: promotions and coupons, filled into the line discounts on the invoice and at the till.
    {"key": "promotions", "ar": "العروض", "en": "Promotions", "url_name": "promotions:list", "module": "sales_operations", "capability": "promotions",
     "permission": "master_data.view_master_data"},
    {"key": "pos", "ar": "الكاشير", "en": "Point of sale", "url_name": "sales:pos", "module": "sales_operations", "capability": "pos", "permission": "sales.create_sales_invoice"},
    {"key": "purchases", "ar": "المشتريات", "en": "Purchases", "url_name": "purchases:list", "module": "purchases", "permission": "purchases.view_purchase_invoices"},
    {"key": "inventory", "ar": "المخزون", "en": "Inventory", "url_name": "inventory:stock", "module": "inventory", "permission": "inventory.view_stock"},
    # T10: a pharmacy finds a drug by name or active ingredient, sees its alternatives in stock, and dispenses prescriptions.
    {"key": "drugs", "ar": "الأدوية والبدائل", "en": "Drugs & alternatives", "url_name": "pharmacy:lookup", "module": "inventory", "capability": "drugs",
     "permission": "master_data.view_master_data"},
    {"key": "warehouses", "ar": "المخازن", "en": "Warehouses", "url_name": "inventory:warehouses", "module": "inventory", "permission": "inventory.view_stock",
     "also": ("/master-data/locations/",)},  # R2-7: the warehouses hub; adding or editing one is still master data
    {"key": "customers", "ar": "العملاء", "en": "Customers", "url_name": "master_data:customers", "module": "customers", "permission": "master_data.view_master_data",
     "allow": "reports.navigation.sees_customers"},
    {"key": "suppliers", "ar": "الموردون", "en": "Suppliers", "url_name": "master_data:suppliers", "module": "suppliers", "permission": "master_data.view_suppliers"},
    {"key": "items", "ar": "الأصناف والخدمات", "en": "Items & services", "url_name": "master_data:items", "module": "items_services",
     "permission": "master_data.view_master_data"},  # HG-048: a waiter or site engineer does not hold it
    {"key": "cashboxes", "ar": "الخزائن", "en": "Cashboxes", "url_name": "cashboxes:list", "module": "cashboxes", "permission": "cashboxes.view_cashboxes"},
    # T3: bank accounts and matching their statements.
    {"key": "banks", "ar": "البنوك", "en": "Banks", "url_name": "banking:list", "module": "cashboxes", "permission": "cashboxes.view_finance"},
    # T4: cheques received and issued, and what falls due.
    {"key": "cheques", "ar": "الشيكات", "en": "Cheques", "url_name": "cheques:register", "module": "cashboxes",
     "permission": ("cashboxes.view_finance", "sales.receive_customer_payment", "purchases.pay_supplier")},
    {"key": "manufacturing", "ar": "التصنيع", "en": "Manufacturing", "url_name": "manufacturing:home", "module": "manufacturing", "permission": "inventory.view_stock"},
    {"key": "projects", "ar": "المشاريع", "en": "Projects", "url_name": "projects:list", "module": "projects",
     "permission": ("sales.view_sales_invoices", "projects.view_projects")},
    {"key": "restaurant", "ar": "الطاولات", "en": "Tables", "url_name": "restaurant:board", "module": "tables_orders",
     "permission": ("sales.view_sales_invoices", "restaurant.take_orders")},
    {"key": "education", "ar": "الطلاب والمجموعات", "en": "Students & groups", "url_name": "education:students", "module": "customers", "activity": "education",
     "permission": "master_data.view_master_data"},
    # EDU-002: a teacher without the school's records still reaches the attendance of their own groups.
    {"key": "classes", "ar": "حصص مجموعاتي", "en": "My classes", "url_name": "education:today", "module": "customers", "activity": "education",
     "allow": "education.attendance.teaches_only"},
    # SVC-001: a workshop works from its work orders (a technician who has orders sees them too).
    {"key": "workorders", "ar": "أوامر الشغل", "en": "Work orders", "url_name": "workorders:list", "module": "sales_operations", "activity": "services",
     "allow": "workorders.services.sees_work_orders"},
    {"key": "medical", "ar": "الملفات الطبية", "en": "Patient files", "url_name": "medical:patients", "module": "customers", "activity": "medical", "allow": "medical.services.can_view"},
    {"key": "appointments", "ar": "المواعيد", "en": "Appointments", "url_name": "appointments:agenda", "module": "appointments_visits", "permission": "sales.view_sales_invoices"},
    # MED-007: reception's waiting list, for clinics and medical centres.
    {"key": "queue", "ar": "قائمة الانتظار", "en": "Waiting list", "url_name": "medical:queue", "module": "appointments_visits", "activity": "medical",
     "allow": "medical.queue.can_use"},
    {"key": "contracts", "ar": "عقود الخدمة", "en": "Service contracts", "url_name": "agreements:list", "module": None, "allow": "agreements.services.can_view"},
    {"key": "packages", "ar": "باكدجات الجلسات", "en": "Session packages", "url_name": "agreements:packages", "module": None, "allow": "agreements.packages.can_view"},
    {"key": "hours", "ar": "ساعات العمل", "en": "Hours worked", "url_name": "agreements:hours", "module": None, "allow": "agreements.hours.can_view"},
    # T9: the day's messages to customers (amounts due, instalments, appointments, refills), sent through WhatsApp.
    {"key": "messages", "ar": "رسائل العملاء", "en": "Customer messages", "url_name": "messaging:outbox", "module": "customers",
     "allow": "reports.navigation.sees_customers"},
    {"key": "staff", "ar": "الموظفون", "en": "Employees", "url_name": "staff:list", "module": "employees_technicians", "allow": "reports.navigation.sees_staff"},
    # T11: who came in today, the month's sheet and leave; and each employee's own check-in button.
    {"key": "attendance", "ar": "الحضور والإجازات", "en": "Attendance & leave", "url_name": "attendance:today", "module": "employees_technicians",
     "allow": "reports.navigation.sees_staff"},
    {"key": "my_attendance", "ar": "حضوري وانصرافي", "en": "My attendance", "url_name": "attendance:me", "module": "employees_technicians",
     "allow": "reports.navigation.checks_in_only"},
    # T12: the month's payroll — salaries, insurance and income tax, posted as debts and paid from a cashbox.
    {"key": "payroll", "ar": "المرتبات", "en": "Payroll", "url_name": "payroll:list", "module": "employees_technicians", "permission": "cashboxes.view_expenses"},
    {"key": "expenses", "ar": "المصروفات", "en": "Expenses", "url_name": "expenses:list", "module": "expenses", "permission": "cashboxes.view_expenses"},
    # R2: features switched on in Settings -> Features get their own place in the menu.
    {"key": "taxes", "ar": "الضرائب", "en": "Taxes", "url_name": "taxes:settings", "module": None, "capability": "vat", "permission": "master_data.view_master_data",
     "allow": "reports.navigation.sees_staff"},
    {"key": "einvoice", "ar": "الفاتورة الإلكترونية", "en": "E-invoicing", "url_name": "einvoice:issuer", "module": None, "capability": "e_invoice", "permission": "settings.view_settings"},
    {"key": "assets", "ar": "الأصول والإهلاك", "en": "Assets & depreciation", "url_name": "fixed_assets:list", "module": None, "capability": "fixed_assets", "permission": "cashboxes.view_expenses"},
    {"key": "reports", "ar": "التقارير", "en": "Reports", "url_name": "report_hub", "module": "reports"},
    {"key": "ledger", "ar": "الحسابات العامة", "en": "Accounting", "url_name": "ledger:summary", "module": None, "permission": "accounting.view_ledger"},
    {"key": "closing", "ar": "إقفال الفترات", "en": "Period closing", "url_name": "closing:list", "module": None, "permission": "closing.run_closing"},
    {"key": "profile", "ar": "ملفي", "en": "My profile", "url_name": "accounts:profile", "module": None},
    {"key": "settings", "ar": "الإعدادات", "en": "Settings", "url_name": "settings_core:overview", "module": None, "permission": "settings.view_settings"},
)

# The phone tab bar has room for four short labels plus "More"; these are the
# sections a counter clerk reaches for first, in order. Whatever a user cannot
# see is skipped and the next one moves up.
TAB_PRIORITY = ("dashboard", "operations", "inventory", "customers", "purchases", "items", "cashboxes", "reports")
TAB_COUNT = 4
TAB_LABELS = {
    "dashboard": {"ar": "الرئيسية", "en": "Home"},
    "operations": {"ar": "البيع", "en": "Sales"},
    "pos": {"ar": "الكاشير", "en": "POS"},
    "purchases": {"ar": "الشراء", "en": "Purchases"},
    "inventory": {"ar": "المخزون", "en": "Stock"},
    "customers": {"ar": "العملاء", "en": "Customers"},
    "items": {"ar": "الأصناف", "en": "Items"},
    "cashboxes": {"ar": "الخزائن", "en": "Cash"},
    "expenses": {"ar": "المصروفات", "en": "Expenses"},
    "reports": {"ar": "التقارير", "en": "Reports"},
    "restaurant": {"ar": "الطاولات", "en": "Tables"},
    "manufacturing": {"ar": "التصنيع", "en": "Production"},
    "projects": {"ar": "المشاريع", "en": "Projects"},
    "medical": {"ar": "الملفات", "en": "Files"},
    "education": {"ar": "الطلاب", "en": "Students"},
    "classes": {"ar": "حصصي", "en": "Classes"},
    "workorders": {"ar": "الأوامر", "en": "Orders"},
    "quotes": {"ar": "العروض", "en": "Quotes"},
    "promotions": {"ar": "الخصومات", "en": "Promos"},
    "drugs": {"ar": "الأدوية", "en": "Drugs"},
    "attendance": {"ar": "الحضور", "en": "Attendance"},
    "messages": {"ar": "الرسائل", "en": "Messages"},
    "my_attendance": {"ar": "حضوري", "en": "Check in"},
    "payroll": {"ar": "المرتبات", "en": "Payroll"},
    "banks": {"ar": "البنوك", "en": "Banks"},
    "cheques": {"ar": "الشيكات", "en": "Cheques"},
    "contracts": {"ar": "العقود", "en": "Contracts"},
    "packages": {"ar": "الباكدجات", "en": "Packages"},
    "hours": {"ar": "الساعات", "en": "Hours"},
}

SHELL_WORDS = {
    "ar": {
        "main_nav": "التنقل الرئيسي",
        "menu": "القائمة",
        "close_menu": "إغلاق القائمة",
        "more": "المزيد",
        "skip": "انتقل إلى المحتوى",
        "logout": "تسجيل الخروج",
        "language": "English",
        "home": "حِسبة — لوحة القيادة",
        "collapse": "طي القائمة",
        "expand": "توسيع القائمة",
        "language_short": "EN",
        "search": "بحث",
        "search_placeholder": "بحث… ( / )",
    },
    "en": {
        "main_nav": "Main navigation",
        "menu": "Menu",
        "close_menu": "Close menu",
        "more": "More",
        "skip": "Skip to content",
        "logout": "Log out",
        "language": "العربية",
        "home": "Hesba — Dashboard",
        "collapse": "Collapse menu",
        "expand": "Expand menu",
        "language_short": "ع",
        "search": "Search",
        "search_placeholder": "Search… ( / )",
    },
}


#: AUDIT-2: who works with customers (patients, students...) and who sees the
#: staff list. A stock keeper never needs the customer list, and in a clinic or
#: a school that list is patients and students; a cashier or stock keeper never
#: needs the staff list. The same office audience sees the tax settings.
CUSTOMER_AUDIENCE = ("sales.view_sales_invoices", "sales.receive_customer_payment", "reports.view_customer_report")
STAFF_AUDIENCE = ("settings.view_settings", "accounting.view_ledger")


def sees_customers(user):
    return any(user_has_permission(user, code) for code in CUSTOMER_AUDIENCE)


def sees_staff(user):
    return any(user_has_permission(user, code) for code in STAFF_AUDIENCE)


def checks_in_only(user):
    """T11: an employee who does not manage the team gets "my attendance" in the menu (managers reach it from the attendance tabs)."""

    from attendance.services import is_linked_employee

    return not sees_staff(user) and is_linked_employee(user)


def nav_items(user, lang, modules):
    words = active_vocabulary()
    items = []
    for item in NAV_ITEMS:
        if item["module"] is not None and item["module"] not in modules:
            continue
        if _hidden_for_entity(item["key"]):
            continue
        if item.get("capability") and not capability_enabled(item["capability"]):
            continue
        permission = item.get("permission")
        # A tuple is any one of them (HG-048: the floor for a waiter, the projects for a site engineer).
        if permission and not any(user_has_permission(user, code) for code in ((permission,) if isinstance(permission, str) else permission)):
            continue
        if item.get("activity") and item["activity"] != _activity():
            continue
        if item.get("allow") and not import_string(item["allow"])(user):
            continue
        label = term(NAV_TERMS[item["key"]], lang, words) if item["key"] in NAV_TERMS else item[lang]
        items.append({"key": item["key"], "label": label, "url_name": item["url_name"], "also": item.get("also", ())})
    order = _lead_first([item["key"] for item in items], _activity())
    position = {key: index for index, key in enumerate(order)}
    return sorted(items, key=lambda item: position[item["key"]])


def current_key(items, path):
    """The section the page at `path` belongs to: the longest matching URL prefix.

    /sales/12/ belongs to sales, /master-data/items/new/ to items. A page outside
    every section (the master-data hub, say) marks nothing.
    """

    best, best_len = None, 0
    for item in items:
        for prefix in (item["url"], *item.get("also", ())):
            if path.startswith(prefix) and len(prefix) > best_len:
                best, best_len = item["key"], len(prefix)
    return best


def display_name(user):
    profile = getattr(user, "hesba_profile", None)
    if profile is not None and profile.display_name:
        return profile.display_name
    return user.get_short_name() or user.get_username()


def app_shell(user, lang, modules, path):
    items = [dict(item, url=reverse(item["url_name"])) for item in nav_items(user, lang, modules)]
    current = current_key(items, path)
    for item in items:
        item["current"] = item["key"] == current

    by_key = {item["key"]: item for item in items}
    words = active_vocabulary()
    lead = [key for key in ACTIVITY_LEAD.get(_activity(), ()) if key in by_key][:1]
    priority = ["dashboard"] + lead + [key for key in TAB_PRIORITY if key not in lead and key != "dashboard"]
    tabs = [dict(by_key[key], label=term(TAB_TERMS[key], lang, words) if key in TAB_TERMS else TAB_LABELS[key][lang])
            for key in priority if key in by_key][:TAB_COUNT]
    tab_keys = {tab["key"] for tab in tabs}
    return {
        "items": items,
        "tabs": tabs,
        # "More" lights up when the current page lives behind it.
        "more_current": current is not None and current not in tab_keys,
        "words": SHELL_WORDS[lang],
        "user_name": display_name(user),
    }
