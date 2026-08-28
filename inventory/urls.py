from django.urls import path

from . import views


app_name = "inventory"

urlpatterns = [
    path("", views.stock_list, name="stock"),
    path("movements/", views.movement_list, name="movements"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
]

