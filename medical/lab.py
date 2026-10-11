"""MED-004: the lab's tests, their normal ranges, and the results per patient (HG-043, design A).

A test has a unit and a normal range (low and/or high). A result is entered
for a patient and a day, as a number (checked against the range) or as text
(``negative``, ``A+``: no flag). The unit and range are copied onto the
result when it is entered, so changing a test's range later never rewrites
an old result's flag. The boundaries count as normal.

Results never move money: the tests are billed like any service, on an
ordinary sales invoice.
"""

import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from .models import LabFlag, LabResult, LabTest

VALUE_LIMIT = Decimal("1000000000")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

FLAG_LABELS = {
    "ar": {"low": "منخفض", "normal": "طبيعي", "high": "مرتفع", "": ""},
    "en": {"low": "Low", "normal": "Normal", "high": "High", "": ""},
}
MESSAGES = {
    "ar": {"name": "اكتب اسم التحليل.", "taken": "اسم التحليل ده موجود.", "number": "«{value}» مش رقم.", "range": "الحد الأدنى أكبر من الحد الأعلى.",
           "date": "تاريخ العينة مش مظبوط.", "future": "تاريخ العينة لسه مجاش.", "nothing": "اكتب نتيجة تحليل واحد على الأقل.", "reason": "اكتب سبب المسح (مثلًا: مريض تاني).",
           "result": "نتيجة «{test}»: «{value}» مش رقم؛ لو النتيجة كلام (زي سلبي) اكتبها في خانة الوصف."},
    "en": {"name": "Enter the test's name.", "taken": "A test with this name exists.", "number": "“{value}” is not a number.",
           "range": "The low limit is above the high limit.", "date": "The sample date is not valid.", "future": "The sample date is still to come.",
           "nothing": "Enter at least one test's result.", "reason": "Give the reason for removing it (for example: another patient).",
           "result": "Result of “{test}”: “{value}” is not a number; if the result is a word (like negative), put it in the text box."},
}


def is_lab():
    from entities.current import effective_activity

    return effective_activity() == ("medical", "lab")


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


THOUSANDS = re.compile(r"-?\d{1,3}(,\d{3})+(\.\d+)?")
DECIMAL_COMMA = re.compile(r"-?\d+,(\d{1,2}|\d{4,})")


def _comma(text):
    """'7,500' and '1,250,000.5' group thousands in threes (as Egyptian lab sheets write a count); '12,5' or '0,75' is a
    decimal comma (as some devices print it). Anything else with a comma is refused rather than guessed."""

    if THOUSANDS.fullmatch(text):
        return text.replace(",", "")
    if DECIMAL_COMMA.fullmatch(text):
        return text.replace(",", ".")
    raise InvalidOperation(text)


def number(raw):
    """A typed number, or None when blank. Arabic digits and the Arabic decimal sign are read.

    A comma is read only when it cannot be misread (``_comma``); otherwise the value is refused rather than guessed:
    a result that silently becomes ten times bigger is worse than a question."""

    text = str(raw if raw is not None else "").strip().translate(ARABIC_DIGITS).replace("٫", ".").replace("٬", ",")
    if not text:
        return None
    if "," in text:
        text = _comma(text)
    value = Decimal(text).quantize(Decimal("0.001"))  # InvalidOperation for anything else
    if not value.is_finite() or abs(value) >= VALUE_LIMIT:  # checked after rounding, so it always fits the column
        raise InvalidOperation(text)
    return value


def flag(value, low, high):
    if value is None or (low is None and high is None):
        return LabFlag.NONE
    if low is not None and value < low:
        return LabFlag.LOW
    if high is not None and value > high:
        return LabFlag.HIGH
    return LabFlag.NORMAL


def _limit(raw, words):
    try:
        return number(raw)
    except (InvalidOperation, ValueError):
        raise ValidationError(words["number"].format(value=str(raw)[:30]))


def _test_snapshot(test):
    def text(value):
        return "" if value is None else str(value)  # a limit of 0 is a limit

    return {"name": test.name, "unit": test.unit, "low": text(test.low), "high": text(test.high), "active": test.active,
            "service": test.service_id, "position": test.position}


@transaction.atomic
def save_test(data, user, test=None, lang="ar"):
    words = _words(lang)
    name = (data.get("name") or "").strip()[:120]
    if not name:
        raise ValidationError(words["name"])
    if LabTest.objects.filter(name__iexact=name).exclude(pk=getattr(test, "pk", None)).exists():
        raise ValidationError(words["taken"])
    low, high = _limit(data.get("low"), words), _limit(data.get("high"), words)
    if low is not None and high is not None and low > high:
        raise ValidationError(words["range"])
    creating = test is None
    before = {} if creating else _test_snapshot(test)
    test = test or LabTest()
    test.name, test.unit, test.low, test.high = name, (data.get("unit") or "").strip()[:30], low, high
    test.service = data.get("service")
    try:
        test.position = max(0, min(int(str(data.get("position") or 0).strip()), 999))
    except ValueError:
        test.position = 0
    test.active = bool(data.get("active", True))
    test.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if creating else AuditEventType.UPDATE, actor=user, module="medical",
                            action="save_lab_test", object_type="medical.LabTest", object_id=str(test.pk), before_data=before,
                            after_data=_test_snapshot(test))
    return test


def _day(raw, words):
    from datetime import date

    if not raw:
        return timezone.localdate()
    try:
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
    except ValueError:
        raise ValidationError(words["date"])
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    return day


@transaction.atomic
def record_results(patient, taken_on, entries, user, lang="ar"):
    """Record (or correct) the patient's results for a day.

    ``entries``: {test: (raw value, text)}; a test with both blank is skipped. Returns the saved results."""

    words = _words(lang)
    day = _day(taken_on, words)
    rows = []
    for test, (raw, text) in entries.items():
        text = (text or "").strip()[:120]
        try:
            value = number(raw)
        except (InvalidOperation, ValueError):
            raise ValidationError(words["result"].format(test=test.name, value=str(raw)[:30]))
        if value is None and not text:
            continue
        rows.append((test, value, text))
    if not rows:
        raise ValidationError(words["nothing"])
    saved = []
    for test, value, text in rows:
        result = LabResult.objects.select_for_update().filter(patient=patient, test=test, taken_on=day).first()
        before = {"value": str(result.value if result.value is not None else ""), "text": result.text_value, "flag": result.flag} if result else {}
        # a correction keeps the range and unit that were on the result that day; a new result takes the test's current ones
        unit, low, high = (result.unit, result.low, result.high) if result else (test.unit, test.low, test.high)
        fields = {"value": value, "text_value": text, "unit": unit, "low": low, "high": high, "flag": flag(value, low, high), "entered_by": user}
        if result is None:
            try:
                with transaction.atomic():
                    result = LabResult.objects.create(patient=patient, test=test, taken_on=day, **fields)
            except IntegrityError:  # entered twice at once: the second corrects the first
                result = LabResult.objects.select_for_update().get(patient=patient, test=test, taken_on=day)
                before = {"value": str(result.value if result.value is not None else ""), "text": result.text_value, "flag": result.flag}
        for key, val in fields.items():
            setattr(result, key, val)
        result.save()
        AuditLog.objects.create(event_type=AuditEventType.UPDATE if before else AuditEventType.CREATE, actor=user, module="medical",
                                action="record_lab_result", object_type="medical.LabResult", object_id=str(result.pk), before_data=before,
                                after_data={"patient": patient.pk, "test": test.name, "day": day.isoformat(),
                                            "value": str(value if value is not None else ""), "text": text, "flag": result.flag})
        saved.append(result)
    return saved


@transaction.atomic
def remove_result(result, user, reason, lang="ar"):
    """A result entered by mistake (wrong patient or test) is removed, with its values and the reason kept in the audit."""

    words = _words(lang)
    reason = (reason or "").strip()[:200]
    if not reason:
        raise ValidationError(words["reason"])
    result = LabResult.objects.select_for_update().get(pk=result.pk)
    AuditLog.objects.create(event_type=AuditEventType.DELETE, actor=user, module="medical", action="remove_lab_result", object_type="medical.LabResult",
                            object_id=str(result.pk), before_data={"patient": result.patient_id, "test": result.test_id, "day": result.taken_on.isoformat(),
                                                                   "value": str(result.value if result.value is not None else ""), "text": result.text_value},
                            after_data={"reason": reason})
    result.delete()


def by_day(patient):
    """[(day, [results])], newest day first."""

    days = {}
    for result in LabResult.objects.filter(patient=patient).select_related("test"):
        days.setdefault(result.taken_on, []).append(result)
    return sorted(days.items(), key=lambda pair: pair[0], reverse=True)


def previous(result):
    """The same test's result on the patient's previous day, for the trend."""

    return (LabResult.objects.filter(patient_id=result.patient_id, test_id=result.test_id, taken_on__lt=result.taken_on)
            .order_by("-taken_on").first())
