"""E5 (HG-093): the two automatic messages to a guardian, an absence and fees still due.

They are sources for the shared outbox (``messaging.services.SOURCES``): this module only reads attendance, fee invoices and the
customer balance, and returns what deserves a message. It writes no sale, payment, cashbox movement, ledger row or journal entry.
Students are often minors, so a message names the student, the group, the day and the centre, and never a reason, a note, an
attendance rate, a grade or a link.

* **Absence.** A mark that says absent, in a class that was held, dated today or yesterday only (``messaging.services.ABSENCE_DAYS``),
  for an active student of the entity being gathered. One message per student per class: the key holds the class and the student, not the
  mark, because saving the attendance sheet deletes and rewrites every mark (``attendance.take``) and a mark's id changes each time.
  Today's class is messaged only once it has ended (``class_over``): a student marked absent mid-class may still arrive.
  A sent or skipped message is never queued again. A mark corrected to present before the message goes out stops being yielded, and the
  gathering (``messaging.services._gather``) withdraws the waiting message; one already sent cannot be taken back.
* **Fees.** A guardian with a posted fee invoice (one a ``FeeCharge`` points at, not cancelled) dated at least ``FEES_AFTER_DAYS`` days
  ago and something still due. The balance is the one the fees-owed screen shows (``billing.dues``, the customer ledger's FIFO), never a
  second way to work one out. One message per guardian, repeated at most every ``every_days`` (7) while it is still due.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from entities import scope as entity_scope

from . import billing, services
from .models import Attendance, FeeCharge, Presence, SessionStatus, Student

#: A fee invoice is reminded this many days after its date. A constant: the template has no lead-time column and adding one is a schema change.
FEES_AFTER_DAYS = 3
ZERO = Decimal("0.00")


def _is_education():
    return entity_scope.entity() is not None and services.is_education_install()


def _centre(lang):
    """The centre's name when the group has several entities (each message names its own); empty otherwise (the company's name serves)."""

    from entities.services import is_multi_entity

    return entity_scope.entity().display(lang) if is_multi_entity() else ""


def _now():
    return timezone.now()


def class_over(session, now=None):
    """Whether the class has ended, so its absences are final enough to tell a guardian. A class on an earlier day is over; today's is
    over once its group's start time plus its length has passed; a group with no start time has no end to wait for. Until then an
    «absent» mark may still be a student on the way (a late scan turns it into «late»), and a guardian must not be alarmed for that."""

    now = timezone.localtime(now or _now())
    group = session.group
    if session.date < now.date() or group.start_time is None:
        return True
    ends = timezone.make_aware(datetime.combine(session.date, group.start_time)) + timedelta(minutes=group.duration_minutes or 0)
    return now >= ends


def absences(today, company, currency):
    """Today's and yesterday's absences in the entity being gathered, one message per student per class (see the module note)."""

    from messaging.models import MessageKind, OutboxMessage, OutboxStatus
    from messaging.services import ABSENCE_DAYS, workorder_language
    from settings_core.templatetags.hesba_format import day_text

    if not _is_education():
        return
    lang = workorder_language()
    first = today - timedelta(days=ABSENCE_DAYS - 1)
    closed = {key.split("@")[0] for key in OutboxMessage.objects.filter(kind=MessageKind.ABSENCE, status__in=(OutboxStatus.SENT, OutboxStatus.SKIPPED),
                                                                      day__gte=first).values_list("key", flat=True)}
    centre = _centre(lang)
    marks = (Attendance.objects.filter(presence=Presence.ABSENT, session__status=SessionStatus.HELD, session__date__gte=first, session__date__lte=today,
                                       student__in=services.scoped(Student.objects).filter(active=True), student__payer__active=True)
             .select_related("student__payer", "session__group").order_by("session__date", "student__name", "pk"))
    for mark in marks:
        key = f"absence:{mark.session_id}:{mark.student_id}"
        payer = mark.student.payer
        phone = payer.whatsapp or payer.phone
        if key in closed or not phone or not class_over(mark.session):
            continue
        yield {"key": key, "name": payer.name, "phone": phone, "link": f"/education/students/{mark.student_id}/", "localised": True,
               "values": {"student": mark.student.name, "group": mark.session.group.name, "date": day_text(mark.session.date, lang), **({"company": centre} if centre else {})}}


def _eligible(today):
    """[(guardian, amount still due)] for the entity being worked in: the guardians with an old posted fee invoice and something still due."""

    cutoff = today - timedelta(days=FEES_AFTER_DAYS)
    owing = {row["party"].pk: row for row in billing.dues(today)}
    if not owing:
        return []
    charges = FeeCharge.objects.filter(invoice__status="posted", invoice__invoice_date__lte=cutoff, invoice__customer_id__in=list(owing),
                                       enrollment__student__in=services.scoped(Student.objects))
    from sales.models import SalesInvoice

    old = {row["customer_id"]: row["credit"] or ZERO for row in
           SalesInvoice.objects.filter(pk__in=charges.values("invoice_id")).values("customer_id").annotate(credit=Sum("remaining_due"))}
    found = []
    for pk, credit in old.items():
        row = owing[pk]
        # The balance is the customer's whole one (the screen's figure); it never names more than the old fee invoices were sold on credit for,
        # and an open piece at least FEES_AFTER_DAYS old (the ledger's FIFO) must still exist, so a paid-off old invoice with a newer debt is not chased.
        amount = min(row["total"], credit)
        if amount > 0 and row["oldest"] is not None and row["oldest"] <= cutoff:
            found.append((row["party"], amount))
    return sorted(found, key=lambda pair: (pair[0].name, pair[0].pk))


def fee_payers(today=None):
    """[(guardian id, amount)] the fees reminder covers in the entity being gathered. The general amount-due reminder skips these guardians."""

    if not _is_education():
        return []
    return [(party.pk, amount) for party, amount in _eligible(today or timezone.localdate())]


def fees(today, company, currency):
    """One message per guardian with fees still due. A message skipped within the repeat days counts like a sent one: the guardian is not
    queued again every day (the gathering already holds back one sent within them)."""

    from messaging.models import MessageKind, OutboxMessage, OutboxStatus
    from messaging.services import template, workorder_language

    if not _is_education():
        return
    current = entity_scope.entity()
    lang = workorder_language()
    centre = _centre(lang)
    suffix = "" if current.is_main else f"@{current.pk}"
    since = today - timedelta(days=template(MessageKind.FEES).every_days - 1)
    held_back = set(OutboxMessage.objects.filter(kind=MessageKind.FEES, status=OutboxStatus.SKIPPED, day__gte=since).values_list("key", flat=True))
    for party, amount in _eligible(today):
        phone = party.whatsapp or party.phone
        if not phone or f"fees:{party.pk}{suffix}" in held_back:
            continue
        yield {"key": f"fees:{party.pk}", "name": party.name, "phone": phone, "link": f"/parties/customer/{party.pk}/", "localised": True,
               "values": {"amount": f"{amount:,.2f} {currency}".strip(), **({"company": centre} if centre else {})}}
