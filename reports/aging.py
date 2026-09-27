"""DUE-001: who owes what, since when. Read-only aging from the party ledgers.

Each customer's (or supplier's) balance is split by age with FIFO: every
payment, return or other decrease settles the oldest open amount first, which
is how a shop reasons about "the oldest invoice is paid first". The total of
the buckets always equals the balance the customer report shows, because both
read the same opening balance and ledger rows.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from config.money import money_round
from master_data.models import Customer, Supplier
from purchases.models import SupplierLedgerEntry
from sales.models import CustomerLedgerEntry
from settings_core.models import SystemSetting


BUCKETS = (("b0_30", 0, 30), ("b31_60", 31, 60), ("b61_90", 61, 90), ("b90_plus", 91, None))
CREDIT_DAYS_KEY = "credit.default_days"
DEFAULT_CREDIT_DAYS = 30
ZERO = Decimal("0.00")


def credit_days():
    value = SystemSetting.objects.filter(key=CREDIT_DAYS_KEY, active=True).values_list("value", flat=True).first()
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CREDIT_DAYS
    return days if 0 <= days <= 365 else DEFAULT_CREDIT_DAYS


def set_credit_days(days, user=None):
    from audit.models import AuditEventType, AuditLog

    days = int(days)
    if not 0 <= days <= 365:
        raise ValueError("Credit days must be between 0 and 365.")
    before = credit_days()
    SystemSetting.objects.update_or_create(key=CREDIT_DAYS_KEY, defaults={"value": str(days), "data_type": SystemSetting.DataType.INTEGER, "active": True, "description": "Days a credit sale may stay unpaid before it counts as overdue"})
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="reports", action="set_credit_days", object_type="SystemSetting", object_id=CREDIT_DAYS_KEY, before_data={"days": before}, after_data={"days": days})
    return days


def _fifo(opening, opening_date, increases, decreases):
    """Open (date, amount) pieces after decreases settle the oldest first."""

    debits = []
    credit = Decimal(decreases)
    if opening > 0:
        debits.append([opening_date, Decimal(opening)])
    elif opening < 0:
        credit += -Decimal(opening)
    debits.extend([day, Decimal(amount)] for day, amount in sorted(increases))
    debits.sort(key=lambda piece: piece[0])
    for piece in debits:
        if credit <= 0:
            break
        used = min(piece[1], credit)
        piece[1] -= used
        credit -= used
    return [(day, amount) for day, amount in debits if amount > 0], credit


def _bucket_of(age):
    for key, low, high in BUCKETS:
        if age >= low and (high is None or age <= high):
            return key
    return BUCKETS[0][0]


def aging_rows(kind="customers", as_of=None):
    """One row per party with a balance: buckets, overdue, oldest date, contact."""

    as_of = as_of or date.today()
    terms = credit_days()
    if kind == "suppliers":
        parties = Supplier.objects.filter(active=True)
        entries = SupplierLedgerEntry.objects.filter(entry_date__lte=as_of, supplier__active=True).values_list("supplier_id", "entry_date", "due_increase", "due_decrease")
    else:
        parties = Customer.objects.filter(active=True)
        entries = CustomerLedgerEntry.objects.filter(entry_date__lte=as_of, customer__active=True).values_list("customer_id", "entry_date", "due_increase", "due_decrease")
    increases, decreases, last_decrease = defaultdict(list), defaultdict(Decimal), {}
    for party_id, day, inc, dec in entries:
        if inc:
            increases[party_id].append((day, inc))
        if dec:
            decreases[party_id] += dec
            last_decrease[party_id] = max(day, last_decrease.get(party_id, day))

    rows = []
    for party in parties:
        opening_date = party.created_at.date() if party.created_at else as_of
        pieces, left_credit = _fifo(party.opening_balance, min(opening_date, as_of), increases.get(party.pk, []), decreases.get(party.pk, Decimal("0")))
        buckets = {key: ZERO for key, *_ in BUCKETS}
        overdue = ZERO
        for day, amount in pieces:
            age = (as_of - day).days
            buckets[_bucket_of(age)] += amount
            if age > terms:
                overdue += amount
        total = money_round(sum(buckets.values(), ZERO))
        if total == 0 and left_credit == 0:
            continue
        rows.append({
            "party": party,
            "code": getattr(party, "customer_code", None) or getattr(party, "supplier_code", ""),
            "name": party.name,
            "phone": party.phone,
            "whatsapp": party.whatsapp or party.phone,
            **{key: money_round(value) for key, value in buckets.items()},
            "total": total,
            "credit": money_round(left_credit),
            "overdue": money_round(overdue),
            "oldest": min((day for day, _ in pieces), default=None),
            "last_payment": last_decrease.get(party.pk),
        })
    rows.sort(key=lambda row: (-row["overdue"], -row["total"]))
    return rows


def aging_totals(rows):
    keys = [key for key, *_ in BUCKETS] + ["total", "overdue", "credit"]
    return {key: money_round(sum((row[key] for row in rows), ZERO)) for key in keys}


def whatsapp_number(raw):
    """International digits for wa.me from what shops type (Egyptian 01… numbers)."""

    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not digits:
        return ""
    if (raw or "").strip().startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("01") and len(digits) == 11:
        return "20" + digits[1:]
    return digits


def reminder_link(row, company_name, currency, lang="ar"):
    number = whatsapp_number(row["whatsapp"])
    if not number:
        return ""
    amount = f"{row['overdue'] or row['total']:,.2f} {currency}"
    if lang == "en":
        text = f"Hello {row['name']}, this is a friendly reminder from {company_name} about the amount due: {amount}. Thank you."
    else:
        text = f"أهلاً {row['name']}، ده تذكير ودّي من {company_name} بالمبلغ المستحق: {amount}. شكرًا لحضرتك."
    return f"https://wa.me/{number}?text={quote(text)}"
