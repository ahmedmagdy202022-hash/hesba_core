from django.contrib import admin

from .models import Category, Customer, Item, Location, Supplier


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("location_code", "name_ar", "is_default", "is_receiving_location", "is_selling_location", "active")
    search_fields = ("location_code", "name_ar", "name_en")
    list_filter = ("is_default", "is_receiving_location", "is_selling_location", "active")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("category_code", "name_ar", "parent", "active")
    search_fields = ("category_code", "name_ar", "name_en")
    list_filter = ("active",)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("item_code", "item_name", "category", "unit", "is_stock_tracked", "active")
    search_fields = ("item_code", "item_name", "barcode", "size", "color")
    list_filter = ("category", "is_stock_tracked", "active")
    readonly_fields = ("average_cost",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("customer_code", "name", "phone", "active")
    search_fields = ("customer_code", "name", "phone", "whatsapp", "email")
    list_filter = ("active",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("supplier_code", "name", "phone", "active")
    search_fields = ("supplier_code", "name", "phone", "whatsapp", "email")
    list_filter = ("active",)
