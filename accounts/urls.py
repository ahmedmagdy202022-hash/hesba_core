from django.urls import path

from . import profile_views, user_views


app_name = "accounts"

urlpatterns = [
    path("", profile_views.profile, name="profile"),
    path("password/", user_views.change_password, name="change_password"),
]

