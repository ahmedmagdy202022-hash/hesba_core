from django.conf import settings
from django.contrib import admin as django_admin
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from django.views.generic import TemplateView

from config.health import healthz

from reports.dashboard_views import dashboard
from reports.status_views import status_counts_report
from reports.functional_views import report_hub
from reports.views import home
from accounts.login_guard import GuardedLoginView
from settings_core.setup_views import after_login, root_redirect, setup_complete, setup_review


urlpatterns = [
    path("", root_redirect, name="root_redirect"),
    path("healthz/", healthz, name="healthz"),
    path("login/", GuardedLoginView.as_view(template_name="registration/login.html", next_page="/start/"), name="login"),
    # Logout must not itself require a login: a signed-out user posting from a
    # stale page was bounced to /login/?next=/logout/ and, after signing back
    # in, landed on a GET of this POST-only view (405).
    path("logout/", login_not_required(LogoutView.as_view(next_page="login")), name="logout"),
    path("start/", after_login, name="after_login"),
    path("setup/", TemplateView.as_view(template_name="setup/setup_gate.html"), name="setup_gate"),
    path("setup/activity/", TemplateView.as_view(template_name="setup/activity_selection.html"), name="setup_activity"),
    path("setup/activity/commercial/", TemplateView.as_view(template_name="setup/activity_commercial_subactivity.html"), name="setup_activity_commercial"),
    path("setup/activity/services/", TemplateView.as_view(template_name="setup/activity_services_subactivity.html"), name="setup_activity_services"),
    path("setup/activity/service/", TemplateView.as_view(template_name="setup/activity_subactivity_placeholder.html"), name="setup_activity_service"),
    path("setup/modules/", TemplateView.as_view(template_name="setup/modules_selection.html"), name="setup_modules"),
    path("setup/review/", setup_review, name="setup_review"),
    path("setup/complete/", setup_complete, name="setup_complete"),
    path("home/", home, name="home"),
    path("dashboard/", dashboard, name="dashboard_snapshot"),
    path("master-data/", include("master_data.urls")),
    path("purchases/", include("purchases.urls")),
    path("inventory/", include("inventory.urls")),
    path("sales/", include("sales.urls")),
    path("cashboxes/", include("cashboxes.urls")),
    path("expenses/", include("expenses.urls")),
    path("print/", include("printing.urls")),
    path("barcode/", include("barcode.urls")),
    path("pricing/", include("pricing.urls")),
    path("taxes/", include("taxes.urls")),
    path("einvoice/", include("einvoice.urls")),
    path("units/", include("units.urls")),
    path("batches/", include("batches.urls")),
    path("variants/", include("variants.urls")),
    path("serials/", include("serials.urls")),
    path("instalments/", include("installments.urls")),
    path("assets/", include("fixed_assets.urls")),
    path("shifts/", include("shifts.urls")),
    path("imports/", include("imports.urls")),
    path("closing/", include("closing.urls")),
    path("profile/", include("accounts.urls")),
    path("settings/", include("settings_core.urls")),
    path("reports/", report_hub, name="report_hub"),
    path("reports/", include("reports.urls")),
    path("status/", status_counts_report, name="status_counts_report"),
    path(settings.ADMIN_URL, django_admin.site.urls),
]


def _superuser_only(request):
    """ADMIN-001: shop owners and staff never reach Django Admin; only a superuser does."""

    return request.user.is_active and request.user.is_superuser


django_admin.site.has_permission = _superuser_only
