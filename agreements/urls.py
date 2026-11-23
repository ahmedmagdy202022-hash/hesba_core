from django.urls import path

from . import package_views, views

app_name = "agreements"

urlpatterns = [
    path("", views.contract_list, name="list"),
    path("<int:pk>/", views.contract_detail, name="detail"),
    path("packages/", package_views.package_list, name="packages"),
    path("packages/<int:pk>/", package_views.package_detail, name="package"),
    path("hours/", package_views.hours_home, name="hours"),
]
