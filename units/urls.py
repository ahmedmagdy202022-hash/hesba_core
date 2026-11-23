from django.urls import path

from . import views


app_name = "units"

urlpatterns = [
    path("", views.unit_index, name="index"),
    path("<int:pk>/", views.item_units, name="item"),
]
