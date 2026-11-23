from django.urls import path

from . import profile_views, two_factor_views, user_views


app_name = "accounts"

urlpatterns = [
    path("", profile_views.profile, name="profile"),
    path("password/", user_views.change_password, name="change_password"),
    path("two-factor/", two_factor_views.two_factor_settings, name="two_factor"),
]

