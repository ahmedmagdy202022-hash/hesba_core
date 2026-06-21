from django.contrib import admin

from .models import Cashbox, CashboxMovement


@admin.register(Cashbox)
class CashboxAdmin(admin.ModelAdmin):
    list_display = ("cashbox_code", "name_ar", "currency", "opening_balance", "is_default", "active")
    search_fields = ("cashbox_code", "name_ar", "name_en")
    list_filter = ("currency", "is_default", "active")


@admin.register(CashboxMovement)
class CashboxMovementAdmin(admin.ModelAdmin):
    list_display = (
        "movement_date",
        "cashbox",
        "movement_type",
        "direction",
        "amount",
        "purchase_invoice",
        "sales_invoice",
        "supplier_payment",
    )
    search_fields = (
        "cashbox__cashbox_code",
        "cashbox__name_ar",
        "purchase_invoice__invoice_number",
        "sales_invoice__invoice_number",
        "supplier_payment__payment_number",
        "description",
    )
    list_filter = ("movement_type", "direction", "movement_date")
    autocomplete_fields = ("cashbox", "purchase_invoice", "sales_invoice", "supplier_payment", "created_by")
