"""POS-002: find a customer at the till by phone or name, or add one without leaving it."""

import json
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from audit.models import AuditEventType, AuditLog
from master_data.models import Customer
from permissions.decorators import require_permission
from permissions.services import user_has_permission


def digits(text):
    return re.sub(r"\D", "", text or "")


def customer_directory():
    """[{id, name, phone}] for the till's lookup box (phone as digits only)."""

    return [{"id": row["pk"], "name": row["name"], "phone": digits(row["phone"]) or digits(row["whatsapp"])}
            for row in Customer.objects.filter(active=True).order_by("name").values("pk", "name", "phone", "whatsapp")]


def _next_code():
    number = Customer.objects.count() + 1
    while Customer.objects.filter(customer_code=f"C-{number:05d}").exists():
        number += 1
    return f"C-{number:05d}"


@transaction.atomic
def quick_add_customer(name, phone, user):
    name, phone = (name or "").strip()[:255], (phone or "").strip()[:50]
    if not name:
        raise ValidationError("name")
    if phone and len(digits(phone)) < 7:
        raise ValidationError("phone")
    if phone:
        existing = next((row for row in customer_directory() if row["phone"] and row["phone"] == digits(phone)), None)
        if existing:
            return Customer.objects.get(pk=existing["id"]), False
    customer = Customer(customer_code=_next_code(), name=name, phone=phone)
    customer.full_clean()
    customer.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="master_data", action="quick_add_customer", object_type="master_data.Customer",
                            object_id=str(customer.pk), before_data={}, after_data={"code": customer.customer_code, "name": name, "phone": phone}, reason="Added at the till.")
    return customer, True


@require_POST
@require_permission("sales.create_sales_invoice")
def pos_add_customer(request):
    if not user_has_permission(request.user, "master_data.manage_parties"):
        raise PermissionDenied("Adding customers needs master_data.manage_parties.")
    try:
        payload = json.loads(request.body or "{}")
    except ValueError:
        payload = {}
    try:
        customer, created = quick_add_customer(payload.get("name"), payload.get("phone"), request.user)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages[0] if exc.messages else "invalid"}, status=400)
    return JsonResponse({"ok": True, "id": customer.pk, "name": customer.name, "phone": digits(customer.phone), "created": created})
