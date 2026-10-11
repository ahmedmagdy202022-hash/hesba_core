"""SVC-003: prepaid session packages for salons and beauty centres.

A package (say six blow-drys at 1200) is sold on one ordinary draft sales
invoice; its revenue counts when that invoice is posted, like any service.
Spreading it over the sessions (deferred revenue) would change ledger posting
and is not part of this design.

Sessions are used one by one once the invoice is posted, never below zero and
never after the package's expiry date. A session entered by mistake is
cancelled (kept, and audited), which gives it back. A package whose invoice was
cancelled can no longer be used.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import Package, PackageUse
from .services import MANAGE, VIEW, ZERO, _date, draft_invoice, selling_location

SUB_ACTIVITIES = ("beauty", "general", "other")
MAX_SESSIONS = 200
PRICE_LIMIT = Decimal("10000000")

STATE_LABELS = {
    "ar": {"draft": "فاتورته لسه مسودة", "active": "شغال", "used_up": "خلص", "expired": "انتهت صلاحيته", "void": "اتلغى"},
    "en": {"draft": "Invoice still a draft", "active": "Active", "used_up": "Used up", "expired": "Expired", "void": "Cancelled"},
}
MESSAGES = {
    "ar": {"customer": "اختار العميل.", "service": "اختار الخدمة.", "sessions": "عدد الجلسات من 1 لـ 200.", "price": "سعر الباكدج لازم يكون رقم صفر أو أكبر.",
           "setup": "لازم يكون فيه مخزن بيع نشط.", "manage": "الباكدجات محتاجة صلاحية إنشاء فاتورة بيع.", "date": "التاريخ مش مظبوط.",
           "expires": "تاريخ انتهاء الباكدج لازم يكون النهارده أو بعده.", "future": "مينفعش تسجّل جلسة في يوم لسه مجاش.",
           "draft": "فاتورة الباكدج لسه مسودة؛ رحّلها الأول عشان الجلسات تتسحب منها.", "void": "فاتورة الباكدج اتلغت؛ الباكدج ده مبقاش شغال.",
           "used_up": "الباكدج خلص؛ مفيش جلسات باقية.", "expired": "الباكدج انتهت صلاحيته يوم {date}.", "already": "الجلسة دي اتلغت قبل كده.",
           "line": "باكدج {sessions} جلسة — {service}", "note": "باكدج {number}: {sessions} جلسة"},
    "en": {"customer": "Choose the customer.", "service": "Choose the service.", "sessions": "The number of sessions is from 1 to 200.",
           "price": "The package price must be zero or more.", "setup": "An active selling location is needed.",
           "manage": "Packages need the create-sales-invoice permission.", "date": "The date is not valid.",
           "expires": "The package's expiry date must be today or later.", "future": "A session cannot be recorded on a day still to come.",
           "draft": "The package's invoice is still a draft; post it first so sessions can be used from it.",
           "void": "The package's invoice was cancelled; this package can no longer be used.", "used_up": "The package is used up; no sessions are left.",
           "expired": "The package expired on {date}.", "already": "This session was already cancelled.",
           "line": "Package of {sessions} sessions — {service}", "note": "Package {number}: {sessions} sessions"},
}


def is_package_business():
    from entities.current import effective_activity

    activity, sub = effective_activity()
    return activity == "services" and (sub or "") in SUB_ACTIVITIES


def can_view(user):
    return is_package_business() and user_has_permission(user, VIEW)


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def used(package):
    return package.uses.filter(cancelled=False).count()


def remaining(package):
    return max(package.sessions - used(package), 0)


def state(package, today=None):
    status = package.invoice.status
    if status == "cancelled":
        return "void"
    if status != "posted":
        return "draft"
    if remaining(package) <= 0:
        return "used_up"
    if package.expires_on and package.expires_on < (today or timezone.localdate()):
        return "expired"
    return "active"


def _audit(user, action, obj, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="agreements", action=action, object_type=f"agreements.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


@transaction.atomic
def sell_package(data, user, lang="ar"):
    """A package and its draft invoice: one line, the whole price."""

    from config.numbering import next_in_series

    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["manage"])
    customer, service = data.get("customer"), data.get("service")
    if customer is None:
        raise ValidationError(words["customer"])
    if service is None:
        raise ValidationError(words["service"])
    try:
        sessions = int(str(data.get("sessions") or "").strip())
    except ValueError:
        raise ValidationError(words["sessions"])
    if not 1 <= sessions <= MAX_SESSIONS:
        raise ValidationError(words["sessions"])
    raw = str(data.get("price") if data.get("price") not in (None, "") else "").strip().replace(",", "")
    try:
        price = Decimal(raw) if raw else Decimal(service.default_sale_price or 0) * sessions
    except (InvalidOperation, ValueError):
        raise ValidationError(words["price"])
    if not price.is_finite() or price < 0 or price >= PRICE_LIMIT:
        raise ValidationError(words["price"])
    expires_on = _date(data.get("expires_on"), words, allow_blank=True)
    if expires_on and expires_on < timezone.localdate():
        raise ValidationError(words["expires"])
    location = selling_location()
    if location is None:
        raise ValidationError(words["setup"])
    number = next_in_series(Package, "number", "PK-", 4)
    price = money_round(price)
    rows = [{"item": service, "quantity": Decimal("1"), "unit_sale_price": price, "line_discount_amount": ZERO,
             "description": words["line"].format(sessions=sessions, service=service.item_name)[:255]}]
    invoice = draft_invoice(customer, rows, words["note"].format(number=number, sessions=sessions), user, location)
    package = Package.objects.create(number=number, customer=customer, service=service, sessions=sessions, price=price, expires_on=expires_on,
                                     invoice=invoice, notes=(data.get("notes") or "").strip()[:255], created_by=user)
    _audit(user, "sell_service_package", package, {"number": number, "customer": customer.pk, "service": service.pk, "sessions": sessions,
                                                   "price": str(price), "invoice": invoice.invoice_number}, event=AuditEventType.CREATE)
    return package


@transaction.atomic
def use_session(package, user, *, used_on=None, employee=None, notes="", lang="ar"):
    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["manage"])
    package = Package.objects.select_for_update().select_related("invoice").get(pk=package.pk)  # two clicks never take the last session twice
    day = _date(used_on or timezone.localdate(), words)
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    status = state(package, day)
    if status == "expired":
        raise ValidationError(words["expired"].format(date=package.expires_on.isoformat()))
    if status != "active":
        raise ValidationError(words[status])
    use = PackageUse.objects.create(package=package, used_on=day, employee=employee, notes=(notes or "").strip()[:255], created_by=user)
    _audit(user, "use_package_session", package, {"use": use.pk, "used_on": day.isoformat(), "employee": getattr(employee, "pk", None),
                                                  "remaining": remaining(package)})
    return use


@transaction.atomic
def cancel_use(use, user, lang="ar"):
    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["manage"])
    use = PackageUse.objects.select_for_update().get(pk=use.pk)
    if use.cancelled:
        raise ValidationError(words["already"])
    use.cancelled = True
    use.save(update_fields=["cancelled"])
    _audit(user, "cancel_package_session", use.package, {"use": use.pk, "remaining": remaining(use.package)}, {"use": use.pk, "cancelled": False})
    return use


def customer_packages(customer):
    return Package.objects.filter(customer=customer).select_related("service", "invoice")


def totals(packages):
    """Sessions sold, used and left, and the posted value, for a list of packages."""

    rows = [(package, used(package)) for package in packages]
    live = [(package, count) for package, count in rows if package.invoice.status == "posted"]
    return {"sold": sum(package.sessions for package, _ in live), "used": sum(count for _, count in live),
            "left": sum(max(package.sessions - count, 0) for package, count in live),
            "value": money_round(sum((package.price for package, _ in live), ZERO))}
