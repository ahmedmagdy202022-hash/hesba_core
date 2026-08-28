from django.urls import path

from . import views


app_name = "master_data"

urlpatterns = [
    path("", views.master_data_hub, name="hub"),

    # Stable aliases used by Dashboard navigation and quick actions.
    path("customers/", views.entity_list, {"entity": "customers"}, name="customers"),
    path("customers/new/", views.entity_create, {"entity": "customers"}, name="customer_create"),
    path("suppliers/", views.entity_list, {"entity": "suppliers"}, name="suppliers"),
    path("suppliers/new/", views.entity_create, {"entity": "suppliers"}, name="supplier_create"),
    path("items/", views.entity_list, {"entity": "items"}, name="items"),
    path("items/new/", views.entity_create, {"entity": "items"}, name="item_create"),
    path("cashboxes/", views.entity_list, {"entity": "cashboxes"}, name="cashboxes"),
    path("locations/", views.entity_list, {"entity": "locations"}, name="locations"),
    path("categories/", views.entity_list, {"entity": "categories"}, name="categories"),

    path("<str:entity>/", views.entity_list, name="list"),
    path("<str:entity>/new/", views.entity_create, name="create"),
    path("<str:entity>/<int:pk>/edit/", views.entity_edit, name="edit"),
]
