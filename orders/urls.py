from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.board, name="list"),
    path("new/", views.new, name="new"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/print/", views.print_order, name="print"),
    path("<int:pk>/delivery/<int:invoice_pk>/", views.delivery_note, name="delivery_note"),
]
