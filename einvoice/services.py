"""EINV-001: build the ETA e-invoice (document type "i", version 1.0) from a posted sales invoice.

Mapping (checked against the ETA SDK, sdk.invoicing.eta.gov.eg, Sept 2026):
- line.salesTotal   = quantity x unit price
- line.discount     = the line discount (before tax)
- line.netTotal     = salesTotal - discount
- T1 (VAT) amount   = netTotal x rate / 100            (SDK rule 42 with no other fees)
- line.total        = netTotal + T1
- extraDiscountAmount = Hesba's invoice discount, which is taken after tax
- totalSalesAmount / totalDiscountAmount / netAmount / taxTotals are the sums
- totalAmount       = sum(line.total) - extraDiscountAmount  == SalesInvoice.total_amount

Nothing is signed or sent here (phase 3). ``build_document`` returns the
document plus the list of problems that would make the authority reject it,
worded for the shop owner, so the data can be completed before sending.
"""

import json
from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from printing.company import company_details
from settings_core.models import SystemSetting

from .models import ItemCode, ReceiverProfile, ReceiverType


ZERO = Decimal("0")
ISSUER_FIELDS = (
    # key, arabic label, english label
    ("einvoice.activity_code", "كود النشاط (4 أرقام من دليل الأنشطة)", "Activity code (4 digits, ETA activity list)"),
    ("einvoice.branch_id", "كود الفرع في المنظومة", "Branch code on the ETA portal"),
    ("einvoice.governate", "المحافظة", "Governorate"),
    ("einvoice.region_city", "المدينة / المنطقة", "City / region"),
    ("einvoice.street", "الشارع", "Street"),
    ("einvoice.building_number", "رقم المبنى", "Building number"),
    ("einvoice.person_id_threshold", "الحد اللي بعده بيانات الشخص الطبيعي إجبارية (جنيه)", "Amount from which a person buyer's ID is required (EGP)"),
)
ISSUER_KEYS = tuple(field[0] for field in ISSUER_FIELDS)
DEFAULT_THRESHOLD = Decimal("50000")  # confirm with the authority's current rule


def issuer_settings():
    values = dict(SystemSetting.objects.filter(key__in=ISSUER_KEYS, active=True).values_list("key", "value"))
    values.setdefault("einvoice.branch_id", "0")
    values.setdefault("einvoice.person_id_threshold", str(DEFAULT_THRESHOLD))
    return {key: values.get(key, "") for key in ISSUER_KEYS}


@transaction.atomic
def save_issuer_settings(values, user):
    before, after = {}, {}
    current = issuer_settings()
    for key in ISSUER_KEYS:
        new = (values.get(key) or "").strip()
        if new == current.get(key, ""):
            continue
        before[key], after[key] = current.get(key, ""), new
        SystemSetting.objects.update_or_create(key=key, defaults={"value": new, "active": True, "description": "E-invoice issuer details"})
    if after:
        AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="einvoice", action="update_issuer", object_type="SystemSetting", object_id="einvoice", before_data=before, after_data=after)
    return bool(after)


def _num(value):
    """ETA amounts: numbers with at most 5 decimals."""

    return float(Decimal(value).quantize(Decimal("0.00001")))


def _issued_at(invoice):
    """The issue moment in UTC, never in the future (an ETA rule)."""

    local = timezone.localtime(invoice.created_at) if invoice.created_at else timezone.localtime()
    at = local.time() if local.date() == invoice.invoice_date else time(12, 0)
    moment = min(timezone.make_aware(datetime.combine(invoice.invoice_date, at)), timezone.now())
    return moment.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_document(invoice, lang="ar"):
    """(document dict, problems list) for a sales invoice."""

    from taxes.models import SalesLineTax

    ar = lang != "en"
    problems = []

    def need(condition, text_ar, text_en):
        if not condition:
            problems.append(text_ar if ar else text_en)

    need(invoice.status == "posted", "الفاتورة لازم تكون مرحّلة.", "The invoice must be posted.")
    company = company_details()
    issuer = issuer_settings()
    need(company["currency"] == "EGP", "عملة الشركة لازم تكون جنيه مصري (الفاتورة بعملة تانية محتاجة سعر صرف، ومش مدعومة لسه).", "The company currency must be EGP (other currencies need an exchange rate and are not supported yet).")
    tax_number = "".join(ch for ch in company["tax_number"] if ch.isdigit())
    need(len(tax_number) == 9, "الرقم الضريبي للشركة (9 أرقام) ناقص أو غلط — من بيانات الشركة.", "The company's 9-digit tax registration number is missing or wrong (Company details).")
    need(len(issuer["einvoice.activity_code"]) == 4 and issuer["einvoice.activity_code"].isdigit(), "كود النشاط (4 أرقام) ناقص — من إعدادات الفاتورة الإلكترونية.", "The 4-digit activity code is missing (E-invoice settings).")
    for key, label_ar, label_en in ISSUER_FIELDS[2:6]:
        need(bool(issuer[key]), f"عنوان الفرع ناقص: {label_ar}.", f"Branch address missing: {label_en}.")

    receiver_profile = ReceiverProfile.objects.filter(customer=invoice.customer).first()
    receiver_type = receiver_profile.receiver_type if receiver_profile else ReceiverType.PERSON
    threshold = Decimal(issuer["einvoice.person_id_threshold"] or DEFAULT_THRESHOLD)
    receiver = {"type": receiver_type, "name": invoice.customer.name}
    needs_details = receiver_type in (ReceiverType.BUSINESS, ReceiverType.FOREIGNER) or invoice.total_amount >= threshold
    if receiver_profile and receiver_profile.tax_id:
        receiver["id"] = receiver_profile.tax_id
    if receiver_profile and receiver_profile.street:
        receiver["address"] = {
            "country": receiver_profile.country or "EG", "governate": receiver_profile.governate, "regionCity": receiver_profile.region_city,
            "street": receiver_profile.street, "buildingNumber": receiver_profile.building_number,
        }
    if needs_details:
        need(bool(receiver.get("id")), f"رقم العميل «{invoice.customer.name}» ناقص (الرقم الضريبي للشركة أو الرقم القومي للشخص).", f"Customer “{invoice.customer.name}” has no ID (tax number for a business, national ID for a person).")
        need("address" in receiver and receiver_profile.governate and receiver_profile.region_city and receiver_profile.building_number, f"عنوان العميل «{invoice.customer.name}» ناقص.", f"Customer “{invoice.customer.name}” has no complete address.")
    if receiver_type == ReceiverType.BUSINESS and receiver.get("id"):
        need(len(receiver["id"]) == 9 and receiver["id"].isdigit(), "الرقم الضريبي للعميل لازم يكون 9 أرقام.", "The customer's tax registration number must be 9 digits.")
    if receiver.get("id") == tax_number and tax_number:
        need(False, "العميل ومُصدِر الفاتورة مينفعش يكونوا نفس الرقم.", "The receiver and the issuer cannot be the same.")

    lines = list(invoice.lines.select_related("item").order_by("line_number"))
    taxes = {row.line_id: row for row in SalesLineTax.objects.filter(line__in=lines).select_related("tax_rate")}
    codes = {row.item_id: row for row in ItemCode.objects.filter(item__in=[line.item_id for line in lines])}
    doc_lines, sums = [], {"sales": ZERO, "discount": ZERO, "net": ZERO, "tax": ZERO, "total": ZERO}
    for line in lines:
        code = codes.get(line.item_id)
        need(code is not None, f"الصنف «{line.item.item_code}» مالوش كود EGS/GS1 مسجّل.", f"Item “{line.item.item_code}” has no registered EGS/GS1 code.")
        sales_total = money_round(line.quantity * line.unit_sale_price)
        discount = money_round(line.line_discount_amount or ZERO)
        net = money_round(sales_total - discount)
        tax_row = taxes.get(line.pk)
        tax = tax_row.tax_amount if tax_row else ZERO
        if tax_row is None:
            need(not invoice.tax_amount, f"السطر {line.line_number} مالوش ضريبة متسجّلة؛ الفاتورة اتعملت قبل تفعيل الضريبة على الأصناف.", f"Line {line.line_number} has no recorded tax; the invoice predates item-level VAT.")
        subtype = (tax_row.tax_rate.eta_subtype if tax_row and tax_row.tax_rate else "") or ("V009" if tax else "V003")
        doc_lines.append({
            "description": line.description or line.item.item_name,
            "itemType": code.code_type if code else "EGS",
            "itemCode": code.code if code else "",
            "unitType": code.unit_type if code else "EA",
            "quantity": _num(line.quantity),
            "internalCode": line.item.item_code,
            "salesTotal": _num(sales_total),
            "total": _num(net + tax),
            "valueDifference": 0,
            "totalTaxableFees": 0,
            "netTotal": _num(net),
            "itemsDiscount": 0,
            "unitValue": {"currencySold": "EGP", "amountEGP": _num(line.unit_sale_price)},
            "discount": {"rate": 0, "amount": _num(discount)},
            "taxableItems": [{"taxType": "T1", "amount": _num(tax), "subType": subtype, "rate": _num(tax_row.rate if tax_row else ZERO)}],
        })
        sums["sales"] += sales_total
        sums["discount"] += discount
        sums["net"] += net
        sums["tax"] += tax
        sums["total"] += net + tax

    extra = money_round(invoice.discount_amount or ZERO)
    total_amount = money_round(sums["total"] - extra)
    need(total_amount == invoice.total_amount, f"إجمالي ملف الفاتورة ({total_amount}) مش مطابق لإجمالي الفاتورة ({invoice.total_amount}).", f"The document total ({total_amount}) does not match the invoice total ({invoice.total_amount}).")

    document = {
        "issuer": {
            "type": "B", "id": tax_number, "name": company["legal_name"] or company["name"],
            "address": {
                "branchId": issuer["einvoice.branch_id"] or "0", "country": "EG", "governate": issuer["einvoice.governate"],
                "regionCity": issuer["einvoice.region_city"], "street": issuer["einvoice.street"], "buildingNumber": issuer["einvoice.building_number"],
            },
        },
        "receiver": receiver,
        "documentType": "i",
        "documentTypeVersion": "1.0",
        "dateTimeIssued": _issued_at(invoice),
        "taxpayerActivityCode": issuer["einvoice.activity_code"],
        "internalID": invoice.invoice_number,
        "invoiceLines": doc_lines,
        "totalSalesAmount": _num(sums["sales"]),
        "totalDiscountAmount": _num(sums["discount"]),
        "netAmount": _num(sums["net"]),
        "taxTotals": [{"taxType": "T1", "amount": _num(sums["tax"])}],
        "extraDiscountAmount": _num(extra),
        "totalItemsDiscountAmount": 0,
        "totalAmount": _num(total_amount),
    }
    return document, problems


def document_json(document):
    return json.dumps({"documents": [document]}, ensure_ascii=False, indent=2)


# ---- ETA-002: signing and sending (portal.py does the talking) ----

STATUS_FROM_PORTAL = {"valid": "valid", "invalid": "invalid", "rejected": "rejected", "cancelled": "cancelled", "submitted": "submitted"}
SEND_WORDS = {
    "ar": {"problems": "البيانات لسه ناقصة؛ كمّلها الأول.", "already": "الفاتورة دي اتبعتت واتقبلت بالفعل.", "pending": "الفاتورة دي اتبعتت ولسه المنظومة بتراجعها.",
           "setup": "الربط مع المنظومة لسه متظبطش على السيرفر ({names}).", "signer": "جهاز التوقيع مش بيرد أو رفض التوقيع؛ اتأكد إن التوكن متركّب والبرنامج شغال.",
           "login": "المنظومة رفضت الدخول؛ راجع Client ID و Client Secret.", "unreachable": "مش قادرين نوصل للمنظومة دلوقتي؛ جرّب كمان شوية.",
           "refused": "المنظومة رفضت الفاتورة: {reason}", "unknown": "الاتصال اتقطع قبل ما نعرف رد المنظومة، وممكن تكون الفاتورة وصلت. مش هنبعتها تاني قبل ما نسأل المنظومة؛ جرّب الإرسال بعد 10 دقايق وهنتأكد الأول.", "reason": "اكتب سبب الإلغاء.", "not_valid": "الإلغاء للفواتير المقبولة بس."},
    "en": {"problems": "The data is still incomplete; complete it first.", "already": "This invoice was already sent and accepted.", "pending": "This invoice was sent and the portal is still checking it.",
           "setup": "The portal connection is not set up on the server yet ({names}).", "signer": "The signer did not answer or refused; check the token is plugged in and the signer is running.",
           "login": "The portal refused the login; check the client ID and secret.", "unreachable": "The portal cannot be reached right now; try again shortly.",
           "refused": "The portal rejected the invoice: {reason}", "unknown": "The connection dropped before the portal answered, so the invoice may have arrived. It will not be sent again before the portal is asked: try again in 10 minutes and it will be checked first.", "reason": "Enter a cancellation reason.", "not_valid": "Only accepted invoices can be cancelled."},
}


def current_submission(invoice):
    """The latest sending in the environment the server now talks to (moving
    from preprod to prod starts a fresh history)."""

    from . import portal
    from .models import Submission

    return Submission.objects.filter(invoice=invoice, environment=portal.settings()["environment"]).first()


def _portal_error(exc, words):
    from . import portal

    text = exc.message or ""
    if text.startswith("unreachable"):
        return words["unreachable"]
    if text in ("no signer", "signer refused"):
        return words["signer"]
    if text == "login refused":
        return words["login"]
    return words["refused"].format(reason=portal.error_text(exc.payload) or exc.status or text)


def _audit_send(submission, user, action):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if action == "eta_send" else AuditEventType.UPDATE, actor=user, module="einvoice",
                            action=action, object_type="einvoice.Submission", object_id=str(submission.pk),
                            after_data={"invoice": submission.invoice.invoice_number, "status": submission.status, "uuid": submission.uuid,
                                        "environment": submission.environment})


STALE_SENDING = timedelta(minutes=10)


def send_invoice(invoice, user, lang="ar"):
    """Sign and send one posted sales invoice. Returns the Submission (rejected ones too).

    The invoice is claimed first: under its row lock a ``sending`` row is
    written and committed, so a second click (or a second user) while the
    signer or the portal is slow is refused instead of sending it twice. The
    portal is called outside any transaction, and the claim then becomes the
    sending's record. A claim left by a server that died mid-call stops
    blocking after ``STALE_SENDING``; the authority itself refuses a document
    it already holds, so sending again cannot make a duplicate.
    """

    from django.core.exceptions import ValidationError

    from sales.models import SalesInvoice

    from . import portal
    from .models import Submission, SubmissionStatus

    words = SEND_WORDS[lang]
    names = portal.missing()
    if names:
        raise ValidationError(words["setup"].format(names=", ".join(names)))
    document, problems = build_document(invoice, lang)
    if problems:
        raise ValidationError(words["problems"])
    environment = portal.settings()["environment"]
    latest = current_submission(invoice)
    if latest and latest.status == SubmissionStatus.SENDING and latest.submitted_at < timezone.now() - STALE_SENDING:
        reconcile_claim(latest, user, lang)  # ask the authority what became of it before anything is sent again
    with transaction.atomic():
        SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
        latest = current_submission(invoice)
        if latest and latest.status in (SubmissionStatus.VALID, SubmissionStatus.CANCEL_REQUESTED):
            raise ValidationError(words["already"])
        elif latest and latest.status in (SubmissionStatus.SUBMITTED, SubmissionStatus.SENDING):
            raise ValidationError(words["pending"])
        claim = Submission.objects.create(invoice=invoice, environment=environment, submitted_by=user, status=SubmissionStatus.SENDING)
    try:
        signed = portal.sign(document)
        portal.token()  # log in first: a refused or unreachable login is known to have sent nothing
    except portal.PortalError as exc:
        claim.delete()  # nothing reached the authority
        raise ValidationError(_portal_error(exc, words))
    try:
        answer = portal.submit([signed])
    except portal.PortalError as exc:
        if exc.message == "submission refused" and exc.status and exc.status < 500:
            # A definite refusal (400, 403, 422…): kept, with the authority's exact answer, like any sending.
            claim.status, claim.message = SubmissionStatus.REJECTED, portal.error_text(exc.payload) or f"HTTP {exc.status}"
            claim.response = {"http_status": exc.status, "body": exc.payload}
            claim.save(update_fields=["status", "message", "response"])
            _audit_send(claim, user, "eta_send")
            return claim
        # Cut off mid-answer, or a 5xx: the authority may hold the invoice. The claim stays, so no
        # second send goes before it is reconciled with the authority (reconcile_claim, after STALE_SENDING).
        _keep_unknown(claim, user, exc)
        raise ValidationError(words["unknown"])
    except Exception as exc:
        _keep_unknown(claim, user, portal.PortalError(f"error: {exc.__class__.__name__}"))
        raise
    accepted = next((row for row in answer.get("acceptedDocuments") or [] if row.get("internalId") == invoice.invoice_number), None)
    rejected = next((row for row in answer.get("rejectedDocuments") or [] if row.get("internalId") == invoice.invoice_number), None)
    claim.submission_id = str(answer.get("submissionUUID") or "")
    claim.status = SubmissionStatus.SUBMITTED if accepted else SubmissionStatus.REJECTED
    claim.uuid, claim.long_id = (accepted or {}).get("uuid", ""), (accepted or {}).get("longId", "")
    claim.message, claim.response = ("" if accepted else portal.error_text(rejected or answer)), answer
    claim.save(update_fields=["submission_id", "status", "uuid", "long_id", "message", "response"])
    _audit_send(claim, user, "eta_send")
    return claim


def _keep_unknown(claim, user, exc):
    claim.message = f"outcome unknown: {exc.message}" + (f" (HTTP {exc.status})" if exc.status else "")
    claim.response = {"http_status": exc.status, "body": exc.payload} if exc.status else {}
    claim.save(update_fields=["message", "response"])
    _audit_send(claim, user, "eta_send_unknown")


def reconcile_claim(claim, user, lang="ar"):
    """A ``sending`` claim whose server died mid-call: look the invoice up on the
    authority by its internal ID over the claim's time. Found: the claim takes
    that document (uuid, long ID, status), so it is never sent twice. Not
    found: the claim is closed as rejected and the invoice may be sent again.
    Either way the outcome is in the audit log. If the authority cannot be
    asked, nothing changes and nothing is sent."""

    from django.core.exceptions import ValidationError

    from . import portal
    from .models import Submission, SubmissionStatus

    words = SEND_WORDS[lang]
    # The sending happened right after the claim (one request's time): a short window around it,
    # well inside the authority's 30-day search limit however old the claim is.
    since = claim.submitted_at - timedelta(minutes=5)
    until = min(claim.submitted_at + timedelta(hours=1), timezone.now() + timedelta(minutes=5))
    try:
        found = portal.search(claim.invoice.invoice_number, since, until)
    except portal.PortalError as exc:
        raise ValidationError(_portal_error(exc, words))
    with transaction.atomic():
        claim = Submission.objects.select_for_update().get(pk=claim.pk)
        if claim.status != SubmissionStatus.SENDING:
            return claim  # someone else settled it meanwhile
        if found:
            row = sorted(found, key=lambda item: str(item.get("dateTimeReceived") or ""))[-1]
            claim.status = _portal_status(row, SubmissionStatus.SUBMITTED)
            claim.uuid, claim.long_id = row.get("uuid") or "", row.get("longId") or ""
            claim.submission_id = str(row.get("submissionUUID") or "")
            claim.message, claim.response = "", row
        else:
            claim.status, claim.message = SubmissionStatus.REJECTED, "not found on the portal: never received"
        claim.save(update_fields=["status", "uuid", "long_id", "submission_id", "message", "response"])
        _audit_send(claim, user, "eta_send_recovered")
    return claim


def _portal_status(answer, current):
    """The local status for the authority's answer about a document. A valid
    document with a cancellation request the receiver has not declined (or a
    newer one since) is a pending cancellation, whatever the row said before."""

    from .models import SubmissionStatus

    status = STATUS_FROM_PORTAL.get(str(answer.get("status", "")).lower(), current)
    if status == SubmissionStatus.VALID:
        requested, declined = answer.get("cancelRequestDate"), answer.get("declineCancelRequestDate")
        if requested and (not declined or str(requested) > str(declined)):
            status = SubmissionStatus.CANCEL_REQUESTED
    return status


def refresh_submission(submission, user, lang="ar"):
    """Read the document's status from the authority (it validates after accepting the submission)."""

    from django.core.exceptions import ValidationError
    from django.db import transaction

    from . import portal
    from .models import Submission, SubmissionStatus

    words = SEND_WORDS[lang]
    if not submission.uuid:
        return submission
    try:
        answer = portal.details(submission.uuid)
    except portal.PortalError as exc:
        raise ValidationError(_portal_error(exc, words))
    with transaction.atomic():
        # Reloaded under its lock: a cancellation saved while the authority was being asked is not lost.
        fresh = Submission.objects.select_for_update().get(pk=submission.pk)
        status = _portal_status(answer, fresh.status)
        if fresh.status == SubmissionStatus.CANCEL_REQUESTED and status == SubmissionStatus.VALID and not answer.get("declineCancelRequestDate"):
            status = SubmissionStatus.CANCEL_REQUESTED  # our request is not on the authority's answer yet
        steps = ((answer.get("validationResults") or {}).get("validationSteps") or [])
        reasons = [portal.error_text(step.get("error") or {}) for step in steps if str(step.get("status", "")).lower() == "invalid"]
        fresh.status = status
        if status == SubmissionStatus.CANCELLED and not fresh.cancelled_at:
            fresh.cancelled_at = timezone.now()
        fresh.long_id = answer.get("longId") or fresh.long_id
        fresh.message = " · ".join(r for r in reasons if r)[:1000]
        fresh.response = answer
        fresh.checked_at = timezone.now()
        fresh.save(update_fields=["status", "long_id", "message", "response", "checked_at", "cancelled_at"])
    for field in ("status", "long_id", "message", "response", "checked_at", "cancelled_at", "cancel_reason"):
        setattr(submission, field, getattr(fresh, field))
    _audit_send(submission, user, "eta_refresh")
    return submission


def cancel_submission(submission, user, reason, lang="ar"):
    from django.core.exceptions import ValidationError

    from . import portal
    from .models import SubmissionStatus

    words = SEND_WORDS[lang]
    reason = (reason or "").strip()[:255]
    if not reason:
        raise ValidationError(words["reason"])
    if submission.status != SubmissionStatus.VALID:
        raise ValidationError(words["not_valid"])
    try:
        portal.cancel(submission.uuid, reason)
    except portal.PortalError as exc:
        raise ValidationError(_portal_error(exc, words))
    # A request, not the end: the receiver of a B2B invoice may decline it within
    # the authority's window. "Refresh" reads the outcome (cancelled, or valid again).
    submission.status, submission.cancel_reason = SubmissionStatus.CANCEL_REQUESTED, reason
    submission.save(update_fields=["status", "cancel_reason"])
    _audit_send(submission, user, "eta_cancel")
    return submission


def public_url(submission):
    """The authority's public page for an accepted document (also what its QR code points to)."""

    if not (submission and submission.uuid and submission.long_id):
        return ""
    given = (submission.response or {}).get("publicUrl") if isinstance(submission.response, dict) else ""
    if given:
        return given  # the authority's own link, when its answer carries one
    base = "https://preprod.invoicing.eta.gov.eg" if submission.environment == "preprod" else "https://invoicing.eta.gov.eg"
    return f"{base}/documents/{submission.uuid}/share/{submission.long_id}"
