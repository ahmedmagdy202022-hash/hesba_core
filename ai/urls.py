from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    path("voice-summary/", views.summary, name="voice_summary"),  # A1 (HG-079)
    path("voice-summary/audio/<str:digest>.mp3", views.audio, name="voice_summary_audio"),
    path("ask/", views.ask, name="ask"),  # A2 (HG-080)
    path("invoice-photo/", views.invoice_photo, name="invoice_photo"),  # A3 (HG-081)
    path("invoice-photo/<str:token>/", views.invoice_photo_review, name="invoice_photo_review"),
    path("invoice-photo/<str:token>/again/", views.invoice_photo_discard, name="invoice_photo_discard"),
    path("invoice-photo/<str:token>/photo/", views.invoice_photo_image, name="invoice_photo_image"),
]
