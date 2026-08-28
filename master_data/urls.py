from django.urls import path

from . import views


app_name = "master_data"

urlpatterns = [
    path("", views.master_data_hub, name="hub"),
    path("<str:entity>/", views.entity_list, name="list"),
    path("<str:entity>/new/", views.entity_create, name="create"),
    path("<str:entity>/<int:pk>/edit/", views.entity_edit, name="edit"),
]
