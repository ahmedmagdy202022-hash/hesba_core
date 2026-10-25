from django.urls import path

from . import views

app_name = "onlinestore"

urlpatterns = [
    path("", views.board, name="board"),
]
