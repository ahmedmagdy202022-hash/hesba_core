"""STAFF-001: add and change employees. Audited like every other master record."""

from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog

from .models import Employee


MESSAGES = {
    "ar": {"name": "اكتب اسم الموظف.", "commission": "نسبة العمولة لازم من 0 لـ 100.", "user": "حساب الدخول ده مربوط بموظف تاني.", "phone": "رقم الموبايل ده مسجّل لموظف تاني."},
    "en": {"name": "Enter the employee's name.", "commission": "The commission must be between 0 and 100.", "user": "That login is already linked to another employee.", "phone": "That mobile number belongs to another employee."},
}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


STAFF_MODULE = "employees_technicians"


def reps_offered():
    """FEEDBACK-R1: employees to pick as a customer's rep or an invoice's seller.

    Only when the employees module is switched on: a pharmacy that never chose
    it must not be asked for a rep, even if sample data put people in."""

    from settings_core.setup_services import module_is_enabled

    if not module_is_enabled(STAFF_MODULE):
        return Employee.objects.none()
    return Employee.objects.filter(active=True)


def clean_phone(value):
    return "".join(ch for ch in (value or "").translate(_DIGITS) if ch.isdigit() or ch == "+")[:50]


def _next_code():
    number = Employee.objects.count() + 1
    while Employee.objects.filter(code=f"E-{number:04d}").exists():
        number += 1
    return f"E-{number:04d}"


def _commission(value, words):
    try:
        number = Decimal(str(value or "0").strip().replace(",", ".") or "0")
    except InvalidOperation:
        raise ValidationError(words["commission"])
    if not number.is_finite() or number < 0 or number > 100:
        raise ValidationError(words["commission"])
    return number.quantize(Decimal("0.01"))


@transaction.atomic
def save_employee(data, actor, employee=None, lang="ar"):
    """Create (``employee`` None) or update an employee from cleaned form values."""

    words = MESSAGES[lang]
    name = (data.get("name") or "").strip()[:255]
    if not name:
        raise ValidationError(words["name"])
    phone = clean_phone(data.get("phone"))
    others = Employee.objects.exclude(pk=employee.pk) if employee else Employee.objects.all()
    if phone and others.filter(phone=phone).exists():
        raise ValidationError(words["phone"])
    user = None
    user_id = str(data.get("user") or "")
    if user_id.isdigit():
        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if user is not None and others.filter(user=user).exists():
            raise ValidationError(words["user"])
    values = {
        "name": name, "title": (data.get("title") or "").strip()[:120], "phone": phone,
        "commission_percent": _commission(data.get("commission_percent"), words), "user": user,
        "active": bool(data.get("active", True)), "notes": (data.get("notes") or "").strip()[:255],
    }
    before = {}
    if employee is None:
        employee = Employee(code=_next_code(), **values)
        action, event = "create_employee", AuditEventType.CREATE
    else:
        before = {key: str(getattr(employee, key)) for key in values}
        for key, value in values.items():
            setattr(employee, key, value)
        action, event = "update_employee", AuditEventType.UPDATE
    employee.full_clean()
    employee.save()
    AuditLog.objects.create(event_type=event, actor=actor, module="staff", action=action, object_type="staff.Employee",
                            object_id=str(employee.pk), before_data=before, after_data={key: str(value) for key, value in values.items()})
    return employee
