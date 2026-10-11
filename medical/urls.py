from django.urls import path

from . import views

app_name = "medical"

urlpatterns = [
    path("", views.patients, name="patients"),
    path("follow-ups/", views.follow_ups, name="follow_ups"),
    path("report/", views.report, name="report"),
    path("patients/<int:pk>/", views.patient_file, name="file"),
    path("patients/<int:pk>/visit/", views.visit_edit, name="visit_new"),
    path("patients/<int:pk>/dental/", views.dental_chart, name="dental"),
    path("visits/<int:visit_pk>/", views.visit_edit, name="visit_edit"),
    path("visits/<int:visit_pk>/print/", views.prescription, name="prescription"),
]
