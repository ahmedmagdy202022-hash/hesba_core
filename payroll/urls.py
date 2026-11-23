from django.urls import path

from . import views


app_name = "payroll"

urlpatterns = [
    path("", views.run_list, name="list"),
    path("salaries/", views.salaries, name="salaries"),
    path("rules/", views.rules_screen, name="rules"),
    path("<int:pk>/", views.run_detail, name="detail"),
]
