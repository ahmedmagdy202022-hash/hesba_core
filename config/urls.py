from django.contrib import admin as django_admin
from django.urls import path

from reports.views import home


urlpatterns = [
    path("", home, name="home"),
    path("admin/", django_admin.site.urls),
]
