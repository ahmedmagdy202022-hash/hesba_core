from django.urls import path

from . import views

app_name = "feedback"

urlpatterns = [
    path("send/", views.send, name="send"),
    path("inbox/", views.inbox, name="inbox"),
]
