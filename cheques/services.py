"""T4 (MARKET-PARITY): cheques received and issued (HG-056).

Received: the customer pays by cheque → an ordinary customer payment into the entity's "cheques in hand" box
(``record_customer_payment``, unchanged); the customer's balance goes down that day. Deposited → a transfer from
the box into a bank account. Cleared → the bank confirmed it (no money moves; it is already in the bank).
Bounced → on the bounce date: the deposit is reversed by a transfer back from the bank (when it was deposited),
the cheque leaves the box, and the amount is on the customer's account again.

Issued: we pay a supplier by cheque → an ordinary supplier payment from the "cheques issued" box, whose balance
below zero is what our cheques still owe (notes payable). Cleared → the supplier cashed it: a transfer from the
bank into that box. Bounced → on the bounce date the amount is owed to the supplier again.

Every step is a new row dated the day it happened, in an open period. The payment is never edited, and
``cancel_customer_payment`` / ``cancel_supplier_payment`` (which reverse on the payment's own date) only run when
a cheque is cancelled before anything happened to it.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from cashboxes.models import (Cashbox, CashboxDirection, CashboxKind, CashboxMovement, CashboxMovementType,
                              CashboxOperationType)
from config.money import money_round
from config.numbering import next_in_series
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import Cheque, ChequeDirection, ChequeStatus

RECEIVE = "sales.receive_customer_payment"
ISSUE = "purchases.pay_supplier"
MOVE = "cashboxes.move_cash"
#: The cheques screen warns about cheques falling due within this many days.
DUE_SOON_DAYS = 7

BOX_NAMES = {
    CashboxKind.CHEQUES_IN: ("CHQ-IN", "شيكات في الإيد", "Cheques in hand"),
    CashboxKind.CHEQUES_OUT: ("CHQ-OUT", "شيكات صادرة", "Cheques issued"),
}

MESSAGES = {
    "ar": {
        "number": "اكتب رقم الشيك.", "bank_name": "اكتب اسم البنك اللي عليه الشيك.", "due": "اكتب تاريخ الاستحقاق.", "amount": "المبلغ لازم رقم أكبر من صفر.",
        "date": "التاريخ مش مفهوم.", "party": "اختار {party}.", "customer": "العميل", "supplier": "المورد", "bank": "اختار الحساب البنكي.",
        "twice": "الشيك ده متسجل قبل كده.", "state": "الشيك ده حالته «{state}»؛ الخطوة دي مش ليه.", "reason": "اكتب سبب الارتداد.", "cancel_reason": "اكتب سبب الإلغاء.",
        "before": "التاريخ ده قبل آخر خطوة في الشيك.", "bank_short": "رصيد البنك مش مكفّي لرد الشيك ده.",
        "received_note": "شيك {number} — {bank}، يستحق {due}", "deposit": "إيداع شيك {number}", "bounce_back": "رد شيك {number} مرتد من البنك",
        "bounced": "شيك {number} مرتد: {reason}", "paid": "صرف شيك {number} للمورد",
    },
    "en": {
        "number": "Type the cheque number.", "bank_name": "Type the bank the cheque is drawn on.", "due": "Type the due date.", "amount": "The amount must be a number above zero.",
        "date": "The date is not understood.", "party": "Choose the {party}.", "customer": "customer", "supplier": "supplier", "bank": "Choose the bank account.",
        "twice": "This cheque is already recorded.", "state": "This cheque is “{state}”; this step does not apply.", "reason": "Type why it bounced.",
        "cancel_reason": "Type why it is cancelled.", "before": "This date is before the cheque's last step.", "bank_short": "The bank balance does not cover returning this cheque.",
        "received_note": "Cheque {number} — {bank}, due {due}", "deposit": "Deposit of cheque {number}", "bounce_back": "Cheque {number} returned unpaid by the bank",
        "bounced": "Cheque {number} bounced: {reason}", "paid": "Cheque {number} paid to the supplier",
    },
}


# ---- who may do what ----

def can_view(user):
    return any(user_has_permission(user, code) for code in ("cashboxes.view_finance", RECEIVE, ISSUE))


def can_receive(user):
    return user_has_permission(user, RECEIVE)


def can_issue(user):
    return user_has_permission(user, ISSUE)


def can_move(user):
    return user_has_permission(user, MOVE)


def _require(user, code):
    if not user_has_permission(user, code):
        raise PermissionDenied(f"This cheque step needs {code}.")


def scoped(queryset=None):
    """Cheques of the entity being worked in: the entity of the box they went through (HG-034)."""

    rows = queryset if queryset is not None else Cheque.objects.all()
    return entity_scope.scope(rows, "box__entity")


def banks():
    return entity_scope.cashboxes(Cashbox.objects).filter(kind=CashboxKind.BANK, active=True)


# ---- the two cheque boxes ----

def box(kind, entity=None):
    """The entity's cheques-in-hand or cheques-issued box, made the first time it is needed."""

    from entities.current import current_entity
    from entities.services import main_entity
    from settings_core.models import ClientProfile

    entity = entity or current_entity() or main_entity()
    found = Cashbox.objects.filter(kind=kind, entity=entity).order_by("pk").first()
    if found is not None:
        return found
    prefix, name_ar, name_en = BOX_NAMES[kind]
    code = prefix if not Cashbox.objects.filter(cashbox_code=prefix).exists() else f"{prefix}-{entity.code}"
    profile = ClientProfile.get_active()
    return Cashbox.objects.create(cashbox_code=code, name_ar=name_ar, name_en=name_en, kind=kind, entity=entity,
                                  currency=getattr(profile, "default_currency", None) or "EGP")


def _balance(cashbox):
    from cashboxes.services import get_cashbox_balance

    return get_cashbox_balance(cashbox)


# ---- reading the form ----

def _date(raw, words, default=None):
    if hasattr(raw, "year"):
        return raw
    text = str(raw or "").strip()
    if not text:
        if default is not None:
            return default
        raise ValidationError(words["date"])
    try:
        return timezone.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(words["date"])


def _amount(raw, words):
    try:
        value = money_round(Decimal(str(raw or "").replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["amount"])
    if value <= 0:
        raise ValidationError(words["amount"])
    return value


def _details(data, words):
    number = str(data.get("number") or "").strip()[:40]
    bank_name = str(data.get("bank_name") or "").strip()[:120]
    if not number:
        raise ValidationError(words["number"])
    if not bank_name:
        raise ValidationError(words["bank_name"])
    if not str(data.get("due_date") or "").strip():
        raise ValidationError(words["due"])
    due = _date(data.get("due_date"), words)
    return number, bank_name, due, _amount(data.get("amount"), words), _date(data.get("received_on"), words, timezone.localdate())


def _audit(user, action, cheque, after=None):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE if action != "receive_cheque" else AuditEventType.CREATE, actor=user, module="cheques",
                            action=action, object_type="cheques.Cheque", object_id=str(cheque.pk), before_data={},
                            after_data={"number": cheque.number, "direction": cheque.direction, "status": cheque.status, "amount": str(cheque.amount),
                                        **(after or {})})


def _not_twice(direction, number, bank_name, words):
    if Cheque.objects.filter(direction=direction, number=number, bank_name__iexact=bank_name).exclude(status=ChequeStatus.CANCELLED).exists():
        raise ValidationError(words["twice"])


def _state(cheque, allowed, words, lang):
    if cheque.status not in allowed:
        from settings_core.display_labels import choice_label

        raise ValidationError(words["state"].format(state=choice_label(cheque, "status", lang)))


def _after(cheque, day, words):
    last = max(d for d in (cheque.received_on, cheque.deposited_on, cheque.cleared_on) if d)
    if day < last:
        raise ValidationError(words["before"])


def _locked(cheque):
    return Cheque.objects.select_for_update(of=("self",)).select_related("box", "bank", "customer", "supplier", "customer_payment", "supplier_payment").get(pk=cheque.pk)


# ---- received cheques ----

@transaction.atomic
def receive(customer, data, user, lang="ar"):
    """A customer pays by cheque: their balance goes down today, and the cheque waits in the cheques box."""

    from sales.models import CustomerPayment
    from sales.services import record_customer_payment

    _require(user, RECEIVE)
    words = MESSAGES[lang]
    if customer is None:
        raise ValidationError(words["party"].format(party=words["customer"]))
    number, bank_name, due, amount, received_on = _details(data, words)
    _not_twice(ChequeDirection.IN, number, bank_name, words)
    holder = box(CashboxKind.CHEQUES_IN)
    note = words["received_note"].format(number=number, bank=bank_name, due=due.isoformat())
    payment = record_customer_payment(next_in_series(CustomerPayment, "payment_number", "CHQ-"), received_on, customer, holder, amount,
                                      user=user, notes=note)
    cheque = Cheque.objects.create(direction=ChequeDirection.IN, number=number, bank_name=bank_name, due_date=due, amount=amount, customer=customer,
                                   customer_payment=payment, box=holder, status=ChequeStatus.IN_HAND, received_on=received_on,
                                   notes=str(data.get("notes") or "").strip(), created_by=user)
    _audit(user, "receive_cheque", cheque, {"customer_id": customer.pk, "payment_id": payment.pk})
    return cheque


@transaction.atomic
def deposit(cheque, bank, user, lang="ar", on=None):
    from cashboxes.services import create_cashbox_operation

    _require(user, MOVE)
    words = MESSAGES[lang]
    cheque = _locked(cheque)
    _state(cheque, (ChequeStatus.IN_HAND,), words, lang)
    if bank is None or bank.kind != CashboxKind.BANK:
        raise ValidationError(words["bank"])
    on = _date(on, words, timezone.localdate())
    _after(cheque, on, words)
    cheque.deposit_operation = create_cashbox_operation(f"CHQ-DEP-{cheque.pk}", on, CashboxOperationType.TRANSFER, cheque.amount,
                                                        words["deposit"].format(number=cheque.number), user, source_cashbox=cheque.box, destination_cashbox=bank)
    cheque.bank, cheque.deposited_on, cheque.status = bank, on, ChequeStatus.DEPOSITED
    cheque.save(update_fields=["deposit_operation", "bank", "deposited_on", "status"])
    _audit(user, "deposit_cheque", cheque, {"bank_id": bank.pk, "on": on.isoformat()})
    return cheque


# ---- issued cheques ----

@transaction.atomic
def issue(supplier, bank, data, user, lang="ar"):
    """We pay a supplier by cheque: they are paid today; the cheque is owed until they cash it."""

    from purchases.models import SupplierPayment
    from purchases.services import record_supplier_payment

    _require(user, ISSUE)
    words = MESSAGES[lang]
    if supplier is None:
        raise ValidationError(words["party"].format(party=words["supplier"]))
    if bank is None or bank.kind != CashboxKind.BANK:
        raise ValidationError(words["bank"])
    values = {key: data.get(key) for key in ("number", "bank_name", "due_date", "amount", "received_on", "notes")}  # a QueryDict gives lists to **
    values["bank_name"] = values["bank_name"] or bank.bank_name or bank.name_ar
    number, bank_name, due, amount, issued_on = _details(values, words)
    _not_twice(ChequeDirection.OUT, number, bank_name, words)
    holder = box(CashboxKind.CHEQUES_OUT, bank.entity)
    note = words["received_note"].format(number=number, bank=bank_name, due=due.isoformat())
    payment = record_supplier_payment(next_in_series(SupplierPayment, "payment_number", "CHQ-"), issued_on, supplier, holder, amount,
                                      user=user, notes=note)
    cheque = Cheque.objects.create(direction=ChequeDirection.OUT, number=number, bank_name=bank_name, due_date=due, amount=amount, supplier=supplier,
                                   supplier_payment=payment, box=holder, bank=bank, status=ChequeStatus.ISSUED, received_on=issued_on,
                                   notes=str(data.get("notes") or "").strip(), created_by=user)
    _audit(user, "issue_cheque", cheque, {"supplier_id": supplier.pk, "payment_id": payment.pk, "bank_id": bank.pk})
    return cheque


# ---- clearing, bouncing, cancelling ----

@transaction.atomic
def clear(cheque, user, lang="ar", on=None):
    """Received: the bank confirmed the deposited cheque. Issued: the supplier cashed it, so it leaves our bank."""

    from cashboxes.services import create_cashbox_operation

    _require(user, MOVE)
    words = MESSAGES[lang]
    cheque = _locked(cheque)
    on = _date(on, words, timezone.localdate())
    if cheque.direction == ChequeDirection.IN:
        _state(cheque, (ChequeStatus.DEPOSITED,), words, lang)
        _after(cheque, on, words)
    else:
        _state(cheque, (ChequeStatus.ISSUED,), words, lang)
        _after(cheque, on, words)
        cheque.payment_operation = create_cashbox_operation(f"CHQ-PAY-{cheque.pk}", on, CashboxOperationType.TRANSFER, cheque.amount,
                                                            words["paid"].format(number=cheque.number), user, source_cashbox=cheque.bank, destination_cashbox=cheque.box)
    cheque.cleared_on, cheque.status = on, ChequeStatus.CLEARED
    cheque.save(update_fields=["payment_operation", "cleared_on", "status"])
    _audit(user, "clear_cheque", cheque, {"on": on.isoformat()})
    return cheque


@transaction.atomic
def bounce(cheque, reason, user, lang="ar", on=None):
    """The cheque came back unpaid. Everything is dated the bounce day, in an open period; the payment stays as it was."""

    from cashboxes.services import _ensure_open_period, create_cashbox_operation

    _require(user, MOVE)
    words = MESSAGES[lang]
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError(words["reason"])
    cheque = _locked(cheque)
    on = _date(on, words, timezone.localdate())
    _ensure_open_period(on)
    note = words["bounced"].format(number=cheque.number, reason=reason)[:255]
    if cheque.direction == ChequeDirection.IN:
        from sales.models import CustomerLedgerEntry, CustomerLedgerEntryType

        _state(cheque, (ChequeStatus.IN_HAND, ChequeStatus.DEPOSITED), words, lang)
        _after(cheque, on, words)
        if cheque.status == ChequeStatus.DEPOSITED:
            # The bank takes the money back first, so the bank is not overstated and the box never goes below zero.
            Cashbox.objects.select_for_update().filter(pk=cheque.bank_id).first()
            if _balance(cheque.bank) < cheque.amount:
                raise ValidationError(words["bank_short"])
            cheque.return_operation = create_cashbox_operation(f"CHQ-RET-{cheque.pk}", on, CashboxOperationType.TRANSFER, cheque.amount,
                                                               words["bounce_back"].format(number=cheque.number), user,
                                                               source_cashbox=cheque.bank, destination_cashbox=cheque.box)
        Cashbox.objects.select_for_update().filter(pk=cheque.box_id).first()
        CashboxMovement.objects.create(cashbox=cheque.box, movement_date=on, movement_type=CashboxMovementType.CHEQUE_BOUNCED, direction=CashboxDirection.OUT,
                                       amount=cheque.amount, customer_payment=cheque.customer_payment, description=note, created_by=user)
        CustomerLedgerEntry.objects.create(customer=cheque.customer, entry_date=on, entry_type=CustomerLedgerEntryType.ADJUSTMENT,
                                           customer_payment=cheque.customer_payment, due_increase=cheque.amount, due_decrease=Decimal("0"),
                                           description=note, created_by=user)
    else:
        from purchases.models import SupplierLedgerEntry, SupplierLedgerEntryType

        _state(cheque, (ChequeStatus.ISSUED,), words, lang)
        _after(cheque, on, words)
        CashboxMovement.objects.create(cashbox=cheque.box, movement_date=on, movement_type=CashboxMovementType.CHEQUE_BOUNCED, direction=CashboxDirection.IN,
                                       amount=cheque.amount, supplier_payment=cheque.supplier_payment, description=note, created_by=user)
        SupplierLedgerEntry.objects.create(supplier=cheque.supplier, entry_date=on, entry_type=SupplierLedgerEntryType.ADJUSTMENT,
                                           supplier_payment=cheque.supplier_payment, due_increase=cheque.amount, due_decrease=Decimal("0"),
                                           description=note, created_by=user)
    cheque.bounced_on, cheque.bounce_reason, cheque.status = on, reason, ChequeStatus.BOUNCED
    cheque.save(update_fields=["return_operation", "bounced_on", "bounce_reason", "status"])
    _audit(user, "bounce_cheque", cheque, {"on": on.isoformat(), "reason": reason})
    return cheque


@transaction.atomic
def cancel(cheque, reason, user, lang="ar"):
    """Recorded by mistake, before anything happened to it: the payment is cancelled as any payment is."""

    words = MESSAGES[lang]
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError(words["cancel_reason"])
    cheque = _locked(cheque)
    if cheque.direction == ChequeDirection.IN:
        from sales.services import cancel_customer_payment

        _require(user, RECEIVE)
        _state(cheque, (ChequeStatus.IN_HAND,), words, lang)
        cheque.status = ChequeStatus.CANCELLED
        cheque.save(update_fields=["status"])
        cancel_customer_payment(cheque.customer_payment_id, user=user, reason=reason)
    else:
        from purchases.services import cancel_supplier_payment

        _require(user, ISSUE)
        _state(cheque, (ChequeStatus.ISSUED,), words, lang)
        cheque.status = ChequeStatus.CANCELLED
        cheque.save(update_fields=["status"])
        cancel_supplier_payment(cheque.supplier_payment_id, user=user, reason=reason)
    _audit(user, "cancel_cheque", cheque, {"reason": reason})
    return cheque


# ---- what is due ----

def owns_operation(operation_id):
    """Whether a cashbox operation is a cheque's deposit, payment or return: only the cheques screen reverses it."""

    from django.db.models import Q

    return Cheque.objects.filter(Q(deposit_operation_id=operation_id) | Q(payment_operation_id=operation_id) | Q(return_operation_id=operation_id)).exists()


def due_soon(today=None, days=DUE_SOON_DAYS):
    """Cheques in hand to deposit, and cheques issued the bank must cover, falling due within ``days`` (or already due)."""

    today = today or timezone.localdate()
    horizon = today + timedelta(days=days)
    return scoped().filter(due_date__lte=horizon, status__in=(ChequeStatus.IN_HAND, ChequeStatus.ISSUED)).select_related("customer", "supplier", "bank")


def totals():
    rows = scoped()
    held = sum((c.amount for c in rows.filter(status=ChequeStatus.IN_HAND)), Decimal("0"))
    issued = sum((c.amount for c in rows.filter(status=ChequeStatus.ISSUED)), Decimal("0"))
    return {"in_hand": money_round(held), "issued": money_round(issued)}
