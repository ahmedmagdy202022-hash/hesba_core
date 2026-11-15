from django.urls import path

from . import costs_views, mrp_views, order_views, schedule_views, trace_views, views, workcentre_views


app_name = "manufacturing"

urlpatterns = [
    path("", views.home, name="home"),
    path("recipes/new/", views.recipe_new, name="recipe_new"),
    path("recipes/<int:pk>/", views.recipe_detail, name="recipe"),
    path("runs/<int:pk>/", views.run_detail, name="run"),
    path("orders/", order_views.board, name="orders"),
    path("orders/<int:pk>/", order_views.order_detail, name="order"),
    path("mrp/", mrp_views.screen, name="mrp"),
    path("trace/", trace_views.trace_screen, name="trace"),
    path("costs/", costs_views.costs_screen, name="costs"),
    path("schedule/", schedule_views.schedule_screen, name="schedule"),
    path("work-centres/", workcentre_views.centres, name="work_centres"),
    path("work-centres/new/", workcentre_views.centre_form, name="work_centre_new"),
    path("work-centres/<int:pk>/", workcentre_views.centre_form, name="work_centre"),
    path("orders/for-sale/<int:line_pk>/", order_views.make_for_sale, name="make_for_sale"),
]
