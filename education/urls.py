from django.urls import path

from . import views, views_exams, views_scan

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
    path("fees/", views.fees, name="fees"),
    path("dues/", views.dues, name="dues"),
    path("teachers/", views.teachers, name="teachers"),
    path("report/", views.report, name="report"),
]
