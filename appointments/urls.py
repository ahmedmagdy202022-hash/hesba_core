from django.urls import path

from . import views


app_name = "appointments"

urlpatterns = [
    path("", views.agenda, name="agenda"),
    path("performance/", views.performance, name="performance"),
    path("<int:pk>/", views.detail, name="detail"),
]
