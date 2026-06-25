from django.contrib import admin as django_admin
from django.urls import path
from django.views.generic import RedirectView
from django.contrib.auth.views import LoginView

from reports.setup_views import setup_gate
from reports.status_views import status_counts_report
from reports.views import dashboard_snapshot, home, report_hub


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="login", permanent=False), name="root_redirect"),
    path("login/", LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("setup/", setup_gate, name="setup_gate"),
    path("home/", home, name="home"),
    path("dashboard/", dashboard_snapshot, name="dashboard_snapshot"),
    path("reports/", report_hub, name="report_hub"),
    path("status/", status_counts_report, name="status_counts_report"),
    path("admin/", django_admin.site.urls),
]
