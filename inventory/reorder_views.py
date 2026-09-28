"""REORDER-001 screen: items to buy soon, grouped by their last supplier, one click to a purchase draft."""

from collections import OrderedDict
from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .reorder import suggestions


WINDOWS = (14, 30, 60, 90)
TARGETS = (7, 14, 30, 60)
MAX_LINES = 15


def _choice(raw, allowed, default):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value in allowed else default


@require_permission("inventory.view_stock")
def reorder(request):
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    window = _choice(request.GET.get("window"), WINDOWS, 30)
    target = _choice(request.GET.get("target"), TARGETS, 14)
    rows = suggestions(window, target)
    groups = OrderedDict()
    for row in rows:
        supplier = row["supplier"]
        key = supplier.pk if supplier else 0
        group = groups.setdefault(key, {"supplier": supplier, "rows": []})
        group["rows"].append(row)
    can_buy = user_has_permission(request.user, "purchases.create_purchase_invoice")
    for group in groups.values():
        params = [("lang", lang)]
        if group["supplier"]:
            params.append(("supplier", group["supplier"].pk))
        for row in group["rows"][:MAX_LINES]:
            params += [("reorder_item", row["item"].pk), ("reorder_qty", row["suggested"]), ("reorder_price", row["last_price"])]
        group["draft_url"] = f"{reverse('purchases:create')}?{urlencode(params)}" if can_buy else ""
    return render(request, "inventory/reorder.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "page_title": "Buy soon" if lang == "en" else "محتاج يتطلب",
        "section": "inventory", "groups": list(groups.values()), "count": len(rows), "window": window, "target": target,
        "windows": WINDOWS, "targets": TARGETS, "can_view_cost": user_has_permission(request.user, "inventory.view_cost"),
    })
