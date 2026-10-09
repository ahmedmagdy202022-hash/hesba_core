from django.urls import path

from . import reorder_views, stocktake_views, views, warehouse_views


app_name = "inventory"

urlpatterns = [
    path("", views.stock_list, name="stock"),
    path("movements/", views.movement_list, name="movements"),
    path("warehouses/", warehouse_views.warehouse_list, name="warehouses"),
    path("warehouses/<int:pk>/", warehouse_views.warehouse_detail, name="warehouse_detail"),
    path("operations/", views.operation_list, name="operations"),
    path("operations/transfer/", views.transfer_create, name="transfer"),
    path("operations/adjustment/", views.adjustment_create, name="adjustment"),
    path("count/", stocktake_views.stocktake, name="stocktake"),
    path("reorder/", reorder_views.reorder, name="reorder"),
    path("operations/<int:pk>/reverse/", views.operation_cancel, name="operation_cancel"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
]
