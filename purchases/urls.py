from django.urls import path

from . import views


app_name = "purchases"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("new/", views.invoice_create, name="create"),
    path("payments/", views.payment_list, name="payments"),
    path("payments/new/", views.payment_create, name="payment_create"),
    path("payments/<int:pk>/cancel/", views.payment_cancel, name="payment_cancel"),
    path("<int:pk>/", views.invoice_detail, name="detail"),
    path("<int:pk>/post/", views.invoice_post, name="post"),
    path("<int:pk>/cancel/", views.invoice_cancel, name="cancel"),
]
