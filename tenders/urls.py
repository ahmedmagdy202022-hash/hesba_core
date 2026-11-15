from django.urls import path

from . import views

app_name = "tenders"

urlpatterns = [
    path("", views.tender_list, name="list"),
    path("new/", views.tender_form, name="new"),
    path("<int:pk>/", views.tender_detail, name="detail"),
    path("<int:pk>/edit/", views.tender_form, name="edit"),
    path("<int:pk>/items/<int:iid>/", views.item_detail, name="item"),
    path("<int:pk>/print/", views.boq_print, name="boq_print"),
    path("<int:pk>/analysis/print/", views.analysis_print, name="analysis_print"),
]
