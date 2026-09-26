"""The one list of sections every signed-in screen can navigate to.

The dashboard and the app shell (sidebar, phone tab bar) both read it, so a
section a user cannot open — module switched off, permission not held — is
hidden the same way everywhere. Hiding is only a courtesy: each view still
enforces its own permission.
"""

from django.urls import reverse

from permissions.services import user_has_permission


NAV_ITEMS = (
    {"key": "dashboard", "ar": "لوحة القيادة", "en": "Dashboard", "url_name": "dashboard_snapshot", "module": None},
    {"key": "operations", "ar": "عمليات البيع", "en": "Sales operations", "url_name": "sales:list", "module": "sales_operations", "permission": "sales.view_sales_invoices"},
    {"key": "purchases", "ar": "المشتريات", "en": "Purchases", "url_name": "purchases:list", "module": "purchases", "permission": "purchases.view_purchase_invoices"},
    {"key": "inventory", "ar": "المخزون", "en": "Inventory", "url_name": "inventory:stock", "module": "inventory", "permission": "inventory.view_stock"},
    {"key": "customers", "ar": "العملاء", "en": "Customers", "url_name": "master_data:customers", "module": "customers"},
    {"key": "suppliers", "ar": "الموردون", "en": "Suppliers", "url_name": "master_data:suppliers", "module": "suppliers", "permission": "master_data.view_suppliers"},
    {"key": "items", "ar": "الأصناف والخدمات", "en": "Items & services", "url_name": "master_data:items", "module": "items_services"},
    {"key": "cashboxes", "ar": "الخزائن", "en": "Cashboxes", "url_name": "cashboxes:list", "module": "cashboxes", "permission": "cashboxes.view_cashboxes"},
    {"key": "expenses", "ar": "المصروفات", "en": "Expenses", "url_name": "expenses:list", "module": "expenses", "permission": "cashboxes.view_expenses"},
    {"key": "reports", "ar": "التقارير", "en": "Reports", "url_name": "report_hub", "module": "reports"},
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
    "purchases": {"ar": "الشراء", "en": "Purchases"},
    "inventory": {"ar": "المخزون", "en": "Stock"},
    "customers": {"ar": "العملاء", "en": "Customers"},
    "items": {"ar": "الأصناف", "en": "Items"},
    "cashboxes": {"ar": "الخزائن", "en": "Cash"},
    "expenses": {"ar": "المصروفات", "en": "Expenses"},
    "reports": {"ar": "التقارير", "en": "Reports"},
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
    },
}


def nav_items(user, lang, modules):
    items = []
    for item in NAV_ITEMS:
        if item["module"] is not None and item["module"] not in modules:
            continue
        permission = item.get("permission")
        if permission and not user_has_permission(user, permission):
            continue
        items.append({"key": item["key"], "label": item[lang], "url_name": item["url_name"]})
    return items


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
    tabs = [dict(by_key[key], label=TAB_LABELS[key][lang]) for key in TAB_PRIORITY if key in by_key][:TAB_COUNT]
    tab_keys = {tab["key"] for tab in tabs}
    return {
        "items": items,
        "tabs": tabs,
        # "More" lights up when the current page lives behind it.
        "more_current": current is not None and current not in tab_keys,
        "words": SHELL_WORDS[lang],
        "user_name": display_name(user),
    }
