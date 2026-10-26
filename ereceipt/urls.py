from django.urls import path

from . import views


app_name = "ereceipt"

urlpatterns = [
    path("", views.board, name="board"),
    path("<int:pk>.json", views.receipt_json, name="json"),
]
