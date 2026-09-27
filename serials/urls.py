from django.urls import path

from . import views


app_name = "serials"

urlpatterns = [
    path("", views.serial_index, name="index"),
    path("items/", views.item_settings, name="items"),
    path("<int:pk>/", views.serial_detail, name="detail"),
]
