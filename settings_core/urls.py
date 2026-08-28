from django.urls import path

from . import operational_views


app_name = "settings_core"

urlpatterns = [
    path("", operational_views.settings_overview, name="overview"),
    path("roles/", operational_views.role_list, name="roles"),
]

