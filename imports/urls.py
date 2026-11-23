from django.urls import path

from . import views


app_name = "imports"

urlpatterns = [
    path("", views.import_home, name="home"),
    path("<int:pk>/", views.batch_detail, name="detail"),
    path("<int:pk>/columns/", views.batch_columns, name="columns"),
    path("templates/<str:name>", views.template_download, name="template"),
]
