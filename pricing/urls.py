from django.urls import path

from . import views


app_name = "pricing"

urlpatterns = [
    path("", views.price_list_index, name="list"),
    path("<int:pk>/", views.price_list_detail, name="detail"),
]
