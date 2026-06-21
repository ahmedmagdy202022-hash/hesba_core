from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import UserProfile
from cashboxes.models import Cashbox
from inventory.models import StockMovement, StockMovementType
from master_data.models import Category, Customer, Item, Location, Supplier
from permissions.models import Role

from .models import (
    ImportBatch,
    ImportBatchStatus,
    ImportReviewStatus,
    ImportRowStatus,
    ImportTargetType,
)
from .services import mark_raw_row_imported, refresh_batch_counters


MODEL_LABELS = {
    "categories": "master_data.Category",
    "locations": "master_data.Location",
    "items": "master_data.Item",
    "customers": "master_data.Customer",
    "suppliers": "master_data.Supplier",
    "cashboxes": "cashboxes.Cashbox",
    "stock": "inventory.StockMovement",
    "opening_balances": "opening_balance",
    "users": "accounts.UserProfile",
}


SUPPORTED_TARGET_TYPES = set(MODEL_LABELS.keys())


TRUE_VALUES = {"1", "true", "yes", "y", "active", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "inactive", "off"}


def get_effective_row_data(raw_row):
    """Return reviewed corrected data when available, otherwise the unchanged raw data."""

    corrected_review = (
        raw_row.reviews.filter(review_status=ImportReviewStatus.CORRECTED)
        .order_by("-reviewed_at", "-id")
        .first()
    )
    if corrected_review and corrected_review.corrected_data:
        return corrected_review.corrected_data
    return raw_row.raw_data or {}


def _value(data, *keys, default=""):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _text(data, *keys, default=""):
    value = _value(data, *keys, default=default)
    if value in (None, ""):
        return default
    return str(value).strip()


def _required_text(data, *keys):
    value = _text(data, *keys)
    if not value:
        raise ValidationError(f"Missing required field: {keys[0]}.")
    return value


def _decimal(data, *keys, default="0"):
    value = _value(data, *keys, default=default)
    if value in (None, ""):
        value = default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Invalid decimal value for {keys[0]}.") from exc


def _bool(data, *keys, default=True):
    value = _value(data, *keys, default=None)
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValidationError(f"Invalid boolean value for {keys[0]}.")


def _date(data, *keys, default=None):
    value = _value(data, *keys, default=default)
    if value in (None, ""):
        if default is None:
            raise ValidationError(f"Missing required date field: {keys[0]}.")
        return default
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"Invalid date value for {keys[0]}; use YYYY-MM-DD.") from exc


def _save_model(obj):
    obj.full_clean()
    obj.save()
    return obj


def apply_category_row(data, batch):
    category_code = _required_text(data, "category_code", "code")
    parent_code = _text(data, "parent_code", "parent_category_code")
    parent = None
    if parent_code:
        parent = Category.objects.get(category_code=parent_code)

    obj = Category.objects.filter(category_code=category_code).first() or Category(category_code=category_code)
    obj.name_ar = _required_text(data, "name_ar", "name", "category_name")
    obj.name_en = _text(data, "name_en", "english_name")
    obj.parent = parent
    obj.active = _bool(data, "active", "is_active", default=True)
    return _save_model(obj), MODEL_LABELS["categories"]


def apply_location_row(data, batch):
    location_code = _required_text(data, "location_code", "code")
    obj = Location.objects.filter(location_code=location_code).first() or Location(location_code=location_code)
    obj.name_ar = _required_text(data, "name_ar", "name", "location_name")
    obj.name_en = _text(data, "name_en", "english_name")
    obj.description = _text(data, "description", "notes")
    obj.is_default = _bool(data, "is_default", default=False)
    obj.is_receiving_location = _bool(data, "is_receiving_location", default=True)
    obj.is_selling_location = _bool(data, "is_selling_location", default=True)
    obj.active = _bool(data, "active", "is_active", default=True)
    return _save_model(obj), MODEL_LABELS["locations"]


def apply_item_row(data, batch):
    item_code = _required_text(data, "item_code", "code")
    category_code = _text(data, "category_code")
    category = Category.objects.get(category_code=category_code) if category_code else None

    obj = Item.objects.filter(item_code=item_code).first() or Item(item_code=item_code)
    obj.barcode = _text(data, "barcode")
    obj.item_name = _required_text(data, "item_name", "name")
    obj.category = category
    obj.size = _text(data, "size")
    obj.color = _text(data, "color")
    obj.unit = _text(data, "unit", default="unit")
    obj.default_sale_price = _decimal(data, "default_sale_price", "sale_price", default="0")
    obj.default_purchase_price = _decimal(data, "default_purchase_price", "purchase_price", default="0")
    obj.average_cost = _decimal(data, "average_cost", "cost", default="0")
    obj.min_stock = _decimal(data, "min_stock", default="0")
    obj.is_stock_tracked = _bool(data, "is_stock_tracked", default=True)
    obj.active = _bool(data, "active", "is_active", default=True)
    obj.import_batch_id = batch.batch_code
    return _save_model(obj), MODEL_LABELS["items"]


def apply_customer_row(data, batch):
    customer_code = _required_text(data, "customer_code", "code")
    obj = Customer.objects.filter(customer_code=customer_code).first() or Customer(customer_code=customer_code)
    obj.name = _required_text(data, "name", "customer_name")
    obj.phone = _text(data, "phone")
    obj.whatsapp = _text(data, "whatsapp")
    obj.email = _text(data, "email")
    obj.address = _text(data, "address")
    obj.opening_balance = _decimal(data, "opening_balance", default="0")
    obj.credit_limit = _decimal(data, "credit_limit", default="0")
    obj.notes = _text(data, "notes")
    obj.active = _bool(data, "active", "is_active", default=True)
    obj.import_batch_id = batch.batch_code
    return _save_model(obj), MODEL_LABELS["customers"]


def apply_supplier_row(data, batch):
    supplier_code = _required_text(data, "supplier_code", "code")
    obj = Supplier.objects.filter(supplier_code=supplier_code).first() or Supplier(supplier_code=supplier_code)
    obj.name = _required_text(data, "name", "supplier_name")
    obj.phone = _text(data, "phone")
    obj.whatsapp = _text(data, "whatsapp")
    obj.email = _text(data, "email")
    obj.address = _text(data, "address")
    obj.opening_balance = _decimal(data, "opening_balance", default="0")
    obj.notes = _text(data, "notes")
    obj.active = _bool(data, "active", "is_active", default=True)
    obj.import_batch_id = batch.batch_code
    return _save_model(obj), MODEL_LABELS["suppliers"]


def apply_cashbox_row(data, batch):
    cashbox_code = _required_text(data, "cashbox_code", "code")
    obj = Cashbox.objects.filter(cashbox_code=cashbox_code).first() or Cashbox(cashbox_code=cashbox_code)
    obj.name_ar = _required_text(data, "name_ar", "name", "cashbox_name")
    obj.name_en = _text(data, "name_en", "english_name")
    obj.opening_balance = _decimal(data, "opening_balance", default="0")
    obj.currency = _text(data, "currency", default="EGP")
    obj.is_default = _bool(data, "is_default", default=False)
    obj.active = _bool(data, "active", "is_active", default=True)
    obj.notes = _text(data, "notes")
    obj.import_batch_id = batch.batch_code
    return _save_model(obj), MODEL_LABELS["cashboxes"]


def apply_stock_row(data, batch, user=None):
    item = Item.objects.get(item_code=_required_text(data, "item_code"))
    location = Location.objects.get(location_code=_required_text(data, "location_code"))
    movement_date = _date(data, "movement_date", "stock_date", default=batch.go_live_date)

    obj = StockMovement(
        movement_date=movement_date,
        movement_type=StockMovementType.OPENING_STOCK,
        item=item,
        location=location,
        quantity=_decimal(data, "quantity", "opening_quantity"),
        unit_cost=_decimal(data, "unit_cost", "average_cost", default="0"),
        notes=_text(data, "notes", default=f"Opening stock import {batch.batch_code}"),
        created_by=user,
    )
    return _save_model(obj), MODEL_LABELS["stock"]


def apply_opening_balance_row(data, batch):
    entity_type = _required_text(data, "entity_type", "account_type", "target_type").lower()
    entity_code = _required_text(data, "entity_code", "code")
    opening_balance = _decimal(data, "opening_balance", "balance")

    if entity_type == "customer":
        obj = Customer.objects.get(customer_code=entity_code)
    elif entity_type == "supplier":
        obj = Supplier.objects.get(supplier_code=entity_code)
    elif entity_type == "cashbox":
        obj = Cashbox.objects.get(cashbox_code=entity_code)
    else:
        raise ValidationError("Opening balance entity_type must be customer, supplier, or cashbox.")

    obj.opening_balance = opening_balance
    obj.import_batch_id = batch.batch_code
    return _save_model(obj), f"opening_balance.{entity_type}"


def apply_user_row(data, batch):
    User = get_user_model()
    username = _required_text(data, "username", "user_name")
    user = User.objects.filter(username=username).first()
    if user is None:
        user = User(username=username)
        user.set_unusable_password()

    user.email = _text(data, "email")
    user.first_name = _text(data, "first_name")
    user.last_name = _text(data, "last_name")
    user.is_active = _bool(data, "active", "is_active", default=True)
    user.full_clean()
    user.save()

    role_code = _text(data, "role_code", "role")
    role = Role.objects.get(code=role_code) if role_code else None
    profile = getattr(user, "hesba_profile", None) or UserProfile(user=user)
    profile.role = role
    profile.display_name = _text(data, "display_name", "name", default=user.get_username())
    profile.phone = _text(data, "phone")
    profile.active = user.is_active
    profile.is_support_user = _bool(data, "is_support_user", default=False)
    profile.must_change_password = _bool(data, "must_change_password", default=True)
    return _save_model(profile), MODEL_LABELS["users"]


APPLY_HANDLERS = {
    "categories": apply_category_row,
    "locations": apply_location_row,
    "items": apply_item_row,
    "customers": apply_customer_row,
    "suppliers": apply_supplier_row,
    "cashboxes": apply_cashbox_row,
    "stock": apply_stock_row,
    "opening_balances": apply_opening_balance_row,
    "users": apply_user_row,
}


@transaction.atomic
def apply_import_batch(batch_id, user=None):
    """Apply an approved import batch to controlled Hesba tables."""

    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    target_type = batch.target_type
    if target_type not in SUPPORTED_TARGET_TYPES:
        raise ValidationError(f"Unsupported import target type: {target_type}.")
    if batch.status != ImportBatchStatus.APPROVED:
        raise ValidationError("Only approved import batches can be applied.")

    rows = list(batch.raw_rows.select_for_update().filter(row_status=ImportRowStatus.VALID).order_by("row_number"))
    if not rows:
        raise ValidationError("Approved import batch has no valid rows to apply.")

    applied = []
    handler = APPLY_HANDLERS[target_type]
    for raw_row in rows:
        data = get_effective_row_data(raw_row)
        if target_type in {"stock"}:
            obj, model_label = handler(data, batch, user=user)
        else:
            obj, model_label = handler(data, batch)
        mark_raw_row_imported(raw_row.id, model_label, obj.pk)
        applied.append(obj)

    refresh_batch_counters(batch)
    return applied
