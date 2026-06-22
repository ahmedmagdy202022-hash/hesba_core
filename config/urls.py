from django.contrib import admin as django_admin
from django.urls import path

from reports.views import dashboard_snapshot, home


urlpatterns = [
    path("", home, name="home"),
    path("dashboard/", dashboard_snapshot, name="dashboard_snapshot"),
    path("admin/", django_admin.site.urls),
]
