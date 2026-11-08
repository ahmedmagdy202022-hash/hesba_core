from django.urls import path

from . import views

app_name = "attachments"

urlpatterns = [
    path("work-orders/<int:pk>/", views.work_order_list, name="work_order_list"),
    path("work-orders/<int:pk>/upload/", views.work_order_upload, name="work_order_upload"),
    path("work-orders/<int:pk>/signature/", views.work_order_signature, name="work_order_signature"),
    path("file/<int:pk>/", views.file, name="file"),
    path("file/<int:pk>/delete/", views.delete, name="delete"),
]
