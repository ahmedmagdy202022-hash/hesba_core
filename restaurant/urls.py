from django.urls import path

from . import views, views_combos, views_kds, views_modifiers


app_name = "restaurant"

urlpatterns = [
    path("", views.board, name="board"),
    path("tables/", views.tables, name="tables"),
    path("costs/", views.menu_costing, name="costing"),
    path("costs/<int:pk>/", views.dish_ingredients, name="dish"),
    # HG-107: the dish's page with one preparation shown first, whatever its age (the stock screen and the approvals inbox link here).
    path("costs/<int:pk>/preparations/<int:run>/", views.dish_ingredients, name="dish_preparation"),
    path("report/", views.report, name="report"),
    path("orders/<int:pk>/", views.order_detail, name="order"),
    path("tickets/<int:pk>/", views.ticket, name="ticket"),
    path("kitchen/", views_kds.kds, name="kds"),
    path("kitchen/feed/", views_kds.kds_feed, name="kds_feed"),
    path("kitchen/act/", views_kds.kds_act, name="kds_act"),
    path("stations/", views_kds.station_setup, name="stations"),
    path("modifiers/", views_modifiers.setup, name="modifiers"),  # R1a (HG-109)
    path("combos/", views_combos.setup, name="combos"),  # R1b (HG-112)
]
