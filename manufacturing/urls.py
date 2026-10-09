from django.urls import path

from . import order_views, views


app_name = "manufacturing"

urlpatterns = [
    path("", views.home, name="home"),
    path("recipes/new/", views.recipe_new, name="recipe_new"),
    path("recipes/<int:pk>/", views.recipe_detail, name="recipe"),
    path("runs/<int:pk>/", views.run_detail, name="run"),
    path("orders/", order_views.board, name="orders"),
    path("orders/<int:pk>/", order_views.order_detail, name="order"),
]
