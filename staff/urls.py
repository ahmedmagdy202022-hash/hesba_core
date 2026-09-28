from django.urls import path

from . import views


app_name = "staff"

urlpatterns = [
    path("", views.employee_list, name="list"),
    path("<int:pk>/", views.employee_detail, name="detail"),
]
