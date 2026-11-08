from django.urls import path

from . import views, views_myday


app_name = "staff"

urlpatterns = [
    path("", views.employee_list, name="list"),
    path("my-day/", views_myday.my_day, name="my_day"),
    path("<int:pk>/", views.employee_detail, name="detail"),
]
