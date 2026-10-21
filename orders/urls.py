from django.urls import path

from . import purchase_views, views

app_name = "orders"

urlpatterns = [
    path("", views.board, name="list"),
    path("new/", views.new, name="new"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/print/", views.print_order, name="print"),
    path("<int:pk>/delivery/<int:invoice_pk>/", views.delivery_note, name="delivery_note"),
    # T2: purchase orders
    path("purchase/", purchase_views.board, name="po_list"),
    path("purchase/new/", purchase_views.new, name="po_new"),
    path("purchase/<int:pk>/", purchase_views.detail, name="po_detail"),
    path("purchase/<int:pk>/print/", purchase_views.print_order, name="po_print"),
]
