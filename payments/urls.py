"""HG-070 (S4): the staff side of payment links (signed in). The public /pay/ addresses are in config/urls.py."""

from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.gateway, name="gateway"),
    path("invoice/<int:invoice_pk>/link/", views.create, name="create"),
    path("link/<int:pk>/cancel/", views.cancel, name="cancel"),
]
