from django.urls import path

from . import views


app_name = "einvoice"

urlpatterns = [
    path("", views.issuer_view, name="issuer"),
    path("items/", views.item_codes, name="items"),
    path("customers/", views.customer_data, name="customers"),
    path("sales/<int:pk>/", views.sales_document, name="sales_document"),
    path("returns/<int:pk>/", views.credit_note, name="credit_note"),  # T7
]
