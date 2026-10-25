from django.urls import path

from . import pos as pos_views
from . import pos_customers
from . import pos_buttons
from offline_pos import views as offline_views
from . import views


app_name = "sales"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("new/", views.invoice_create, name="create"),
    path("pos/", pos_views.pos, name="pos"),
    path("pos/promotion/", pos_views.promotion_quote, name="pos_promotion"),  # T5
    path("promotion-quote/", pos_views.promotion_quote, name="promotion_quote"),  # T5: the invoice form, with or without the till
    path("pos/customers/new/", pos_customers.pos_add_customer, name="pos_add_customer"),
    path("pos/buttons/", pos_buttons.pos_buttons, name="pos_buttons"),  # AN7 (G07)
    # POS-003: queued offline sales, posted once each when the till is back online.
    path("pos/sync/", offline_views.sync_sale, name="pos_sync"),
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
