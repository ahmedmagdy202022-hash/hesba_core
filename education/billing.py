"""EDU-003: the month's fee invoices, made through the ordinary sales engine.

One draft invoice per payer (parent) for the month, one line per enrolment:
- a monthly group: one month at the group's fee, less the enrolment's discount;
- a per-class group: the classes the student attended that month (late counts);
- a whole-course group: once, the first time it is billed.

Every line uses the course's own service item, so the invoice, its posting,
the customer's ledger and its payments are exactly those of any sale. A
charge records which enrolment and month a line billed, so running the
month again bills only what is new; a cancelled invoice frees its month.
"""

import calendar
import re
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import Attendance, Enrollment, FeeBasis, FeeCharge, Presence, SessionStatus

ZERO = Decimal("0")
MONTHS_AR = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")

MESSAGES = {
    "ar": {"month": "الشهر مش مظبوط.", "future": "مينفعش تعمل فواتير لشهر لسه مجاش.",
           "entity": "اختار الكيان (الفرع) اللي هتعمل فواتيره الأول من أعلى الشاشة؛ كل كيان بيتفوتر على مخزنه.",
           "invoice_date": "تاريخ الفاتورة مش مظبوط.", "nothing": "مفيش حاجة جديدة تتفوتر في الشهر ده.",
           "setup": "لازم يكون فيه مخزن بيع نشط (الفاتورة بتتسجل عليه).", "note": "مصروفات {month}"},
    "en": {"month": "The month is not valid.", "future": "A month still to come cannot be billed.",
           "entity": "Choose the entity (branch) to bill first, at the top of the screen; each entity is billed on its own location.",
           "invoice_date": "The invoice date is not valid.", "nothing": "Nothing new to bill for this month.",
           "setup": "An active selling location is needed (the invoice is recorded against it).", "note": "Fees for {month}"},
}


def month_of(raw):
    """The first day of the month in ``raw`` (``YYYY-MM``, a date, or None for this month)."""

    if raw in (None, ""):
        today = timezone.localdate()
        return today.replace(day=1)
    if hasattr(raw, "year"):
        return raw.replace(day=1)
    text = str(raw).strip()
    if not re.fullmatch(r"\d{4}-\d{2}", text):  # the whole value, not a valid start with anything after it
        raise ValidationError(MESSAGES["ar"]["month"])
    try:
        return date(int(text[:4]), int(text[5:]), 1)
    except ValueError:
        raise ValidationError(MESSAGES["ar"]["month"])


def month_end(first):
    return first.replace(day=calendar.monthrange(first.year, first.month)[1])


def month_label(first, lang="ar"):
    names = MONTHS_EN if lang == "en" else MONTHS_AR
    return f"{names[first.month - 1]} {first.year}"


def _live(charges):
    return charges.exclude(invoice__status="cancelled")


def _attended(enrollment, first, last):
    """Classes attended in [first, last] while this enrolment ran. A student stopped and enrolled again keeps
    each class on one enrolment only: the newer one from its start, the older one up to the day before."""

    from datetime import timedelta

    first = max(first, enrollment.start_date, enrollment.group.starts_on or first)
    if enrollment.end_date:
        last = min(last, enrollment.end_date)
    if enrollment.group.ends_on:
        last = min(last, enrollment.group.ends_on)
    newer = (Enrollment.objects.filter(student_id=enrollment.student_id, group_id=enrollment.group_id, pk__gt=enrollment.pk,
                                       start_date__gte=enrollment.start_date).order_by("start_date").values_list("start_date", flat=True).first())
    if newer is not None:
        last = min(last, newer - timedelta(days=1))
    if last < first:
        return 0
    return Attendance.objects.filter(student=enrollment.student, session__group=enrollment.group, session__status=SessionStatus.HELD,
                                     session__date__gte=first, session__date__lte=last,
                                     presence__in=(Presence.PRESENT, Presence.LATE)).count()


def due_lines(first, payer=None):
    """What the month would bill: [{enrollment, quantity, price, discount, amount}], nothing already billed."""

    last = month_end(first)
    # A month is billed only while both the enrolment and the group's own term run in it (attendance keeps to the same dates).
    running = (Enrollment.objects.filter(start_date__lte=last).filter(Q(end_date__isnull=True) | Q(end_date__gte=first))
               .filter(Q(group__starts_on__isnull=True) | Q(group__starts_on__lte=last)).filter(Q(group__ends_on__isnull=True) | Q(group__ends_on__gte=first))
               .select_related("student__payer", "group__course__item").order_by("student__payer__name", "student__name", "pk"))
    from .services import scoped
    from .models import Student

    running = running.filter(student__in=scoped(Student.objects))  # only the entity being worked in (HG-034)
    if payer is not None:
        running = running.filter(student__payer=payer)
    lines = []
    for enrollment in running:
        group = enrollment.group
        basis = group.course.basis
        fee = group.effective_fee
        if basis == FeeBasis.COURSE:
            if _live(enrollment.charges.all()).exists():
                continue
            quantity = Decimal("1")
        elif basis == FeeBasis.SESSION:
            # Classes recorded after an earlier run this month are billed then: only what is not billed yet.
            billed = _live(enrollment.charges.filter(month=first)).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
            quantity = Decimal(_attended(enrollment, first, last)) - billed
        else:
            if _live(enrollment.charges.filter(month=first)).exists():
                continue
            quantity = Decimal("1")
        if quantity <= 0 or fee <= 0:
            continue
        gross = money_round(quantity * fee)
        discount = money_round(gross * enrollment.discount_percent / Decimal("100"))
        lines.append({"enrollment": enrollment, "quantity": quantity, "price": fee, "discount": discount, "amount": gross - discount})
    return lines


def preview(first):
    """The month by payer: [{payer, lines, total}] in name order."""

    from taxes.services import compute_lines, vat_enabled

    lines = due_lines(first)
    # The invoice adds VAT when the business charges it, so the preview does the same: what it shows is what is billed.
    taxes = [tax for *_, tax in compute_lines([{"item": line["enrollment"].group.course.item, "quantity": line["quantity"],
                                                "unit_sale_price": line["price"], "line_discount_amount": line["discount"]} for line in lines])] \
        if vat_enabled() and lines else [ZERO] * len(lines)
    by_payer = OrderedDict()
    for line, tax in zip(lines, taxes):
        payer = line["enrollment"].student.payer
        row = by_payer.setdefault(payer.pk, {"payer": payer, "lines": [], "net": ZERO, "tax": ZERO, "total": ZERO})
        line["tax"] = tax
        row["lines"].append(line)
        row["net"] += line["amount"]
        row["tax"] += tax
        row["total"] += line["amount"] + tax
    return list(by_payer.values())


def _location():
    from entities import scope as entity_scope
    from master_data.models import Location

    return entity_scope.locations(Location.objects).filter(active=True, is_selling_location=True).order_by("-is_default", "pk").first()


def _invoice(payer, lines, first, user, invoice_date, words, lang):
    from config.numbering import next_in_series
    from sales.models import SalesInvoice
    from taxes.services import create_sales_draft_with_tax

    location = _location()
    if location is None:
        raise ValidationError(words["setup"])
    label = month_label(first, lang)
    rows = []
    for line in lines:
        enrollment = line["enrollment"]
        rows.append({"item": enrollment.group.course.item, "quantity": line["quantity"], "unit_sale_price": line["price"],
                     "line_discount_amount": line["discount"],
                     "description": f"{enrollment.student.name} — {enrollment.group.name} — {label}"[:255]})
    header = {"invoice_date": invoice_date, "customer": payer, "selling_location": location, "cashbox": None, "discount_amount": ZERO,
              "tax_amount": ZERO, "paid_now": ZERO, "notes": words["note"].format(month=label)}
    for attempt in range(5):
        number = next_in_series(SalesInvoice, "invoice_number", "SI-")  # AUTONUM: the same series as the invoice form
        try:
            with transaction.atomic():
                return create_sales_draft_with_tax({**header, "invoice_number": number}, rows, user)
        except IntegrityError:
            if attempt == 4 or not SalesInvoice.objects.filter(invoice_number=number).exists():
                raise


@transaction.atomic
def bill_month(first, user, *, post=False, invoice_date=None, lang="ar"):
    """Make the month's invoices (drafts, or posted when ``post``). Returns the invoices made."""

    from master_data.models import Customer
    from sales.services import post_sales_invoice

    from entities.current import current_entity
    from entities.services import is_multi_entity

    words = MESSAGES[lang]
    first = month_of(first)
    if first > timezone.localdate().replace(day=1):
        raise ValidationError(words["future"])
    if current_entity() is None and is_multi_entity():
        raise ValidationError(words["entity"])  # the whole group at once would put every branch's fees on one location (Codex on #187)
    invoice_date = invoice_date or min(timezone.localdate(), month_end(first))
    payers = {line["enrollment"].student.payer_id for line in due_lines(first)}
    made = []
    # Each payer is locked while its lines are worked out again, so two runs at once never bill one month twice.
    for payer in Customer.objects.select_for_update().filter(pk__in=payers).order_by("name", "pk"):
        lines = due_lines(first, payer)
        if not lines:
            continue
        invoice = _invoice(payer, lines, first, user, invoice_date, words, lang)
        FeeCharge.objects.bulk_create([FeeCharge(enrollment=line["enrollment"], month=first, invoice=invoice, quantity=line["quantity"],
                                                 amount=line["amount"]) for line in lines])
        if post:
            post_sales_invoice(invoice.pk, user)
            invoice.refresh_from_db()
        made.append(invoice)
    if not made:
        raise ValidationError(words["nothing"])
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="education", action="bill_month", object_type="education.FeeCharge",
                            object_id=first.isoformat(), before_data={},
                            after_data={"month": first.isoformat(), "invoices": [invoice.invoice_number for invoice in made], "posted": bool(post),
                                        "total": str(sum((invoice.total_amount for invoice in made), ZERO))})
    return made


def billed(first):
    """The month's invoices already made: [{invoice, students, total}]."""

    from .models import Student
    from .services import scoped

    charges = (_live(FeeCharge.objects.filter(month=first, enrollment__student__in=scoped(Student.objects))).select_related("invoice__customer", "enrollment__student")
               .order_by("invoice__customer__name", "invoice_id"))
    rows = OrderedDict()
    for charge in charges:
        row = rows.setdefault(charge.invoice_id, {"invoice": charge.invoice, "students": []})
        if charge.enrollment.student.name not in row["students"]:
            row["students"].append(charge.enrollment.student.name)
    return list(rows.values())


def dues(as_of=None):
    """Parents who owe fees: the customers' aging, kept to those who pay for a student."""

    from reports.aging import aging_rows

    from .models import Student

    from .services import scoped

    payers = set(scoped(Student.objects).values_list("payer_id", flat=True))
    return [row for row in aging_rows("customers", as_of) if row["party"].pk in payers and row["total"] > 0]
