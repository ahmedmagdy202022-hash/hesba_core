"""T8 (MARKET-PARITY): e-receipts v1.2 — built, chained per device, queued for 24 hours, and printed with their QR.

* ``issue`` runs when a till sale (an invoice numbered POS-…) is posted, through the posting's audit row, so posting
  itself is unchanged. Under the device's lock it builds the receipt with the device's last UUID as
  ``previousUUID`` (empty for the first), computes the receipt's own UUID — the SHA-256, as 64 hex characters, of
  the receipt serialized the authority's way with its UUID empty — and moves the device on to it.
* A sale whose data is incomplete (company, address, item codes, a device) is kept as "not issued" with what is
  missing; once it is completed, ``issue_again`` issues it at the end of the chain.
* The queue holds issued receipts not sent yet, each due within 24 hours of its issue. Sending needs the device's
  credentials on the authority's receipt portal, which is the part still to be set up.
"""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from .models import Device, Receipt, ReceiptStatus

ZERO = Decimal("0")
WINDOW = timedelta(hours=24)
#: The amount from which a person buyer's ID and name are required on a receipt (receipt v1.2).
PERSON_ID_THRESHOLD = Decimal("150000")
PORTAL = {"preprod": "https://preprod.invoicing.eta.gov.eg", "prod": "https://invoicing.eta.gov.eg"}

PROBLEMS = {
    "ar": {"device": "سجّل جهاز الكاشير (الرقم المسلسل وكود الفرع) في شاشة الإيصالات.", "rin": "الرقم الضريبي للشركة (9 أرقام) ناقص — من بيانات الشركة.",
           "activity": "كود النشاط (4 أرقام) ناقص — من إعدادات الفاتورة الإلكترونية.", "address": "عنوان الفرع ناقص: {label}.",
           "code": "الصنف «{item}» مالوش كود EGS/GS1 مسجّل.", "buyer": "الإيصال فوق {limit} جنيه: لازم اسم العميل ورقمه القومي.",
           "total": "إجمالي الإيصال ({total}) مش مطابق للفاتورة ({amount}).", "currency": "عملة الشركة لازم تكون جنيه مصري."},
    "en": {"device": "Register the till (serial number and branch code) on the receipts screen.", "rin": "The company's 9-digit tax registration number is missing (Company details).",
           "activity": "The 4-digit activity code is missing (E-invoice settings).", "address": "Branch address missing: {label}.",
           "code": "Item “{item}” has no registered EGS/GS1 code.", "buyer": "A receipt above {limit} EGP needs the customer's name and national ID.",
           "total": "The receipt total ({total}) does not match the invoice ({amount}).", "currency": "The company currency must be EGP."},
}


def enabled():
    from settings_core.capabilities import capability_enabled

    return capability_enabled("e_receipt")


def device():
    return Device.objects.filter(active=True).order_by("pk").first()


def _is_till_sale(invoice):
    return invoice.invoice_number.startswith("POS-")


def _payment_method(invoice):
    from sales.pos_buttons import card_cashbox

    card = card_cashbox()
    return "V" if card is not None and invoice.cashbox_id == card.pk else "C"


def build(invoice, till, previous_uuid="", lang="ar"):
    """(receipt dict with an empty uuid, problems) for a posted sale on ``till``."""

    from einvoice.models import ItemCode, ReceiverProfile
    from einvoice.services import ISSUER_FIELDS, _issued_at, _num, issuer_settings
    from printing.company import company_details
    from taxes.models import SalesLineTax

    words = PROBLEMS["en" if lang == "en" else "ar"]
    problems = []
    company, issuer = company_details(), issuer_settings()
    if company["currency"] != "EGP":
        problems.append(words["currency"])
    rin = "".join(ch for ch in company["tax_number"] if ch.isdigit())
    if len(rin) != 9:
        problems.append(words["rin"])
    if not (len(issuer["einvoice.activity_code"]) == 4 and issuer["einvoice.activity_code"].isdigit()):
        problems.append(words["activity"])
    for key, label_ar, label_en in ISSUER_FIELDS[2:6]:
        if not issuer[key]:
            problems.append(words["address"].format(label=label_en if lang == "en" else label_ar))
    if till is None:
        problems.append(words["device"])
    lines = list(invoice.lines.select_related("item").order_by("line_number"))
    taxes = {row.line_id: row for row in SalesLineTax.objects.filter(line__in=lines).select_related("tax_rate")}
    codes = {row.item_id: row for row in ItemCode.objects.filter(item__in=[line.item_id for line in lines])}
    items, sums = [], {"sales": ZERO, "commercial": ZERO, "net": ZERO, "tax": ZERO, "total": ZERO}
    for line in lines:
        code = codes.get(line.item_id)
        if code is None:
            problems.append(words["code"].format(item=line.item.item_code))
        total_sale = money_round(line.quantity * line.unit_sale_price)
        discount = money_round(line.line_discount_amount or ZERO)
        net = money_round(total_sale - discount)
        tax_row = taxes.get(line.pk)
        tax = tax_row.tax_amount if tax_row else ZERO
        subtype = (tax_row.tax_rate.eta_subtype if tax_row and tax_row.tax_rate else "") or ("V009" if tax else "V003")
        item = {"internalCode": line.item.item_code, "description": (line.description or line.item.item_name)[:500], "itemType": code.code_type if code else "EGS",
                "itemCode": code.code if code else "", "unitType": code.unit_type if code else "EA", "quantity": _num(line.quantity),
                "unitPrice": _num(line.unit_sale_price), "netSale": _num(net), "totalSale": _num(total_sale), "total": _num(net + tax)}
        if discount:
            item["commercialDiscountData"] = [{"amount": _num(discount), "description": "خصم"}]
        item["taxableItems"] = [{"taxType": "T1", "amount": _num(tax), "subType": subtype, "rate": _num(tax_row.rate if tax_row else ZERO)}]
        items.append(item)
        sums["sales"] += total_sale
        sums["commercial"] += discount
        sums["net"] += net
        sums["tax"] += tax
        sums["total"] += net + tax
    extra = money_round(invoice.discount_amount or ZERO)
    total = money_round(sums["total"] - extra)
    if total != invoice.total_amount:
        problems.append(words["total"].format(total=total, amount=invoice.total_amount))
    from sales.pos import WALK_IN_CODE

    buyer = {"type": "P"}
    profile = ReceiverProfile.objects.filter(customer=invoice.customer).first()
    if invoice.customer.customer_code != WALK_IN_CODE:
        buyer["name"] = invoice.customer.name[:100]
        if invoice.customer.phone:
            buyer["mobileNumber"] = invoice.customer.phone[:30]
    if profile and profile.tax_id:
        buyer["id"] = profile.tax_id
    if total >= PERSON_ID_THRESHOLD and not (buyer.get("id") and buyer.get("name")):
        problems.append(words["buyer"].format(limit=f"{PERSON_ID_THRESHOLD:,.0f}"))
    receipt = {
        # Optional header fields are left out; previousUUID is empty for the device's first receipt.
        "header": {"dateTimeIssued": _issued_at(invoice), "receiptNumber": invoice.invoice_number, "uuid": "", "previousUUID": previous_uuid, "currency": "EGP"},
        "documentType": {"receiptType": "s", "typeVersion": "1.2"},
        "seller": {"rin": rin, "companyTradeName": (company["legal_name"] or company["name"])[:200], "branchCode": till.branch_code if till else "0",
                   "branchAddress": {"country": "EG", "governate": issuer["einvoice.governate"], "regionCity": issuer["einvoice.region_city"],
                                     "street": issuer["einvoice.street"], "buildingNumber": issuer["einvoice.building_number"]},
                   "deviceSerialNumber": till.serial_number if till else "", "activityCode": issuer["einvoice.activity_code"]},
        "buyer": buyer,
        "itemData": items,
        "totalSales": _num(sums["sales"]), "totalCommercialDiscount": _num(sums["commercial"]), "totalItemsDiscount": 0,
        "extraReceiptDiscountData": [{"amount": _num(extra), "description": "خصم على الإيصال"}] if extra else [],
        "netAmount": _num(sums["net"]), "feesAmount": 0, "totalAmount": _num(total),
        "taxTotals": [{"taxType": "T1", "amount": _num(sums["tax"])}], "paymentMethod": _payment_method(invoice), "adjustment": 0,
    }
    return receipt, problems


def receipt_uuid(receipt):
    """SHA-256 of the receipt serialized the authority's way, its own UUID empty: 64 hex characters."""

    from einvoice.portal import serialize

    blank = {**receipt, "header": {**receipt["header"], "uuid": ""}}
    return hashlib.sha256(serialize(blank).encode("utf-8")).hexdigest()


def _audit(receipt, action):
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=None, module="ereceipt", action=action, object_type="ereceipt.Receipt",
                            object_id=str(receipt.pk), before_data={}, after_data={"invoice": receipt.invoice.invoice_number, "status": receipt.status,
                                                                                  "uuid": receipt.uuid, "previous": receipt.previous_uuid})


@transaction.atomic
def issue(invoice, lang="ar"):
    """Issue (or keep as not issued) the receipt of a posted till sale. Returns the Receipt."""

    existing = Receipt.objects.select_for_update().filter(invoice=invoice).first()
    if existing is not None and existing.status != ReceiptStatus.NOT_ISSUED:
        return existing
    till = Device.objects.select_for_update().filter(pk=getattr(device(), "pk", None)).first()  # one receipt at a time per device
    document, problems = build(invoice, till, till.last_uuid if till else "", lang)
    receipt = existing or Receipt(invoice=invoice)
    if problems:
        receipt.status, receipt.problems, receipt.device = ReceiptStatus.NOT_ISSUED, problems, till
        receipt.save()
        return receipt
    uuid = receipt_uuid(document)
    document["header"]["uuid"] = uuid
    receipt.device, receipt.status, receipt.problems = till, ReceiptStatus.QUEUED, []
    receipt.uuid, receipt.previous_uuid, receipt.issued_at = uuid, till.last_uuid, timezone.now()
    receipt.document_text = json.dumps(document, ensure_ascii=False)  # the order it was hashed in
    receipt.save()
    till.last_uuid = uuid
    till.save(update_fields=["last_uuid"])
    _audit(receipt, "issue_receipt")
    return receipt


def issue_again(receipt, lang="ar"):
    """A receipt kept as not issued, once its data is complete: issued now, at the end of the device's chain."""

    return issue(receipt.invoice, lang)


def issued_moment(receipt):
    """The issue time written in the hashed receipt (``dateTimeIssued``, UTC): what the 24 hours count from."""

    from datetime import datetime, timezone as dt_timezone

    text = receipt.document.get("header", {}).get("dateTimeIssued", "")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return receipt.issued_at


def scoped(queryset):
    """Receipts of the entity being worked in (HG-034): through their invoice's selling location."""

    from entities import scope as entity_scope

    return entity_scope.scope(queryset, "invoice__" + entity_scope.SALES_INVOICE)


def queue():
    """Issued receipts not sent yet, oldest first, each due 24 hours after the issue time written in it (Codex #271:
    not after the moment it was made, which can be later for an offline sale or one completed afterwards)."""

    now = timezone.now()
    rows = []
    for receipt in scoped(Receipt.objects.filter(status=ReceiptStatus.QUEUED)).select_related("invoice"):
        due = issued_moment(receipt) + WINDOW
        rows.append({"receipt": receipt, "due": due, "late": due < now})
    return sorted(rows, key=lambda row: (row["due"], row["receipt"].pk))


def qr_text(receipt, environment="prod"):
    """The single line the receipt's QR code holds: the authority's share link, the total and the issuer's RIN."""

    if not receipt.uuid:
        return ""
    header, seller = receipt.document["header"], receipt.document["seller"]
    url = f"{PORTAL.get(environment, PORTAL['prod'])}/receipts/search/{receipt.uuid}/share/{header['dateTimeIssued']}"
    return f"{url}#Total:{Decimal(str(receipt.document['totalAmount'])):.3f},IssuerRIN:{seller['rin']}"


def printed_qr(invoice):
    """What the printed receipt shows for its e-receipt: the QR line and the UUID, or None before it is issued."""

    from einvoice import portal

    receipt = Receipt.objects.filter(invoice=invoice).exclude(uuid="").first()
    if receipt is None:
        return None
    return {"text": qr_text(receipt, portal.settings()["environment"]), "uuid": receipt.uuid}


# ---- following the till ----

def _on_audit(sender, instance, created, **kwargs):
    if not created or instance.module != "sales" or instance.action != "post_sales_invoice" or not enabled():
        return
    from sales.models import SalesInvoice

    invoice = SalesInvoice.objects.select_related("customer").get(pk=int(instance.object_id))
    if _is_till_sale(invoice):
        issue(invoice)


def connect():
    post_save.connect(_on_audit, sender=AuditLog, dispatch_uid="ereceipt-follows-the-till", weak=False)
