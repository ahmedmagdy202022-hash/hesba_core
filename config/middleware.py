class HesbaLanguageMiddleware:
    """Small project-level language helper.

    This is intentionally lightweight for the current UI phase:
    - keeps the selected lang in session from ?lang=ar/en
    - injects the global Hesba language switch script into normal app pages
    - avoids Django admin and login because they have their own behavior
    """

    SCRIPT_TAG = '<script src="/static/hesba/js/hesba_i18n.js?v=112"></script>'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        requested_lang = request.GET.get("lang")
        if requested_lang in {"ar", "en"}:
            request.session["hesba_lang"] = requested_lang
        elif "hesba_lang" not in request.session:
            request.session["hesba_lang"] = "ar"

        response = self.get_response(request)
        lang = request.session.get("hesba_lang", "ar")
        response["Content-Language"] = "en" if lang == "en" else "ar"

        if not self._should_inject(request, response):
            return response

        try:
            content = response.content.decode(response.charset or "utf-8")
        except Exception:
            return response

        if self.SCRIPT_TAG in content or "/static/hesba/js/hesba_i18n.js" in content:
            return response

        marker = "</body>"
        if marker not in content:
            return response

        content = content.replace(marker, f"    {self.SCRIPT_TAG}\n{marker}", 1)
        response.content = content.encode(response.charset or "utf-8")
        response["Content-Length"] = str(len(response.content))
        return response

    def _should_inject(self, request, response):
        if getattr(response, "streaming", False):
            return False
        if response.status_code != 200:
            return False
        content_type = response.get("Content-Type", "")
        if "text/html" not in content_type:
            return False
        path = request.path or ""
        if path.startswith("/admin/") or path.startswith("/login/"):
            return False
        return True
