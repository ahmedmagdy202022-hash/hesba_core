from django.urls import path

from . import functional_views


app_name = "reports"

urlpatterns = [
    path("sales/", functional_views.sales_report_view, name="sales"),
    path("purchases/", functional_views.purchase_report_view, name="purchases"),
    path("inventory/", functional_views.inventory_report_view, name="inventory"),
    path("customers/", functional_views.customer_report_view, name="customers"),
    path("suppliers/", functional_views.supplier_report_view, name="suppliers"),
    path("cashboxes/", functional_views.cashbox_report_view, name="cashboxes"),
    path("profit/", functional_views.profit_report_view, name="profit"),
]

