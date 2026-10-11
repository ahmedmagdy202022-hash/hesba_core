from django.urls import path

from . import views

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
    path("groups/<int:pk>/attendance/", views.take_attendance, name="attendance"),
    path("fees/", views.fees, name="fees"),
    path("dues/", views.dues, name="dues"),
]
