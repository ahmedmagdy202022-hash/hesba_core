from django.urls import path

from . import views


app_name = "restaurant"

urlpatterns = [
    path("", views.board, name="board"),
    path("tables/", views.tables, name="tables"),
    path("costs/", views.menu_costing, name="costing"),
    path("costs/<int:pk>/", views.dish_ingredients, name="dish"),
    path("report/", views.report, name="report"),
    path("orders/<int:pk>/", views.order_detail, name="order"),
    path("tickets/<int:pk>/", views.ticket, name="ticket"),
]
