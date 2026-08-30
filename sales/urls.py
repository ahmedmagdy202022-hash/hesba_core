from django.urls import path

from . import views


app_name = "sales"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("new/", views.invoice_create, name="create"),
    path("collections/", views.payment_list, name="payments"),
    path("collections/new/", views.payment_create, name="payment_create"),
    path("collections/<int:pk>/cancel/", views.payment_cancel, name="payment_cancel"),
    path("returns/<int:pk>/", views.return_detail, name="return_detail"),
    path("returns/<int:pk>/reverse/", views.return_cancel, name="return_cancel"),
    path("<int:pk>/", views.invoice_detail, name="detail"),
    path("<int:pk>/returns/new/", views.return_create, name="return_create"),
    path("<int:pk>/post/", views.invoice_post, name="post"),
    path("<int:pk>/cancel/", views.invoice_cancel, name="cancel"),
]
