from django.urls import path

from . import views


app_name = "closing"

urlpatterns = [
    path("", views.period_list, name="list"),
    path("<int:pk>/", views.period_detail, name="detail"),
    path("<int:pk>/close/", views.period_close, name="close"),
    path("<int:pk>/reopen/", views.period_reopen, name="reopen"),
]

