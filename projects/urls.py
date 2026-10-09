from django.urls import path

from . import contract_views, views


app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("<int:pk>/", views.project_detail, name="detail"),
    # CONTRACT-002
    path("<int:pk>/boq/", contract_views.boq, name="boq"),
    path("<int:pk>/certificates/", contract_views.certificates, name="certificates"),
    path("<int:pk>/certificates/<int:number>/", contract_views.certificate_detail, name="certificate"),
    path("<int:pk>/certificates/<int:number>/print/", contract_views.certificate_print, name="certificate_print"),
    path("<int:pk>/payments/", contract_views.payments, name="payments"),
    path("<int:pk>/subcontracts/", contract_views.subcontracts, name="subcontracts"),
    path("<int:pk>/budget/", contract_views.budget, name="budget"),
]
