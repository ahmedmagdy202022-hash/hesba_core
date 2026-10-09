"""CONTRACT-001: projects for contractors: progress bills, materials, costs, margin.

Rules, stated once:

* a progress bill is a *draft* sales invoice made through
  ``create_sales_draft_with_tax`` for the project's customer; it is posted,
  collected and printed from the sales screen like any other invoice;
* materials for a site leave stock through the inventory engine's own
  ``adjust_stock`` (direction out, at the authoritative average cost), under
  its own permission and period rules; the project records which operation;
* an expense (a subcontractor, transport, a daily worker) is recorded on the
  expenses screen and then linked here;
* every link is one-to-one: an invoice, expense or stock operation belongs to
  at most one project;
* the figures only read posted documents: billed is before tax and after
  posted returns, collected is what the posted invoices no longer owe, cost is
  posted materials plus posted expenses plus linked service purchases
  (CONTRACT-002). Nothing here moves money or stock.

CONTRACT-002 (HG-038) adds the bill of quantities, progress certificates,
retention and advances (``contract.py``) and subcontractors, service
purchases and the budget (``costs.py``).
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import Project, ProjectExpense, ProjectInvoice, ProjectIssue, ProjectStatus
from entities import scope as entity_scope


BILLING_ITEM_CODE = "PRJ-BILL"
OPEN = (ProjectStatus.PLANNED, ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD)
MESSAGES = {
    "ar": {
        "name": "اكتب اسم المشروع.", "customer": "اختار العميل.", "value": "قيمة العقد مش صحيحة.", "dates": "تاريخ النهاية قبل البداية.",
        "closed": "المشروع ده خلص أو اتلغى.", "amount": "المبلغ لازم أكبر من صفر.", "description": "اكتب وصف المستخلص.", "setup": "لازم يكون فيه مخزن بيع نشط.",
        "other_customer": "الفاتورة دي لعميل تاني.", "linked": "ده مربوط بمشروع بالفعل.", "not_posted": "اربط الفواتير أو المصروفات المرحّلة بس.",
        "qty": "الكمية لازم أكبر من صفر.", "item": "اختار الصنف.", "customer_locked": "المشروع عليه فواتير أو تحصيلات؛ مينفعش يتغير صاحبه.", "location": "اختار المخزن.", "stock_item": "الصنف ده مش متتبع في المخزون.",
    },
    "en": {
        "name": "Enter the project name.", "customer": "Choose the customer.", "value": "Invalid contract value.", "dates": "The end date is before the start.",
        "closed": "This project is done or cancelled.", "amount": "The amount must be above zero.", "description": "Describe the progress bill.", "setup": "An active selling location is needed.",
        "other_customer": "This invoice is for another customer.", "linked": "This is already linked to a project.", "not_posted": "Only posted invoices or expenses can be linked.",
        "qty": "The quantity must be above zero.", "item": "Choose the item.", "customer_locked": "The project has invoices or payments; its owner cannot change.", "location": "Choose the location.", "stock_item": "This item is not stock-tracked.",
    },
}


def _audit(project, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.Project",
                            object_id=str(project.pk), before_data=before or {}, after_data=after)


def _amount(value, words, key="value", allow_zero=True):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", ""))
    except InvalidOperation:
        raise ValidationError(words[key])
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ValidationError(words[key])
    return money_round(number)


def _next_code():
    number = Project.objects.count() + 1
    while Project.objects.filter(code=f"P-{number:04d}").exists():
        number += 1
    return f"P-{number:04d}"


@transaction.atomic
def save_project(data, user, project=None, lang="ar"):
    words = MESSAGES[lang]
    name = (data.get("name") or "").strip()[:255]
    if not name:
        raise ValidationError(words["name"])
    if data.get("customer") is None:
        raise ValidationError(words["customer"])
    start, end = data.get("start_date"), data.get("end_date")
    if start and end and end < start:
        raise ValidationError(words["dates"])
    values = {"name": name, "customer": data["customer"], "site": (data.get("site") or "").strip()[:255],
              "contract_value": _amount(data.get("contract_value"), words), "start_date": start, "end_date": end,
              "notes": (data.get("notes") or "").strip()[:255]}
    status = data.get("status")
    if status in ProjectStatus.values:
        values["status"] = status
    created = project is None
    if not created:
        project = Project.objects.select_for_update().get(pk=project.pk)
        # CONTRACT-002: invoices, payments and retention belong to the owner they were made for.
        if values["customer"].pk != project.customer_id and (project.invoices.exists() or project.payments.exists()
                                                              or project.certificates.exists() or project.retention_releases.exists()):
            raise ValidationError(words["customer_locked"])
    before = {} if created else {key: str(getattr(project, key)) for key in values}
    project = project or Project(code=_next_code(), created_by=user)
    for key, value in values.items():
        setattr(project, key, value)
    project.save()
    _audit(project, user, "create_project" if created else "change_project", {key: str(value) for key, value in values.items()}, before,
           AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return project


def _open(project, words):
    project = Project.objects.select_for_update().select_related("customer").get(pk=project.pk)
    if project.status not in OPEN:
        raise ValidationError(words["closed"])
    return project


def service_item(code, name):
    """A reserved service line, never a stock item: the item with ``code`` when
    it is an active non-stock service, else the first such ``code-2``,
    ``code-3``… (made when missing). A catalog item that took the code and
    tracks stock is left alone, so a bill never moves stock or books its value
    to inventory."""

    from master_data.models import Item

    candidate, number = code, 1
    while True:
        item = Item.objects.filter(item_code=candidate).first()
        if item is None:
            return Item.objects.create(item_code=candidate, item_name=name, is_stock_tracked=False, default_sale_price=0)
        if item.active and not item.is_stock_tracked:
            return item
        number += 1
        candidate = f"{code}-{number}"


def billing_item():
    """The service line a progress bill uses unless another service is chosen."""

    return service_item(BILLING_ITEM_CODE, "مستخلص أعمال")


@transaction.atomic
def bill_progress(project, user, *, amount, description, item=None, lang="ar"):
    """A lump-sum progress certificate (no bill of quantities). Returns its draft invoice.

    CONTRACT-002: it is a certificate like any other, so the project's
    retention and advance recovery apply to it too.
    """

    from .contract import create_certificate

    return create_certificate(project, user, amount=amount, description=description, item=item, lang=lang).invoice


@transaction.atomic
def link_invoice(project, invoice, user, lang="ar"):
    words = MESSAGES[lang]
    project = Project.objects.select_for_update().get(pk=project.pk)
    if invoice.customer_id != project.customer_id:
        raise ValidationError(words["other_customer"])
    if invoice.status == "cancelled":
        raise ValidationError(words["not_posted"])
    if ProjectInvoice.objects.filter(invoice=invoice).exists():
        raise ValidationError(words["linked"])
    link = ProjectInvoice.objects.create(project=project, invoice=invoice, label=invoice.invoice_number, created_by=user)
    _audit(project, user, "link_project_invoice", {"invoice": invoice.invoice_number})
    return link


@transaction.atomic
def link_expense(project, expense, user, lang="ar", heading=None):
    from cashboxes.models import CashboxOperationStatus

    words = MESSAGES[lang]
    project = Project.objects.select_for_update().get(pk=project.pk)
    if expense.cashbox_operation.status != CashboxOperationStatus.POSTED:
        raise ValidationError(words["not_posted"])
    if ProjectExpense.objects.filter(expense=expense).exists():
        raise ValidationError(words["linked"])
    from .models import CostHeading

    heading = heading if heading in CostHeading.values else CostHeading.OTHER  # CONTRACT-002: the budget heading
    link = ProjectExpense.objects.create(project=project, expense=expense, heading=heading, created_by=user)
    _audit(project, user, "link_project_expense", {"expense": expense.expense_number, "amount": str(expense.amount)})
    return link


@transaction.atomic
def unlink(project, link, user):
    """Take an invoice or expense off the project. The document itself is untouched."""

    kind = type(link).__name__
    label = link.invoice.invoice_number if isinstance(link, ProjectInvoice) else link.expense.expense_number
    link.delete()
    _audit(project, user, "unlink_project_document", {"kind": kind, "document": label})


@transaction.atomic
def issue_materials(project, user, *, item, location, quantity, operation_date=None, lang="ar"):
    """Take materials out of stock for the site, through the inventory engine."""

    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock

    words = MESSAGES[lang]
    project = _open(project, words)
    if item is None:
        raise ValidationError(words["item"])
    if not item.is_stock_tracked:
        raise ValidationError(words["stock_item"])
    if location is None:
        raise ValidationError(words["location"])
    try:
        quantity = Decimal(str(quantity).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        quantity = Decimal("0")
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError(words["qty"])
    sequence = project.issues.count() + 1
    operation = adjust_stock(
        f"PI-{project.code}-{sequence:03d}", operation_date or timezone.localdate(), item, location, StockAdjustmentDirection.OUT,
        quantity.quantize(Decimal("0.001")), f"صرف خامات لمشروع {project.code} / Materials for project {project.code}", user,
    )
    ProjectIssue.objects.create(project=project, operation=operation, created_by=user)
    _audit(project, user, "issue_project_materials", {"operation": operation.reference_number, "item": item.item_code, "quantity": str(operation.quantity)})
    return operation


def summary(project):
    """What the project earned, collected and cost, from posted documents only."""

    from appointments.services import net_sales
    from cashboxes.models import CashboxOperationStatus
    from inventory.models import StockOperationStatus

    # As the entity being worked in sees it (HG-034): its own documents only.
    invoices = entity_scope.scope(project.invoices.select_related("invoice"), "invoice__" + entity_scope.SALES_INVOICE)
    posted = [link.invoice for link in invoices.filter(invoice__status="posted")]
    billed = money_round(sum((net_sales(invoice) for invoice in posted), Decimal("0")))
    invoiced = money_round(sum((Decimal(invoice.total_amount) for invoice in posted), Decimal("0")))
    due = money_round(sum((Decimal(invoice.remaining_due) for invoice in posted), Decimal("0")))
    drafts = invoices.filter(invoice__status="draft").aggregate(total=Sum("invoice__total_amount"))["total"] or Decimal("0")
    issues = entity_scope.scope(project.issues.select_related("operation").filter(operation__status=StockOperationStatus.POSTED),
                                tuple("operation__" + path for path in entity_scope.STOCK_OPERATION))
    materials = money_round(sum((issue.operation.quantity * issue.operation.unit_cost for issue in issues), Decimal("0")))
    expenses = entity_scope.scope(project.expenses.filter(expense__cashbox_operation__status=CashboxOperationStatus.POSTED), "expense__" + entity_scope.EXPENSE)
    expenses = money_round(expenses.aggregate(total=Sum("expense__amount"))["total"] or 0)
    from .contract import contract_value
    from .costs import purchases_cost

    purchases = purchases_cost(project)  # CONTRACT-002: subcontractor bills and other service purchases
    cost = money_round(materials + expenses + purchases)
    contract = contract_value(project)
    return {
        "contract": contract, "billed": billed, "invoiced": invoiced, "collected": money_round(invoiced - due), "due": due, "drafts": money_round(drafts),
        "remaining": money_round(max(contract - billed, Decimal("0"))), "over": money_round(max(billed - contract, Decimal("0"))),
        "progress": int(min(billed / contract * 100, Decimal("999"))) if contract > 0 else 0,
        "materials": materials, "expenses": expenses, "purchases": purchases, "cost": cost, "profit": money_round(billed - cost),
        "margin": int((billed - cost) / billed * 100) if billed > 0 else 0,
    }
