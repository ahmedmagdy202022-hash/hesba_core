from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("accounts/", views.accounts, name="accounts"),
    path("accounts/new/", views.account_edit, name="account_new"),
    path("accounts/<int:pk>/", views.account_edit, name="account_edit"),
    path("accounts/<int:pk>/ledger/", views.account_ledger, name="account_ledger"),
    path("journal/", views.journal, name="journal"),
    path("trial-balance/", views.trial_balance, name="trial_balance"),
    path("reconciliation/", views.reconciliation, name="reconciliation"),
]
