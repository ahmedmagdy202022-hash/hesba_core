from django.urls import path

from . import views


app_name = "pharmacy"

urlpatterns = [
    path("", views.lookup, name="lookup"),
    path("drugs/<int:item_pk>/", views.drug, name="drug"),
    path("prescriptions/", views.board, name="prescriptions"),
    path("prescriptions/new/", views.new, name="new"),
    path("prescriptions/<int:pk>/", views.detail, name="prescription"),
    path("prescriptions/<int:pk>/print/", views.print_prescription, name="print"),
]
