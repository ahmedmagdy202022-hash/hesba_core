from django.urls import path

from . import views

app_name = "attachments"

urlpatterns = [
    path("work-orders/<int:pk>/", views.work_order_list, name="work_order_list"),
    path("work-orders/<int:pk>/upload/", views.work_order_upload, name="work_order_upload"),
    path("work-orders/<int:pk>/signature/", views.work_order_signature, name="work_order_signature"),
    path("visits/<int:pk>/", views.visit_list, name="visit_list"),
    path("visits/<int:pk>/upload/", views.visit_upload, name="visit_upload"),
    path("patient-files/<int:pk>/", views.patient_file_list, name="patient_file_list"),
    path("patient-files/<int:pk>/upload/", views.patient_file_upload, name="patient_file_upload"),
    path("file/<int:pk>/", views.file, name="file"),
    path("file/<int:pk>/delete/", views.delete, name="delete"),
]
