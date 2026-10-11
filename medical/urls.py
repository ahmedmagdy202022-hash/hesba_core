from django.urls import path

from . import views

app_name = "medical"

urlpatterns = [
    path("", views.patients, name="patients"),
    path("follow-ups/", views.follow_ups, name="follow_ups"),
    path("report/", views.report, name="report"),
    path("queue/", views.queue_desk, name="queue"),
    path("insurance/", views.insurance_list, name="insurance"),
    path("insurance/<int:pk>/", views.insurance_contract, name="insurance_contract"),
    path("insurance/<int:pk>/claim/", views.insurance_claim_print, name="insurance_claim"),
    path("patients/<int:pk>/insurance/", views.patient_cover, name="patient_cover"),
    path("queue/screen/", views.queue_screen, name="queue_screen"),
    path("patients/<int:pk>/", views.patient_file, name="file"),
    path("patients/<int:pk>/visit/", views.visit_edit, name="visit_new"),
    path("patients/<int:pk>/dental/", views.dental_chart, name="dental"),
    path("patients/<int:pk>/animals/new/", views.animal_new, name="animal_new"),
    path("animals/<int:pk>/", views.animal_card, name="animal"),
    path("patients/<int:pk>/packages/", views.session_packages, name="packages"),
    path("patients/<int:pk>/lab/", views.lab_results, name="lab"),
    path("patients/<int:pk>/lab/<str:day>/print/", views.lab_sheet, name="lab_sheet"),
    path("lab/tests/", views.lab_tests, name="lab_tests"),
    path("visits/<int:visit_pk>/", views.visit_edit, name="visit_edit"),
    path("visits/<int:visit_pk>/print/", views.prescription, name="prescription"),
]
