from django.urls import path

from . import views, views_week


app_name = "appointments"

urlpatterns = [
    path("", views.agenda, name="agenda"),
    path("week/", views_week.week, name="week"),
    path("week.json", views.week_json, name="week_json"),
    path("performance/", views.performance, name="performance"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/move/", views.move, name="move"),
]
