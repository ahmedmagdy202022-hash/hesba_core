from django.urls import path

from . import views


app_name = "taxes"

urlpatterns = [
    path("", views.tax_settings, name="settings"),
    path("report/", views.tax_report, name="report"),
]
