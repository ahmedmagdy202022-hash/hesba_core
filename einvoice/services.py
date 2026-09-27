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
from datetime import datetime, time, timezone as dt_timezone
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
