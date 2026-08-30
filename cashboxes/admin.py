from django.contrib import admin

from config.admin import ViewOnlyAdminMixin

from .models import Cashbox, CashboxMovement
from .models import OpeningBalanceTarget
from .services import target_has_operational_use


@admin.register(Cashbox)
class CashboxAdmin(admin.ModelAdmin):
    list_display = ("cashbox_code", "name_ar", "currency", "opening_balance", "is_default", "active")
    search_fields = ("cashbox_code", "name_ar", "name_en")
    list_filter = ("currency", "is_default", "active")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and target_has_operational_use(OpeningBalanceTarget.CASHBOX, obj):
            fields.extend(("opening_balance", "currency"))
        return tuple(dict.fromkeys(fields))


@admin.register(CashboxMovement)
class CashboxMovementAdmin(ViewOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "movement_date",
        "cashbox",
        "movement_type",
        "direction",
        "amount",
        "purchase_invoice",
        "sales_invoice",
        "supplier_payment",
        "customer_payment",
    )
    search_fields = (
        "cashbox__cashbox_code",
        "cashbox__name_ar",
        "purchase_invoice__invoice_number",
        "sales_invoice__invoice_number",
        "supplier_payment__payment_number",
        "customer_payment__payment_number",
        "description",
    )
    list_filter = ("movement_type", "direction", "movement_date")
    autocomplete_fields = ("cashbox", "purchase_invoice", "sales_invoice", "supplier_payment", "customer_payment", "created_by")
