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
    "education": ("appointments",),
    "services": ("appointments",),
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
    {"key": "pos", "ar": "الكاشير", "en": "Point of sale", "url_name": "sales:pos", "module": "sales_operations", "capability": "pos", "permission": "sales.create_sales_invoice"},
    {"key": "purchases", "ar": "المشتريات", "en": "Purchases", "url_name": "purchases:list", "module": "purchases", "permission": "purchases.view_purchase_invoices"},
    {"key": "inventory", "ar": "المخزون", "en": "Inventory", "url_name": "inventory:stock", "module": "inventory", "permission": "inventory.view_stock"},
    {"key": "warehouses", "ar": "المخازن", "en": "Warehouses", "url_name": "master_data:locations", "module": "inventory", "permission": "inventory.view_stock"},
    {"key": "customers", "ar": "العملاء", "en": "Customers", "url_name": "master_data:customers", "module": "customers"},
    {"key": "suppliers", "ar": "الموردون", "en": "Suppliers", "url_name": "master_data:suppliers", "module": "suppliers", "permission": "master_data.view_suppliers"},
    {"key": "items", "ar": "الأصناف والخدمات", "en": "Items & services", "url_name": "master_data:items", "module": "items_services"},
    {"key": "cashboxes", "ar": "الخزائن", "en": "Cashboxes", "url_name": "cashboxes:list", "module": "cashboxes", "permission": "cashboxes.view_cashboxes"},
    {"key": "manufacturing", "ar": "التصنيع", "en": "Manufacturing", "url_name": "manufacturing:home", "module": "manufacturing", "permission": "inventory.view_stock"},
    {"key": "projects", "ar": "المشاريع", "en": "Projects", "url_name": "projects:list", "module": "projects", "permission": "sales.view_sales_invoices"},
    {"key": "restaurant", "ar": "الطاولات", "en": "Tables", "url_name": "restaurant:board", "module": "tables_orders", "permission": "sales.view_sales_invoices"},
    {"key": "medical", "ar": "الملفات الطبية", "en": "Patient files", "url_name": "medical:patients", "module": "customers", "activity": "medical", "allow": "medical.services.can_view"},
    {"key": "appointments", "ar": "المواعيد", "en": "Appointments", "url_name": "appointments:agenda", "module": "appointments_visits", "permission": "sales.view_sales_invoices"},
    {"key": "staff", "ar": "الموظفون", "en": "Employees", "url_name": "staff:list", "module": "employees_technicians", "permission": "master_data.view_master_data"},
    {"key": "expenses", "ar": "المصروفات", "en": "Expenses", "url_name": "expenses:list", "module": "expenses", "permission": "cashboxes.view_expenses"},
    # R2: features switched on in Settings -> Features get their own place in the menu.
    {"key": "taxes", "ar": "الضرائب", "en": "Taxes", "url_name": "taxes:settings", "module": None, "capability": "vat", "permission": "master_data.view_master_data"},
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
        if permission and not user_has_permission(user, permission):
            continue
        if item.get("activity") and item["activity"] != _activity():
            continue
        if item.get("allow") and not import_string(item["allow"])(user):
            continue
        label = term(NAV_TERMS[item["key"]], lang, words) if item["key"] in NAV_TERMS else item[lang]
        items.append({"key": item["key"], "label": label, "url_name": item["url_name"]})
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
        prefix = item["url"]
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
