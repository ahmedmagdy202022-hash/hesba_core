from django.urls import path

from printing.views import company_settings

from . import operational_views


app_name = "settings_core"

urlpatterns = [
    path("", operational_views.settings_overview, name="overview"),
    path("roles/", operational_views.role_list, name="roles"),
    path("modules/", operational_views.module_settings, name="modules"),
    path("currency/", operational_views.currency_settings, name="currency"),
    path("company/", company_settings, name="company"),
]

