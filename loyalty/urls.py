from django.urls import path

from . import views


app_name = "loyalty"

urlpatterns = [
    path("", views.loyalty_home, name="home"),
]
