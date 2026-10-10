"""CONTRACT-002 (HG-038): the contract side of a project.

* **Bill of quantities (المقايسة):** the items of work, each with a quantity
  and a rate. Its total is the contract value once it has lines.
* **Progress certificate (مستخلص):** the cumulative quantity done to date per
  item, less what earlier certificates already billed, priced at the item's
  rate. A project without a bill of quantities is billed as a lump sum. The
  certificate's invoice is an ordinary draft sales invoice
  (``create_sales_draft_with_tax``) carrying the gross work value, posted,
  collected, returned and cancelled from the sales screens as any other.
* **Retention (ضمان الأعمال):** a share of each certificate's gross the owner
  keeps until handover. It stays part of what the owner owes; the project
  shows it apart from what is due now, and the general ledger keeps it in
  retention receivable until it is released.
* **Advance (الدفعة المقدمة):** an ordinary customer payment marked as the
  owner's advance. Each certificate recovers a share of its gross, never more
  than what is left of the advance.
* **Collections:** ordinary customer payments marked as received for this
  project, so its due-now figure is exact even when the owner has several.

Rules, stated once:

* a certificate whose invoice is cancelled stops counting (its quantities,
  retention and recovery are free again);
* retention is held only once its certificate is posted;
* one certificate at a time may be a draft, so quantities to date stay in
  order; a draft can be withdrawn (its draft invoice is deleted: a draft has
  no ledger, stock or cash rows);
* the rate of a bill-of-quantities item is fixed once it has been certified,
  and its quantity cannot drop below what was certified.

Net payable on a certificate = its invoice total − retention − recovery.
Due now on the project = what its posted invoices still add to the owner's
account (after paid-now and posted returns) − retention held − recovered
advance − collections. The owner's account itself (the customer ledger) is
untouched: retention and advances are only split out of it for reading.
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
from .models import (
    BoqLine, Certificate, CertificateLine, Project, ProjectInvoice, ProjectPayment, ProjectPaymentKind, RetentionRelease,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
QTY = Decimal("0.001")

MESSAGES = {
    "ar": {
        "rate": "النسبة لازم تكون من 0 لـ 100.", "description": "اكتب وصف البند.", "qty": "الكمية لازم أكبر من صفر.", "price": "الفئة مش صحيحة.",
        "rate_locked": "البند ده اتعمل عليه مستخلص، فالفئة بتاعته ثابتة.", "below_certified": "مينفعش كمية البند تقل عن اللي اتعمل بيه مستخلصات ({qty}).",
        "boq_used": "البند ده اتعمل عليه مستخلص ومينفعش يتمسح.", "one_draft": "فيه مستخلص لسه مسودة ({number}): رحّله أو اسحبه الأول.",
        "below_previous": "الكمية حتى تاريخه للبند {code} أقل من اللي اتعمل بيه مستخلصات قبل كده ({qty}).", "no_work": "مفيش كميات جديدة في المستخلص ده.",
        "boq_needed": "المشروع ده ليه مقايسة: اعمل المستخلص بالكميات.", "amount": "المبلغ لازم أكبر من صفر.", "lump_description": "اكتب وصف المستخلص.",
        "not_draft": "المستخلص ده اترحّل؛ لو فيه غلط اعمل مرتجع أو إلغاء من شاشة الفاتورة.", "withdraw_blocked": "الفاتورة دي مربوطة بحاجة تانية ومينفعش تتسحب.",
        "release_more": "المبلغ أكبر من ضمان الأعمال المحتجز ({held}).", "cashbox": "اختار الخزنة.", "other_customer": "التحصيل ده لعميل تاني.",
        "payment_linked": "التحصيل ده مربوط بمشروع بالفعل.", "payment_status": "اربط التحصيلات المرحّلة بس.", "date": "التاريخ مش صحيح.",
        "advance_used": "المستخلصات خصمت من الدفعة المقدمة دي بالفعل، فمينفعش يتفك ربطها.",
        "boq_after_lump": "المشروع ده اتعمل عليه مستخلص مقطوعية؛ مينفعش تضيف مقايسة بعده عشان الأعمال اللي اتفوترت متتفوترش تاني.", "date_order": "تاريخ المستخلص لازم ميكونش قبل آخر مستخلص ({date}).",
        "reversed": "الإفراج ده اتلغى بالفعل.", "rates_total": "مجموع نسبة ضمان الأعمال ونسبة استرداد الدفعة المقدمة لازم ميزيدش عن 100%.", "instalment": "التحصيل ده قسط على خطة تقسيط؛ يتربط بالمشروع كتحصيل بس، ولو فاتورته مربوطة بالمشروع ده.", "entity": "مستخلصات المشروع ده بتتعمل من كيان تاني؛ اشتغل من الكيان ده عشان ضمان الأعمال والدفعة المقدمة يفضلوا في مكان واحد.",
    },
    "en": {
        "rate": "The rate must be between 0 and 100.", "description": "Describe the item.", "qty": "The quantity must be above zero.", "price": "Invalid rate.",
        "rate_locked": "This item is already on a certificate, so its rate is fixed.", "below_certified": "The quantity cannot drop below what is already certified ({qty}).",
        "boq_used": "This item is on a certificate and cannot be deleted.", "one_draft": "Certificate {number} is still a draft: post or withdraw it first.",
        "below_previous": "The quantity to date for item {code} is below what earlier certificates billed ({qty}).", "no_work": "No new quantities on this certificate.",
        "boq_needed": "This project has a bill of quantities: certify by quantities.", "amount": "The amount must be above zero.", "lump_description": "Describe the certificate.",
        "not_draft": "This certificate is posted; correct it with a return or a cancellation on the invoice screen.", "withdraw_blocked": "This invoice is linked to something else and cannot be withdrawn.",
        "release_more": "The amount is more than the retention held ({held}).", "cashbox": "Choose the cashbox.", "other_customer": "This collection is for another customer.",
        "payment_linked": "This collection is already linked to a project.", "payment_status": "Only posted collections can be linked.", "date": "Invalid date.",
        "advance_used": "Certificates already recovered this advance, so it cannot be unlinked.",
        "boq_after_lump": "This project already has a lump-sum certificate; a bill of quantities cannot be added after it, so billed work is never billed twice.", "date_order": "A certificate cannot be dated before the latest one ({date}).",
        "reversed": "This release is already reversed.", "rates_total": "Retention and advance recovery together cannot exceed 100%.", "instalment": "This collection pays an instalment plan; it can be linked only as a collection, and only when its invoice is on this project.", "entity": "This project's certificates are billed from another entity; work in that entity so its retention and advance stay in one place.",
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


def _rate(value, words):
    number = _decimal(value)
    if number is None or number < 0 or number > HUNDRED:
        raise ValidationError(words["rate"])
    return number.quantize(Decimal("0.01"))


def _quantity(value, words, allow_zero=False):
    number = _decimal(value)
    if number is None or number < 0 or (number == 0 and not allow_zero):
        raise ValidationError(words["qty"])
    return number.quantize(QTY)


def _money(value, words, key="amount"):
    number = _decimal(value)
    if number is None or number <= 0:
        raise ValidationError(words[key])
    return money_round(number)


def _locked(project, lang):
    """The project row, locked, and refused when it is done or cancelled or
    when its money lives in another entity than the one being worked in."""

    project = services._open(project, services.MESSAGES[lang])
    _reach(project, MESSAGES[lang])
    return project


def _held(project, lang):
    """The project row, locked, for a change to money it already holds."""

    project = Project.objects.select_for_update().get(pk=project.pk)
    _reach(project, MESSAGES[lang])
    return project


# ---- terms ----

@transaction.atomic
def save_terms(project, user, *, retention_rate, advance_recovery_rate, lang="ar"):
    words = MESSAGES[lang]
    project = _held(project, lang)
    retention, recovery = _rate(retention_rate, words), _rate(advance_recovery_rate, words)
    if retention + recovery > HUNDRED:
        raise ValidationError(words["rates_total"])  # the deductions can never be more than the certificate
    before = {"retention_rate": str(project.retention_rate), "advance_recovery_rate": str(project.advance_recovery_rate)}
    project.retention_rate, project.advance_recovery_rate = retention, recovery
    project.save(update_fields=["retention_rate", "advance_recovery_rate"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="projects", action="change_project_terms", object_type="projects.Project",
                            object_id=str(project.pk), before_data=before, after_data={"retention_rate": str(retention), "advance_recovery_rate": str(recovery)})
    return project


# ---- bill of quantities ----

def live_certificates(project):
    return project.certificates.exclude(invoice__status="cancelled")


def certified_quantity(boq_line):
    """What live certificates (draft or posted) billed on this item, less what
    posted sales returns took back from those invoice lines."""

    from sales.models import SalesReturnLine

    lines = CertificateLine.objects.filter(boq_line=boq_line).exclude(certificate__invoice__status="cancelled")
    billed = lines.aggregate(total=Sum("quantity"))["total"] or ZERO
    returned = SalesReturnLine.objects.filter(source_line__certificate_line__in=lines, sales_return__status="posted").aggregate(
        total=Sum("quantity"))["total"] or ZERO
    return billed - returned


def return_shares(amount, invoice):
    """{return id: the part of ``amount`` (a certificate's gross, retention or
    recovery) that return takes back}, for every return of the invoice.

    Each return's share is fixed when it is made and never moves afterwards:
    the returns are replayed in the order they were made and cancelled, and a
    new return takes the returned share of the amount on the total returned by
    then (it and the returns still standing), less what those already took. So
    the standing returns always add up to exactly the returned share of the
    amount (a full return takes back all of it, never a cent more or less),
    and cancelling a return reverses its own share without moving the others'.
    The general ledger books these same figures, so the project and the books
    agree."""

    total, amount = Decimal(invoice.total_amount), Decimal(amount or 0)
    if total <= 0 or not amount:
        return {}
    events = []
    for row in invoice.returns.all():
        made = row.created_at
        events.append((made, 0, row.pk, row, True))
        if row.status != "posted":
            events.append((row.cancelled_at or made, 1, row.pk, row, False))
    shares, standing = {}, {}
    for *_, row, made in sorted(events, key=lambda event: event[:3]):
        if not made:
            standing.pop(row.pk, None)
            continue
        returned = sum((Decimal(other.total_amount) for other in standing.values()), Decimal(row.total_amount))
        taken = sum((shares[pk] for pk in standing), ZERO)
        shares[row.pk] = money_round(amount * min(returned, total) / total) - taken
        standing[row.pk] = row
    return shares


def return_share(amount, invoice, sales_return):
    return return_shares(amount, invoice).get(sales_return.pk, ZERO)


def effective(certificate):
    """(gross, retention, recovery) of a certificate after its posted returns."""

    posted = set(certificate.invoice.returns.filter(status="posted").values_list("pk", flat=True))

    def left(amount):
        taken = sum((share for pk, share in return_shares(amount, certificate.invoice).items() if pk in posted), ZERO)
        return money_round(max(Decimal(amount) - taken, ZERO))

    return left(certificate.gross), left(certificate.retention_amount), left(certificate.recovery_amount)


def line_amount(line):
    return money_round(Decimal(line.quantity) * Decimal(line.rate))


def boq_total(project):
    return money_round(sum((line_amount(line) for line in project.boq.all()), ZERO))


def contract_value(project):
    """The bill of quantities' total once it has lines; the typed value before that."""

    return boq_total(project) if project.boq.exists() else Decimal(project.contract_value)


@transaction.atomic
def save_boq_line(project, user, data, line=None, lang="ar"):
    words = MESSAGES[lang]
    project = _locked(project, lang)
    description = (data.get("description") or "").strip()[:255]
    if not description:
        raise ValidationError(words["description"])
    quantity = _quantity(data.get("quantity"), words)
    rate = _decimal(data.get("rate"))
    if rate is None or rate < 0:
        raise ValidationError(words["price"])
    rate = money_round(rate)
    values = {"code": (data.get("code") or "").strip()[:20], "description": description, "unit": (data.get("unit") or "").strip()[:30],
              "quantity": quantity, "rate": rate}
    created = line is None
    if created and not project.boq.exists() and live_certificates(project).exists():
        raise ValidationError(words["boq_after_lump"])  # those certificates have no quantities to start the BOQ from
    if created:
        top = project.boq.order_by("-line_number").values_list("line_number", flat=True).first() or 0
        line = BoqLine(project=project, line_number=top + 1, created_by=user)
    else:
        line = BoqLine.objects.select_for_update().get(pk=line.pk, project=project)
        certified = certified_quantity(line)
        if line.certificate_lines.exclude(certificate__invoice__status="cancelled").exists() and rate != line.rate:
            raise ValidationError(words["rate_locked"])
        if quantity < certified:
            raise ValidationError(words["below_certified"].format(qty=certified.normalize()))
    for key, value in values.items():
        setattr(line, key, value)
    line.save()
    _audit(project, user, "add_boq_line" if created else "change_boq_line",
           {"line": line.line_number, **{key: str(value) for key, value in values.items()}}, AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return line


@transaction.atomic
def delete_boq_line(project, line, user, lang="ar"):
    words = MESSAGES[lang]
    project = _locked(project, lang)
    line = BoqLine.objects.select_for_update().get(pk=line.pk, project=project)
    if line.certificate_lines.exists():
        raise ValidationError(words["boq_used"])
    _audit(project, user, "delete_boq_line", {"line": line.line_number, "description": line.description}, AuditEventType.DELETE)
    line.delete()


# ---- payments: advances and collections ----

def _posted_payments(project, kind):
    return project.payments.filter(kind=kind, payment__status="posted")


def advance_received(project):
    return money_round(_posted_payments(project, ProjectPaymentKind.ADVANCE).aggregate(total=Sum("payment__amount"))["total"] or ZERO)


def advance_recovered(project, posted_only=False):
    certificates = live_certificates(project).select_related("invoice")
    if posted_only:
        certificates = certificates.filter(invoice__status="posted")
    return money_round(sum((effective(c)[2] for c in certificates), ZERO))


def advance_left(project):
    """What of the advance no live certificate has claimed yet (drafts included,
    so two certificates never recover the same money)."""

    return money_round(max(advance_received(project) - advance_recovered(project), ZERO))


@transaction.atomic
def receive_payment(project, user, *, kind, cashbox, amount, payment_date=None, notes="", lang="ar"):
    """An ordinary customer payment, recorded through the sales engine and marked for this project."""

    from config.numbering import next_in_series
    from sales.models import CustomerPayment
    from sales.services import record_customer_payment

    words = MESSAGES[lang]
    project = _locked(project, lang)
    if cashbox is None:
        raise ValidationError(words["cashbox"])
    _same_entity(project, cashbox, words)  # the project's money stays in one entity
    amount = _money(amount, words)
    kind = kind if kind in ProjectPaymentKind.values else ProjectPaymentKind.COLLECTION
    label = "دفعة مقدمة" if kind == ProjectPaymentKind.ADVANCE else "تحصيل مستخلصات"
    payment = record_customer_payment(
        next_in_series(CustomerPayment, "payment_number", "CP-"), payment_date or timezone.localdate(), project.customer, cashbox, amount, user,
        notes=f"{label} — مشروع {project.code}",
    )
    ProjectPayment.objects.create(project=project, payment=payment, kind=kind, created_by=user)
    _audit(project, user, f"project_{kind}", {"payment": payment.payment_number, "amount": str(amount)})
    return payment


@transaction.atomic
def link_payment(project, payment, user, *, kind, lang="ar"):
    from closing.services import ensure_period_is_open

    words = MESSAGES[lang]
    project = _held(project, lang)
    if payment.customer_id != project.customer_id:
        raise ValidationError(words["other_customer"])
    if payment.status != "posted":
        raise ValidationError(words["payment_status"])
    if ProjectPayment.objects.filter(payment=payment).exists():
        raise ValidationError(words["payment_linked"])
    kind = kind if kind in ProjectPaymentKind.values else ProjectPaymentKind.COLLECTION
    if hasattr(payment, "instalment_link"):
        # It settles an instalment plan: a collection of this project only when the plan's invoice is on it,
        # never an advance (the plan already counts it against its invoice).
        plan_invoice = payment.instalment_link.plan.invoice_id
        if kind != ProjectPaymentKind.COLLECTION or not project.invoices.filter(invoice_id=plan_invoice).exists():
            raise ValidationError(words["instalment"])
    _same_entity(project, payment.cashbox, words)
    _in_scope(payment.cashbox, words)
    if kind == ProjectPaymentKind.ADVANCE:
        # An advance moves the payment's journal entry from receivable to
        # customer advances on its own date: a closed month stays closed.
        ensure_period_is_open(payment.payment_date)
    link = ProjectPayment.objects.create(project=project, payment=payment, kind=kind, created_by=user)
    _audit(project, user, "link_project_payment", {"payment": payment.payment_number, "kind": kind})
    return link


@transaction.atomic
def unlink_payment(project, link, user, lang="ar"):
    """Take a payment off the project. An advance a certificate already recovered stays."""

    from closing.services import ensure_period_is_open

    words = MESSAGES[lang]
    project = _held(project, lang)
    link = ProjectPayment.objects.select_related("payment__cashbox").get(pk=link.pk, project=project)
    _in_scope(link.payment.cashbox, words)
    if link.kind == ProjectPaymentKind.ADVANCE:
        ensure_period_is_open(link.payment.payment_date)  # the same entry moves back
    if link.kind == ProjectPaymentKind.ADVANCE and (advance_received(project) - Decimal(link.payment.amount) < advance_recovered(project)
                                                    or lowest_held(advance_events(project, leave_out=link.pk)) < 0):
        raise ValidationError(words["advance_used"])
    _audit(project, user, "unlink_project_payment", {"payment": link.payment.payment_number, "kind": link.kind})
    link.delete()


# ---- certificates ----

def _next_number(project):
    return (project.certificates.order_by("-number").values_list("number", flat=True).first() or 0) + 1


@transaction.atomic
def create_certificate(project, user, *, quantities=None, amount=None, description="", item=None, certificate_date=None, notes="", lang="ar"):
    """A draft progress certificate and its draft invoice.

    ``quantities`` maps a bill-of-quantities line id to the cumulative quantity
    done to date (blank or missing: unchanged). Without a bill of quantities,
    ``amount`` and ``description`` make a lump-sum certificate.
    """

    from master_data.models import Location
    from sales.models import SalesInvoice
    from taxes.services import create_sales_draft_with_tax
    from entities import scope as entity_scope

    words = MESSAGES[lang]
    project = _locked(project, lang)
    draft = live_certificates(project).filter(invoice__status="draft").first()
    if draft is not None:
        raise ValidationError(words["one_draft"].format(number=draft.number))
    boq = list(project.boq.select_for_update())
    quantities = quantities or {}
    rows = []
    if boq and not quantities and amount not in (None, ""):
        raise ValidationError(words["boq_needed"])
    if boq:
        for line in boq:
            raw = quantities.get(line.pk, quantities.get(str(line.pk), ""))
            previous = certified_quantity(line)
            if raw in (None, ""):
                continue
            cumulative = _quantity(raw, words, allow_zero=True)
            if cumulative < previous:
                raise ValidationError(words["below_previous"].format(code=line.code or line.line_number, qty=previous.normalize()))
            done = cumulative - previous
            if done > 0:
                rows.append((line, previous, done, money_round(done * Decimal(line.rate))))
        if not rows:
            raise ValidationError(words["no_work"])
        invoice_lines = [{"item": item or services.billing_item(), "quantity": done, "unit_sale_price": Decimal(line.rate), "line_discount_amount": ZERO,
                          "description": f"{line.code} {line.description}".strip()[:255]} for line, _, done, _ in rows]
        gross = money_round(sum((amount_ for *_, amount_ in rows), ZERO))
        label = (description or "").strip()
    else:
        if quantities:
            raise ValidationError(words["boq_needed"])
        gross = _money(amount, words)
        label = (description or "").strip()[:255]
        if not label:
            raise ValidationError(words["lump_description"])
        invoice_lines = [{"item": item or services.billing_item(), "quantity": Decimal("1"), "unit_sale_price": gross, "line_discount_amount": ZERO,
                          "description": label}]
    locations = entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True)
    established = project_entity(project)
    if established is not None:
        # One entity per project: its retention and advances are held in one place.
        locations = entity_locations(locations, established)
    location = locations.order_by("-is_default", "pk").first()
    if location is None:
        raise ValidationError(words["entity"] if established is not None else services.MESSAGES[lang]["setup"])
    number = _next_number(project)
    invoice_number = f"PB-{project.code}-{number:02d}"
    sequence = number
    while SalesInvoice.objects.filter(invoice_number=invoice_number).exists():
        sequence += 1
        invoice_number = f"PB-{project.code}-{sequence:02d}"
    retention = money_round(gross * Decimal(project.retention_rate) / HUNDRED)
    day = certificate_date or timezone.localdate()
    latest = live_certificates(project).order_by("-certificate_date").values_list("certificate_date", flat=True).first()
    if latest and day < latest:
        raise ValidationError(words["date_order"].format(date=latest.isoformat()))  # quantities to date run forward in time
    # Only what was received by the certificate's date, and stays unclaimed on every date after it.
    available = min(advance_left(project), max(lowest_held(advance_events(project), day), ZERO))
    recovery = min(money_round(gross * Decimal(project.advance_recovery_rate) / HUNDRED), available, max(gross - retention, ZERO))
    title = f"مستخلص رقم {number}" + (f" — {label}" if label else "")
    invoice = create_sales_draft_with_tax(
        {"invoice_number": invoice_number, "invoice_date": day, "customer": project.customer, "selling_location": location, "cashbox": None,
         "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO, "notes": f"مشروع {project.code}: {title}"[:255]},
        invoice_lines, user,
    )
    certificate = Certificate.objects.create(project=project, number=number, certificate_date=day, invoice=invoice, gross=gross,
                                             retention_rate=project.retention_rate, retention_amount=retention, recovery_amount=recovery,
                                             notes=(notes or label)[:255], created_by=user)
    invoice_lines = list(invoice.lines.order_by("line_number"))  # one invoice line per certified item, in order
    CertificateLine.objects.bulk_create([CertificateLine(certificate=certificate, boq_line=line, sales_line=sales_line, previous_quantity=previous,
                                                         quantity=done, rate=line.rate, amount=value)
                                         for (line, previous, done, value), sales_line in zip(rows, invoice_lines)])
    ProjectInvoice.objects.create(project=project, invoice=invoice, label=title[:120], created_by=user)
    _audit(project, user, "create_certificate", {"certificate": number, "invoice": invoice_number, "gross": str(gross),
                                                 "retention": str(retention), "recovery": str(recovery)}, AuditEventType.CREATE)
    return certificate


@transaction.atomic
def withdraw_certificate(project, certificate, user, lang="ar"):
    """Delete a draft certificate and its draft invoice (a draft has no ledger, stock or cash rows)."""

    words = MESSAGES[lang]
    project = _held(project, lang)
    certificate = Certificate.objects.select_related("invoice").select_for_update(of=("self",)).get(pk=certificate.pk, project=project)
    invoice = certificate.invoice
    if invoice.status != "draft":
        raise ValidationError(words["not_draft"])
    details = {"certificate": certificate.number, "invoice": invoice.invoice_number, "gross": str(certificate.gross), "total": str(invoice.total_amount)}
    ProjectInvoice.objects.filter(invoice=invoice).delete()
    certificate.delete()
    try:
        with transaction.atomic():
            invoice.delete()
    except ProtectedError:
        raise ValidationError(words["withdraw_blocked"])
    _audit(project, user, "withdraw_certificate", details, AuditEventType.DELETE)


def entity_of(location):
    from entities.services import main_entity

    return (location.entity_id if location else None) or main_entity().pk


def entity_locations(locations, entity_id):
    from django.db.models import Q

    from entities.services import main_entity

    if entity_id == main_entity().pk:
        return locations.filter(Q(entity_id=entity_id) | Q(entity__isnull=True))
    return locations.filter(entity_id=entity_id)


def project_entity(project):
    """The one entity a project's money lives in, for good: that of its first
    certificate (cancelled ones too, so a cancellation never moves the project
    and its past releases to another entity), else of its first linked
    invoice, else of its first linked payment (None before any). Every link
    is checked against it first, so these never disagree."""

    first = project.certificates.select_related("invoice__selling_location").order_by("number").first()
    if first is not None:
        return entity_of(first.invoice.selling_location)
    # An ordinary invoice already on the project counts too: its collections must stay linkable.
    linked = project.invoices.select_related("invoice__selling_location").order_by("pk").first()
    if linked is not None:
        return entity_of(linked.invoice.selling_location)
    payment = project.payments.select_related("payment__cashbox").order_by("pk").first()
    if payment is not None:
        from entities.services import main_entity

        return payment.payment.cashbox.entity_id or main_entity().pk
    return None


def _same_entity(project, cashbox, words):
    from entities.services import main_entity

    established = project_entity(project)
    if established is not None and (cashbox.entity_id or main_entity().pk) != established:
        raise ValidationError(words["entity"])


def in_reach(project):
    """Whether the entity being worked in holds this project's money: always
    for the whole group, and for an entity only when the project's money is
    there (or it has none yet)."""

    from entities.current import current_entity

    chosen = current_entity()
    if chosen is None:
        return True
    established = project_entity(project)
    return established is None or established == chosen.pk


def _reach(project, words):
    if not in_reach(project):
        raise ValidationError(words["entity"])


def _in_scope(cashbox, words):
    """A payment's cashbox must belong to the entity being worked in."""

    from cashboxes.models import Cashbox
    from entities import scope as entity_scope

    if not entity_scope.cashboxes(Cashbox.objects.filter(pk=cashbox.pk)).exists():
        raise ValidationError(words["entity"])


def net_payable(certificate):
    return money_round(Decimal(certificate.invoice.total_amount) - certificate.retention_amount - certificate.recovery_amount)


def certificate_rows(certificate):
    """The certificate as the owner reads it: per item, the previous, this and the cumulative quantity and value."""

    rows = []
    for line in certificate.lines.select_related("boq_line"):
        cumulative = line.previous_quantity + line.quantity
        rows.append({"line": line, "boq": line.boq_line, "cumulative": cumulative, "previous_value": money_round(line.previous_quantity * line.rate),
                     "cumulative_value": money_round(cumulative * line.rate)})
    return rows


# ---- retention ----

def retention_held(project):
    held = sum((effective(c)[1] for c in live_certificates(project).filter(invoice__status="posted").select_related("invoice")), ZERO)
    return money_round(held - retention_released(project))


def retention_released(project):
    return money_round(project.retention_releases.filter(reversed_on__isnull=True).aggregate(total=Sum("amount"))["total"] or ZERO)


def held_events(documents, releases):
    """[(date, change)] of the retention a set of documents holds: each posted
    document's retention on its date, each return's share off on its date (and
    back on the date it was cancelled), each release off on its date (and back
    when it was reversed). A cancelled document is reversed on its own date,
    so it never held anything. These are the general ledger's own dates."""

    events = []
    for invoice, amount in documents:
        if invoice.status != "posted" or not amount:
            continue
        events.append((invoice.invoice_date, Decimal(amount)))
        shares = return_shares(amount, invoice)
        for row in invoice.returns.all():
            share = shares.get(row.pk, ZERO)
            if not share:
                continue
            events.append((row.return_date, -share))
            if row.status != "posted":
                events.append((row.reversal_date or row.return_date, share))
    for release in releases:
        events.append((release.release_date, -Decimal(release.amount)))
        if release.reversed_on:
            events.append((release.reversed_on, Decimal(release.amount)))
    return events


def lowest_held(events, day=None):
    """The lowest balance of ``events`` on ``day`` and every date after it (on
    any date when ``day`` is None): what a release dated ``day`` may take."""

    changes = defaultdict(lambda: ZERO)
    for when, change in events:
        changes[when] += change
    running, before, lowest = ZERO, ZERO, None
    for when in sorted(changes):
        running += changes[when]
        if day is not None and when < day:
            before = running
            continue
        lowest = running if lowest is None else min(lowest, running)
    candidates = [] if lowest is None else [lowest]
    if day is not None and day not in changes:
        candidates.append(before)  # the balance on the day itself
    return money_round(min(candidates)) if candidates else ZERO


def advance_events(project, leave_out=None):
    """[(date, change)] of the advance not yet recovered: each posted advance on
    its payment date; each live certificate's recovery off on its invoice's
    date (drafts too, so two certificates never claim the same money), and
    back with its returns' shares on their dates."""

    events = [(link.payment.payment_date, Decimal(link.payment.amount))
              for link in _posted_payments(project, ProjectPaymentKind.ADVANCE).select_related("payment").exclude(pk=leave_out)]
    certificates = live_certificates(project).select_related("invoice")
    events += [(day, -change) for day, change in held_events([(c.invoice, c.recovery_amount) for c in certificates if c.invoice.status == "posted"], [])]
    events += [(c.invoice.invoice_date, -Decimal(c.recovery_amount)) for c in certificates if c.invoice.status == "draft" and c.recovery_amount]
    return events


def retention_events(project):
    certificates = project.certificates.select_related("invoice")
    return held_events([(c.invoice, c.retention_amount) for c in certificates], project.retention_releases.all())


def ensure_consistent(project, lang="en"):
    """HG-038: refuse any change (a cancellation, a return, a reversal) that
    would leave retention released beyond what is held, or certificates
    recovering more advance than was received. Called inside the change's own
    transaction, so a refusal undoes it."""

    # The same lock as certificates and releases take, so these never race them.
    project = Project.objects.select_for_update().get(pk=project.pk)
    if retention_held(project) < 0:
        raise ValidationError(CONSISTENCY[lang]["retention"])
    if lowest_held(retention_events(project)) < 0:
        # Held today, but not on some past date: a cancellation is dated back to its invoice, and a
        # release (even one reversed since) once took this retention. Disallowed outright: return instead.
        raise ValidationError(CONSISTENCY[lang]["retention_history"])
    if advance_recovered(project) > advance_received(project) or lowest_held(advance_events(project)) < 0:  # on any date
        raise ValidationError(CONSISTENCY[lang]["advance"])
    for line in project.boq.all():
        latest = CertificateLine.objects.filter(boq_line=line).exclude(certificate__invoice__status="cancelled").order_by("-certificate__number").first()
        if latest is not None and certified_quantity(line) > latest.previous_quantity + latest.quantity:
            raise ValidationError(CONSISTENCY[lang]["quantity"])


CONSISTENCY = {
    "en": {"retention": "This would leave more retention released than is held on the project; reverse the release first.",
           "advance": "Certificates on this project already recovered this advance; cancel or return those certificates first.",
           "quantity": "A later certificate already billed the quantity this return gave back; cancel or return that certificate first.",
           "retention_history": "Retention from this certificate was released at some point (even if that release was reversed later). A cancellation is dated back to the invoice and would rewrite that history, so it is not allowed: make a sales return instead."},
    "ar": {"retention": "كده الإفراج عن ضمان الأعمال هيبقى أكبر من المحتجز في المشروع؛ ألغِ الإفراج الأول.",
           "advance": "مستخلصات المشروع خصمت من الدفعة المقدمة دي بالفعل؛ ألغِ المستخلصات دي أو اعملها مرتجع الأول.",
           "quantity": "مستخلص بعده فوتر الكمية اللي المرتجع ده رجّعها؛ ألغِ المستخلص ده أو اعمله مرتجع الأول.",
           "retention_history": "ضمان الأعمال بتاع المستخلص ده اتفرج عنه قبل كده (حتى لو الإفراج اتلغى بعدها). الإلغاء بيرجع لتاريخ الفاتورة وهيغيّر اللي حصل، فمش مسموح: اعمل مرتجع مبيعات بدله."},
}


@transaction.atomic
def release_retention(project, user, *, amount, release_date=None, notes="", lang="ar"):
    from closing.services import ensure_period_is_open

    words = MESSAGES[lang]
    project = _held(project, lang)
    amount = _money(amount, words)
    day = release_date or timezone.localdate()
    ensure_period_is_open(day)  # the release is a dated ledger entry: closed months stay closed
    # What is held on that date and stays held on every date after it: a
    # release never runs ahead of the certificates (and returns) that fund it.
    held = max(lowest_held(retention_events(project), day), ZERO)
    if amount > held:
        raise ValidationError(words["release_more"].format(held=held))
    release = RetentionRelease.objects.create(project=project, release_date=day, amount=amount, notes=(notes or "").strip()[:255], created_by=user)
    _audit(project, user, "release_retention", {"amount": str(amount), "date": str(release.release_date)})
    return release


@transaction.atomic
def reverse_release(project, release, user, *, reversal_date=None, lang="ar"):
    """Undo a release on a date (append-only: the release stays, marked reversed)."""

    from closing.services import ensure_period_is_open

    words = MESSAGES[lang]
    project = _held(project, lang)
    release = RetentionRelease.objects.select_for_update().get(pk=release.pk, project=project)
    if release.reversed_on:
        raise ValidationError(words["reversed"])
    day = reversal_date or timezone.localdate()
    if day < release.release_date:
        raise ValidationError(words["date"])
    ensure_period_is_open(day)
    release.reversed_on, release.reversed_by = day, user
    release.save(update_fields=["reversed_on", "reversed_by"])
    _audit(project, user, "reverse_retention_release", {"release": release.pk, "amount": str(release.amount), "date": str(day)})
    return release


# ---- what the owner owes on this project ----

def position(project):
    """The owner's side of the contract, from posted documents only, as the
    entity being worked in sees it: its own invoices, certificates and
    payments (all of them for the whole group). Releases live in the
    project's entity, so another entity sees none."""

    from entities import scope as entity_scope
    from sales.models import SalesReturn

    links = entity_scope.scope(project.invoices.select_related("invoice").filter(invoice__status="posted"), "invoice__" + entity_scope.SALES_INVOICE)
    added = ZERO
    for link in links:
        invoice = link.invoice
        returned = SalesReturn.objects.filter(source_invoice=invoice, status="posted").aggregate(total=Sum("due_amount"))["total"] or ZERO
        added += Decimal(invoice.remaining_due) - returned
    certificates = entity_scope.scope(live_certificates(project).filter(invoice__status="posted"), "invoice__" + entity_scope.SALES_INVOICE)
    figures = [effective(c) for c in certificates.select_related("invoice")]
    gross = money_round(sum((f[0] for f in figures), ZERO))
    retention = money_round(sum((f[1] for f in figures), ZERO))
    released = retention_released(project) if in_reach(project) else ZERO
    recovered = money_round(sum((f[2] for f in figures), ZERO))
    payments = entity_scope.scope(project.payments.filter(payment__status="posted"), "payment__" + entity_scope.CUSTOMER_PAYMENT)
    collections = money_round(payments.filter(kind=ProjectPaymentKind.COLLECTION).aggregate(total=Sum("payment__amount"))["total"] or ZERO)
    received = money_round(payments.filter(kind=ProjectPaymentKind.ADVANCE).aggregate(total=Sum("payment__amount"))["total"] or ZERO)
    contract = contract_value(project)
    return {
        "contract": contract, "certified": gross, "certified_progress": int(min(gross / contract * 100, Decimal("999"))) if contract > 0 else 0,
        "retention": retention, "released": released, "retention_held": money_round(retention - released),
        "advance": received, "recovered": recovered, "advance_left": money_round(max(received - recovered, ZERO)),
        "collections": collections, "due_now": money_round(added - (retention - released) - recovered - collections),
    }


def due_now_many(projects):
    """{project id: due now} for a page of projects, as ``position`` computes it,
    in a handful of grouped queries instead of a dozen per project. A project
    whose certificates have returns (their shares need ``effective``) or whose
    releases depend on the working entity falls back to ``position`` itself."""

    from entities import scope as entity_scope
    from entities.current import current_entity
    from sales.models import SalesReturn

    ids = [project.pk for project in projects]
    if not ids:
        return {}

    def grouped(queryset, field):
        return {row["project_id"]: row["total"] or ZERO for row in queryset.values("project_id").annotate(total=Sum(field))}

    invoice_scope = "invoice__" + entity_scope.SALES_INVOICE
    links = entity_scope.scope(ProjectInvoice.objects.filter(project_id__in=ids, invoice__status="posted"), invoice_scope)
    remaining = grouped(links, "invoice__remaining_due")
    returned = {row["source_invoice__project_link__project_id"]: row["total"] or ZERO for row in
                SalesReturn.objects.filter(status="posted", source_invoice__project_link__in=links)
                .values("source_invoice__project_link__project_id").annotate(total=Sum("due_amount"))}
    certificates = entity_scope.scope(Certificate.objects.filter(project_id__in=ids, invoice__status="posted"), invoice_scope)
    retention, recovery = grouped(certificates, "retention_amount"), grouped(certificates, "recovery_amount")
    with_returns = set(certificates.filter(invoice__returns__isnull=False).values_list("project_id", flat=True))
    payments = entity_scope.scope(ProjectPayment.objects.filter(project_id__in=ids, payment__status="posted", kind=ProjectPaymentKind.COLLECTION),
                                  "payment__" + entity_scope.CUSTOMER_PAYMENT)
    collections = grouped(payments, "payment__amount")
    released = grouped(RetentionRelease.objects.filter(project_id__in=ids, reversed_on__isnull=True), "amount")
    scoped = current_entity() is not None
    result = {}
    for project in projects:
        pk = project.pk
        if pk in with_returns or (scoped and released.get(pk)):
            result[pk] = position(project)["due_now"]
            continue
        held = Decimal(retention.get(pk, ZERO)) - Decimal(released.get(pk, ZERO))
        result[pk] = money_round(Decimal(remaining.get(pk, ZERO)) - Decimal(returned.get(pk, ZERO)) - held
                                 - Decimal(recovery.get(pk, ZERO)) - Decimal(collections.get(pk, ZERO)))
    return result
