from django.utils import translation
from django.utils.cache import add_never_cache_headers, patch_cache_control


class NoStoreHtmlMiddleware:
    """Keep rendered pages out of the browser cache and back/forward cache.

    Every HTML page here is either behind a login (balances, invoices, stock)
    or carries a CSRF token. A cached copy therefore does harm in two ways:
    the Back button after logout re-displays financial screens to whoever is at
    the device, and a page cached before the last login posts a stale token
    (the FIX-001 logout 403). ``no-store`` stops both.

    Only HTML is touched, and only when the view has not already chosen its own
    Cache-Control. Static files are served outside Django and are unaffected.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.has_header("Cache-Control"):
            return response
        if not response.get("Content-Type", "").startswith("text/html"):
            return response
        add_never_cache_headers(response)
        patch_cache_control(response, private=True)
        return response


class PageLanguageMiddleware:
    """UX Wave 3: an English page (``?lang=en``) renders Django's own formatting in English.

    LANGUAGE_CODE is "ar" and there is no LocaleMiddleware: each page picks its language from ``?lang=``.
    Without this, a bare ``{{ invoice.invoice_date }}`` on an English page printed «10 نوفمبر، 2026» and a bare
    decimal took the Arabic locale's comma. Only the query string is read (never POST or a cookie), and only
    "en" switches; everything else stays Arabic exactly as before. Display only: no figure is computed here.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.GET.get("lang") or "").lower() == "en":
            with translation.override("en"):
                return self.get_response(request)
        return self.get_response(request)
