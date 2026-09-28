from django.urls import path

from . import views


app_name = "parties"

urlpatterns = [
    path("<str:kind>/<int:pk>/", views.party_card, name="card"),
    path("<str:kind>/<int:pk>/statement/", views.party_statement_print, name="statement"),
]
