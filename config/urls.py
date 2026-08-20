from django.contrib import admin as django_admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from django.views.generic import TemplateView

from reports.status_views import status_counts_report
from reports.views import dashboard_snapshot, home, report_hub
from settings_core.setup_views import after_login, root_redirect, setup_complete, setup_review


urlpatterns = [
    path("", root_redirect, name="root_redirect"),
    path("login/", LoginView.as_view(template_name="registration/login.html", next_page="/start/"), name="login"),
    path("logout/", LogoutView.as_view(next_page="login"), name="logout"),
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
    path("dashboard/", dashboard_snapshot, name="dashboard_snapshot"),
    path("reports/", report_hub, name="report_hub"),
    path("status/", status_counts_report, name="status_counts_report"),
    path("admin/", django_admin.site.urls),
]
