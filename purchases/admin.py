from django.contrib import admin

from config.admin import ViewOnlyAdminMixin

from .models import (
    PurchaseInvoice,
    PurchaseLine,
    SupplierLedgerEntry,
    SupplierPayment,
)


class PurchaseLineInline(ViewOnlyAdminMixin, admin.TabularInline):
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
class PurchaseInvoiceAdmin(ViewOnlyAdminMixin, admin.ModelAdmin):
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
    readonly_fields = (
        "status",
        "payment_status",
        "subtotal",
        "total_amount",
        "remaining_due",
        "created_at",
        "updated_at",
    )


@admin.register(PurchaseLine)
class PurchaseLineAdmin(ViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("invoice", "line_number", "item", "quantity", "unit_purchase_price", "line_total_amount")
    search_fields = ("invoice__invoice_number", "item__item_code", "item__item_name")
    list_filter = ("invoice__status",)
    autocomplete_fields = ("invoice", "item")


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(ViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("payment_number", "payment_date", "supplier", "cashbox", "amount", "status")
    search_fields = ("payment_number", "supplier__supplier_code", "supplier__name", "cashbox__cashbox_code")
    list_filter = ("status", "payment_date", "cashbox")
    autocomplete_fields = ("supplier", "cashbox", "created_by")


@admin.register(SupplierLedgerEntry)
class SupplierLedgerEntryAdmin(ViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "entry_date",
        "supplier",
        "entry_type",
        "due_increase",
        "due_decrease",
        "purchase_invoice",
        "supplier_payment",
    )
    search_fields = (
        "supplier__supplier_code",
        "supplier__name",
        "purchase_invoice__invoice_number",
        "supplier_payment__payment_number",
    )
    list_filter = ("entry_type", "entry_date")
    autocomplete_fields = ("supplier", "purchase_invoice", "supplier_payment", "created_by")
