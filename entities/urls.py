from django.urls import path

from . import views

app_name = "entities"

urlpatterns = [
    path("", views.entity_list, name="list"),
    path("new/", views.entity_edit, name="new"),
    path("<int:pk>/", views.entity_edit, name="edit"),
    path("stock/", views.where_is, name="where"),
]
