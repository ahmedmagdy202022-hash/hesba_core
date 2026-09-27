from django.urls import path

from . import views


app_name = "batches"

urlpatterns = [
    path("", views.batch_index, name="index"),
]
