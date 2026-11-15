"""Contracting C4 (HG-123): the rules of a letter of guarantee. Nothing here moves money.

* **Who.** Managing (adding, editing, extending, reducing, releasing, liquidating, cancelling) needs
  ``cashboxes.manage_cashboxes`` or ``sales.create_sales_invoice`` (owner question 11's default). A site engineer with
  ``projects.view_projects`` sees the project's guarantees on the project page, without amounts. No permission code is new.
* **Alerts.** An active guarantee is *expiring* from N days before its expiry through the expiry day itself (N is the
  ``guarantees.alert_days`` system setting, 30 by default), and *expired* from the day after while still active.
* **Advance payment guarantee.** Its page shows the project's advance left (``projects.contract.position``) and
  suggests reducing the guarantee to it, or releasing it once the advance is recovered. The suggestion is never applied
  by itself.
* **Retention guarantee.** Recorded only. Releasing the cash retention held on the project stays the ordinary
  ``release_retention`` on the project's payments page.
* **Liquidation.** The event is recorded; the money the owner takes is an ordinary bank cash-out the user records.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import EventKind, Guarantee, GuaranteeEvent, GuaranteeKind, GuaranteeStatus

ZERO = Decimal("0")
MANAGE = ("cashboxes.manage_cashboxes", "sales.create_sales_invoice")
VIEW_PROJECTS = "projects.view_projects"
SET_DAYS = "cashboxes.manage_cashboxes"
ALERT_DAYS_KEY = "guarantees.alert_days"
DEFAULT_ALERT_DAYS = 30
MAX_ALERT_DAYS = 365
#: The largest amount one letter may carry: the field holds 14 digits, and nothing in the app's other typed money fields
#: goes past a billion.
AMOUNT_LIMIT = Decimal("1000000000")

LABELS = {
    "ar": {
        "kinds": {"bid": "ضمان ابتدائي (دخول مناقصة)", "performance": "ضمان نهائي (حسن تنفيذ)", "advance": "ضمان دفعة مقدمة",
                  "retention": "ضمان بدل محتجزات", "other": "ضمان آخر"},
        "statuses": {"active": "ساري", "released": "مُفرج عنه", "liquidated": "مُسيَّل", "cancelled": "ملغي"},
        "events": {"extend": "تمديد", "reduce": "تخفيض", "release": "إفراج", "liquidate": "تسييل", "cancel": "إلغاء"},
        "alerts": {"expiring": "قرّب ينتهي", "expired": "انتهى وهو ساري"},
    },
    "en": {
        "kinds": {"bid": "Bid bond", "performance": "Performance bond", "advance": "Advance payment guarantee",
                  "retention": "Retention guarantee", "other": "Other guarantee"},
        "statuses": {"active": "Active", "released": "Released", "liquidated": "Liquidated", "cancelled": "Cancelled"},
        "events": {"extend": "Extended", "reduce": "Reduced", "release": "Released", "liquidate": "Liquidated", "cancel": "Cancelled"},
        "alerts": {"expiring": "Expiring soon", "expired": "Expired, still active"},
    },
}

MESSAGES = {
    "ar": {
        "kind": "اختار نوع الضمان.", "number": "اكتب رقم خطاب الضمان زي ما هو في البنك.",
        "bank": "اختار حساب البنك اللي أصدر الخطاب (حساب بنكي في الشركة دي).", "beneficiary": "اختار المستفيد (العميل).",
        "project": "المشروع ده مش موجود.", "amount": "اكتب مبلغ الضمان رقم أكبر من صفر.",
        "too_big": "المبلغ أكبر من المسموح لخطاب واحد.", "issued": "اكتب تاريخ الإصدار.", "expires": "اكتب تاريخ الانتهاء.",
        "order": "تاريخ الانتهاء لازم يكون بعد تاريخ الإصدار أو نفس اليوم.", "margin": "نسبة الغطاء النقدي لازم تكون بين 0 و 100.",
        "commission": "العمولة لازم تكون رقم مش سالب.", "duplicate": "فيه خطاب بنفس الرقم في البنك ده.",
        "not_active": "الخطاب ده مش ساري، فمينفعش يتعمل عليه حاجة.", "locked": "بعد أول حركة، المبلغ وتاريخ الانتهاء بيتغيروا بالتمديد أو التخفيض بس.",
        "event_date": "اكتب تاريخ الحركة (مش قبل تاريخ الإصدار).", "new_expiry": "تاريخ الانتهاء الجديد لازم يكون بعد الحالي ({current}).",
        "new_amount": "المبلغ الجديد لازم يكون أكبر من صفر وأقل من الحالي ({current}).",
        "days": "عدد أيام التنبيه لازم يكون من 1 لـ {max}.", "denied": "مش معاك صلاحية تدير خطابات الضمان.",
        "note_required": "اكتب سبب الإلغاء.",
    },
    "en": {
        "kind": "Choose the kind of guarantee.", "number": "Type the letter's number as the bank wrote it.",
        "bank": "Choose the bank account that issued the letter (a bank account of this entity).", "beneficiary": "Choose the beneficiary (customer).",
        "project": "That project does not exist.", "amount": "Type the guarantee amount as a number above zero.",
        "too_big": "The amount is larger than one letter may carry.", "issued": "Type the issue date.", "expires": "Type the expiry date.",
        "order": "The expiry date must be on or after the issue date.", "margin": "The cash margin must be between 0 and 100 percent.",
        "commission": "The commission must be a number, not negative.", "duplicate": "This bank already has a letter with that number.",
        "not_active": "This letter is not active, so nothing more can be done on it.", "locked": "After the first event, the amount and expiry change only by an extension or a reduction.",
        "event_date": "Type the event date (not before the issue date).", "new_expiry": "The new expiry must be after the current one ({current}).",
        "new_amount": "The new amount must be above zero and below the current one ({current}).",
        "days": "The alert days must be between 1 and {max}.", "denied": "You do not have the permission to manage letters of guarantee.",
        "note_required": "Type the reason for cancelling.",
    },
}


def today():
    """The one place «today» comes from, so a test can fix the date."""

    return timezone.localdate()


def _lang(lang):
    return "en" if lang == "en" else "ar"


# ---- who ------------------------------------------------------------------------------------------------------------


def can_manage(user):
    return any(user_has_permission(user, code) for code in MANAGE)


def can_see_card(user):
    """The project page's guarantees card: managers with amounts, a site engineer without."""

    return can_manage(user) or user_has_permission(user, VIEW_PROJECTS)


def can_set_days(user):
    """The alert days are one setting for the whole business: the owner's and the manager's (cashbox master data)."""

    return user_has_permission(user, SET_DAYS)


def _require(user, lang):
    if not can_manage(user):
        raise PermissionDenied(MESSAGES[_lang(lang)]["denied"])


# ---- what -----------------------------------------------------------------------------------------------------------


def scoped(queryset=None):
    """The guarantees of the entity being worked in (HG-034; NULL is the main entity)."""

    queryset = Guarantee.objects.all() if queryset is None else queryset
    return entity_scope.scope(queryset, "entity")


def banks():
    """Bank accounts of the entity being worked in, the only issuers a letter may name."""

    from cashboxes.models import Cashbox, CashboxKind

    return entity_scope.cashboxes(Cashbox.objects).filter(kind=CashboxKind.BANK, active=True)


def alert_days():
    from settings_core.models import SystemSetting

    raw = SystemSetting.objects.filter(key=ALERT_DAYS_KEY, active=True).values_list("value", flat=True).first()
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_ALERT_DAYS
    return days if 1 <= days <= MAX_ALERT_DAYS else DEFAULT_ALERT_DAYS


def save_alert_days(raw, user, lang="ar"):
    from settings_core.models import SystemSetting

    if not can_set_days(user):
        raise PermissionDenied(MESSAGES[_lang(lang)]["denied"])
    words = MESSAGES[_lang(lang)]
    try:
        days = int(str(raw or "").strip())
    except ValueError:
        raise ValidationError(words["days"].format(max=MAX_ALERT_DAYS))
    if not 1 <= days <= MAX_ALERT_DAYS:
        raise ValidationError(words["days"].format(max=MAX_ALERT_DAYS))
    before = alert_days()
    SystemSetting.objects.update_or_create(key=ALERT_DAYS_KEY, defaults={
        "value": str(days), "data_type": SystemSetting.DataType.INTEGER, "active": True,
        "description": "HG-123: how many days before its expiry a letter of guarantee is listed as expiring."})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="guarantees", action="save_alert_days",
                            object_type="settings_core.SystemSetting", object_id=ALERT_DAYS_KEY, before_data={"days": before}, after_data={"days": days})
    return days


def expiring(queryset, on=None, days=None):
    """Active guarantees whose expiry is between ``on`` and N days after it, both days included."""

    on = on or today()
    days = alert_days() if days is None else days
    return queryset.filter(status=GuaranteeStatus.ACTIVE, expires_on__gte=on, expires_on__lte=on + timedelta(days=days))


def expired(queryset, on=None):
    """Active guarantees whose expiry has passed: the bank may refuse to pay them, or the owner may call them."""

    return queryset.filter(status=GuaranteeStatus.ACTIVE, expires_on__lt=on or today())


def alert(guarantee, on=None, days=None):
    """``"expired"``, ``"expiring"`` or ``""`` for one guarantee."""

    if guarantee.status != GuaranteeStatus.ACTIVE:
        return ""
    on = on or today()
    days = alert_days() if days is None else days
    left = (guarantee.expires_on - on).days
    if left < 0:
        return "expired"
    return "expiring" if left <= days else ""


def counts(on=None, days=None):
    """The alerts of the entity being worked in, for the dashboard tile."""

    queryset = scoped()
    return {"expiring": expiring(queryset, on, days).count(), "expired": expired(queryset, on).count(),
            "active": queryset.filter(status=GuaranteeStatus.ACTIVE).count()}


def margin_amount(guarantee):
    """The memo cash margin, from the current amount."""

    if guarantee.margin_percent is None:
        return None
    return money_round(guarantee.amount * guarantee.margin_percent / Decimal("100"))


def issued_amount(guarantee):
    """The amount the letter was issued for: before its first reduction, or its amount if never reduced."""

    first = guarantee.events.filter(kind=EventKind.REDUCE).order_by("pk").first()
    return first.previous_amount if first else guarantee.amount


def first_expiry(guarantee):
    first = guarantee.events.filter(kind=EventKind.EXTEND).order_by("pk").first()
    return first.previous_expiry if first else guarantee.expires_on


def label(group, value, lang="ar"):
    return LABELS[_lang(lang)][group].get(value, value)


# ---- the advance payment guarantee ----------------------------------------------------------------------------------


def advance_suggestion(guarantee):
    """For an active advance payment guarantee on a project: the project's advance received and left, and what to do.

    ``action`` is ``"reduce"`` (to ``amount``, the advance left) when less is left than the guarantee covers, ``"release"``
    when an advance was received and is fully recovered, or ``""``. Shown only; nothing is applied here."""

    if guarantee.kind != GuaranteeKind.ADVANCE or guarantee.project_id is None or guarantee.status != GuaranteeStatus.ACTIVE:
        return None
    from projects.contract import position

    figures = position(guarantee.project)
    left, received = figures["advance_left"], figures["advance"]
    action, amount = "", None
    if left > 0 and left < guarantee.amount:
        action, amount = "reduce", left
    elif left <= 0 and received > 0:
        action = "release"
    return {"received": received, "left": left, "action": action, "amount": amount}


# ---- writing --------------------------------------------------------------------------------------------------------


def _decimal(raw, words, key, allow_zero=False, optional=False):
    text = str(raw if raw is not None else "").strip().replace(",", "").replace("٬", "")
    if not text:
        if optional:
            return None
        raise ValidationError(words[key])
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValidationError(words[key])
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ValidationError(words[key])
    return number


def _date(raw, words, key, optional=False):
    if hasattr(raw, "year"):
        return raw
    value = parse_date(str(raw or "").strip()) if raw else None
    if value is None and not optional:
        raise ValidationError(words[key])
    return value


def _audit(user, action, guarantee, before=None, after=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="guarantees", action=action, object_type="guarantees.Guarantee",
                            object_id=str(guarantee.pk), before_data=before or {}, after_data=after or {})


def _snapshot(guarantee):
    return {"kind": guarantee.kind, "number": guarantee.number, "bank": guarantee.bank_id, "beneficiary": guarantee.beneficiary_id,
            "project": guarantee.project_id, "amount": str(guarantee.amount), "currency": guarantee.currency,
            "issued_on": guarantee.issued_on.isoformat(), "expires_on": guarantee.expires_on.isoformat(),
            "margin_percent": None if guarantee.margin_percent is None else str(guarantee.margin_percent),
            "commission": None if guarantee.commission is None else str(guarantee.commission), "status": guarantee.status, "notes": guarantee.notes}


@transaction.atomic
def save_guarantee(data, user, guarantee=None, lang="ar"):
    """Add a guarantee, or correct one that is still active. Once it has an event, its amount, issue and expiry dates
    change only through ``extend`` and ``reduce``, so the history stays true."""

    from master_data.models import Customer
    from projects.models import Project

    _require(user, lang)
    words = MESSAGES[_lang(lang)]
    if guarantee is not None:
        guarantee = Guarantee.objects.select_for_update().get(pk=guarantee.pk)
        if guarantee.status != GuaranteeStatus.ACTIVE:
            raise ValidationError(words["not_active"])
    kind = str(data.get("kind") or "")
    if kind not in GuaranteeKind.values:
        raise ValidationError(words["kind"])
    number = str(data.get("number") or "").strip()[:60]
    if not number:
        raise ValidationError(words["number"])
    bank = data.get("bank")
    if bank is None or not banks().filter(pk=bank.pk).exists():
        raise ValidationError(words["bank"])
    beneficiary = data.get("beneficiary")
    if beneficiary is None or not isinstance(beneficiary, Customer):
        raise ValidationError(words["beneficiary"])
    project = data.get("project")
    if project is not None and not isinstance(project, Project):
        raise ValidationError(words["project"])
    amount = money_round(_decimal(data.get("amount"), words, "amount"))
    if amount >= AMOUNT_LIMIT:
        raise ValidationError(words["too_big"])
    issued_on = _date(data.get("issued_on"), words, "issued")
    expires_on = _date(data.get("expires_on"), words, "expires")
    if expires_on < issued_on:
        raise ValidationError(words["order"])
    margin = _decimal(data.get("margin_percent"), words, "margin", allow_zero=True, optional=True)
    if margin is not None:
        if margin > 100:
            raise ValidationError(words["margin"])
        margin = margin.quantize(Decimal("0.01"))
    commission = _decimal(data.get("commission"), words, "commission", allow_zero=True, optional=True)
    if commission is not None:
        commission = money_round(commission)
        if commission >= AMOUNT_LIMIT:
            raise ValidationError(words["too_big"])
    duplicate = Guarantee.objects.filter(bank=bank, number__iexact=number).exclude(status=GuaranteeStatus.CANCELLED)
    if guarantee is not None:
        duplicate = duplicate.exclude(pk=guarantee.pk)
    if duplicate.exists():
        raise ValidationError(words["duplicate"])
    if guarantee is not None and guarantee.events.exists() and (
            amount != guarantee.amount or expires_on != guarantee.expires_on or issued_on != guarantee.issued_on):
        raise ValidationError(words["locked"])
    before = _snapshot(guarantee) if guarantee is not None else None
    target = guarantee or Guarantee(created_by=user)
    target.kind, target.number, target.bank, target.beneficiary, target.project = kind, number, bank, beneficiary, project
    target.amount, target.issued_on, target.expires_on = amount, issued_on, expires_on
    target.margin_percent, target.commission = margin, commission
    target.currency = (bank.currency or "EGP")[:10]
    target.notes = str(data.get("notes") or "").strip()[:2000]
    target.entity = bank.entity
    target.save()
    _audit(user, "update" if before else "create", target, before, _snapshot(target), AuditEventType.UPDATE if before else AuditEventType.CREATE)
    return target


def _locked(guarantee, user, lang):
    _require(user, lang)
    locked = Guarantee.objects.select_for_update().get(pk=guarantee.pk)
    if locked.status != GuaranteeStatus.ACTIVE:
        raise ValidationError(MESSAGES[_lang(lang)]["not_active"])
    return locked


def _event_date(guarantee, raw, words):
    day = _date(raw, words, "event_date", optional=True) if raw not in (None, "") else today()
    if day is None or day < guarantee.issued_on:
        raise ValidationError(words["event_date"])
    return day


def _record(guarantee, user, kind, day, note, new_expiry=None, new_amount=None):
    event = GuaranteeEvent.objects.create(guarantee=guarantee, kind=kind, event_date=day, previous_expiry=guarantee.expires_on,
                                          previous_amount=guarantee.amount, new_expiry=new_expiry, new_amount=new_amount,
                                          note=str(note or "").strip()[:255], user=user)
    return event


@transaction.atomic
def extend(guarantee, user, new_expiry, event_date=None, note="", lang="ar"):
    words = MESSAGES[_lang(lang)]
    guarantee = _locked(guarantee, user, lang)
    day = _event_date(guarantee, event_date, words)
    new_expiry = _date(new_expiry, words, "expires")
    if new_expiry <= guarantee.expires_on:
        raise ValidationError(words["new_expiry"].format(current=guarantee.expires_on.isoformat()))
    before = _snapshot(guarantee)
    event = _record(guarantee, user, EventKind.EXTEND, day, note, new_expiry=new_expiry)
    guarantee.expires_on = new_expiry
    guarantee.save(update_fields=["expires_on", "updated_at"])
    _audit(user, "extend", guarantee, before, _snapshot(guarantee))
    return event


@transaction.atomic
def reduce(guarantee, user, new_amount, event_date=None, note="", lang="ar"):
    words = MESSAGES[_lang(lang)]
    guarantee = _locked(guarantee, user, lang)
    day = _event_date(guarantee, event_date, words)
    message = words["new_amount"].format(current=f"{guarantee.amount:,.2f}")
    try:
        new_amount = money_round(_decimal(new_amount, words, "amount"))
    except ValidationError:
        raise ValidationError(message)
    if not ZERO < new_amount < guarantee.amount:
        raise ValidationError(message)
    before = _snapshot(guarantee)
    event = _record(guarantee, user, EventKind.REDUCE, day, note, new_amount=new_amount)
    guarantee.amount = new_amount
    guarantee.save(update_fields=["amount", "updated_at"])
    _audit(user, "reduce", guarantee, before, _snapshot(guarantee))
    return event


def _close(guarantee, user, kind, status, event_date, note, lang, note_required=False):
    words = MESSAGES[_lang(lang)]
    guarantee = _locked(guarantee, user, lang)
    day = _event_date(guarantee, event_date, words)
    if note_required and not str(note or "").strip():
        raise ValidationError(words["note_required"])
    before = _snapshot(guarantee)
    event = _record(guarantee, user, kind, day, note)
    guarantee.status = status
    guarantee.save(update_fields=["status", "updated_at"])
    _audit(user, kind, guarantee, before, _snapshot(guarantee))
    return event


@transaction.atomic
def release(guarantee, user, event_date=None, note="", lang="ar"):
    """The bank's promise ends: the letter came back or the owner wrote it off. Nothing moves."""

    return _close(guarantee, user, EventKind.RELEASE, GuaranteeStatus.RELEASED, event_date, note, lang)


@transaction.atomic
def liquidate(guarantee, user, event_date=None, note="", lang="ar"):
    """The owner called the letter. Only the event is recorded: the money the bank pays out of the account is an
    ordinary bank cash-out the user records on the bank's own screen."""

    return _close(guarantee, user, EventKind.LIQUIDATE, GuaranteeStatus.LIQUIDATED, event_date, note, lang)


@transaction.atomic
def cancel(guarantee, user, event_date=None, note="", lang="ar"):
    """Recorded by mistake or never issued. A reason is required."""

    return _close(guarantee, user, EventKind.CANCEL, GuaranteeStatus.CANCELLED, event_date, note, lang, note_required=True)


def project_guarantees(project):
    """A project's guarantees in the entity being worked in, active first, by expiry."""

    rows = list(scoped(Guarantee.objects.select_related("bank", "beneficiary")).filter(project=project).exclude(status=GuaranteeStatus.CANCELLED))
    rows.sort(key=lambda g: (g.status != GuaranteeStatus.ACTIVE, g.expires_on, g.pk))
    return rows
