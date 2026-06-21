from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction

from cashboxes.models import Cashbox
from master_data.models import Category, Customer, Item, Location, Supplier
from permissions.models import Role

from .apply_services import (
    SUPPORTED_TARGET_TYPES,
    _bool,
    _date,
    _decimal,
    _required_text,
    _text,
    get_effective_row_data,
)
from .models import ImportBatch, ImportBatchStatus, ImportRowStatus
from .services import mark_raw_row_validation, refresh_batch_counters


FINAL_BATCH_STATUSES = {
    ImportBatchStatus.APPROVED,
    ImportBatchStatus.IMPORTED,
    ImportBatchStatus.CANCELLED,
}


def _as_error_list(error):
    if hasattr(error, "message_dict"):
        return [f"{field}: {message}" for field, messages in error.message_dict.items() for message in messages]
    if hasattr(error, "messages"):
        return list(error.messages)
    return [str(error)]


def _nonnegative_decimal(data, *keys, default="0"):
    value = _decimal(data, *keys, default=default)
    if value < 0:
        raise ValidationError(f"{keys[0]} cannot be negative.")
    return value


def _positive_decimal(data, *keys):
    value = _decimal(data, *keys)
    if value <= Decimal("0"):
        raise ValidationError(f"{keys[0]} must be greater than zero.")
    return value


def validate_category_data(data, batch):
    category_code = _required_text(data, "category_code", "code")
    _required_text(data, "name_ar", "name", "category_name")
    parent_code = _text(data, "parent_code", "parent_category_code")
    if parent_code:
        if parent_code == category_code:
            raise ValidationError("Category cannot be its own parent.")
        Category.objects.get(category_code=parent_code)
    _bool(data, "active", "is_active", default=True)


def validate_location_data(data, batch):
    _required_text(data, "location_code", "code")
    _required_text(data, "name_ar", "name", "location_name")
    _bool(data, "is_default", default=False)
    _bool(data, "is_receiving_location", default=True)
    _bool(data, "is_selling_location", default=True)
    _bool(data, "active", "is_active", default=True)


def validate_item_data(data, batch):
    _required_text(data, "item_code", "code")
    _required_text(data, "item_name", "name")
    category_code = _text(data, "category_code")
    if category_code:
        Category.objects.get(category_code=category_code)
    _nonnegative_decimal(data, "default_sale_price", "sale_price", default="0")
    _nonnegative_decimal(data, "default_purchase_price", "purchase_price", default="0")
    _nonnegative_decimal(data, "average_cost", "cost", default="0")
    _nonnegative_decimal(data, "min_stock", default="0")
    _bool(data, "is_stock_tracked", default=True)
    _bool(data, "active", "is_active", default=True)


def validate_customer_data(data, batch):
    _required_text(data, "customer_code", "code")
    _required_text(data, "name", "customer_name")
    _nonnegative_decimal(data, "credit_limit", default="0")
    _decimal(data, "opening_balance", default="0")
    _bool(data, "active", "is_active", default=True)


def validate_supplier_data(data, batch):
    _required_text(data, "supplier_code", "code")
    _required_text(data, "name", "supplier_name")
    _decimal(data, "opening_balance", default="0")
    _bool(data, "active", "is_active", default=True)


def validate_cashbox_data(data, batch):
    _required_text(data, "cashbox_code", "code")
    _required_text(data, "name_ar", "name", "cashbox_name")
    _decimal(data, "opening_balance", default="0")
    _bool(data, "is_default", default=False)
    _bool(data, "active", "is_active", default=True)


def validate_stock_data(data, batch):
    item = Item.objects.get(item_code=_required_text(data, "item_code"))
    Location.objects.get(location_code=_required_text(data, "location_code"))
    _date(data, "movement_date", "stock_date", default=batch.go_live_date)
    _positive_decimal(data, "quantity", "opening_quantity")
    _nonnegative_decimal(data, "unit_cost", "average_cost", default="0")
    if not item.is_stock_tracked:
        raise ValidationError("Opening stock cannot be imported for a non-stock-tracked item.")


def validate_opening_balance_data(data, batch):
    entity_type = _required_text(data, "entity_type", "account_type", "target_type").lower()
    entity_code = _required_text(data, "entity_code", "code")
    _decimal(data, "opening_balance", "balance")
    if entity_type == "customer":
        Customer.objects.get(customer_code=entity_code)
    elif entity_type == "supplier":
        Supplier.objects.get(supplier_code=entity_code)
    elif entity_type == "cashbox":
        Cashbox.objects.get(cashbox_code=entity_code)
    else:
        raise ValidationError("Opening balance entity_type must be customer, supplier, or cashbox.")


def validate_user_data(data, batch):
    _required_text(data, "username", "user_name")
    role_code = _text(data, "role_code", "role")
    if role_code:
        Role.objects.get(code=role_code)
    _bool(data, "active", "is_active", default=True)
    _bool(data, "is_support_user", default=False)
    _bool(data, "must_change_password", default=True)


VALIDATORS = {
    "categories": validate_category_data,
    "locations": validate_location_data,
    "items": validate_item_data,
    "customers": validate_customer_data,
    "suppliers": validate_supplier_data,
    "cashboxes": validate_cashbox_data,
    "stock": validate_stock_data,
    "opening_balances": validate_opening_balance_data,
    "users": validate_user_data,
}


def validate_raw_import_row(raw_row):
    target_type = raw_row.batch.target_type
    if target_type not in SUPPORTED_TARGET_TYPES:
        return [f"Unsupported import target type: {target_type}."]

    data = get_effective_row_data(raw_row)
    try:
        VALIDATORS[target_type](data, raw_row.batch)
    except ObjectDoesNotExist as exc:
        return [f"Referenced record was not found: {exc}."]
    except ValidationError as exc:
        return _as_error_list(exc)
    return []


@transaction.atomic
def validate_import_batch(batch_id):
    """Validate all rows in a batch and mark each row valid or invalid."""

    batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status in FINAL_BATCH_STATUSES:
        raise ValidationError("Cannot validate approved, imported, or cancelled batches.")
    if batch.target_type not in SUPPORTED_TARGET_TYPES:
        raise ValidationError(f"Unsupported import target type: {batch.target_type}.")

    result = {"valid": 0, "invalid": 0, "errors": {}}
    rows = batch.raw_rows.select_for_update().order_by("row_number")
    for raw_row in rows:
        errors = validate_raw_import_row(raw_row)
        mark_raw_row_validation(raw_row.id, is_valid=not errors, errors=errors)
        if errors:
            result["invalid"] += 1
            result["errors"][raw_row.row_number] = errors
        else:
            result["valid"] += 1

    refresh_batch_counters(batch)
    return result
