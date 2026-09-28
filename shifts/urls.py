from django.urls import path

from . import views


app_name = "shifts"

urlpatterns = [
    path("", views.my_shift, name="mine"),
    path("<int:pk>/", views.shift_detail, name="detail"),
]
