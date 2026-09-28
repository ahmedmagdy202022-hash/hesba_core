from django.urls import path

from . import views


app_name = "manufacturing"

urlpatterns = [
    path("", views.home, name="home"),
    path("recipes/<int:pk>/", views.recipe_detail, name="recipe"),
    path("runs/<int:pk>/", views.run_detail, name="run"),
]
