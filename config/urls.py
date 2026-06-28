from django.contrib import admin as django_admin
from django.urls import path
from django.views.generic import RedirectView, TemplateView
from django.contrib.auth.views import LoginView, LogoutView

from reports.status_views import status_counts_report
from reports.views import dashboard_snapshot, home, report_hub


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="login", permanent=False), name="root_redirect"),
    path("login/", LoginView.as_view(template_name="registration/login.html", next_page="/setup/"), name="login"),
    path("logout/", LogoutView.as_view(next_page="login"), name="logout"),
    path("setup/", TemplateView.as_view(template_name="setup/setup_gate.html"), name="setup_gate"),
    path("setup/activity/", TemplateView.as_view(template_name="setup/activity_selection.html"), name="setup_activity"),
    path("setup/activity/commercial/", TemplateView.as_view(template_name="setup/activity_subactivity_placeholder.html"), name="setup_activity_commercial"),
    path("setup/activity/service/", TemplateView.as_view(template_name="setup/activity_subactivity_placeholder.html"), name="setup_activity_service"),
    path("home/", home, name="home"),
    path("dashboard/", dashboard_snapshot, name="dashboard_snapshot"),
    path("reports/", report_hub, name="report_hub"),
    path("status/", status_counts_report, name="status_counts_report"),
    path("admin/", django_admin.site.urls),
]
