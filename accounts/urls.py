from django.urls import path

from . import profile_views


app_name = "accounts"

urlpatterns = [path("", profile_views.profile, name="profile")]

