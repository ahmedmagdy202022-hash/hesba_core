from django.contrib import admin

from .models import Cashbox


@admin.register(Cashbox)
class CashboxAdmin(admin.ModelAdmin):
    list_display = ("cashbox_code", "name_ar", "currency", "opening_balance", "is_default", "active")
    search_fields = ("cashbox_code", "name_ar", "name_en")
    list_filter = ("currency", "is_default", "active")
