from django.urls import path

from . import views


app_name = "printing"

urlpatterns = [
    path("sales/<int:pk>/", views.sales_invoice, name="sales_invoice"),
    path("sales-returns/<int:pk>/", views.sales_return, name="sales_return"),
    path("collections/<int:pk>/", views.customer_payment, name="customer_payment"),
    path("purchases/<int:pk>/", views.purchase_invoice, name="purchase_invoice"),
    path("purchase-returns/<int:pk>/", views.purchase_return, name="purchase_return"),
    path("supplier-payments/<int:pk>/", views.supplier_payment, name="supplier_payment"),
]
