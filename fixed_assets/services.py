"""ASSET-001: register, depreciate (straight line, monthly), dispose of and cancel assets.

Conventions, stated once so every screen agrees:

* a full month of depreciation from the month the asset is put in service;
* each month is (cost - salvage) / useful months to the piastre, the last
  month taking the rounding, so the total is exactly cost - salvage;
* a month is charged on its first day: a report from the 1st to the 31st of
  a month includes that month's depreciation;
* no depreciation in the month of disposal or after it;
* disposal result = proceeds - book value when disposed.
"""

import calendar
from datetime import date
from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from settings_core.capabilities import capability_enabled

from .models import AssetStatus, FixedAsset


ZERO = Decimal("0.00")
MAX_MONTHS = 600
MESSAGES = {
    "ar": {
        "name": "اكتب اسم الأصل.", "cost": "التكلفة لازم أكبر من صفر.", "salvage": "قيمة الخردة لازم أقل من التكلفة ومش سالبة.",
        "months": "العمر الإنتاجي لازم من 1 لـ {max} شهر.", "not_active": "الأصل مش مستخدم (اتباع أو اتلغى).", "before": "تاريخ البيع لازم بعد تاريخ بدء الاستخدام.",
        "proceeds": "مبلغ البيع مش صحيح.", "reason": "اكتب السبب.",
    },
    "en": {
        "name": "Enter the asset's name.", "cost": "The cost must be above zero.", "salvage": "The salvage value must be below the cost and not negative.",
        "months": "The useful life must be 1 to {max} months.", "not_active": "The asset is not in use (disposed or cancelled).", "before": "The disposal date must be after the in-service date.",
        "proceeds": "Invalid sale amount.", "reason": "Enter a reason.",
    },
}


def assets_enabled():
    return capability_enabled("fixed_assets")


def month_start(day):
    return date(day.year, day.month, 1)


def add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def monthly_amounts(asset):
    depreciable = Decimal(asset.cost) - Decimal(asset.salvage_value)
    each = (depreciable / asset.useful_months).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return [each] * (asset.useful_months - 1) + [money_round(depreciable - each * (asset.useful_months - 1))]


def charged_months(asset):
    """[(month start, amount)] for every month the asset is (or will be) charged."""

    if asset.status == AssetStatus.CANCELLED:
        return []
    first = month_start(asset.in_service_on)
    stop = month_start(asset.disposed_on) if asset.status == AssetStatus.DISPOSED and asset.disposed_on else None
    rows = []
    for index, amount in enumerate(monthly_amounts(asset)):
        month = add_months(first, index)
        if stop is not None and month >= stop:
            break
        rows.append((month, amount))
    return rows


def accumulated(asset, as_of):
    return money_round(sum((amount for month, amount in charged_months(asset) if month <= as_of), ZERO))


def book_value(asset, as_of):
    return money_round(Decimal(asset.cost) - accumulated(asset, as_of))


def disposal_result(asset):
    if asset.status != AssetStatus.DISPOSED:
        return None
    return money_round(Decimal(asset.disposal_proceeds) - book_value(asset, asset.disposed_on))


def schedule(asset, today=None):
    today = today or timezone.localdate()
    rows, running = [], ZERO
    for month, amount in charged_months(asset):
        running += amount
        rows.append({"month": month, "amount": amount, "accumulated": money_round(running), "book_value": money_round(Decimal(asset.cost) - running), "charged": month <= today})
    return rows


def _assets():
    return FixedAsset.objects.exclude(status=AssetStatus.CANCELLED)


def depreciation_between(date_from=None, date_to=None):
    """Depreciation charged in the window (months whose first day falls inside it)."""

    total = ZERO
    for asset in _assets():
        for month, amount in charged_months(asset):
            if (date_from is None or month >= date_from) and (date_to is None or month <= date_to):
                total += amount
    return money_round(total)


def disposal_results_between(date_from=None, date_to=None):
    total = ZERO
    for asset in FixedAsset.objects.filter(status=AssetStatus.DISPOSED):
        if (date_from is None or asset.disposed_on >= date_from) and (date_to is None or asset.disposed_on <= date_to):
            total += disposal_result(asset)
    return money_round(total)


def _next_code():
    last = FixedAsset.objects.select_for_update().order_by("-id").first()
    # DEPLOY-001: count, not the row id — PostgreSQL ids skip after a rolled-back
    # attempt, and a gap in document numbers reads as a missing document.
    sequence = FixedAsset.objects.count() + 1 if last else 1
    code = f"FA-{sequence:05d}"
    while FixedAsset.objects.filter(code=code).exists():
        sequence += 1
        code = f"FA-{sequence:05d}"
    return code


def _audit(asset, user, action, after, before=None, reason=""):
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE if action == "register_asset" else AuditEventType.UPDATE, actor=user, module="fixed_assets", action=action,
        object_type="fixed_assets.FixedAsset", object_id=str(asset.pk), before_data=before or {}, after_data=after, reason=reason,
    )


@transaction.atomic
def register_asset(*, name, category, in_service_on, cost, salvage_value, useful_months, user, cashbox=None, notes="", lang="ar"):
    """Add an asset; with a cashbox, its cost is paid through a direct cash-out operation."""

    from cashboxes.models import CashboxOperationType
    from cashboxes.services import create_cashbox_operation

    words = MESSAGES[lang]
    name = (name or "").strip()[:255]
    cost, salvage_value = money_round(Decimal(cost)), money_round(Decimal(salvage_value or 0))
    if not name:
        raise ValidationError(words["name"])
    if cost <= 0:
        raise ValidationError(words["cost"])
    if salvage_value < 0 or salvage_value >= cost:
        raise ValidationError(words["salvage"])
    if not 1 <= int(useful_months) <= MAX_MONTHS:
        raise ValidationError(words["months"].format(max=MAX_MONTHS))
    asset = FixedAsset(code=_next_code(), name=name, category=category, in_service_on=in_service_on, cost=cost, salvage_value=salvage_value,
                       useful_months=int(useful_months), notes=(notes or "").strip()[:255], created_by=user)
    asset.full_clean()
    if cashbox is not None:
        asset.payment_operation = create_cashbox_operation(f"{asset.code}-BUY", in_service_on, CashboxOperationType.DIRECT_OUT, cost,
                                                           f"Fixed asset purchase: {name}", user, source_cashbox=cashbox)
    asset.save()
    _audit(asset, user, "register_asset", {"code": asset.code, "name": name, "cost": str(cost), "salvage": str(salvage_value), "months": asset.useful_months,
                                           "in_service_on": in_service_on.isoformat(), "paid_from": cashbox.pk if cashbox else None})
    return asset


@transaction.atomic
def dispose_asset(asset, *, disposed_on, proceeds, user, cashbox=None, lang="ar"):
    """Sold or scrapped: depreciation stops; proceeds optionally come into a cashbox."""

    from cashboxes.models import CashboxOperationType
    from cashboxes.services import create_cashbox_operation

    words = MESSAGES[lang]
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    if asset.status != AssetStatus.ACTIVE:
        raise ValidationError(words["not_active"])
    if disposed_on < asset.in_service_on:
        raise ValidationError(words["before"])
    proceeds = money_round(Decimal(proceeds or 0))
    if proceeds < 0:
        raise ValidationError(words["proceeds"])
    if proceeds > 0 and cashbox is not None:
        asset.disposal_operation = create_cashbox_operation(f"{asset.code}-SELL", disposed_on, CashboxOperationType.DIRECT_IN, proceeds,
                                                            f"Fixed asset sale: {asset.name}", user, destination_cashbox=cashbox)
    asset.status, asset.disposed_on, asset.disposal_proceeds = AssetStatus.DISPOSED, disposed_on, proceeds
    asset.save(update_fields=["status", "disposed_on", "disposal_proceeds", "disposal_operation"])
    _audit(asset, user, "dispose_asset", {"disposed_on": disposed_on.isoformat(), "proceeds": str(proceeds), "result": str(disposal_result(asset))})
    return asset


@transaction.atomic
def cancel_asset(asset, user, reason, lang="ar"):
    """An asset entered by mistake: it stops counting, and its cash payment is reversed."""

    from cashboxes.services import cancel_cashbox_operation

    words = MESSAGES[lang]
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    if asset.status != AssetStatus.ACTIVE:
        raise ValidationError(words["not_active"])
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(words["reason"])
    if asset.payment_operation_id:
        cancel_cashbox_operation(asset.payment_operation_id, timezone.localdate(), f"Fixed asset {asset.code} cancelled: {reason}", user)
    asset.status = AssetStatus.CANCELLED
    asset.save(update_fields=["status"])
    _audit(asset, user, "cancel_asset", {"status": asset.status}, {"status": AssetStatus.ACTIVE}, reason=reason[:255])
    return asset
