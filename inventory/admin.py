from django.contrib import admin

from .models import StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("movement_date", "movement_type", "item", "location", "quantity", "unit_cost", "purchase_invoice")
    search_fields = ("item__item_code", "item__item_name", "location__location_code", "location__name_ar", "purchase_invoice__invoice_number")
    list_filter = ("movement_type", "movement_date", "location")
    autocomplete_fields = ("item", "location", "purchase_invoice", "purchase_line", "created_by")
