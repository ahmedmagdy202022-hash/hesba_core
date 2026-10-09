from django.urls import path

from . import transfer_request_views, reorder_views, stocktake_views, views, warehouse_views


app_name = "inventory"

urlpatterns = [
    path("", views.stock_list, name="stock"),
    path("movements/", views.movement_list, name="movements"),
    path("warehouses/", warehouse_views.warehouse_list, name="warehouses"),
    path("warehouses/<int:pk>/", warehouse_views.warehouse_detail, name="warehouse_detail"),
    path("warehouses/<int:pk>/levels/", transfer_request_views.levels, name="warehouse_levels"),
    path("warehouses/requests/", transfer_request_views.request_list, name="transfer_requests"),
    path("warehouses/requests/new/", transfer_request_views.request_new, name="transfer_request_new"),
    path("warehouses/requests/<int:pk>/", transfer_request_views.request_detail, name="transfer_request"),
    path("operations/", views.operation_list, name="operations"),
    path("operations/transfer/", views.transfer_create, name="transfer"),
    path("operations/adjustment/", views.adjustment_create, name="adjustment"),
    path("count/", stocktake_views.stocktake, name="stocktake"),
    path("reorder/", reorder_views.reorder, name="reorder"),
    path("operations/<int:pk>/reverse/", views.operation_cancel, name="operation_cancel"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
]
