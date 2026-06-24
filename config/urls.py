from django.contrib import admin as django_admin
from django.urls import path

from accounts.views import HesbaLoginView, HesbaLogoutView
from reports.status_views import status_counts_report
from reports.views import dashboard_snapshot, home, report_hub


urlpatterns = [
    path("login/", HesbaLoginView.as_view(), name="login"),
    path("logout/", HesbaLogoutView.as_view(), name="logout"),
    path("", home, name="home"),
    path("dashboard/", dashboard_snapshot, name="dashboard_snapshot"),
    path("reports/", report_hub, name="report_hub"),
    path("status/", status_counts_report, name="status_counts_report"),
    path("admin/", django_admin.site.urls),
]
