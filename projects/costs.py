"""CONTRACT-002 (HG-038): the cost side of a project.

* **Subcontract:** work given to a supplier, with its value and the retention
  we hold from each of their bills.
* **Subcontractor bill (مستخلص مقاول باطن):** an ordinary draft purchase
  invoice (``create_purchase_draft_with_tax``) of one service line for the
  gross work value, posted, paid, returned and cancelled from the purchases
  screens. Net payable = its total − retention. Retention is held once the bill
  is posted, until it is released back to the subcontractor.
* **Other service purchases** (equipment hire, a site service) are linked to
  the project with a cost heading. A purchase invoice with stock lines cannot
  be linked: those goods reach the site through "issue from stock", so they
  are never counted twice.
* **Budget:** a planned amount per cost heading, read against what the
  project actually cost: materials issued, linked expenses and linked service
  purchases (posted, before tax, after posted returns).
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from . import services
from .models import BudgetLine, CostHeading, Project, ProjectPurchase, Subcontract, SubcontractBill, SubcontractRelease

ZERO = Decimal("0")
HUNDRED = Decimal("100")
SUBCONTRACT_ITEM_CODE = "PRJ-SUB"

MESSAGES = {
    "ar": {
        "supplier": "اختار مقاول الباطن (من الموردين).", "scope": "اكتب الأعمال المسندة.", "value": "القيمة مش صحيحة.", "rate": "النسبة لازم تكون من 0 لـ 100.",
        "amount": "المبلغ لازم أكبر من صفر.", "description": "اكتب وصف المستخلص.", "setup": "لازم يكون فيه مخزن استلام نشط.",
        "stock_lines": "الفاتورة دي فيها أصناف مخزنية: اصرفها للمشروع من المخزن بدل ما تربطها.", "linked": "الفاتورة دي مربوطة بمشروع بالفعل.",
        "cancelled": "الفاتورة دي ملغية.", "not_draft": "المستخلص ده اترحّل؛ لو فيه غلط اعمل مرتجع أو إلغاء من شاشة المشتريات.",
        "withdraw_blocked": "الفاتورة دي مربوطة بحاجة تانية ومينفعش تتسحب.", "release_more": "المبلغ أكبر من ضمان الأعمال المحتجز ({held}).",
        "bill_link": "دي فاتورة مستخلص مقاول باطن؛ اسحبها من شاشة مقاولي الباطن.",
        "reversed": "الإفراج ده اتلغى بالفعل.", "date": "التاريخ مش صحيح.",
    },
    "en": {
        "supplier": "Choose the subcontractor (a supplier).", "scope": "Describe the work given.", "value": "Invalid value.", "rate": "The rate must be between 0 and 100.",
        "amount": "The amount must be above zero.", "description": "Describe the bill.", "setup": "An active receiving location is needed.",
        "stock_lines": "This invoice has stock items: issue them to the project from stock instead of linking it.", "linked": "This invoice is already linked to a project.",
        "cancelled": "This invoice is cancelled.", "not_draft": "This bill is posted; correct it with a return or a cancellation on the purchases screen.",
        "withdraw_blocked": "This invoice is linked to something else and cannot be withdrawn.", "release_more": "The amount is more than the retention held ({held}).",
        "bill_link": "This is a subcontractor bill; withdraw it from the subcontractors screen.",
        "reversed": "This release is already reversed.", "date": "Invalid date.",
    },
}


def _audit(project, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.Project",
                            object_id=str(project.pk), after_data=after)


def _decimal(value):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    return number if number.is_finite() else None


def _money(value, words, key="amount", allow_zero=False):
    number = _decimal(value)
    if number is None or number < 0 or (number == 0 and not allow_zero):
        raise ValidationError(words[key])
    return money_round(number)


def _rate(value, words):
    number = _decimal(value)
    if number is None or number < 0 or number > HUNDRED:
        raise ValidationError(words["rate"])
    return number.quantize(Decimal("0.01"))


def subcontract_item():
    """The service line a subcontractor bill uses."""

    from master_data.models import Item

    item, _ = Item.objects.get_or_create(
        item_code=SUBCONTRACT_ITEM_CODE,
        defaults={"item_name": "أعمال مقاول باطن", "is_stock_tracked": False, "default_sale_price": 0},
    )
    return item


# ---- subcontracts ----

@transaction.atomic
def save_subcontract(project, user, data, subcontract=None, lang="ar"):
    words = MESSAGES[lang]
    project = services._open(project, services.MESSAGES[lang])
    if data.get("supplier") is None:
        raise ValidationError(words["supplier"])
    scope = (data.get("scope") or "").strip()[:255]
    if not scope:
        raise ValidationError(words["scope"])
    values = {"supplier": data["supplier"], "scope": scope, "value": _money(data.get("value"), words, "value", allow_zero=True),
              "retention_rate": _rate(data.get("retention_rate"), words)}
    created = subcontract is None
    subcontract = subcontract or Subcontract(project=project, created_by=user)
    for key, value in values.items():
        setattr(subcontract, key, value)
    subcontract.save()
    _audit(project, user, "create_subcontract" if created else "change_subcontract",
           {"subcontract": subcontract.pk, "supplier": values["supplier"].supplier_code, "scope": scope, "value": str(values["value"]),
            "retention_rate": str(values["retention_rate"])}, AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return subcontract


def live_bills(subcontract):
    return subcontract.bills.exclude(invoice__status="cancelled")


@transaction.atomic
def bill_subcontract(subcontract, user, *, amount, description, bill_date=None, lang="ar"):
    """A draft purchase invoice for the subcontractor's work, linked to the project as a cost."""

    from master_data.models import Location
    from purchases.models import PurchaseInvoice
    from taxes.services import create_purchase_draft_with_tax
    from entities import scope as entity_scope

    words = MESSAGES[lang]
    subcontract = Subcontract.objects.select_related("project", "supplier").get(pk=subcontract.pk)
    project = services._open(subcontract.project, services.MESSAGES[lang])
    gross = _money(amount, words)
    label = (description or "").strip()[:255]
    if not label:
        raise ValidationError(words["description"])
    location = entity_scope.locations(Location.objects).filter(active=True, is_receiving_location=True).order_by("-is_default", "pk").first()
    if location is None:
        raise ValidationError(words["setup"])
    number = (subcontract.bills.order_by("-number").values_list("number", flat=True).first() or 0) + 1
    sequence = SubcontractBill.objects.filter(subcontract__project=project).count() + 1
    invoice_number = f"SC-{project.code}-{sequence:02d}"
    while PurchaseInvoice.objects.filter(invoice_number=invoice_number).exists():
        sequence += 1
        invoice_number = f"SC-{project.code}-{sequence:02d}"
    title = f"مستخلص مقاول باطن رقم {number} — {label}"
    invoice = create_purchase_draft_with_tax(
        {"invoice_number": invoice_number, "invoice_date": bill_date or timezone.localdate(), "supplier": subcontract.supplier,
         "receiving_location": location, "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO,
         "notes": f"مشروع {project.code}: {title}"[:255]},
        [{"item": subcontract_item(), "quantity": Decimal("1"), "unit_purchase_price": gross, "line_discount_amount": ZERO, "description": label}],
        user,
    )
    retention = money_round(gross * Decimal(subcontract.retention_rate) / HUNDRED)
    bill = SubcontractBill.objects.create(subcontract=subcontract, number=number, bill_date=invoice.invoice_date, invoice=invoice, gross=gross,
                                          retention_rate=subcontract.retention_rate, retention_amount=retention, description=label, created_by=user)
    ProjectPurchase.objects.create(project=project, invoice=invoice, heading=CostHeading.SUBCONTRACT, created_by=user)
    _audit(project, user, "bill_subcontract", {"subcontract": subcontract.pk, "bill": number, "invoice": invoice_number, "gross": str(gross),
                                               "retention": str(retention)}, AuditEventType.CREATE)
    return bill


@transaction.atomic
def withdraw_bill(bill, user, lang="ar"):
    words = MESSAGES[lang]
    bill = SubcontractBill.objects.select_related("invoice", "subcontract__project").select_for_update(of=("self",)).get(pk=bill.pk)
    invoice = bill.invoice
    if invoice.status != "draft":
        raise ValidationError(words["not_draft"])
    project = bill.subcontract.project
    details = {"subcontract": bill.subcontract_id, "bill": bill.number, "invoice": invoice.invoice_number, "gross": str(bill.gross)}
    ProjectPurchase.objects.filter(invoice=invoice).delete()
    bill.delete()
    try:
        with transaction.atomic():
            invoice.delete()
    except ProtectedError:
        raise ValidationError(words["withdraw_blocked"])
    _audit(project, user, "withdraw_subcontract_bill", details, AuditEventType.DELETE)


def return_share(amount, invoice, purchase_return):
    """The part of a bill's retention (or gross) a purchase return takes back,
    the same share the general ledger uses."""

    total = Decimal(invoice.total_amount)
    if total <= 0 or not amount:
        return ZERO
    return money_round(Decimal(amount) * Decimal(purchase_return.total_amount) / total)


def effective_bill(bill):
    """(gross, retention) of a subcontractor bill after its posted purchase returns."""

    gross, retention = Decimal(bill.gross), Decimal(bill.retention_amount)
    for purchase_return in bill.invoice.returns.filter(status="posted"):
        gross -= return_share(bill.gross, bill.invoice, purchase_return)
        retention -= return_share(bill.retention_amount, bill.invoice, purchase_return)
    return money_round(max(gross, ZERO)), money_round(max(retention, ZERO))


def bill_net_payable(bill):
    return money_round(Decimal(bill.invoice.total_amount) - bill.retention_amount)


def subcontract_retention_held(subcontract):
    held = sum((effective_bill(b)[1] for b in live_bills(subcontract).filter(invoice__status="posted").select_related("invoice")), ZERO)
    released = subcontract.releases.filter(reversed_on__isnull=True).aggregate(total=Sum("amount"))["total"] or ZERO
    return money_round(held - released)


CONSISTENCY = {
    "en": "This would leave more subcontractor retention released than is held; reverse the release first.",
    "ar": "كده الإفراج عن ضمان أعمال مقاول الباطن هيبقى أكبر من المحتجز؛ ألغِ الإفراج الأول.",
}


def ensure_consistent(subcontract, lang="en"):
    """HG-038: refuse a cancellation, return or reversal that would leave
    released retention above what is held (called inside that change)."""

    if subcontract_retention_held(subcontract) < 0:
        raise ValidationError(CONSISTENCY[lang])


@transaction.atomic
def release_subcontract_retention(subcontract, user, *, amount, release_date=None, notes="", lang="ar"):
    from closing.services import ensure_period_is_open

    words = MESSAGES[lang]
    subcontract = Subcontract.objects.select_for_update().select_related("project").get(pk=subcontract.pk)
    amount = _money(amount, words)
    day = release_date or timezone.localdate()
    ensure_period_is_open(day)  # a dated ledger entry: closed months stay closed
    held = subcontract_retention_held(subcontract)
    if amount > held:
        raise ValidationError(words["release_more"].format(held=held))
    release = SubcontractRelease.objects.create(subcontract=subcontract, release_date=day, amount=amount,
                                                notes=(notes or "").strip()[:255], created_by=user)
    _audit(subcontract.project, user, "release_subcontract_retention", {"subcontract": subcontract.pk, "amount": str(amount)})
    return release


@transaction.atomic
def reverse_subcontract_release(release, user, *, reversal_date=None, lang="ar"):
    from closing.services import ensure_period_is_open

    words = MESSAGES[lang]
    release = SubcontractRelease.objects.select_for_update().select_related("subcontract__project").get(pk=release.pk)
    if release.reversed_on:
        raise ValidationError(words["reversed"])
    day = reversal_date or timezone.localdate()
    if day < release.release_date:
        raise ValidationError(words["date"])
    ensure_period_is_open(day)
    release.reversed_on, release.reversed_by = day, user
    release.save(update_fields=["reversed_on", "reversed_by"])
    _audit(release.subcontract.project, user, "reverse_subcontract_release", {"release": release.pk, "amount": str(release.amount), "date": str(day)})
    return release


def subcontract_figures(subcontract):
    posted = live_bills(subcontract).filter(invoice__status="posted").select_related("invoice")
    billed = money_round(sum((effective_bill(b)[0] for b in posted), ZERO))
    value = Decimal(subcontract.value)
    return {"value": value, "billed": billed, "remaining": money_round(max(value - billed, ZERO)), "over": money_round(max(billed - value, ZERO)),
            "retention_held": subcontract_retention_held(subcontract),
            "drafts": money_round(live_bills(subcontract).filter(invoice__status="draft").aggregate(total=Sum("gross"))["total"] or ZERO)}


# ---- other service purchases ----

@transaction.atomic
def link_purchase(project, invoice, user, *, heading=CostHeading.OTHER, lang="ar"):
    words = MESSAGES[lang]
    project = Project.objects.select_for_update().get(pk=project.pk)
    if invoice.status == "cancelled":
        raise ValidationError(words["cancelled"])
    if ProjectPurchase.objects.filter(invoice=invoice).exists():
        raise ValidationError(words["linked"])
    if invoice.lines.filter(item__is_stock_tracked=True).exists():
        raise ValidationError(words["stock_lines"])
    heading = heading if heading in CostHeading.values else CostHeading.OTHER
    link = ProjectPurchase.objects.create(project=project, invoice=invoice, heading=heading, created_by=user)
    _audit(project, user, "link_project_purchase", {"invoice": invoice.invoice_number, "heading": heading})
    return link


@transaction.atomic
def unlink_purchase(project, link, user, lang="ar"):
    words = MESSAGES[lang]
    if hasattr(link.invoice, "subcontract_bill"):
        raise ValidationError(words["bill_link"])
    _audit(project, user, "unlink_project_purchase", {"invoice": link.invoice.invoice_number})
    link.delete()


def net_purchase(invoice):
    """What a posted purchase cost: before tax, after its posted returns (the
    returns' tax share taken at the invoice's own rate, as ``net_sales`` does)."""

    total = Decimal(invoice.total_amount)
    if total <= 0 or invoice.status != "posted":
        return ZERO
    before_tax = total - Decimal(invoice.tax_amount or 0)
    returned = invoice.returns.filter(status="posted").aggregate(total=Sum("total_amount"))["total"] or ZERO
    return money_round(max(before_tax - returned * before_tax / total, ZERO))


# ---- cost by heading and budget ----

def actual_by_heading(project):
    from cashboxes.models import CashboxOperationStatus
    from inventory.models import StockOperationStatus

    actual = defaultdict(lambda: ZERO)
    for issue in project.issues.select_related("operation").filter(operation__status=StockOperationStatus.POSTED):
        actual[CostHeading.MATERIALS] += money_round(issue.operation.quantity * issue.operation.unit_cost)
    for link in project.expenses.select_related("expense").filter(expense__cashbox_operation__status=CashboxOperationStatus.POSTED):
        actual[link.heading] += Decimal(link.expense.amount)
    for link in project.purchases.select_related("invoice"):
        actual[link.heading] += net_purchase(link.invoice)
    return {heading: money_round(value) for heading, value in actual.items()}


def purchases_cost(project):
    return money_round(sum((net_purchase(link.invoice) for link in project.purchases.select_related("invoice")), ZERO))


@transaction.atomic
def save_budget(project, user, amounts, lang="ar"):
    words = MESSAGES[lang]
    project = Project.objects.select_for_update().get(pk=project.pk)
    saved = {}
    for heading in CostHeading.values:
        if heading not in amounts:
            continue
        value = _money(amounts.get(heading), words, "value", allow_zero=True)
        BudgetLine.objects.update_or_create(project=project, heading=heading, defaults={"amount": value})
        saved[heading] = str(value)
    _audit(project, user, "change_project_budget", saved)


def budget_rows(project, lang="ar"):
    from settings_core.display_labels import choice_label

    planned = {line.heading: Decimal(line.amount) for line in project.budget.all()}
    actual = actual_by_heading(project)
    rows = []
    for heading in CostHeading.values:
        budget, spent = planned.get(heading, ZERO), actual.get(heading, ZERO)
        if not budget and not spent and heading not in planned:
            continue
        rows.append({"heading": heading, "label": choice_label(BudgetLine(heading=heading), "heading", lang), "budget": money_round(budget),
                     "actual": spent, "variance": money_round(budget - spent), "used": int(spent / budget * 100) if budget > 0 else None})
    total_budget = money_round(sum((row["budget"] for row in rows), ZERO))
    total_actual = money_round(sum((row["actual"] for row in rows), ZERO))
    return {"rows": rows, "budget": total_budget, "actual": total_actual, "variance": money_round(total_budget - total_actual),
            "used": int(total_actual / total_budget * 100) if total_budget > 0 else None}
