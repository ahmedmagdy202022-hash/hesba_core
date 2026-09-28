from django.urls import path

from . import views


app_name = "restaurant"

urlpatterns = [
    path("", views.board, name="board"),
    path("tables/", views.tables, name="tables"),
    path("orders/<int:pk>/", views.order_detail, name="order"),
    path("tickets/<int:pk>/", views.ticket, name="ticket"),
]
