from django.urls import path

from . import views


app_name = "cashboxes"

urlpatterns = [
    path("", views.cashbox_list, name="list"),
    path("movements/", views.movement_list, name="movements"),
    path("<int:pk>/", views.cashbox_detail, name="detail"),
]

