from django.urls import path

from . import views, views_certificates, views_exams, views_paylinks, views_scan

app_name = "education"

urlpatterns = [
    path("", views.students, name="students"),
    path("students/new/", views.student_new, name="student_new"),
    path("students/<int:pk>/", views.student_detail, name="student"),
    path("groups/", views.groups, name="groups"),
    path("groups/new/", views.group_new, name="group_new"),
    path("groups/<int:pk>/", views.group_detail, name="group"),
    path("courses/", views.courses, name="courses"),
    path("today/", views.today, name="today"),
    path("nursery/", views.nursery_day, name="nursery"),
    path("scan/", views_scan.scan_page, name="scan"),
    path("scan/mark/", views_scan.scan_mark, name="scan_mark"),
    path("cards/", views_scan.card_sheet, name="cards"),
    path("groups/<int:pk>/attendance/", views.take_attendance, name="attendance"),
    path("groups/<int:pk>/exams/", views_exams.group_exams, name="exams"),
    path("groups/<int:pk>/exams/new/", views_exams.exam_new, name="exam_new"),
    path("exams/<int:pk>/", views_exams.exam_sheet, name="exam"),
    path("students/<int:pk>/report-card/", views_exams.report_card, name="report_card"),
    path("certificates/", views_certificates.certificate_list, name="certificates"),
    path("certificates/issue/", views_certificates.certificate_issue, name="certificate_issue"),
    path("certificates/<int:pk>/", views_certificates.certificate_print, name="certificate"),
    path("certificates/<int:pk>/revoke/", views_certificates.certificate_revoke, name="certificate_revoke"),
    path("fees/", views.fees, name="fees"),
    path("fees/paylink/<int:invoice_pk>/", views_paylinks.make, name="paylink_make"),
    path("fees/paylinks/", views_paylinks.bulk, name="paylinks_bulk"),
    path("fees/paylinks/result/", views_paylinks.result, name="paylinks_result"),
    path("dues/", views.dues, name="dues"),
    path("teachers/", views.teachers, name="teachers"),
    path("report/", views.report, name="report"),
]
