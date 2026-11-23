from django.urls import path

from . import views


app_name = "cheques"

urlpatterns = [
    path("", views.register, name="register"),
]
