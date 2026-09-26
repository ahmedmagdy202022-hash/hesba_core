"""GATE-001: a switched-off module's screens are closed at the destination.

Hiding a section from the sidebar only hides the link; before this gate a
bookmarked /purchases/ still opened, and could still post, after the owner
switched Purchases off. The gate answers every URL of a switched-off module
with a clear page saying so, and a way to Settings.

Rules:
- Nothing is deleted or hidden in the data: switching a module back on brings
  every screen back as it was.
- Reports stay open. The roadmap's fixed rule keeps historical entries and
  reports after a module is switched off, and the report pages only read.
- The gate only applies once setup is complete. Before that there are no
  module choices to honour, and the setup flow itself must stay reachable.
- A module with no backend yet (expenses, appointments) has no screens to
  close, so it never appears here.
"""

from django.shortcuts import render

from permissions.services import user_has_permission

from . import setup_catalog as catalog
from .models import ClientProfile
from .setup_services import enabled_modules


# URL prefix -> module slug. The longest matching prefix wins, so the
# master-data sub-sections can each follow their own module.
MODULE_ROUTES = (
    ("/sales/", "sales_operations"),
    ("/purchases/", "purchases"),
    ("/inventory/", "inventory"),
    ("/cashboxes/", "cashboxes"),
    ("/master-data/customers/", "customers"),
    ("/master-data/suppliers/", "suppliers"),
    ("/master-data/items/", "items_services"),
    ("/master-data/categories/", "items_services"),
    ("/master-data/cashboxes/", "cashboxes"),
)

WORDS = {
    "ar": {
        "page_title": "الموديول غير مفعّل",
        "dashboard": "لوحة القيادة",
        "language": "English",
        "title": "موديول «{module}» غير مفعّل",
        "body": "الشاشة دي تبع موديول مقفول في الإعداد الحالي. بياناتك محفوظة زي ما هي، وهترجع تظهر أول ما الموديول يتفعّل.",
        "owner_hint": "اطلب من صاحب الحساب يفعّله.",
        "settings": "فتح الإعدادات",
        "back": "العودة للوحة القيادة",
    },
    "en": {
        "page_title": "Module not enabled",
        "dashboard": "Dashboard",
        "language": "العربية",
        "title": "The “{module}” module is not enabled",
        "body": "This screen belongs to a module that is switched off for this business. Your data is kept as it is and comes back as soon as the module is switched on.",
        "owner_hint": "Ask the account owner to switch it on.",
        "settings": "Open settings",
        "back": "Back to the dashboard",
    },
}


def module_for_path(path):
    best, best_len = None, 0
    for prefix, slug in MODULE_ROUTES:
        if path.startswith(prefix) and len(prefix) > best_len:
            best, best_len = slug, len(prefix)
    return best


def closed_module(path):
    """The module that closes `path`, or None when the page may open."""

    slug = module_for_path(path)
    if slug is None:
        return None
    profile = ClientProfile.get_active()
    if profile is None or not profile.setup_is_complete:
        return None
    return None if slug in enabled_modules() else slug


def module_disabled_response(request, slug):
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    words = WORDS[lang]
    module = catalog.module_label(slug, lang)
    context = {
        "lang": lang,
        "dir": "ltr" if lang == "en" else "rtl",
        "words": words,
        "page_title": words["page_title"],
        "module_slug": slug,
        "title": words["title"].format(module=module),
        "can_open_settings": user_has_permission(request.user, "settings.view_settings"),
    }
    return render(request, "settings_core/module_disabled.html", context, status=403)


class ModuleGateMiddleware:
    """Runs after authentication, so anonymous users still go to login first."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return None
        slug = closed_module(request.path)
        if slug is None:
            return None
        return module_disabled_response(request, slug)
