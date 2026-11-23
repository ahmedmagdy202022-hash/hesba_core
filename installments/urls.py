from django.urls import path

from . import views


app_name = "installments"

urlpatterns = [
    path("", views.plan_list, name="list"),
    path("new/<int:invoice_pk>/", views.plan_create, name="create"),
    path("<int:pk>/", views.plan_detail, name="detail"),
]
