from django.urls import path

from . import views


app_name = "fixed_assets"

urlpatterns = [
    path("", views.asset_list, name="list"),
    path("<int:pk>/", views.asset_detail, name="detail"),
]
