from django.urls import path

from accounts import user_views
from printing.views import company_settings

from . import operational_views


app_name = "settings_core"

urlpatterns = [
    path("", operational_views.settings_overview, name="overview"),
    path("roles/", operational_views.role_list, name="roles"),
    path("modules/", operational_views.module_settings, name="modules"),
    path("currency/", operational_views.currency_settings, name="currency"),
    path("company/", company_settings, name="company"),
    path("users/", user_views.user_list, name="users"),
    path("users/new/", user_views.user_create, name="user_create"),
    path("users/<int:pk>/", user_views.user_edit, name="user_edit"),
    path("users/<int:pk>/password/", user_views.user_reset_password, name="user_reset_password"),
]

