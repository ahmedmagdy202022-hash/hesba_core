from django.urls import path

from . import (costs_views, floor_views, maintenance_views, mrp_views, order_views, outside_views, quality_views, quote_views, schedule_views, trace_views, views,
               workcentre_views)


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
    # Manufacturing F5 (HG-136): quality checks, the hold before sale, release and reject
    path("quality/", quality_views.quality_screen, name="quality"),
    path("quality/orders/<int:pk>/", quality_views.order_check, name="quality_order"),
    path("quality/purchase-lines/<int:pk>/", quality_views.purchase_check, name="quality_purchase_line"),
    path("quality/checks/<int:pk>/", quality_views.check_detail, name="quality_check"),
    path("quality/checks/<int:pk>/certificate/", quality_views.certificate_upload, name="quality_certificate"),
    # Manufacturing F4 (HG-137): a stage done at an outside supplier, and the supplier's bill
    path("outside/", outside_views.outside_screen, name="outside"),
    path("orders/<int:pk>/outside/", outside_views.order_action, name="outside_action"),
    # Manufacturing F8 (HG-140): the production worker's floor screen, and the photo of a defect
    path("floor/", floor_views.floor_screen, name="floor"),
    path("floor/<int:pk>/photo/", floor_views.photo_upload, name="floor_photo"),
    # Manufacturing F7 (HG-138): a quotation line's specifications and cost sheet, made into a custom production order
    path("quotes/lines/<int:line_pk>/", quote_views.quote_sheet, name="quote_sheet"),
    # Manufacturing F10 (HG-141): machine maintenance: what is down and due, one work centre's page, one job's page
    path("maintenance/", maintenance_views.maintenance_screen, name="maintenance"),
    path("work-centres/<int:pk>/maintenance/", maintenance_views.centre_page, name="maintenance_centre"),
    path("maintenance/jobs/<int:pk>/", maintenance_views.job_page, name="maintenance_job"),
]
