from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


LOGIN_CHECKPOINT_CODE = "109_LOGIN_AND_DEVICE_SHELL_STABILIZATION"


def login_shell(request):
    """Render the responsive login shell for the current device checkpoint.

    This view intentionally performs no authentication or onboarding logic. The
    form routes to the existing safe dashboard until real login handling is added
    in a later checkpoint.
    """

    return render(
        request,
        "accounts/login.html",
        {
            "checkpoint_code": LOGIN_CHECKPOINT_CODE,
            "dashboard_url": reverse("dashboard_snapshot"),
            "home_url": reverse("home"),
            "reports_url": reverse("report_hub"),
            "status_url": reverse("status_counts_report"),
            "language_options": [
                {"code": "ar", "label": "العربية"},
                {"code": "en", "label": "English"},
            ],
        },
    )


def csrf_failure(request, reason=""):
    """Replace Django's raw CSRF 403 page with one a shop owner can act on.

    The usual trigger is a stale page, not an attack: logging in rotates the
    CSRF secret, so a tab or back-button copy rendered before that login posts
    a token that no longer matches. Protection is unchanged — the request is
    still refused — but the user gets a way forward instead of a debug page.
    """

    if request.path == reverse("logout"):
        if not request.user.is_authenticated:
            # Already signed out: there is nothing left to protect, so finish
            # the journey the user started.
            return redirect("login")
        # Still signed in. Do not log out on an unverified request; offer a
        # freshly tokened form so one click completes it.
        return render(
            request, "accounts/csrf_failure.html", {"logout_retry": True}, status=403
        )

    return render(
        request,
        "accounts/csrf_failure.html",
        {"logout_retry": False, "reload_url": _reload_url(request)},
        status=403,
    )


def _reload_url(request):
    """The page the rejected form was on, so a reload keeps its context.

    The failing request is usually a POST whose target means little as a GET:
    reloading /setup/complete/ would drop the choices that lived only in the
    review page's query string. The same-site Referer is that page. Anything
    else falls back to the request path.
    """

    referer = request.META.get("HTTP_REFERER", "")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer
    return request.path
