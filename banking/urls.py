from django.urls import path

from . import views


app_name = "banking"

urlpatterns = [
    path("", views.bank_list, name="list"),
    path("<int:pk>/", views.bank_account, name="account"),
]
