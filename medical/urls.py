from django.urls import path

from . import views, views_inpatient
from . import views_rx
from . import views_templates

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
    # M8 (HG-088): everything under inpatient/ closes with the «الإقامة والأسرّة» capability.
    path("inpatient/", views_inpatient.beds, name="beds"),
    path("inpatient/admit/", views_inpatient.admit, name="admit"),
    path("inpatient/setup/", views_inpatient.wards, name="wards"),
    path("inpatient/<int:pk>/", views_inpatient.stay, name="stay"),
    path("visits/<int:visit_pk>/", views.visit_edit, name="visit_edit"),
    path("visits/<int:visit_pk>/print/", views.prescription, name="prescription"),
    path("drugs/search/", views_rx.drug_search, name="drug_search"),  # M2 (HG-086)
    path("templates/", views_templates.template_list, name="visit_templates"),  # M1 (HG-089)
    path("templates/new/", views_templates.template_edit, name="visit_template_new"),
    path("templates/data/", views_templates.templates_data, name="visit_templates_data"),
    path("templates/save/", views_templates.template_from_visit, name="visit_template_from_visit"),
    path("templates/<int:pk>/", views_templates.template_edit, name="visit_template_edit"),
    path("templates/<int:pk>/<str:action>/", views_templates.template_action, name="visit_template_action"),
]
