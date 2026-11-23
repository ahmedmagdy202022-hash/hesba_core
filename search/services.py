"""SEARCH-001: one box that finds customers, suppliers, items, invoices and serials.

Each group is searched only when the user may open what it links to and its
module (or capability) is switched on, so search never shows a record its
screen would refuse.
"""

from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.urls import reverse

from permissions.services import user_has_permission
from settings_core.capabilities import capability_enabled
from settings_core.setup_services import usable_modules


LIMIT = 8


def _digits(text):
    return "".join(ch for ch in text if ch.isdigit())


def _plain(field):
    """A phone field with the spaces and dashes people type taken out."""

    return Replace(Replace(field, Value(" "), Value("")), Value("-"), Value(""))


def search(user, query, lang="ar"):
    query = (query or "").strip()
    if len(query) < 2:
        return []
    modules = set(usable_modules())
    groups = []

    def add(key, title, rows):
        if rows:
            groups.append({"key": key, "title": title, "rows": rows})

    en = lang == "en"
    phone = _digits(query)
    if user_has_permission(user, "master_data.view_master_data"):
        from master_data.models import Customer, Item, Supplier

        if "customers" in modules:
            match = Q(name__icontains=query) | Q(customer_code__icontains=query)
            if len(phone) >= 4:
                match |= Q(plain_phone__contains=phone) | Q(plain_whatsapp__contains=phone)
            can_card = user_has_permission(user, "reports.view_customer_report")
            customers = Customer.objects.annotate(plain_phone=_plain("phone"), plain_whatsapp=_plain("whatsapp"))
            add("customers", "Customers" if en else "العملاء", [
                {"label": row.name, "meta": " · ".join(part for part in (row.customer_code, row.phone) if part),
                 "url": reverse("parties:card", args=["customer", row.pk]) if can_card else f"{reverse('master_data:customers')}?q={row.customer_code}"}
                for row in customers.filter(match, active=True).order_by("name")[:LIMIT]
            ])
        if "suppliers" in modules:
            match = Q(name__icontains=query) | Q(supplier_code__icontains=query)
            if len(phone) >= 4:
                match |= Q(plain_phone__contains=phone)
            can_card = user_has_permission(user, "reports.view_supplier_report")
            add("suppliers", "Suppliers" if en else "الموردين", [
                {"label": row.name, "meta": " · ".join(part for part in (row.supplier_code, row.phone) if part),
                 "url": reverse("parties:card", args=["supplier", row.pk]) if can_card else f"{reverse('master_data:suppliers')}?q={row.supplier_code}"}
                for row in Supplier.objects.annotate(plain_phone=_plain("phone")).filter(match, active=True).order_by("name")[:LIMIT]
            ])
        if "items_services" in modules:
            can_stock = user_has_permission(user, "inventory.view_stock") and "inventory" in modules
            add("items", "Items" if en else "الأصناف", [
                {"label": row.item_name, "meta": " · ".join(part for part in (row.item_code, row.barcode, row.size, row.color) if part),
                 "url": reverse("inventory:item_detail", args=[row.pk]) if can_stock else f"{reverse('master_data:items')}?q={row.item_code}"}
                for row in Item.objects.filter(Q(item_name__icontains=query) | Q(item_code__icontains=query) | Q(barcode=query), active=True).order_by("item_code")[:LIMIT]
            ])
    if "sales_operations" in modules and user_has_permission(user, "sales.view_sales_invoices"):
        from sales.models import SalesInvoice

        add("sales", "Sales invoices" if en else "فواتير البيع", [
            {"label": row.invoice_number, "meta": f"{row.customer.name} · {row.invoice_date:%Y-%m-%d} · {row.total_amount:,.2f}", "url": reverse("sales:detail", args=[row.pk])}
            for row in SalesInvoice.objects.filter(invoice_number__icontains=query).select_related("customer").order_by("-invoice_date", "-id")[:LIMIT]
        ])
    if "purchases" in modules and user_has_permission(user, "purchases.view_purchase_invoices"):
        from purchases.models import PurchaseInvoice

        add("purchases", "Purchase invoices" if en else "فواتير الشراء", [
            {"label": row.invoice_number, "meta": f"{row.supplier.name} · {row.invoice_date:%Y-%m-%d} · {row.total_amount:,.2f}", "url": reverse("purchases:detail", args=[row.pk])}
            for row in PurchaseInvoice.objects.filter(invoice_number__icontains=query).select_related("supplier").order_by("-invoice_date", "-id")[:LIMIT]
        ])
    if capability_enabled("serials") and user_has_permission(user, "inventory.view_stock"):
        from serials.models import SerialNumber
        from serials.services import normalize

        add("serials", "Serial numbers" if en else "السيريالات", [
            {"label": row.serial, "meta": row.item.item_name, "url": reverse("serials:detail", args=[row.pk])}
            for row in SerialNumber.objects.filter(serial__icontains=normalize(query)).select_related("item").order_by("serial")[:LIMIT]
        ])
    return groups
