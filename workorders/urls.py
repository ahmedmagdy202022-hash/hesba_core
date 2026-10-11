from django.urls import path

from . import views

app_name = "workorders"

urlpatterns = [
    path("", views.board, name="list"),
    path("new/", views.new, name="new"),
    path("report/", views.report, name="report"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/receipt/", views.receipt, name="receipt"),
]
