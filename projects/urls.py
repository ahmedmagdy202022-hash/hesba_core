from django.urls import path

from . import contract_views, views, views_documents, views_equipment, views_purchase_requests, views_reports, views_schedule, views_subcontracts, views_variations


app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("report/", views.report, name="report"),
    # Contracting C7 (HG-125): purchase requests across projects: waiting for approval (the office) and mine (the engineer)
    path("purchase-requests/", views_purchase_requests.inbox, name="purchase_requests_all"),
    path("purchase-requests/mine/", views_purchase_requests.inbox, {"show": "mine"}, name="purchase_requests_mine"),
    # Contracting C6 (HG-129): the equipment register (owned and hired machines) and a project's equipment hours
    path("equipment/", views_equipment.register, name="equipment_register"),
    path("equipment/<int:eid>/", views_equipment.machine_edit, name="equipment_edit"),
    path("<int:pk>/", views.project_detail, name="detail"),
    # Contracting C10 (HG-135): the owner's follow-up link (the public page itself is printing.share's /share/<token>/)
    path("<int:pk>/owner-link/", views.owner_link, name="owner_link"),
    # CONTRACT-002
    path("<int:pk>/boq/", contract_views.boq, name="boq"),
    path("<int:pk>/certificates/", contract_views.certificates, name="certificates"),
    path("<int:pk>/certificates/<int:number>/", contract_views.certificate_detail, name="certificate"),
    path("<int:pk>/certificates/<int:number>/print/", contract_views.certificate_print, name="certificate_print"),
    path("<int:pk>/payments/", contract_views.payments, name="payments"),
    path("<int:pk>/equipment/", views_equipment.equipment_tab, name="equipment"),
    path("<int:pk>/subcontracts/", contract_views.subcontracts, name="subcontracts"),
    # Contracting C11 (HG-128): one subcontract: its lines, bills by quantities, advance and payments, and the printed bill
    path("<int:pk>/subcontracts/<int:sid>/", views_subcontracts.subcontract_page, name="subcontract"),
    path("<int:pk>/subcontracts/<int:sid>/bills/<int:number>/print/", views_subcontracts.bill_print, name="subcontract_bill_print"),
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
    # Contracting C1 (HG-103): variation orders and extra works
    path("<int:pk>/variations/", views_variations.variations_list, name="variations"),
    path("<int:pk>/variations/new/", views_variations.variation_form, name="variation_new"),
    path("<int:pk>/variations/<int:oid>/", views_variations.variation_detail, name="variation"),
    path("<int:pk>/variations/<int:oid>/edit/", views_variations.variation_form, name="variation_edit"),
    path("<int:pk>/variations/<int:oid>/files/", views_variations.variation_files, name="variation_files"),
    path("<int:pk>/variations/<int:oid>/print/", views_variations.variation_print, name="variation_print"),
    # Contracting C3 (HG-124): the schedule and its delay alerts
    path("<int:pk>/schedule/", views_schedule.schedule_tab, name="schedule"),
    path("<int:pk>/schedule/<int:tid>/", views_schedule.task_page, name="schedule_task"),
    # Contracting C7 (HG-125): the project's purchase requests, with approval
    path("<int:pk>/purchase-requests/", views_purchase_requests.requests_tab, name="purchase_requests"),
    path("<int:pk>/purchase-requests/new/", views_purchase_requests.request_form, name="purchase_request_new"),
    path("<int:pk>/purchase-requests/<int:rid>/", views_purchase_requests.request_detail, name="purchase_request"),
    path("<int:pk>/purchase-requests/<int:rid>/edit/", views_purchase_requests.request_form, name="purchase_request_edit"),
]
