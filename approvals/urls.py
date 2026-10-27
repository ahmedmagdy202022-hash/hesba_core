from django.urls import path

from . import views

app_name = "approvals"

urlpatterns = [
    path("", views.inbox, name="inbox"),
]
