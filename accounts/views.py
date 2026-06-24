from django.shortcuts import render
from django.urls import reverse


LOGIN_CHECKPOINT_CODE = "109_LOGIN_AND_DEVICE_SHELL_STABILIZATION"


def login_shell(request):
    """Render the responsive login shell for the current device checkpoint.

    This view intentionally performs no authentication or setup logic. The form
    routes to the existing safe dashboard until real login handling is added in a
    later checkpoint.
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
