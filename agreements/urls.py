from django.urls import path

from . import views

app_name = "agreements"

urlpatterns = [
    path("", views.contract_list, name="list"),
    path("<int:pk>/", views.contract_detail, name="detail"),
]
