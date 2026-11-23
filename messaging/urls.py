from django.urls import path

from . import campaign_views, views


app_name = "messaging"

urlpatterns = [
    path("", views.outbox, name="outbox"),
    # S8: loyalty / marketing campaigns, put on the outbox one message per customer.
    path("campaigns/", campaign_views.campaign_list, name="campaigns"),
    path("campaigns/new/", campaign_views.campaign_new, name="campaign_new"),
    path("campaigns/audience/", campaign_views.audience_count, name="campaign_audience"),
    path("campaigns/<int:pk>/", campaign_views.campaign_detail, name="campaign"),
    path("campaigns/<int:pk>/delete/", campaign_views.campaign_delete, name="campaign_delete"),
]
