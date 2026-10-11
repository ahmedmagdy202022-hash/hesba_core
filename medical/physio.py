"""MED-005: physiotherapy session packages (HG-043, design A).

A package (say ten sessions at 3000) is sold on one ordinary draft sales
invoice, made through ``taxes.services.create_sales_draft_with_tax``. Its
revenue counts when that invoice is posted, like any service; spreading it
over the sessions (deferred revenue) would change ledger posting and is not
part of this design.

Sessions are used one by one once the invoice is posted. A package never goes
below zero sessions. A session entered by mistake is cancelled (kept, and
audited), which gives it back. A package whose invoice was cancelled can no
longer be used.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import PackageSession, SessionPackage

ZERO = Decimal("0")
SELL = "sales.create_sales_invoice"
MAX_SESSIONS = 200
PRICE_LIMIT = Decimal("10000000")

MESSAGES = {
    "ar": {"service": "اختار الخدمة.", "sessions": "عدد الجلسات من 1 لـ 200.", "price": "سعر الباكدج لازم يكون رقم صفر أو أكبر.",
           "setup": "لازم يكون فيه مخزن بيع نشط.", "sell": "بيع باكدج محتاج صلاحية إنشاء فاتورة بيع.",
           "not_posted": "فاتورة الباكدج لسه مسودة؛ رحّلها الأول عشان الجلسات تتسحب منها.", "void": "فاتورة الباكدج اتلغت؛ الباكدج ده مبقاش شغال.",
           "empty": "الباكدج خلص؛ مفيش جلسات باقية.", "date": "التاريخ مش مظبوط.", "future": "مينفعش تسجّل جلسة في يوم لسه مجاش.",
           "already": "الجلسة دي اتلغت قبل كده.", "line": "باكدج {sessions} جلسة — {service}", "note_invoice": "باكدج جلسات: {sessions} جلسة"},
    "en": {"service": "Choose the service.", "sessions": "The number of sessions is from 1 to 200.", "price": "The package price must be zero or more.",
           "setup": "An active selling location is needed.", "sell": "Selling a package needs the create-sales-invoice permission.",
           "not_posted": "The package's invoice is still a draft; post it first so sessions can be used from it.",
           "void": "The package's invoice was cancelled; this package can no longer be used.", "empty": "The package is used up; no sessions are left.",
           "date": "The date is not valid.", "future": "A session cannot be recorded on a day still to come.", "already": "This session was already cancelled.",
           "line": "Package of {sessions} sessions — {service}", "note_invoice": "Session package: {sessions} sessions"},
}


def is_physio():
    from entities.current import effective_activity

    return effective_activity() == ("medical", "physio")


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def used(package):
    return package.uses.filter(cancelled=False).count()


def state(package):
    """{"used", "left", "status"}: status is draft (invoice not posted), active, finished or void."""

    count = used(package)
    status = package.invoice.status
    if status == "cancelled":
        label = "void"
    elif status != "posted":
        label = "draft"
    else:
        label = "finished" if count >= package.sessions else "active"
    return {"used": count, "left": max(package.sessions - count, 0), "status": label}


def _audit(user, action, package, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="medical", action=action, object_type="medical.SessionPackage",
                            object_id=str(package.pk), before_data=before or {}, after_data=after)


@transaction.atomic
def sell(patient, data, user, lang="ar"):
    """Sell a package: one draft invoice for it. ``data``: service, sessions, price (default sessions × the service's price), notes."""

    from config.numbering import next_in_series
    from entities import scope as entity_scope
    from master_data.models import Location
    from permissions.services import user_has_permission
    from sales.models import SalesInvoice
    from taxes.services import create_sales_draft_with_tax

    words = _words(lang)
    if not user_has_permission(user, SELL):
        raise PermissionDenied(words["sell"])
    service = data.get("service")
    if service is None:
        raise ValidationError(words["service"])
    try:
        sessions = int(str(data.get("sessions") or "").strip())
    except ValueError:
        raise ValidationError(words["sessions"])
    if not 1 <= sessions <= MAX_SESSIONS:
        raise ValidationError(words["sessions"])
    raw = data.get("price")
    if raw in (None, ""):
        price = money_round(Decimal(str(service.default_sale_price or 0)) * sessions)
        if price >= PRICE_LIMIT:
            raise ValidationError(words["price"])
    else:
        try:
            price = Decimal(str(raw).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            raise ValidationError(words["price"])
        if not price.is_finite() or price < 0 or price >= PRICE_LIMIT:
            raise ValidationError(words["price"])
        price = money_round(price)
    location = entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()
    if location is None:
        raise ValidationError(words["setup"])
    rows = [{"item": service, "quantity": Decimal("1"), "unit_sale_price": price, "line_discount_amount": ZERO,
             "description": words["line"].format(sessions=sessions, service=service.item_name)[:255]}]
    header = {"invoice_date": timezone.localdate(), "customer": patient, "selling_location": location, "cashbox": None,
              "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO, "notes": words["note_invoice"].format(sessions=sessions)[:255]}
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
    package = SessionPackage.objects.create(patient=patient, service=service, sessions=sessions, price=price, invoice=invoice,
                                            notes=(data.get("notes") or "").strip()[:255], created_by=user)
    _audit(user, "sell_session_package", package, {"patient": patient.pk, "sessions": sessions, "price": str(price), "invoice": invoice.invoice_number},
           event=AuditEventType.CREATE)
    return package


@transaction.atomic
def use(package, user, *, used_on=None, therapist=None, visit=None, notes="", lang="ar"):
    """Use one session. Refused while the invoice is a draft, once it is cancelled, or when nothing is left."""

    words = _words(lang)
    package = SessionPackage.objects.select_for_update(of=("self",)).select_related("invoice").get(pk=package.pk)  # two at once never overdraw it
    status = package.invoice.status
    if status == "cancelled":
        raise ValidationError(words["void"])
    if status != "posted":
        raise ValidationError(words["not_posted"])
    if used(package) >= package.sessions:
        raise ValidationError(words["empty"])
    day = used_on or timezone.localdate()
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    session = PackageSession.objects.create(package=package, used_on=day, therapist=therapist,
                                            visit=visit if visit is not None and visit.patient_id == package.patient_id else None,
                                            notes=(notes or "").strip()[:255], created_by=user)
    _audit(user, "use_package_session", package, {"session": session.pk, "used_on": day.isoformat(), "left": package.sessions - used(package)})
    return session


@transaction.atomic
def cancel_use(session, user, lang="ar"):
    words = _words(lang)
    session = PackageSession.objects.select_for_update().select_related("package").get(pk=session.pk)
    if session.cancelled:
        raise ValidationError(words["already"])
    session.cancelled = True
    session.save(update_fields=["cancelled"])
    _audit(user, "cancel_package_session", session.package, {"session": session.pk, "cancelled": True}, {"cancelled": False})
    return session
