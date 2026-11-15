from django.urls import path

from . import views, views_brands, views_callcentre, views_combos, views_kds, views_modifiers, views_qr, views_reservations


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
    path("orders/<int:pk>/check/", views.check, name="check"),  # R3 (HG-113): «اطبع الشيك»
    path("tickets/<int:pk>/", views.ticket, name="ticket"),
    path("kitchen/", views_kds.kds, name="kds"),
    path("kitchen/feed/", views_kds.kds_feed, name="kds_feed"),
    path("kitchen/act/", views_kds.kds_act, name="kds_act"),
    path("stations/", views_kds.station_setup, name="stations"),
    path("modifiers/", views_modifiers.setup, name="modifiers"),  # R1a (HG-109)
    path("combos/", views_combos.setup, name="combos"),  # R1b (HG-112)
    path("qr/", views_qr.qr_cards, name="qr_cards"),  # R5a (HG-115): one printed card per table
    path("qr/<int:pk>/rotate/", views_qr.qr_rotate, name="qr_rotate"),
    path("requests/<int:pk>/", views_qr.request_act, name="request_act"),
    path("call/", views_callcentre.call, name="call"),  # R7 (HG-116): the caller by number, their addresses and the zone
    path("zones/", views_callcentre.zone_setup, name="zones"),
    path("brands/", views_brands.setup, name="brands"),  # R11 (HG-117): several brands from one kitchen
    path("reservations/", views_reservations.reservations_view, name="reservations"),  # R8 (HG-118): table reservations
]
