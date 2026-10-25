"""T3 (MARKET-PARITY): bank accounts and matching their statements (HG-055).

A bank account is a cashbox of kind ``bank``: money reaches it and leaves it
through the same movements as any cashbox (a customer paying by transfer, a
supplier paid from the bank, a transfer from the cash drawer). Its statement
from the bank is uploaded as a CSV or Excel file and each line is matched to
its movement: by itself when the amount is the same and the date is within
``MATCH_DAYS`` days, or by hand. Matching never changes an amount or a
balance. A fee the bank took that is not in the books yet is recorded from its
line as a bank fee (an ordinary cashbox operation) and matched in the same step.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from cashboxes.models import Cashbox, CashboxKind, CashboxMovement, CashboxOperationType
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import BankStatement, StatementLine

ZERO = Decimal("0.00")
VIEW = ("cashboxes.view_finance",)
WORK = "cashboxes.move_cash"
#: A statement line and a movement of the same amount are the same money when their dates are this close.
MATCH_DAYS = 3
MAX_LINES = 3000

#: Column names a bank's export uses, after the import screen's normalising (lower case, spaces to _).
HEADERS = {
    "date": ("date", "التاريخ", "تاريخ", "value_date", "تاريخ_الحركة", "تاريخ_القيمة", "transaction_date", "posting_date", "booking_date"),
    "description": ("description", "البيان", "الوصف", "details", "narrative", "التفاصيل", "particulars", "memo"),
    "reference": ("reference", "ref", "المرجع", "رقم_المرجع", "رقم_العملية", "cheque_no", "رقم_الشيك"),
    "amount": ("amount", "المبلغ", "القيمة"),
    "debit": ("debit", "مدين", "سحب", "withdrawal", "withdrawals", "منصرف", "خصم"),
    "credit": ("credit", "دائن", "إيداع", "ايداع", "deposit", "deposits", "وارد", "إضافة"),
    "balance": ("balance", "الرصيد", "running_balance"),
}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")

MESSAGES = {
    "ar": {
        "not_bank": "ده مش حساب بنكي.", "file": "ارفع كشف الحساب ملف CSV أو Excel.", "file_too_big": "الملف أكبر من 5 ميجا.",
        "file_unreadable": "الملف ده مش مقروء؛ احفظه CSV أو Excel وجرب تاني.", "columns": "الملف لازم يكون فيه عمود التاريخ، وعمود المبلغ أو عمودين مدين ودائن.",
        "row": "سطر {n}: {problem}", "bad_date": "التاريخ مش مفهوم ({value})", "bad_amount": "المبلغ مش مفهوم ({value})", "empty": "الكشف مفيهوش ولا سطر.",
        "too_many": "الكشف أكبر من {n} سطر؛ قسّمه على شهور.", "other_account": "الحركة دي على حساب تاني.", "amount": "المبلغ مش زي الكشف؛ لو فيه فرق سجّله الأول.",
        "taken": "الحركة دي متطابقة مع سطر تاني.", "matched": "السطر ده متطابق بالفعل.", "not_fee": "ده مش مبلغ اتخصم من الحساب.",
        "fee_reason": "مصروفات بنكية", "closing": "رصيد آخر الكشف لازم رقم.",
    },
    "en": {
        "not_bank": "This is not a bank account.", "file": "Upload the statement as a CSV or Excel file.", "file_too_big": "The file is over 5 MB.",
        "file_unreadable": "This file cannot be read; save it as CSV or Excel and try again.",
        "columns": "The file needs a date column, and an amount column or debit and credit columns.",
        "row": "Row {n}: {problem}", "bad_date": "the date is not understood ({value})", "bad_amount": "the amount is not understood ({value})",
        "empty": "The statement has no lines.", "too_many": "The statement has more than {n} lines; split it by month.",
        "other_account": "This movement is on another account.", "amount": "The amount differs from the statement; record the difference first.",
        "taken": "This movement is already matched to another line.", "matched": "This line is already matched.",
        "not_fee": "This is not money taken from the account.", "fee_reason": "Bank charges", "closing": "The statement's closing balance must be a number.",
    },
}


# ---- who may do what ----

def can_view(user):
    return all(user_has_permission(user, code) for code in VIEW)


def can_work(user):
    return can_view(user) and user_has_permission(user, WORK)


def _require_work(user):
    if not can_work(user):
        raise PermissionDenied("Matching a bank statement needs cashboxes.move_cash and cashboxes.view_finance.")


def accounts():
    """The bank accounts of the entity being worked in."""

    return entity_scope.cashboxes(Cashbox.objects).filter(kind=CashboxKind.BANK, active=True)


def statements(cashbox):
    return BankStatement.objects.filter(cashbox=cashbox)


# ---- balances ----

def signed(movement):
    return movement.amount if movement.direction == "in" else -movement.amount


def balance(cashbox, as_of=None):
    """Opening balance plus every movement up to ``as_of``: the books' figure, the same formula as the cashbox report."""

    rows = cashbox.movements.all()
    if as_of is not None:
        rows = rows.filter(movement_date__lte=as_of)
    money_in = rows.filter(direction="in").aggregate(t=Sum("amount"))["t"] or ZERO
    money_out = rows.filter(direction="out").aggregate(t=Sum("amount"))["t"] or ZERO
    return cashbox.opening_balance + money_in - money_out


def _live(movements):
    """Movements that still stand: neither a reversal nor reversed (a cancelled operation nets to nothing)."""

    return movements.filter(reversal_of__isnull=True, reversal_movements__isnull=True)


# ---- reading the bank's file ----

def _column(row, key):
    for name in HEADERS[key]:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return ""


def _has(rows, key):
    return any(name in rows[0] for name in HEADERS[key])


def parse_amount(raw):
    """A bank's way of writing money: thousands commas, a currency word, (brackets) or a minus sign for money out."""

    text = str(raw or "").translate(ARABIC_DIGITS).strip().replace(",", "")
    found = re.search(r"\d+(?:\.\d+)?", text)
    if not found:
        return None
    try:
        value = Decimal(found.group()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    negative = (text.startswith("(") and text.endswith(")")) or "-" in text
    return -value if negative else value


def parse_date(raw):
    text = str(raw or "").translate(ARABIC_DIGITS).strip()
    text = re.split(r"[ T]", text, maxsplit=1)[0]
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def read_statement(uploaded, lang="ar"):
    """[{date, description, reference, amount}] and the last printed balance (or None), from the bank's CSV or Excel file."""

    from imports.screen_services import read_upload

    words = MESSAGES[lang]
    if uploaded is None:
        raise ValidationError(words["file"])
    try:
        rows = read_upload(uploaded)
    except ValidationError as exc:
        code = exc.messages[0] if exc.messages else "file_unreadable"
        raise ValidationError(words.get(code, words["file_unreadable"]))
    if not rows:
        raise ValidationError(words["empty"])
    if len(rows) > MAX_LINES:
        raise ValidationError(words["too_many"].format(n=MAX_LINES))
    if not _has(rows, "date") or not (_has(rows, "amount") or _has(rows, "debit") or _has(rows, "credit")):
        raise ValidationError(words["columns"])
    lines, problems, closing = [], [], None
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        day = parse_date(_column(row, "date"))
        if day is None:
            problems.append(words["row"].format(n=number, problem=words["bad_date"].format(value=_column(row, "date") or "—")))
            continue
        if _column(row, "amount"):
            amount = parse_amount(_column(row, "amount"))
            raw = _column(row, "amount")
        else:
            money_in, money_out = parse_amount(_column(row, "credit")) or ZERO, parse_amount(_column(row, "debit")) or ZERO
            amount, raw = abs(money_in) - abs(money_out), f"{_column(row, 'credit')}/{_column(row, 'debit')}"
        if amount is None:
            problems.append(words["row"].format(n=number, problem=words["bad_amount"].format(value=raw)))
            continue
        if not amount:
            continue  # a balance-only line ("brought forward")
        lines.append({"date": day, "description": str(_column(row, "description"))[:255], "reference": str(_column(row, "reference"))[:80], "amount": amount})
        printed = parse_amount(_column(row, "balance"))
        if printed is not None:
            closing = printed
    if problems:
        raise ValidationError(problems[:5])
    if not lines:
        raise ValidationError(words["empty"])
    return lines, closing


# ---- importing and matching ----

def _audit(user, action, obj, after):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="banking", action=action, object_type=f"banking.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


@transaction.atomic
def import_statement(cashbox, uploaded, user, lang="ar", closing_balance=""):
    """Keep the statement's lines and match what can be matched by itself. Returns (statement, how many matched)."""

    _require_work(user)
    words = MESSAGES[lang]
    if cashbox.kind != CashboxKind.BANK:
        raise ValidationError(words["not_bank"])
    lines, printed = read_statement(uploaded, lang)
    if str(closing_balance or "").strip():
        printed = parse_amount(closing_balance)
        if printed is None:
            raise ValidationError(words["closing"])
    first, last = min(line["date"] for line in lines), max(line["date"] for line in lines)
    statement = BankStatement.objects.create(cashbox=cashbox, name=f"{first.isoformat()} → {last.isoformat()}", closing_balance=printed, imported_by=user)
    StatementLine.objects.bulk_create(StatementLine(statement=statement, line_date=line["date"], description=line["description"],
                                                    reference=line["reference"], amount=line["amount"]) for line in lines)
    matched = auto_match(statement, user)
    _audit(user, "import_statement", statement, {"cashbox_id": cashbox.pk, "lines": len(lines), "matched": matched,
                                                  "closing_balance": str(printed) if printed is not None else None})
    return statement, matched


def candidates(line, exclude=(), days=MATCH_DAYS):
    """Unmatched movements of the line's account with the same signed amount and a date within ``days``, nearest first."""

    direction, amount = ("in", line.amount) if line.amount > 0 else ("out", -line.amount)
    rows = _live(CashboxMovement.objects.filter(cashbox_id=line.statement.cashbox_id, direction=direction, amount=amount,
                                                movement_date__gte=line.line_date - timedelta(days=days),
                                                movement_date__lte=line.line_date + timedelta(days=days),
                                                statement_line__isnull=True)).exclude(pk__in=list(exclude))
    return sorted(rows, key=lambda movement: (abs((movement.movement_date - line.line_date).days), movement.pk))


@transaction.atomic
def auto_match(statement, user):
    _require_work(user)
    taken, matched, now = set(), 0, timezone.now()
    for line in statement.lines.select_for_update().filter(movement__isnull=True).select_related("statement"):
        found = candidates(line, taken)
        if not found:
            continue
        line.movement, line.matched_by, line.matched_at = found[0], user, now
        line.save(update_fields=["movement", "matched_by", "matched_at"])
        taken.add(found[0].pk)
        matched += 1
    return matched


@transaction.atomic
def match(line, movement, user, lang="ar"):
    _require_work(user)
    words = MESSAGES[lang]
    line = StatementLine.objects.select_for_update().select_related("statement").get(pk=line.pk)
    if line.movement_id:
        raise ValidationError(words["matched"])
    if movement.cashbox_id != line.statement.cashbox_id:
        raise ValidationError(words["other_account"])
    if signed(movement) != line.amount:
        raise ValidationError(words["amount"])
    line.movement, line.matched_by, line.matched_at = movement, user, timezone.now()
    try:
        with transaction.atomic():
            line.save(update_fields=["movement", "matched_by", "matched_at"])
    except IntegrityError:
        raise ValidationError(words["taken"])
    _audit(user, "match_line", line, {"movement_id": movement.pk, "amount": str(line.amount)})
    return line


@transaction.atomic
def unmatch(line, user):
    _require_work(user)
    line = StatementLine.objects.select_for_update().get(pk=line.pk)
    was = line.movement_id
    line.movement, line.matched_by, line.matched_at = None, None, None
    line.save(update_fields=["movement", "matched_by", "matched_at"])
    _audit(user, "unmatch_line", line, {"movement_id": was})
    return line


@transaction.atomic
def record_fee(line, user, lang="ar"):
    """A fee the bank took that is not in the books: recorded as a bank fee on the line's date, then matched to it."""

    from cashboxes.services import create_cashbox_operation

    _require_work(user)
    words = MESSAGES[lang]
    line = StatementLine.objects.select_for_update().select_related("statement__cashbox").get(pk=line.pk)
    if line.movement_id:
        raise ValidationError(words["matched"])
    if line.amount >= 0:
        raise ValidationError(words["not_fee"])
    operation = create_cashbox_operation(f"BANKFEE-{line.statement_id}-{line.pk}", line.line_date, CashboxOperationType.BANK_FEE, -line.amount,
                                         line.description or words["fee_reason"], user, source_cashbox=line.statement.cashbox)
    return match(line, operation.movements.get(), user, lang)


@transaction.atomic
def delete_statement(statement, user):
    """A statement uploaded by mistake goes, with its matches; no amount or balance changes."""

    _require_work(user)
    _audit(user, "delete_statement", statement, {"cashbox_id": statement.cashbox_id, "lines": statement.lines.count()})
    statement.delete()


# ---- the reconciliation ----

def reconciliation(statement):
    """The statement beside the books, up to the statement's last day.

    * ``book``: the account's balance in Hesba on that day;
    * ``outstanding``: movements in the books since the statement began that the bank has not shown yet
      (a deposit on its way, a cheque not cashed);
    * ``unrecorded``: lines on the statement that are not in the books yet (a fee, interest);
    * ``expected``: what the statement should say once both are accounted for (book − outstanding + unrecorded);
    * ``difference``: the statement's closing balance minus ``expected``, when the statement gives one. Zero means it reconciles.
    """

    lines = list(statement.lines.select_related("movement").order_by("line_date", "pk"))
    cashbox = statement.cashbox
    first = min((line.line_date for line in lines), default=timezone.localdate())
    last = max((line.line_date for line in lines), default=timezone.localdate())
    book = balance(cashbox, last)
    outstanding = list(_live(cashbox.movements.filter(movement_date__gte=first - timedelta(days=MATCH_DAYS), movement_date__lte=last,
                                                      statement_line__isnull=True)).order_by("movement_date", "pk"))
    unrecorded = [line for line in lines if line.movement_id is None]
    outstanding_total = sum((signed(movement) for movement in outstanding), ZERO)
    unrecorded_total = sum((line.amount for line in unrecorded), ZERO)
    expected = book - outstanding_total + unrecorded_total
    difference = None if statement.closing_balance is None else statement.closing_balance - expected
    return {"lines": lines, "first": first, "last": last, "book": book, "outstanding": outstanding, "outstanding_total": outstanding_total,
            "unrecorded": unrecorded, "unrecorded_total": unrecorded_total, "expected": expected, "difference": difference,
            "matched": len(lines) - len(unrecorded), "count": len(lines)}


def open_lines(cashbox):
    return StatementLine.objects.filter(statement__cashbox=cashbox, movement__isnull=True).count()


def overview():
    """Each bank account with its balance, its last statement and the lines still open."""

    rows = []
    for cashbox in accounts().order_by("cashbox_code"):
        rows.append({"cashbox": cashbox, "balance": balance(cashbox), "last": statements(cashbox).first(), "open": open_lines(cashbox)})
    return rows
