"""MED-008: medical insurance (HG-042, design A: two ordinary invoices per insured visit).

An insurance company is an ordinary customer carrying a contract: the share of
a visit it pays (one figure, with an own figure for any service that differs)
and the day of the month its claim is sent. A patient is covered by one
insurer, with a member number and an optional expiry date.

An insured appointment is billed as two ordinary draft sales invoices through
the unchanged tax-aware sales engine: the patient's for their share, and the
insurer's for the rest (the rounding piastre goes to the insurer). Posting
them is what moves each customer's account; the claim is a read-only list of
the insurer's posted invoices in a month, and the insurer pays it through the
ordinary customer collection. A rejected line is the existing sales return
against the insurer's invoice. Nothing here posts, or touches a ledger, aging
or a report calculation.
"""

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from .models import InsuranceContract, InsuredBill, PatientCover, ServiceCoverage

ZERO = Decimal("0")
HUNDRED = Decimal("100")
VIEW = "sales.view_sales_invoices"
MANAGE = "sales.create_sales_invoice"

MESSAGES = {
    "ar": {"manage": "التأمين محتاج صلاحية إنشاء فاتورة بيع.", "insurer": "اختار شركة التأمين أو اكتب اسمها.",
           "percent": "نسبة التحمّل لازم تكون رقم أكبر من صفر ولحد 100.", "service_percent": "نسبة الخدمة لازم تكون رقم من 0 لـ 100.",
           "claim_day": "يوم المطالبة من 1 لـ 28.", "taken": "الشركة دي ليها عقد بالفعل.", "patient_insurer": "شركة التأمين مينفعش تكون نفس المريض.",
           "service": "اختار الخدمة.", "contract": "اختار شركة تأمين شغالة.", "date": "التاريخ مش مظبوط.",
           "no_cover": "المريض ده مش عليه تأمين شغال؛ اعمل الفاتورة العادية.", "expired": "تأمين المريض انتهى يوم {date}.",
           "not_covered": "الخدمة دي مش مغطاة في عقد {insurer}؛ اعمل الفاتورة العادية.", "no_service": "اختار الخدمة الأول عشان تتعمل الفاتورة.",
           "not_done": "الميعاد لازم يكون وصل أو خلص عشان يتفوتر.", "billed": "الميعاد ده اتعملت فاتورته بالفعل.", "setup": "لازم يكون فيه مخزن بيع نشط.",
           "patient_line": "{service} — نصيب المريض {share}%", "insurer_line": "{service} — {patient}{member} — تحمّل {percent}%",
           "patient_note": "ميعاد {number}: نصيب المريض", "insurer_note": "مطالبة تأمين — ميعاد {number}", "member": " — رقم {member}",
           "month": "الشهر مش مظبوط."},
    "en": {"manage": "Insurance needs the create-sales-invoice permission.", "insurer": "Choose the insurance company or type its name.",
           "percent": "The covered share must be a number above zero and up to 100.", "service_percent": "A service's share must be a number from 0 to 100.",
           "claim_day": "The claim day is from 1 to 28.", "taken": "This company already has a contract.", "patient_insurer": "The insurer cannot be the patient.",
           "service": "Choose the service.", "contract": "Choose an active insurance company.", "date": "The date is not valid.",
           "no_cover": "This patient has no active insurance; make the ordinary invoice.", "expired": "The patient's insurance expired on {date}.",
           "not_covered": "This service is not covered by {insurer}'s contract; make the ordinary invoice.", "no_service": "Choose the service first to make the invoice.",
           "not_done": "The appointment must be arrived or done to be billed.", "billed": "This appointment already has its invoice.", "setup": "An active selling location is needed.",
           "patient_line": "{service} — patient's share {share}%", "insurer_line": "{service} — {patient}{member} — covered {percent}%",
           "patient_note": "Appointment {number}: patient's share", "insurer_note": "Insurance claim — appointment {number}", "member": " — member {member}",
           "month": "The month is not valid."},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def is_insurance_install():
    """Clinics, medical centres, hospitals, labs, dental and physio; not vets."""

    from entities.current import effective_activity

    activity, sub = effective_activity()
    return activity == "medical" and sub != "vet"


def can_view(user):
    return is_insurance_install() and user_has_permission(user, VIEW)


def can_manage(user):
    return user_has_permission(user, MANAGE)


def _audit(user, action, obj, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="medical", action=action, object_type=f"medical.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


def _percent(raw, message, allow_zero=False):
    try:
        value = Decimal(str(raw if raw is not None else "").strip().replace("%", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError(message)
    if not value.is_finite() or value > HUNDRED or value < 0 or (value == 0 and not allow_zero):
        raise ValidationError(message)
    value = value.quantize(Decimal("0.01"))
    if value == 0 and not allow_zero:
        raise ValidationError(message)
    return value


def contracts(active_only=False):
    rows = InsuranceContract.objects.select_related("insurer")
    return rows.filter(active=True, insurer__active=True) if active_only else rows


@transaction.atomic
def save_contract(data, user, contract=None, lang="ar"):
    """Create or change a contract. ``data``: insurer (a customer) or name (a new one), coverage_percent, claim_day, notes, active."""

    from config.numbering import next_in_series
    from master_data.models import Customer

    words = _words(lang)
    if not can_manage(user):
        raise PermissionDenied(words["manage"])
    percent = _percent(data.get("coverage_percent"), words["percent"])
    try:
        claim_day = int(str(data.get("claim_day") or "1").strip())
    except ValueError:
        raise ValidationError(words["claim_day"])
    if not 1 <= claim_day <= 28:
        raise ValidationError(words["claim_day"])
    if contract is None:
        insurer = data.get("insurer")
        name = (data.get("name") or "").strip()[:255]
        if insurer is None and not name:
            raise ValidationError(words["insurer"])
        if insurer is not None and InsuranceContract.objects.filter(insurer=insurer).exists():
            raise ValidationError(words["taken"])
        if insurer is None:
            insurer = Customer.objects.create(customer_code=next_in_series(Customer, "customer_code", "C-"), name=name,
                                              phone=(data.get("phone") or "").strip()[:50])
        contract = InsuranceContract(insurer=insurer, created_by=user)
        before = {}
    else:
        contract = InsuranceContract.objects.select_for_update().get(pk=contract.pk)
        before = {"coverage_percent": str(contract.coverage_percent), "claim_day": contract.claim_day, "active": contract.active}
    contract.coverage_percent, contract.claim_day = percent, claim_day
    contract.notes = (data.get("notes") or "").strip()[:255]
    contract.active = bool(data.get("active", True))
    creating = contract.pk is None
    contract.save()
    _audit(user, "create_insurance_contract" if creating else "update_insurance_contract", contract,
           {"insurer": contract.insurer_id, "coverage_percent": str(percent), "claim_day": claim_day, "active": contract.active}, before,
           AuditEventType.CREATE if creating else AuditEventType.UPDATE)
    return contract


@transaction.atomic
def set_service_percent(contract, service, raw, user, lang="ar"):
    """A service's own share in a contract; a blank figure removes it (the contract's share applies again)."""

    words = _words(lang)
    if not can_manage(user):
        raise PermissionDenied(words["manage"])
    if service is None:
        raise ValidationError(words["service"])
    if raw in (None, ""):
        ServiceCoverage.objects.filter(contract=contract, service=service).delete()
        _audit(user, "remove_service_coverage", contract, {"service": service.pk})
        return None
    percent = _percent(raw, words["service_percent"], allow_zero=True)
    row, _ = ServiceCoverage.objects.update_or_create(contract=contract, service=service, defaults={"percent": percent})
    _audit(user, "set_service_coverage", contract, {"service": service.pk, "percent": str(percent)})
    return row


def percent_for(contract, service):
    """The share of ``service`` the insurer pays: the service's own figure, else the contract's."""

    own = ServiceCoverage.objects.filter(contract=contract, service=service).values_list("percent", flat=True).first()
    return contract.coverage_percent if own is None else own


def split(price, percent):
    """(insurer's share, patient's share) of ``price``: the patient's is rounded, the insurer takes the rest, so they add up to the piastre."""

    price = money_round(price)
    patient = money_round(price * (HUNDRED - percent) / HUNDRED)
    return price - patient, patient


@transaction.atomic
def set_cover(patient, data, user, lang="ar"):
    """Put a patient on an insurer (``data``: contract, member_number, expires_on), or take them off it (no contract)."""

    from .services import can_write

    words = _words(lang)
    if not (can_manage(user) or can_write(user)):
        raise PermissionDenied(words["manage"])
    contract = data.get("contract")
    current = PatientCover.objects.select_for_update().filter(patient=patient).first()
    if contract is None:
        if current is not None:
            _audit(user, "remove_patient_cover", current, {}, {"contract": current.contract_id, "member_number": current.member_number},
                   AuditEventType.DELETE)
            current.delete()
        return None
    if not contract.active:
        raise ValidationError(words["contract"])
    if contract.insurer_id == patient.pk:
        raise ValidationError(words["patient_insurer"])
    raw = data.get("expires_on")
    try:
        expires = date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        raise ValidationError(words["date"])
    cover = current or PatientCover(patient=patient)
    before = {"contract": cover.contract_id, "member_number": cover.member_number} if current else {}
    cover.contract, cover.expires_on, cover.updated_by = contract, expires, user
    cover.member_number = (data.get("member_number") or "").strip()[:60]
    cover.save()
    _audit(user, "set_patient_cover", cover, {"patient": patient.pk, "contract": contract.pk, "member_number": cover.member_number,
                                              "expires_on": expires.isoformat() if expires else ""}, before)
    return cover


def cover_for(patient, day=None):
    """The patient's cover when it can be billed on ``day`` (active insurer, not expired), else None."""

    cover = PatientCover.objects.select_related("contract__insurer").filter(patient=patient).first()
    if cover is None or not cover.contract.active or not cover.contract.insurer.active:
        return None
    if cover.expires_on and cover.expires_on < (day or timezone.localdate()):
        return None
    return cover


@transaction.atomic
def bill_insured(appointment, user, lang="ar"):
    """The insured appointment's two draft invoices. Returns the ``InsuredBill``."""

    from appointments.models import Appointment, AppointmentStatus
    from master_data.models import Location
    from taxes.services import create_sales_draft_with_tax

    words = _words(lang)
    if not can_manage(user):
        raise PermissionDenied(words["manage"])
    appointment = Appointment.objects.select_for_update(of=("self",)).select_related("service", "customer").get(pk=appointment.pk)
    if appointment.invoice_id:  # two clicks never make four invoices
        raise ValidationError(words["billed"])
    if appointment.status not in (AppointmentStatus.ARRIVED, AppointmentStatus.DONE):
        raise ValidationError(words["not_done"])
    if appointment.service is None:
        raise ValidationError(words["no_service"])
    today = timezone.localdate()
    cover = PatientCover.objects.select_related("contract__insurer").filter(patient=appointment.customer).first()
    if cover is not None and cover.expires_on and cover.expires_on < today:
        raise ValidationError(words["expired"].format(date=cover.expires_on.isoformat()))
    cover = cover_for(appointment.customer, today)
    if cover is None:
        raise ValidationError(words["no_cover"])
    contract = cover.contract
    percent = percent_for(contract, appointment.service)
    insurer_share, patient_share = split(appointment.price, percent)
    if insurer_share <= 0:
        raise ValidationError(words["not_covered"].format(insurer=contract.insurer.name))
    location = entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()
    if location is None:
        raise ValidationError(words["setup"])
    service = appointment.service.item_name
    member = words["member"].format(member=cover.member_number) if cover.member_number else ""
    header = {"invoice_date": today, "selling_location": location, "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO}

    def line(amount, description):
        return [{"item": appointment.service, "quantity": Decimal("1"), "unit_sale_price": amount, "line_discount_amount": ZERO,
                 "description": description[:255]}]

    insurer_invoice = create_sales_draft_with_tax(
        {**header, "invoice_number": f"INV-{appointment.number}-INS", "customer": contract.insurer,
         "notes": words["insurer_note"].format(number=appointment.number)[:255]},
        line(insurer_share, words["insurer_line"].format(service=service, patient=appointment.customer.name, member=member, percent=_figure(percent))), user)
    patient_invoice = None
    if patient_share > 0:
        patient_invoice = create_sales_draft_with_tax(
            {**header, "invoice_number": f"INV-{appointment.number}", "customer": appointment.customer,
             "notes": words["patient_note"].format(number=appointment.number)[:255]},
            line(patient_share, words["patient_line"].format(service=service, share=_figure(HUNDRED - percent))), user)
    bill = InsuredBill.objects.create(appointment=appointment, contract=contract, member_number=cover.member_number, price=money_round(appointment.price),
                                      percent=percent, insurer_share=insurer_share, patient_share=patient_share, insurer_invoice=insurer_invoice,
                                      patient_invoice=patient_invoice, created_by=user)
    appointment.invoice = patient_invoice or insurer_invoice  # the appointment shows as billed, so it is never billed again
    fields = ["invoice"]
    if appointment.status != AppointmentStatus.DONE:
        appointment.status = AppointmentStatus.DONE
        fields.append("status")
    appointment.save(update_fields=fields)
    _audit(user, "bill_insured_appointment", bill, {"appointment": appointment.number, "insurer": contract.insurer_id, "percent": str(percent),
                                                    "insurer_invoice": insurer_invoice.invoice_number, "insurer_share": str(insurer_share),
                                                    "patient_invoice": patient_invoice.invoice_number if patient_invoice else "",
                                                    "patient_share": str(patient_share)}, event=AuditEventType.CREATE)
    return bill


def _figure(percent):
    """50.00 → 50, 12.50 → 12.5."""

    text = f"{percent:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def bill_of(appointment):
    return InsuredBill.objects.select_related("contract__insurer", "insurer_invoice", "patient_invoice").filter(appointment=appointment).first()


def bills_for(appointments):
    """{appointment pk: its InsuredBill} for those of ``appointments`` billed with insurance, in one query."""

    ids = [appointment.pk for appointment in appointments]
    return {bill.appointment_id: bill for bill in InsuredBill.objects.filter(appointment_id__in=ids).select_related("insurer_invoice", "patient_invoice")}


def invoices_of(appointment, bill=None):
    """Every invoice an appointment was billed on: its own, and both of an insured one (``bill`` when already read)."""

    found = [appointment.invoice] if appointment.invoice_id else []
    if bill is None:
        bill = InsuredBill.objects.select_related("insurer_invoice", "patient_invoice").filter(appointment_id=appointment.pk).first()
    if bill is not None:
        for invoice in (bill.patient_invoice, bill.insurer_invoice):
            if invoice is not None and invoice.pk not in {one.pk for one in found}:
                found.append(invoice)
    return found


def month_bounds(raw, today=None, lang="ar"):
    """(first day, last day) of a "YYYY-MM" month; this month when blank."""

    today = today or timezone.localdate()
    if not raw:
        return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])
    try:
        year, month = (int(part) for part in str(raw).split("-"))
        first = date(year, month, 1)
    except (TypeError, ValueError):
        raise ValidationError(_words(lang)["month"])
    return first, first.replace(day=monthrange(year, month)[1])


def claim(contract, first, last):
    """The insurer's claim for [first, last]: its posted insured invoices in this entity, with what was returned (rejected) on each.

    Returns {"rows": [...], "totals": {...}}; read-only."""

    from sales.models import SalesReturn

    bills = list(entity_scope.scope(InsuredBill.objects, "insurer_invoice__" + entity_scope.SALES_INVOICE)
                 .filter(contract=contract, insurer_invoice__status="posted", insurer_invoice__invoice_date__gte=first,
                         insurer_invoice__invoice_date__lte=last)
                 .select_related("appointment__customer", "appointment__service", "insurer_invoice")
                 .order_by("insurer_invoice__invoice_date", "pk"))
    returned = dict(SalesReturn.objects.filter(source_invoice__in=[bill.insurer_invoice_id for bill in bills], status="posted")
                    .values("source_invoice").annotate(total=Sum("total_amount")).values_list("source_invoice", "total"))
    rows, totals = [], {"count": 0, "price": ZERO, "claimed": ZERO, "rejected": ZERO, "net": ZERO, "patient": ZERO}
    for bill in bills:
        invoice = bill.insurer_invoice
        back = money_round(Decimal(returned.get(invoice.pk) or 0))
        net = money_round(Decimal(invoice.total_amount) - back)
        rows.append({"bill": bill, "invoice": invoice, "patient": bill.appointment.customer, "service": bill.appointment.service,
                     "claimed": invoice.total_amount, "rejected": back, "net": net})
        totals["count"] += 1
        totals["price"] += bill.price
        totals["claimed"] += invoice.total_amount
        totals["rejected"] += back
        totals["net"] += net
        totals["patient"] += bill.patient_share
    return {"rows": rows, "totals": totals}


def unpaired():
    """Insured visits where one invoice was cancelled and the other was not: each needs a look (cancel the other, or bill again)."""

    from django.db.models import Q

    one_side = (Q(insurer_invoice__status="cancelled") & ~Q(patient_invoice__status="cancelled")) | (
        ~Q(insurer_invoice__status="cancelled") & Q(patient_invoice__status="cancelled"))
    return list(entity_scope.scope(InsuredBill.objects, "insurer_invoice__" + entity_scope.SALES_INVOICE)
                .filter(one_side, patient_invoice__isnull=False).select_related("appointment__customer", "insurer_invoice", "patient_invoice", "contract__insurer"))


def next_claim_day(contract, today=None):
    """The next day the contract's claim is due: its claim day this month, or next month's once it has passed."""

    today = today or timezone.localdate()
    if today.day <= contract.claim_day:
        return today.replace(day=contract.claim_day)
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(year, month, contract.claim_day)


def outstanding(contract):
    """What the insurer owes now: its customer balance (never below zero)."""

    from parties.services import balance

    return max(balance("customer", contract.insurer), ZERO)
