"""Contracting C11 (HG-128): a subcontract's own bill of quantities, bills by quantities, and the advance paid to the
subcontractor. It mirrors the client side (``contract.py``) and reuses its arithmetic (``return_shares``,
``held_events``, ``lowest_held``) rather than copying it.

* **Subcontract lines:** the work given, each with a quantity and a rate, optionally covering one item of the client's
  bill of quantities. Their total may not pass the subcontract's value.
* **Bill by quantities:** the input is the cumulative quantity done to date per line. This bill = cumulative − what
  live bills already billed on the line, net of the posted purchase returns made by the bill's date. It is one draft
  purchase invoice with one line per subcontract line, through the unchanged ``create_purchase_draft_with_tax``.
  A subcontract without lines keeps the lump-sum bill (``costs.bill_subcontract``).
* **Caps (owner question 7):** a cumulative above the line's quantity is refused; a billed gross above the
  subcontract's value is refused (raise the value first: that is the subcontract's variation); a cumulative above what
  the owner has been certified for on the linked client item is a warning, never a refusal.
* **Advance:** an ordinary supplier payment (``purchases.services.record_supplier_payment``, unchanged) marked as the
  subcontractor's advance. Each bill recovers min(gross × rate, the advance left on its date and every later date,
  gross − retention). The general ledger keeps the advance in «دفعات مقدمة لمقاولي الباطن» (1112) until bills
  recover it; cancelling an advance a bill already recovered is refused (``guards.after_supplier_payment_change``).

Net payable on a bill = its invoice total − retention − recovery (VAT is on the full gross).
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from . import services
from .models import (
    BoqLine, CertificateLine, Subcontract, SubcontractBillLine, SubcontractLine, SubcontractPayment, SubcontractPaymentKind,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
QTY = Decimal("0.001")

MESSAGES = {
    "ar": {
        "description": "اكتب وصف البند.", "qty": "الكمية لازم أكبر من صفر.", "price": "اكتب فئة البند (سعر الوحدة) رقم صفر أو أكبر.",
        "boq_line": "اختار بند من مقايسة المشروع ده.",
        "value_needed": "اكتب قيمة عقد مقاول الباطن الأول: مجموع البنود لازم ميزيدش عنها.",
        "lines_over_value": "مجموع بنود مقاول الباطن ({total}) هيبقى أكبر من قيمة العقد ({value})؛ زوّد قيمة العقد الأول.",
        "rate_locked": "البند ده اتعمل عليه مستخلص، ففئته ثابتة.", "below_billed": "مينفعش كمية البند تقل عن اللي اتعمل بيه مستخلصات ({qty}).",
        "line_used": "البند ده اتعمل عليه مستخلص ومينفعش يتمسح.",
        "lines_after_lump": "مقاول الباطن ده اتعمل له مستخلص بمبلغ مقطوع؛ مينفعش تضيف بنود بعده عشان الأعمال اللي اتفوترت متتفوترش تاني.",
        "no_lines": "مقاول الباطن ده مفيش له بنود: اعمل المستخلص بمبلغ مقطوع.",
        "one_draft": "فيه مستخلص لمقاول الباطن لسه مسودة ({number}): رحّله أو اسحبه الأول.",
        "below_previous": "الكمية حتى تاريخه للبند {code} أقل من اللي اتعمل بيه مستخلصات قبل كده ({qty}).",
        "over_line": "الكمية حتى تاريخه للبند {code} ({qty}) أكبر من كميته في عقد مقاول الباطن ({limit}).",
        "no_work": "مفيش كميات جديدة في المستخلص ده.",
        "over_value": "المستخلص ده هيخلّي اللي اتعمل بيه مستخلصات ({billed}) أكبر من قيمة عقد مقاول الباطن ({value})؛ لو فيه أعمال زيادة زوّد قيمة العقد الأول.",
        "date_order": "تاريخ المستخلص لازم ميكونش قبل آخر مستخلص ({date}).",
        "cashbox": "اختار الخزنة.", "amount": "المبلغ لازم أكبر من صفر.",
        "ahead": "البند {code}: الكمية حتى تاريخه ({qty}) أكتر من اللي اتعمل بيه مستخلص للمالك ({certified}).",
        "advance": "مستخلصات مقاول الباطن خصمت من الدفعة المقدمة دي بالفعل؛ ألغِ المستخلصات دي أو اعملها مرتجع الأول.",
        "quantity": "مستخلص لمقاول الباطن بعده فوتر الكمية اللي المرتجع ده رجّعها؛ ألغِ المستخلص ده أو اعمله مرتجع الأول.",
        "advance_label": "دفعة مقدمة لمقاول الباطن", "payment_label": "دفعة من مستخلصات مقاول الباطن",
    },
    "en": {
        "description": "Describe the item.", "qty": "The quantity must be above zero.", "price": "Type the item's rate (price per unit) as a number, 0 or more.",
        "boq_line": "Choose an item of this project's bill of quantities.",
        "value_needed": "Type the subcontract's value first: the lines cannot add up to more than it.",
        "lines_over_value": "The subcontract's lines ({total}) would add up to more than its value ({value}); raise the value first.",
        "rate_locked": "This item is already on a bill, so its rate is fixed.", "below_billed": "The quantity cannot drop below what is already billed ({qty}).",
        "line_used": "This item is on a bill and cannot be deleted.",
        "lines_after_lump": "This subcontract already has a lump-sum bill; lines cannot be added after it, so billed work is never billed twice.",
        "no_lines": "This subcontract has no lines: make a lump-sum bill.",
        "one_draft": "Subcontractor bill {number} is still a draft: post or withdraw it first.",
        "below_previous": "The quantity to date for item {code} is below what earlier bills billed ({qty}).",
        "over_line": "The quantity to date for item {code} ({qty}) is above its quantity in the subcontract ({limit}).",
        "no_work": "No new quantities on this bill.",
        "over_value": "This bill would take what is billed ({billed}) above the subcontract's value ({value}); raise the value first if there is extra work.",
        "date_order": "A bill cannot be dated before the latest one ({date}).",
        "cashbox": "Choose the cashbox.", "amount": "The amount must be above zero.",
        "ahead": "Item {code}: the quantity to date ({qty}) is more than the owner has been certified for ({certified}).",
        "advance": "Bills of this subcontract already recovered this advance; cancel or return those bills first.",
        "quantity": "A later subcontractor bill already billed the quantity this return gave back; cancel or return that bill first.",
        "advance_label": "Subcontractor advance", "payment_label": "Payment against subcontractor bills",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _audit(project, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.Project",
                            object_id=str(project.pk), after_data=after)


def _decimal(value):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    return number if number.is_finite() else None


def _quantity(value, words, allow_zero=False):
    number = _decimal(value)
    if number is None or number < 0 or (number == 0 and not allow_zero):
        raise ValidationError(words["qty"])
    return number.quantize(QTY)


def _price(value, words):
    number = _decimal(value)
    if number is None or number < 0:
        raise ValidationError(words["price"])
    return money_round(number)


def _code(line):
    return str(line.line_number)


def _fmt(quantity):
    """A quantity as people type it: 2500, 12.5 (never 2.5E+3)."""

    return format(Decimal(quantity).normalize(), "f")


# ---- the subcontract's lines ----

def line_amount(line):
    return money_round(Decimal(line.quantity) * Decimal(line.rate))


def lines_total(subcontract):
    return money_round(sum((line_amount(line) for line in subcontract.lines.all()), ZERO))


def live_bills(subcontract):
    return subcontract.bills.exclude(invoice__status="cancelled")


def billed_quantity(line, as_of=None):
    """What live bills (draft or posted) billed on this line, less what posted purchase returns took back from those
    invoice lines. ``as_of`` counts only returns made by that date, as ``contract.certified_quantity`` does."""

    from purchases.models import PurchaseReturnLine

    lines = SubcontractBillLine.objects.filter(subcontract_line=line).exclude(bill__invoice__status="cancelled")
    billed = lines.aggregate(total=Sum("quantity"))["total"] or ZERO
    returns = PurchaseReturnLine.objects.filter(source_line__subcontract_bill_line__in=lines, purchase_return__status="posted")
    if as_of is not None:
        returns = returns.filter(purchase_return__return_date__lte=as_of)
    returned = returns.aggregate(total=Sum("quantity"))["total"] or ZERO
    return billed - returned


def owner_certified(boq_line, as_of=None):
    """What the owner has been certified for on a client item: posted certificates (a draft is still under review),
    less the posted sales returns made by ``as_of``."""

    from sales.models import SalesReturnLine

    lines = CertificateLine.objects.filter(boq_line=boq_line, certificate__invoice__status="posted")
    if as_of is not None:
        lines = lines.filter(certificate__certificate_date__lte=as_of)
    certified = lines.aggregate(total=Sum("quantity"))["total"] or ZERO
    returns = SalesReturnLine.objects.filter(source_line__certificate_line__in=lines, sales_return__status="posted")
    if as_of is not None:
        returns = returns.filter(sales_return__return_date__lte=as_of)
    return certified - (returns.aggregate(total=Sum("quantity"))["total"] or ZERO)


def _locked(subcontract, lang):
    """The subcontract row, locked, on an open project whose subcontract money is in reach of this entity."""

    from .costs import MESSAGES as COST_MESSAGES, _reach

    subcontract = Subcontract.objects.select_related("project", "supplier").get(pk=subcontract.pk)
    services._open(subcontract.project, services.MESSAGES["en" if lang == "en" else "ar"])
    subcontract = Subcontract.objects.select_for_update().select_related("project", "supplier").get(pk=subcontract.pk)
    _reach(subcontract, COST_MESSAGES["en" if lang == "en" else "ar"])
    return subcontract


@transaction.atomic
def save_line(subcontract, user, data, line=None, lang="ar"):
    words = _words(lang)
    subcontract = _locked(subcontract, lang)
    project = subcontract.project
    description = (data.get("description") or "").strip()[:255]
    if not description:
        raise ValidationError(words["description"])
    quantity = _quantity(data.get("quantity"), words)
    rate = _price(data.get("rate"), words)
    boq_line = data.get("boq_line")
    if boq_line not in (None, ""):
        if not isinstance(boq_line, BoqLine):
            raw = str(boq_line)
            boq_line = BoqLine.objects.filter(pk=raw, project=project).first() if raw.isdigit() else None
        if boq_line is None or boq_line.project_id != project.pk:
            raise ValidationError(words["boq_line"])
    else:
        boq_line = None
    if line is None:
        if live_bills(subcontract).filter(lines__isnull=True).exists():
            raise ValidationError(words["lines_after_lump"])
    else:
        line = SubcontractLine.objects.select_for_update().get(pk=line.pk, subcontract=subcontract)
        billed = billed_quantity(line)
        if SubcontractBillLine.objects.filter(subcontract_line=line).exclude(bill__invoice__status="cancelled").exists() and rate != line.rate:
            raise ValidationError(words["rate_locked"])
        if quantity < billed:
            raise ValidationError(words["below_billed"].format(qty=_fmt(billed)))
    value = Decimal(subcontract.value)
    if value <= 0:
        raise ValidationError(words["value_needed"])
    others = subcontract.lines.exclude(pk=line.pk) if line is not None else subcontract.lines.all()
    others = sum((line_amount(other) for other in others), ZERO)
    total = money_round(others + quantity * rate)
    if total > value:
        raise ValidationError(words["lines_over_value"].format(total=total, value=money_round(value)))
    created = line is None
    if created:
        number = (subcontract.lines.order_by("-line_number").values_list("line_number", flat=True).first() or 0) + 1
        line = SubcontractLine(subcontract=subcontract, line_number=number, created_by=user)
    line.description, line.unit, line.quantity, line.rate, line.boq_line = description, (data.get("unit") or "").strip()[:30], quantity, rate, boq_line
    line.save()
    _audit(project, user, "create_subcontract_line" if created else "change_subcontract_line",
           {"subcontract": subcontract.pk, "line": line.line_number, "quantity": str(quantity), "rate": str(rate),
            "boq_line": boq_line.pk if boq_line else None}, AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return line


@transaction.atomic
def delete_line(subcontract, line, user, lang="ar"):
    words = _words(lang)
    subcontract = _locked(subcontract, lang)
    line = SubcontractLine.objects.select_for_update().get(pk=line.pk, subcontract=subcontract)
    if line.bill_lines.exists():  # cancelled bills too: their rows keep pointing at it
        raise ValidationError(words["line_used"])
    _audit(subcontract.project, user, "delete_subcontract_line", {"subcontract": subcontract.pk, "line": line.line_number}, AuditEventType.DELETE)
    line.delete()


# ---- the value cap ----

def billed_gross(subcontract):
    """Every live bill's gross: posted ones after their posted returns, drafts as they stand (so two drafts never
    share the same room under the value)."""

    from .costs import effective_bill

    bills = live_bills(subcontract).select_related("invoice")
    return money_round(sum((effective_bill(b)[0] if b.invoice.status == "posted" else Decimal(b.gross) for b in bills), ZERO))


def check_value(subcontract, gross, lang="ar"):
    """Owner question 7 (default): a bill may not take the billed gross above the subcontract's value. A value of 0
    means none was typed (allowed today), so nothing is capped."""

    value = Decimal(subcontract.value)
    if value <= 0:
        return
    billed = money_round(billed_gross(subcontract) + Decimal(gross))
    if billed > value:
        raise ValidationError(_words(lang)["over_value"].format(billed=billed, value=money_round(value)))


# ---- the advance ----

def _posted_payments(subcontract, kind):
    return subcontract.payments.filter(kind=kind, payment__status="posted")


def advance_received(subcontract):
    return money_round(_posted_payments(subcontract, SubcontractPaymentKind.ADVANCE).aggregate(total=Sum("payment__amount"))["total"] or ZERO)


def effective_recovery(bill):
    """A bill's recovery after its posted purchase returns (each return takes its share, as the general ledger does)."""

    from .contract import return_shares

    if not bill.recovery_amount:
        return ZERO
    posted = set(bill.invoice.returns.filter(status="posted").values_list("pk", flat=True))
    taken = sum((share for pk, share in return_shares(bill.recovery_amount, bill.invoice).items() if pk in posted), ZERO)
    return money_round(max(Decimal(bill.recovery_amount) - taken, ZERO))


def advance_recovered(subcontract):
    """What live bills (drafts too, so two bills never recover the same money) recovered."""

    bills = live_bills(subcontract).select_related("invoice")
    return money_round(sum((effective_recovery(b) if b.invoice.status == "posted" else Decimal(b.recovery_amount) for b in bills), ZERO))


def advance_left(subcontract):
    return money_round(max(advance_received(subcontract) - advance_recovered(subcontract), ZERO))


def advance_events(subcontract):
    """[(date, change)] of the advance not yet recovered: each posted advance on its payment date; each live bill's
    recovery off on its invoice's date (drafts too), and back with its returns' shares on their dates. The same shape
    as ``contract.advance_events``, built on ``contract.held_events``."""

    from .contract import held_events

    events = [(link.payment.payment_date, Decimal(link.payment.amount))
              for link in _posted_payments(subcontract, SubcontractPaymentKind.ADVANCE).select_related("payment")]
    bills = list(live_bills(subcontract).select_related("invoice"))
    events += [(day, -change) for day, change in held_events([(b.invoice, b.recovery_amount) for b in bills if b.invoice.status == "posted"], [])]
    events += [(b.invoice.invoice_date, -Decimal(b.recovery_amount)) for b in bills if b.invoice.status == "draft" and b.recovery_amount]
    return events


def recovery_for(subcontract, gross, retention, day):
    """min(gross × rate, the advance left on ``day`` and every later date, gross − retention)."""

    from .contract import lowest_held

    rate = Decimal(subcontract.advance_recovery_rate)
    if rate <= 0:
        return ZERO
    available = min(advance_left(subcontract), max(lowest_held(advance_events(subcontract), day), ZERO))
    return min(money_round(Decimal(gross) * rate / HUNDRED), available, max(Decimal(gross) - Decimal(retention), ZERO))


# ---- bills by quantities ----

@transaction.atomic
def bill_by_quantities(subcontract, user, *, quantities, description="", bill_date=None, lang="ar"):
    """A draft subcontractor bill from the cumulative quantity to date per line (blank or missing: unchanged). The bill
    carries ``warnings``: the lines whose cumulative passes what the owner has been certified for (never refused)."""

    from .costs import MESSAGES as COST_MESSAGES, new_bill, subcontract_item

    words = _words(lang)
    subcontract = _locked(subcontract, lang)
    project = subcontract.project
    lines = list(SubcontractLine.objects.select_for_update().filter(subcontract=subcontract).order_by("line_number"))
    if not lines:
        raise ValidationError(words["no_lines"])
    draft = live_bills(subcontract).filter(invoice__status="draft").order_by("number").first()
    if draft is not None:
        raise ValidationError(words["one_draft"].format(number=draft.number))
    day = bill_date or timezone.localdate()
    latest = live_bills(subcontract).order_by("-bill_date").values_list("bill_date", flat=True).first()
    if latest and day < latest:
        raise ValidationError(words["date_order"].format(date=latest.isoformat()))  # quantities to date run forward in time
    quantities = quantities or {}
    rows = []
    for line in lines:
        raw = quantities.get(line.pk, quantities.get(str(line.pk), ""))
        if raw in (None, ""):
            continue
        previous = billed_quantity(line, as_of=day)  # a return made after the bill's date had not happened yet
        cumulative = _quantity(raw, words, allow_zero=True)
        if cumulative < previous:
            raise ValidationError(words["below_previous"].format(code=_code(line), qty=_fmt(previous)))
        if cumulative > line.quantity:
            raise ValidationError(words["over_line"].format(code=_code(line), qty=_fmt(cumulative), limit=_fmt(line.quantity)))
        done = cumulative - previous
        if done > 0:
            rows.append((line, previous, done, money_round(done * Decimal(line.rate))))
    if not rows:
        raise ValidationError(words["no_work"])
    gross = money_round(sum((value for *_, value in rows), ZERO))
    check_value(subcontract, gross, lang)
    item = subcontract_item()
    invoice_lines = [{"item": item, "quantity": done, "unit_purchase_price": Decimal(line.rate), "line_discount_amount": ZERO,
                      "description": f"{line.line_number} {line.description}"[:255]} for line, _, done, _ in rows]
    label = (description or "").strip()[:255]
    bill = new_bill(subcontract, user, project, COST_MESSAGES["en" if lang == "en" else "ar"], gross=gross, label=label, day=day, lines=invoice_lines)
    purchase_lines = list(bill.invoice.lines.order_by("line_number"))  # one invoice line per billed subcontract line, in order
    SubcontractBillLine.objects.bulk_create([
        SubcontractBillLine(bill=bill, subcontract_line=line, purchase_line=purchase_line, previous_quantity=previous, quantity=done,
                            rate=line.rate, amount=value)
        for (line, previous, done, value), purchase_line in zip(rows, purchase_lines)])
    _audit(project, user, "bill_subcontract_quantities", {"subcontract": subcontract.pk, "bill": bill.number, "invoice": bill.invoice.invoice_number,
                                                          "gross": str(gross), "retention": str(bill.retention_amount),
                                                          "recovery": str(bill.recovery_amount)}, AuditEventType.CREATE)
    bill.warnings = bill_warnings(bill, lang)
    return bill


def bill_warnings(bill, lang="ar"):
    """Trap 5 / owner question 7: the lines of a bill whose cumulative quantity is above what the owner had been
    certified for on the linked client item by the bill's date. A warning only."""

    words = _words(lang)
    found = []
    for row in bill.lines.select_related("subcontract_line__boq_line"):
        boq_line = row.subcontract_line.boq_line
        if boq_line is None:
            continue
        cumulative = row.previous_quantity + row.quantity
        certified = owner_certified(boq_line, as_of=bill.bill_date)
        if cumulative > certified:
            found.append(words["ahead"].format(code=_code(row.subcontract_line), qty=_fmt(cumulative), certified=_fmt(certified)))
    return found


def bill_rows(bill):
    """The bill as the subcontractor reads it: per line, the previous, this and the cumulative quantity and value."""

    rows = []
    for row in bill.lines.select_related("subcontract_line"):
        cumulative = row.previous_quantity + row.quantity
        rows.append({"row": row, "line": row.subcontract_line, "cumulative": cumulative,
                     "previous_value": money_round(row.previous_quantity * row.rate), "cumulative_value": money_round(cumulative * row.rate)})
    return rows


# ---- payments: the advance and payments against bills ----

@transaction.atomic
def pay(subcontract, user, *, kind, cashbox, amount, payment_date=None, notes="", lang="ar"):
    """An ordinary supplier payment, recorded through the purchases engine and marked for this subcontract."""

    from django.db import IntegrityError

    from config.numbering import next_in_series
    from entities.services import main_entity
    from purchases.models import SupplierPayment
    from purchases.services import record_supplier_payment

    from .contract import _in_scope
    from .costs import MESSAGES as COST_MESSAGES, subcontract_entity

    words = _words(lang)
    cost_words = COST_MESSAGES["en" if lang == "en" else "ar"]
    subcontract = _locked(subcontract, lang)
    if cashbox is None:
        raise ValidationError(words["cashbox"])
    established = subcontract_entity(subcontract)
    if established is not None and (cashbox.entity_id or main_entity().pk) != established:
        raise ValidationError(cost_words["entity"])  # the advance and the bills that recover it stay in one entity (trap 10)
    _in_scope(cashbox, cost_words)
    number = _decimal(amount)
    if number is None or number <= 0:
        raise ValidationError(words["amount"])
    amount = money_round(number)
    kind = kind if kind in SubcontractPaymentKind.values else SubcontractPaymentKind.PAYMENT
    label = words["advance_label"] if kind == SubcontractPaymentKind.ADVANCE else words["payment_label"]
    project = subcontract.project
    text = f"{label} — مشروع {project.code}" + (f" — {notes.strip()}" if (notes or "").strip() else "")
    for attempt in range(5):
        # The SP- series is shared with every supplier payment: two at once may pick the same next number.
        payment_number = next_in_series(SupplierPayment, "payment_number", "SP-")
        try:
            with transaction.atomic():
                payment = record_supplier_payment(payment_number, payment_date or timezone.localdate(), subcontract.supplier, cashbox, amount, user,
                                                  notes=text[:255])
            break
        except IntegrityError:
            if attempt == 4 or not SupplierPayment.objects.filter(payment_number=payment_number).exists():
                raise
    SubcontractPayment.objects.create(subcontract=subcontract, payment=payment, kind=kind, created_by=user)
    _audit(project, user, f"subcontract_{kind}", {"subcontract": subcontract.pk, "payment": payment.payment_number, "amount": str(amount)},
           AuditEventType.CREATE)
    return payment


# ---- consistency (the guard) ----

def check(subcontract, lang="en"):
    """HG-128: refuse a change (cancelling an advance, a bill, a return or its cancellation) that would leave the bills
    recovering more advance than was paid, on any date, or a returned quantity billed twice. Called by
    ``costs.ensure_consistent`` inside the engine's own transaction, with the subcontract row locked."""

    words = _words(lang)
    from .contract import lowest_held

    if advance_recovered(subcontract) > advance_received(subcontract) or lowest_held(advance_events(subcontract)) < 0:
        raise ValidationError(words["advance"])
    for line in subcontract.lines.all():
        latest = (SubcontractBillLine.objects.filter(subcontract_line=line).exclude(bill__invoice__status="cancelled")
                  .order_by("-bill__number").first())
        if latest is not None and billed_quantity(line) > latest.previous_quantity + latest.quantity:
            raise ValidationError(words["quantity"])


# ---- figures ----

def figures(subcontract):
    received = advance_received(subcontract)
    recovered = advance_recovered(subcontract)
    paid = money_round(_posted_payments(subcontract, SubcontractPaymentKind.PAYMENT).aggregate(total=Sum("payment__amount"))["total"] or ZERO)
    return {"lines_total": lines_total(subcontract), "advance": received, "recovered": recovered,
            "advance_left": money_round(max(received - recovered, ZERO)), "paid": paid}


def line_rows(subcontract):
    rows = []
    for line in subcontract.lines.select_related("boq_line"):
        billed = billed_quantity(line)
        certified = owner_certified(line.boq_line) if line.boq_line_id else None
        rows.append({"line": line, "amount": line_amount(line), "billed": billed,
                     "done": int(min(billed / line.quantity * 100, Decimal("999"))) if line.quantity else 0,
                     "certified": certified, "ahead": certified is not None and billed > certified})
    return rows

