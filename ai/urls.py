from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    path("voice-summary/", views.summary, name="voice_summary"),  # A1 (HG-079)
    path("voice-summary/audio/<str:digest>.mp3", views.audio, name="voice_summary_audio"),
]
