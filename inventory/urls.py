from django.urls import path

from . import views


app_name = "inventory"

urlpatterns = [
    path("", views.stock_list, name="stock"),
    path("movements/", views.movement_list, name="movements"),
    path("operations/", views.operation_list, name="operations"),
    path("operations/transfer/", views.transfer_create, name="transfer"),
    path("operations/adjustment/", views.adjustment_create, name="adjustment"),
    path("operations/<int:pk>/reverse/", views.operation_cancel, name="operation_cancel"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
]
