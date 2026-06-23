from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import PurchaseInvoice, PurchaseLine, SupplierLedgerEntry, SupplierPayment
from .services import cancel_posted_purchase_invoice, post_purchase_invoice


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 0
    autocomplete_fields = ("item",)
    fields = ("line_number", "item", "description", "quantity", "unit_purchase_price", "line_discount_amount", "line_total_amount")


@admin.action(description="Post selected purchase invoices")
def post_selected_purchase_invoices(modeladmin, request, queryset):
    done = 0
    for invoice in queryset:
        try:
            post_purchase_invoice(invoice.id, user=request.user)
            done += 1
        except ValidationError as exc:
            messages.error(request, f"{invoice}: {exc}")
    messages.success(request, f"Posted purchase invoices: {done}")


@admin.action(description="Cancel selected posted purchase invoices")
def cancel_selected_purchase_invoices(modeladmin, request, queryset):
    done = 0
    for invoice in queryset:
        try:
            cancel_posted_purchase_invoice(invoice.id, user=request.user, reason="Admin action")
            done += 1
        except ValidationError as exc:
            messages.error(request, f"{invoice}: {exc}")
    messages.success(request, f"Cancelled purchase invoices: {done}")


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "invoice_date", "supplier", "receiving_location", "status", "payment_status", "total_amount", "paid_now", "remaining_due")
    search_fields = ("invoice_number", "supplier__supplier_code", "supplier__name")
    list_filter = ("status", "payment_status", "invoice_date", "receiving_location")
    autocomplete_fields = ("supplier", "receiving_location", "cashbox", "created_by")
    inlines = (PurchaseLineInline,)
    actions = (post_selected_purchase_invoices, cancel_selected_purchase_invoices)


@admin.register(PurchaseLine)
class PurchaseLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "line_number", "item", "quantity", "unit_purchase_price", "line_total_amount")
    search_fields = ("invoice__invoice_number", "item__item_code", "item__item_name")
    list_filter = ("invoice__status",)
    autocomplete_fields = ("invoice", "item")


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(admin.ModelAdmin):
    list_display = ("payment_number", "payment_date", "supplier", "cashbox", "amount", "status")
    search_fields = ("payment_number", "supplier__supplier_code", "supplier__name", "cashbox__cashbox_code")
    list_filter = ("status", "payment_date", "cashbox")
    autocomplete_fields = ("supplier", "cashbox", "created_by")


@admin.register(SupplierLedgerEntry)
class SupplierLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("entry_date", "supplier", "entry_type", "due_increase", "due_decrease", "purchase_invoice", "supplier_payment")
    search_fields = ("supplier__supplier_code", "supplier__name", "purchase_invoice__invoice_number", "supplier_payment__payment_number")
    list_filter = ("entry_type", "entry_date")
    autocomplete_fields = ("supplier", "purchase_invoice", "supplier_payment", "created_by")
