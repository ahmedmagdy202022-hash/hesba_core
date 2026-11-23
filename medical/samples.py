"""M9 (HG-092): the lab's sample code, and which tests a patient has on a day.

A sample is one patient's tube(s) of one day, the same pair M6's link is keyed on (patient, ``taken_on``): no table.
Its code is ``L<patient id>-<yyyymmdd>`` (``L12-20261112``). That is two numbers the lab already prints on its
papers: it carries no phone, no name, no diagnosis, and it is **not** M6's token (a signed, secret, short-lived key):
a label is read by anyone who holds the tube, so the code opens nothing by itself. Typing or scanning it only helps a
signed-in member of the lab's staff find the patient's page; it is looked up behind the same permission and the same
lab-entity check as the page it opens.

"Ordered" has no table of its own: the lab bills its tests as ordinary services on a sales invoice (``LabTest.service``),
so a test is **ordered** for a patient on a day when a live (not cancelled) sales invoice of the entity being worked in
has that patient, that day and a line with the test's service. A test that already has a result that day counts too.
This module only reads: it never writes, posts or prices anything.
"""

import re
from datetime import date

from django.utils import timezone

from entities import scope as entity_scope
from master_data.models import Customer

from .lab import ARABIC_DIGITS
from .models import LabResult, LabTest

PREFIX = "L"
FORM = re.compile(r"L(\d{1,9})-(\d{8})")
SHORT_NAME = 12  # a test with no analyzer code shows this many characters of its name on the label
MAX_TESTS_ON_LABEL = 6


def sample_code(patient, day):
    return f"{PREFIX}{int(patient.pk)}-{day:%Y%m%d}"


def clean(raw):
    """What a scanner or a keyboard typed, as a code: no spaces, upper case, Latin digits (an Arabic keyboard types Arabic ones)."""

    return "".join(str(raw or "").translate(ARABIC_DIGITS).split()).upper()[:40]


def parse(raw):
    """(patient id, day) in a typed or scanned code, or None. The day may be in the future: callers decide."""

    found = FORM.fullmatch(clean(raw))
    if found is None:
        return None
    text = found.group(2)
    try:
        return int(found.group(1)), date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None


def patients():
    """Whom a sample can belong to: the lab's patients (the walk-in customer is nobody), as the lab screens list them."""

    return Customer.objects.exclude(customer_code="WALK-IN")


def find(raw, today=None):
    """(patient, day) for a code that names a real patient and a day that has come, else None (one answer for every failure)."""

    parsed = parse(raw)
    if parsed is None:
        return None
    pk, day = parsed
    if day > (today or timezone.localdate()):
        return None
    patient = patients().filter(pk=pk).first()
    return (patient, day) if patient is not None else None


def short_name(test):
    return test.code or test.name[:SHORT_NAME].strip()


def ordered(pairs):
    """{(patient id, day): {test id}} of the tests ordered or already resulted, for many patient-and-day pairs in few queries.

    Ordered = a live sales invoice of the entity being worked in, that patient, that day, with a line of the test's service
    (draft or posted; never a cancelled one). Resulted = a result row that day, in any entity (lab results are group-wide)."""

    from sales.models import SalesInvoice

    pairs = set(pairs)
    found = {pair: set() for pair in pairs}
    if not pairs:
        return found
    ids = {pid for pid, _ in pairs}
    days = {day for _, day in pairs}
    for pid, day, test_id in LabResult.objects.filter(patient_id__in=ids, taken_on__in=days).values_list("patient_id", "taken_on", "test_id"):
        if (pid, day) in found:
            found[(pid, day)].add(test_id)
    by_service = {}
    for test_id, service_id in LabTest.objects.filter(service__isnull=False).values_list("pk", "service_id"):
        by_service.setdefault(service_id, set()).add(test_id)
    if by_service:
        invoices = (entity_scope.scope(SalesInvoice.objects.exclude(status="cancelled"), entity_scope.SALES_INVOICE)
                    .filter(customer_id__in=ids, invoice_date__in=days, lines__item_id__in=list(by_service)))
        for pid, day, item_id in invoices.values_list("customer_id", "invoice_date", "lines__item_id"):
            if (pid, day) in found and item_id in by_service:
                found[(pid, day)] |= by_service[item_id]
    return found


def tests_of(patient, day):
    """The tests of this sample, in the lab's order: ordered or resulted that day (a switched-off test is listed only if it has a result)."""

    ids = ordered([(patient.pk, day)])[(patient.pk, day)]
    return [test for test in LabTest.objects.filter(pk__in=ids) if test.active or test.results.filter(patient=patient, taken_on=day).exists()]
