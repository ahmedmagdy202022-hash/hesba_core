from django.urls import path

from . import views

app_name = "giftcards"

urlpatterns = [
    path("", views.card_list, name="list"),
    path("<int:pk>/", views.card_detail, name="detail"),
    path("<int:pk>/receipt/", views.receipt, name="receipt"),
    path("invoice/<int:invoice_pk>/pay/", views.pay_invoice, name="pay_invoice"),
]
