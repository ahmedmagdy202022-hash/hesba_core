from django.urls import path

from . import views

app_name = "guarantees"

urlpatterns = [
    path("", views.guarantee_list, name="list"),
    path("new/", views.guarantee_form, name="new"),
    path("<int:pk>/", views.guarantee_detail, name="detail"),
    path("<int:pk>/edit/", views.guarantee_form, name="edit"),
]
