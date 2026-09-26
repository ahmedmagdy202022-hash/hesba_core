"""USERS-001: a user with a temporary password changes it before anything else."""

from urllib.parse import quote

from django.shortcuts import redirect


ALLOWED_PREFIXES = ("/profile/password/", "/logout/", "/login/", "/static/", "/manifest", "/sw.js", "/favicon")


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not request.path.startswith(ALLOWED_PREFIXES):
            profile = getattr(user, "hesba_profile", None)
            if profile is not None and profile.must_change_password:
                return redirect(f"/profile/password/?next={quote(request.get_full_path())}")
        return self.get_response(request)
