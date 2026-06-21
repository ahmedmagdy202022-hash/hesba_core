from django.contrib import admin

from .models import PurchaseInvoice, PurchaseLine


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 0
    autocomplete_fields = ("item",)
    fields = (
        "line_number",
        "item",
        "description",
        "quantity",
        "unit_purchase_price",
        "line_discount_amount",
        "line_total_amount",
    )


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "invoice_date",
        "supplier",
        "receiving_location",
        "status",
        "payment_status",
        "total_amount",
        "paid_now",
        "remaining_due",
    )
    search_fields = ("invoice_number", "supplier__supplier_code", "supplier__name")
    list_filter = ("status", "payment_status", "invoice_date", "receiving_location")
    autocomplete_fields = ("supplier", "receiving_location", "cashbox", "created_by")
    inlines = (PurchaseLineInline,)


@admin.register(PurchaseLine)
class PurchaseLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "line_number", "item", "quantity", "unit_purchase_price", "line_total_amount")
    search_fields = ("invoice__invoice_number", "item__item_code", "item__item_name")
    list_filter = ("invoice__status",)
    autocomplete_fields = ("invoice", "item")
