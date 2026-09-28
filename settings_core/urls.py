from django.urls import path

from accounts import two_factor_views, user_views
from printing.views import company_settings

from . import backup_views, operational_views


app_name = "settings_core"

urlpatterns = [
    path("", operational_views.settings_overview, name="overview"),
    path("roles/", operational_views.role_list, name="roles"),
    path("modules/", operational_views.module_settings, name="modules"),
    path("capabilities/", operational_views.capability_settings, name="capabilities"),
    path("currency/", operational_views.currency_settings, name="currency"),
    path("company/", company_settings, name="company"),
    path("backups/", backup_views.backup_key, name="backup_key"),
    path("users/", user_views.user_list, name="users"),
    path("users/new/", user_views.user_create, name="user_create"),
    path("users/<int:pk>/", user_views.user_edit, name="user_edit"),
    path("users/<int:pk>/password/", user_views.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/two-factor/reset/", two_factor_views.user_reset_two_factor, name="user_reset_two_factor"),
]

