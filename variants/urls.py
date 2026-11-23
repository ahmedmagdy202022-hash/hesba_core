from django.urls import path

from . import views


app_name = "variants"

urlpatterns = [
    path("", views.group_index, name="index"),
    path("<int:pk>/", views.group_detail, name="group"),
]
