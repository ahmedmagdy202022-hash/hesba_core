"""T2: purchase orders live under /purchases/orders/, so the purchases module (not the quotations feature) opens them."""

from django.urls import path

from . import purchase_views

app_name = "purchase_orders"

urlpatterns = [
    path("", purchase_views.board, name="list"),
    path("new/", purchase_views.new, name="new"),
    path("<int:pk>/", purchase_views.detail, name="detail"),
    path("<int:pk>/print/", purchase_views.print_order, name="print"),
]
