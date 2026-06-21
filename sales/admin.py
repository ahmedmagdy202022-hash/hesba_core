from django.contrib import admin

from .models import CustomerLedgerEntry, SalesInvoice, SalesLine


class SalesLineInline(admin.TabularInline):
    model = SalesLine
    extra = 0
    autocomplete_fields = ("item",)
    fields = (
        "line_number",
        "item",
        "description",
        "quantity",
        "unit_sale_price",
        "line_discount_amount",
        "line_total_amount",
    )


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "invoice_date",
        "customer",
        "selling_location",
        "status",
        "payment_status",
        "total_amount",
        "paid_now",
        "remaining_due",
    )
    search_fields = ("invoice_number", "customer__customer_code", "customer__name")
    list_filter = ("status", "payment_status", "invoice_date", "selling_location")
    autocomplete_fields = ("customer", "selling_location", "cashbox", "created_by")
    inlines = (SalesLineInline,)


@admin.register(SalesLine)
class SalesLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "line_number", "item", "quantity", "unit_sale_price", "line_total_amount")
    search_fields = ("invoice__invoice_number", "item__item_code", "item__item_name")
    list_filter = ("invoice__status",)
    autocomplete_fields = ("invoice", "item")


@admin.register(CustomerLedgerEntry)
class CustomerLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("entry_date", "customer", "entry_type", "due_increase", "due_decrease", "sales_invoice")
    search_fields = ("customer__customer_code", "customer__name", "sales_invoice__invoice_number")
    list_filter = ("entry_type", "entry_date")
    autocomplete_fields = ("customer", "sales_invoice", "created_by")
