from django.contrib import admin as django_admin
from django.urls import path


urlpatterns = [
    path("admin/", django_admin.site.urls),
]
