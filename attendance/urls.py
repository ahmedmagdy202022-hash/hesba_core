from django.urls import path

from . import views


app_name = "attendance"

urlpatterns = [
    path("", views.today, name="today"),
    path("me/", views.me, name="me"),
    path("month/", views.month, name="month"),
    path("leave/", views.leaves, name="leaves"),
    path("device/", views.device_import, name="import"),
]
