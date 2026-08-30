from django.urls import path

from . import views


app_name = "cashboxes"

urlpatterns = [
    path("", views.cashbox_list, name="list"),
    path("movements/", views.movement_list, name="movements"),
    path("operations/", views.operation_list, name="operations"),
    path("operations/new/", views.operation_create, name="operation_create"),
    path("operations/<int:pk>/reverse/", views.operation_cancel, name="operation_cancel"),
    path("<int:pk>/", views.cashbox_detail, name="detail"),
]
