"""MED-003: the dental chart and the treatment plan in stages (HG-043, design A).

The chart is one ``ToothRecord`` per tooth that has something to say (a tooth
with no record is healthy). Teeth use FDI numbers: 11-48 for permanent teeth,
51-85 for primary ones.

The plan is a list of ``TreatmentStep``: a service on a tooth (or on the whole
mouth), in a numbered stage. A step is planned, then done (on a day, by a
doctor, optionally in a visit) or cancelled. The done steps that are not on an
invoice yet are billed together as one ordinary draft sales invoice through
``taxes.services.create_sales_draft_with_tax``; posting it on the sales page
is what touches the customer's account and the cashbox. If that invoice is
cancelled, its steps can be billed again.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import StepStatus, ToothCondition, ToothRecord, TreatmentStep

ZERO = Decimal("0")
BILL = "sales.create_sales_invoice"
PRICE_LIMIT = Decimal("10000000")
MAX_STAGE = 20

#: Display order of the chart, as the dentist faces the patient: upper right, upper left / lower right, lower left.
PERMANENT = ((18, 17, 16, 15, 14, 13, 12, 11), (21, 22, 23, 24, 25, 26, 27, 28),
             (48, 47, 46, 45, 44, 43, 42, 41), (31, 32, 33, 34, 35, 36, 37, 38))
PRIMARY = ((55, 54, 53, 52, 51), (61, 62, 63, 64, 65), (85, 84, 83, 82, 81), (71, 72, 73, 74, 75))
TEETH = frozenset(tooth for row in PERMANENT + PRIMARY for tooth in row)

CONDITION_LABELS = {
    "ar": {"healthy": "سليمة", "caries": "تسوّس", "filled": "حشو", "root_canal": "حشو عصب", "crown": "طربوش", "bridge": "كوبري",
           "implant": "زرعة", "fractured": "مكسورة", "missing": "مخلوعة / مش موجودة"},
    "en": {"healthy": "Healthy", "caries": "Caries", "filled": "Filled", "root_canal": "Root canal", "crown": "Crown", "bridge": "Bridge",
           "implant": "Implant", "fractured": "Fractured", "missing": "Missing"},
}
STATUS_LABELS = {
    "ar": {"planned": "مخطط", "done": "اتعمل", "cancelled": "اتلغى"},
    "en": {"planned": "Planned", "done": "Done", "cancelled": "Cancelled"},
}
MESSAGES = {
    "ar": {"tooth": "رقم السنة مش صحيح (ترقيم FDI: من 11 لـ 48، وللّبني من 51 لـ 85).", "condition": "اختار حالة السنة.",
           "service": "اختار الخدمة.", "price": "السعر لازم يكون رقم صفر أو أكبر.", "stage": "رقم المرحلة من 1 لـ 20.",
           "not_planned": "الخطوة دي مش مخططة؛ مينفعش تتغيّر.", "nothing": "مفيش خطوات اتعملت ولسه ماتفوترتش.",
           "setup": "لازم يكون فيه مخزن بيع نشط.", "bill": "عمل الفاتورة محتاج صلاحية إنشاء فاتورة بيع.",
           "date": "التاريخ مش مظبوط.", "future": "مينفعش خطوة تتسجّل إنها اتعملت في يوم لسه مجاش.",
           "note_invoice": "علاج أسنان: {steps}"},
    "en": {"tooth": "The tooth number is not valid (FDI: 11 to 48, primary teeth 51 to 85).", "condition": "Choose the tooth's condition.",
           "service": "Choose the service.", "price": "The price must be zero or more.", "stage": "The stage is a number from 1 to 20.",
           "not_planned": "This step is not planned any more; it cannot change.", "nothing": "There are no done steps waiting for an invoice.",
           "setup": "An active selling location is needed.", "bill": "Making the invoice needs the create-sales-invoice permission.",
           "date": "The date is not valid.", "future": "A step cannot be recorded as done on a day still to come.",
           "note_invoice": "Dental treatment: {steps}"},
}


def is_dental():
    from entities.current import effective_activity

    return effective_activity() == ("medical", "dental")


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _tooth(raw, words, allow_none=False):
    if raw in (None, "") and allow_none:
        return None
    try:
        tooth = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValidationError(words["tooth"])
    if tooth not in TEETH:
        raise ValidationError(words["tooth"])
    return tooth


def _price(raw, default, words):
    if raw in (None, ""):
        return money_round(default or ZERO)
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["price"])
    if not value.is_finite() or value < 0 or value >= PRICE_LIMIT:
        raise ValidationError(words["price"])
    return money_round(value)


def _audit(user, action, patient, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="medical", action=action, object_type="master_data.Customer",
                            object_id=str(patient.pk), before_data=before or {}, after_data=after)


def chart(patient):
    """{tooth: ToothRecord} for the patient's recorded teeth."""

    return {record.tooth: record for record in ToothRecord.objects.filter(patient=patient)}


@transaction.atomic
def set_tooth(patient, tooth, condition, user, notes="", lang="ar"):
    words = _words(lang)
    tooth = _tooth(tooth, words)
    if condition not in ToothCondition.values:
        raise ValidationError(words["condition"])
    notes = (notes or "").strip()[:255]
    record = ToothRecord.objects.select_for_update().filter(patient=patient, tooth=tooth).first()
    before = {"tooth": tooth, "condition": record.condition, "notes": record.notes} if record else {}
    if record is None:
        try:
            with transaction.atomic():
                record = ToothRecord.objects.create(patient=patient, tooth=tooth, condition=condition, notes=notes, updated_by=user)
        except IntegrityError:  # two saves of the same tooth at once: the second updates the first
            record = ToothRecord.objects.select_for_update().get(patient=patient, tooth=tooth)
    record.condition, record.notes, record.updated_by = condition, notes, user
    record.save()
    _audit(user, "set_tooth", patient, {"tooth": tooth, "condition": condition, "notes": notes}, before)
    return record


@transaction.atomic
def plan_step(patient, data, user, lang="ar"):
    """Add a planned step. ``data``: service (Item), tooth (optional), price (default the service's), stage, notes, doctor."""

    words = _words(lang)
    service = data.get("service")
    if service is None:
        raise ValidationError(words["service"])
    tooth = _tooth(data.get("tooth"), words, allow_none=True)
    try:
        stage = int(str(data.get("stage") or 1).strip())
    except (TypeError, ValueError):
        raise ValidationError(words["stage"])
    if not 1 <= stage <= MAX_STAGE:
        raise ValidationError(words["stage"])
    step = TreatmentStep.objects.create(patient=patient, tooth=tooth, service=service, price=_price(data.get("price"), service.default_sale_price, words),
                                        stage=stage, notes=(data.get("notes") or "").strip()[:255], doctor=data.get("doctor"), created_by=user)
    _audit(user, "plan_treatment_step", patient, {"step": step.pk, "tooth": tooth, "service": service.item_code, "price": str(step.price), "stage": stage},
           event=AuditEventType.CREATE)
    return step


def _locked_planned(step, words):
    step = TreatmentStep.objects.select_for_update().select_related("patient", "service").get(pk=step.pk)
    if step.status != StepStatus.PLANNED:
        raise ValidationError(words["not_planned"])
    return step


@transaction.atomic
def complete_step(step, user, *, done_on=None, doctor=None, visit=None, lang="ar"):
    words = _words(lang)
    step = _locked_planned(step, words)
    done_on = done_on or timezone.localdate()
    if done_on > timezone.localdate():
        raise ValidationError(words["future"])
    step.status, step.done_on = StepStatus.DONE, done_on
    if doctor is not None:
        step.doctor = doctor
    if visit is not None and visit.patient_id == step.patient_id:
        step.visit = visit
    step.save()
    _audit(user, "complete_treatment_step", step.patient, {"step": step.pk, "status": "done", "done_on": done_on.isoformat()}, {"status": "planned"})
    return step


@transaction.atomic
def cancel_step(step, user, lang="ar"):
    words = _words(lang)
    step = _locked_planned(step, words)
    step.status = StepStatus.CANCELLED
    step.save(update_fields=["status"])
    _audit(user, "cancel_treatment_step", step.patient, {"step": step.pk, "status": "cancelled"}, {"status": "planned"})
    return step


def unbilled(patient):
    """Done steps not on a live invoice (never billed, or their invoice was cancelled)."""

    return (TreatmentStep.objects.filter(patient=patient, status=StepStatus.DONE)
            .filter(Q(invoice__isnull=True) | Q(invoice__status="cancelled")).select_related("service"))


def plan_totals(patient):
    steps = list(TreatmentStep.objects.filter(patient=patient).exclude(status=StepStatus.CANCELLED))
    planned = sum((step.price for step in steps if step.status == StepStatus.PLANNED), ZERO)
    done = sum((step.price for step in steps if step.status == StepStatus.DONE), ZERO)
    waiting = sum((step.price for step in unbilled(patient)), ZERO)
    return {"planned": planned, "done": done, "plan": planned + done, "unbilled": waiting}


@transaction.atomic
def bill_done(patient, user, lang="ar"):
    """One draft sales invoice for the patient's done, unbilled steps. Returns the invoice."""

    from config.numbering import next_in_series
    from entities import scope as entity_scope
    from master_data.models import Customer, Location
    from permissions.services import user_has_permission
    from sales.models import SalesInvoice
    from taxes.services import create_sales_draft_with_tax

    words = _words(lang)
    if not user_has_permission(user, BILL):
        raise PermissionDenied(words["bill"])
    Customer.objects.select_for_update().filter(pk=patient.pk).exists()  # two clicks at once never bill a step twice
    steps = list(unbilled(patient).select_for_update(of=("self",)))
    if not steps:
        raise ValidationError(words["nothing"])
    location = entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()
    if location is None:
        raise ValidationError(words["setup"])
    rows = [{"item": step.service, "quantity": Decimal("1"), "unit_sale_price": step.price, "line_discount_amount": ZERO,
             "description": (f"{step.service.item_name} — {step.tooth}" if step.tooth else step.service.item_name)[:255]} for step in steps]
    header = {"invoice_date": timezone.localdate(), "customer": patient, "selling_location": location, "cashbox": None,
              "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO,
              "notes": words["note_invoice"].format(steps=", ".join(str(step.tooth or "—") for step in steps))[:255]}
    invoice = None
    for attempt in range(5):
        number = next_in_series(SalesInvoice, "invoice_number", "SI-")
        try:
            with transaction.atomic():
                invoice = create_sales_draft_with_tax({**header, "invoice_number": number}, rows, user)
            break
        except IntegrityError:
            if attempt == 4 or not SalesInvoice.objects.filter(invoice_number=number).exists():
                raise
    TreatmentStep.objects.filter(pk__in=[step.pk for step in steps]).update(invoice=invoice)
    _audit(user, "bill_treatment_steps", patient, {"invoice": invoice.invoice_number, "steps": [step.pk for step in steps],
                                                   "total": str(invoice.total_amount)})
    return invoice
