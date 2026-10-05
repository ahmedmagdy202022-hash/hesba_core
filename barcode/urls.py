from django.urls import path

from . import views


app_name = "barcode"

urlpatterns = [
    path("labels/", views.labels, name="labels"),
    path("labels/print/", views.labels_print, name="labels_print"),
    path("generate/", views.generate_missing, name="generate"),
    path("designs/", views.designs, name="designs"),
    path("designs/new/", views.design_edit, name="design_new"),
    path("designs/<int:pk>/", views.design_edit, name="design_edit"),
    path("designs/<int:pk>/delete/", views.design_delete, name="design_delete"),
]
