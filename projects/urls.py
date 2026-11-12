from django.urls import path

from . import contract_views, views, views_documents, views_reports


app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("report/", views.report, name="report"),
    path("<int:pk>/", views.project_detail, name="detail"),
    # CONTRACT-002
    path("<int:pk>/boq/", contract_views.boq, name="boq"),
    path("<int:pk>/certificates/", contract_views.certificates, name="certificates"),
    path("<int:pk>/certificates/<int:number>/", contract_views.certificate_detail, name="certificate"),
    path("<int:pk>/certificates/<int:number>/print/", contract_views.certificate_print, name="certificate_print"),
    path("<int:pk>/payments/", contract_views.payments, name="payments"),
    path("<int:pk>/subcontracts/", contract_views.subcontracts, name="subcontracts"),
    path("<int:pk>/budget/", contract_views.budget, name="budget"),
    # Contracting C2a / C2b (HG-095): the project's documents and its daily site reports
    path("<int:pk>/documents/", views_documents.documents_tab, name="documents"),
    path("<int:pk>/documents/upload/", views_documents.document_upload, name="document_upload"),
    path("<int:pk>/files/<int:attachment_pk>/", views_documents.file, name="file"),
    path("<int:pk>/files/<int:attachment_pk>/delete/", views_documents.file_delete, name="file_delete"),
    path("<int:pk>/reports/", views_reports.reports_list, name="diary"),
    path("<int:pk>/reports/new/", views_reports.report_form, name="diary_new"),
    path("<int:pk>/reports/<int:rid>/", views_reports.report_detail, name="diary_day"),
    path("<int:pk>/reports/<int:rid>/edit/", views_reports.report_form, name="diary_edit"),
    path("<int:pk>/reports/<int:rid>/photos/", views_reports.report_photos, name="diary_photos"),
    path("<int:pk>/reports/<int:rid>/print/", views_reports.report_print, name="diary_print"),
]
