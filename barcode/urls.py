from django.urls import path

from . import views


app_name = "barcode"

urlpatterns = [
    path("labels/", views.labels, name="labels"),
    path("labels/print/", views.labels_print, name="labels_print"),
    path("generate/", views.generate_missing, name="generate"),
]
