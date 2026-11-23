from django.urls import path

from . import views


app_name = "expenses"

urlpatterns = [
    path("", views.expense_list, name="list"),
    path("new/", views.expense_create, name="create"),
    path("<int:pk>/cancel/", views.expense_cancel, name="cancel"),
    path("categories/", views.category_list, name="categories"),
    path("categories/quick/", views.category_quick_create, name="category_quick_create"),
    path("categories/<int:pk>/toggle/", views.category_toggle, name="category_toggle"),
]
