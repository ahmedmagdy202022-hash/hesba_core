from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("accounts/", views.accounts, name="accounts"),
    path("accounts/new/", views.account_edit, name="account_new"),
    path("accounts/<int:pk>/", views.account_edit, name="account_edit"),
]
