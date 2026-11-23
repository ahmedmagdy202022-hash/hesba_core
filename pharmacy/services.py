"""T10 (MARKET-PARITY): drug data, alternatives and dispensing a prescription (HG-062).

Nothing here posts, moves stock or touches a balance. Drug data sits beside the
item; alternatives are the other active items with the same active
ingredient, with what each has on the shelf. A prescription is dispensed on the
ordinary sales invoice form, which saves an ordinary draft and then calls
:func:`link_dispense` in the same transaction; whether a prescription is still
waiting is read from the invoices it is linked to, so a cancelled invoice frees
it by itself.
"""

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from master_data.models import Item
from permissions.services import user_has_permission

from .models import DosageForm, DrugInfo, Prescription, PrescriptionInvoice, PrescriptionLine, PrescriptionStatus as Status

ZERO = Decimal("0")
CAPABILITY = "drugs"
LOOKUP, EDIT = "master_data.view_master_data", "master_data.manage_items"
VIEW, MANAGE = "sales.view_sales_invoices", "sales.create_sales_invoice"
#: A chronic prescription shows as due this many days before its refill date.
DUE_AHEAD_DAYS = 3
#: The ordinary invoice form takes up to 20 lines.
INVOICE_LINES = 20

FORM_LABELS = {
    "ar": {"tablet": "أقراص", "capsule": "كبسول", "syrup": "شراب", "suspension": "معلق", "injection": "حقن", "drops": "نقط",
           "cream": "كريم أو مرهم", "spray": "بخاخ", "sachet": "أكياس فوار", "suppository": "لبوس", "other": "شكل تاني"},
    "en": {"tablet": "Tablets", "capsule": "Capsules", "syrup": "Syrup", "suspension": "Suspension", "injection": "Injection", "drops": "Drops",
           "cream": "Cream or ointment", "spray": "Spray", "sachet": "Sachets", "suppository": "Suppositories", "other": "Other"},
}

STATE_LABELS = {
    "ar": {"open": "مستنية الصرف", "dispensed": "اتصرفت", "due": "ميعاد التجديد", "waiting": "اتصرفت، والتجديد جاي", "cancelled": "اتلغت"},
    "en": {"open": "Waiting to be dispensed", "dispensed": "Dispensed", "due": "Refill due", "waiting": "Dispensed, refill coming", "cancelled": "Cancelled"},
}

MESSAGES = {
    "ar": {"no_capability": "قاعدة الأدوية مقفولة في الإعدادات.", "ingredient": "اكتب المادة الفعالة.", "item": "اختار الصنف.",
           "patient": "اكتب اسم المريض.", "date": "التاريخ مش مظبوط.", "refill": "التجديد بيبقى عدد أيام من 1 لـ 365.",
           "line": "اكتب اسم الدوا اللي في الروشتة أو اختار الصنف.", "quantity": "الكمية لازم تكون أكبر من صفر.",
           "locked": "الروشتة دي اتصرفت أو اتلغت؛ مينفعش تتعدّل.", "cancelled": "الروشتة دي اتلغت.",
           "dispensed": "الروشتة {number} اتصرفت قبل كده.", "nothing": "مفيش ولا دوا متحدد له صنف على الروشتة؛ اختار الأصناف الأول.",
           "other_customer": "الفاتورة دي لعميل تاني غير صاحب الروشتة {number}؛ رجّع العميل أو اعمل الفاتورة من غير الروشتة.",
           "not_alternative": "الصنف ده مش بديل للدوا اللي على الروشتة.", "has_dispenses": "الروشتة دي عليها فواتير صرف؛ مينفعش تتلغي.",
           "note_invoice": "روشتة {number}", "too_many": "الروشتة بتاخد لحد {limit} دوا؛ اعمل روشتة تانية للباقي."},
    "en": {"no_capability": "The drug database is switched off in Settings.", "ingredient": "Enter the active ingredient.", "item": "Choose the item.",
           "patient": "Enter the patient's name.", "date": "The date is not valid.", "refill": "The refill is a number of days from 1 to 365.",
           "line": "Enter the drug written on the prescription or choose the item.", "quantity": "The quantity must be more than zero.",
           "locked": "This prescription is dispensed or cancelled, so it cannot change.", "cancelled": "This prescription is cancelled.",
           "dispensed": "Prescription {number} was already dispensed.", "nothing": "No drug on the prescription has an item yet; choose the items first.",
           "other_customer": "This invoice is for another customer than prescription {number}'s; put the customer back or make the invoice without the prescription.",
           "not_alternative": "That item is not an alternative to the drug on the prescription.", "has_dispenses": "This prescription has dispensing invoices and cannot be cancelled.",
           "note_invoice": "Prescription {number}", "too_many": "A prescription takes up to {limit} drugs; record a second one for the rest."},
}


# ---- who may do what ----

def enabled():
    from settings_core.capabilities import capability_enabled

    return capability_enabled(CAPABILITY)


def can_lookup(user):
    return user_has_permission(user, LOOKUP)


def can_edit(user):
    return user_has_permission(user, EDIT)


def can_view(user):
    return user_has_permission(user, VIEW)


def can_manage(user):
    return user_has_permission(user, MANAGE)


def scoped(queryset=None):
    return entity_scope.scope((queryset if queryset is not None else Prescription.objects).all(), "entity")


def _audit(user, action, obj, after, event=AuditEventType.UPDATE, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="pharmacy", action=action, object_type=f"pharmacy.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


def _require(user, permission, words):
    if not enabled():
        raise ValidationError(words["no_capability"])
    if not user_has_permission(user, permission):
        raise PermissionDenied(f"The pharmacy screens need {permission}.")


# ---- matching ingredients and strengths ----

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")
_SPLIT = re.compile(r"\s*(?:\+|/|،|,|&)\s*")
_STRENGTH_UNITS = (("ملليجرام", "mg"), ("مليجرام", "mg"), ("ملجم", "mg"), ("مجم", "mg"), ("مج", "mg"), ("ميكروجرام", "mcg"),
                   ("ملليلتر", "ml"), ("مللي", "ml"), ("مل", "ml"), ("جرام", "g"), ("جم", "g"), ("وحدة", "iu"), ("وحده", "iu"), ("٪", "%"))


def _plain(text):
    text = str(text or "").translate(_DIGITS).strip().lower()
    return text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")


def ingredient_key(text):
    """The active ingredient reduced for matching: one spelling per ingredient, a combination in a fixed order.

    "Amoxicillin + Clavulanic acid" and "clavulanic acid/amoxicillin" give the same key."""

    parts = [re.sub(r"[\s\-_.()]+", "", part) for part in _SPLIT.split(_plain(text))]
    return "+".join(sorted(part for part in parts if part))[:255]


def strength_key(text):
    """"500 مجم", "500mg" and "٥٠٠ ملجم" all give "500mg"."""

    text = _plain(text)
    for word, unit in _STRENGTH_UNITS:
        text = text.replace(word, unit)
    return re.sub(r"\s+", "", text)[:60]


#: Words a drug file gives the dosage form, reduced as ``_plain`` reduces them.
FORM_WORDS = {
    "tablet": ("اقراص", "قرص", "tab", "tabs", "tablet", "tablets", "f.c.tab", "شريط"), "capsule": ("كبسول", "كبسوله", "cap", "caps", "capsule", "capsules"),
    "syrup": ("شراب", "syrup", "syr"), "suspension": ("معلق", "suspension", "susp"), "injection": ("حقن", "حقنه", "امبول", "injection", "inj", "amp", "vial"),
    "drops": ("نقط", "قطره", "drops", "drop"), "cream": ("كريم", "مرهم", "جيل", "cream", "ointment", "gel", "oint"),
    "spray": ("بخاخ", "spray", "inhaler"), "sachet": ("اكياس", "كيس", "فوار", "sachet", "sachets", "effervescent"),
    "suppository": ("لبوس", "suppository", "supp"), "other": ("other", "اخر", "تاني"),
}


def form_from_text(text):
    word = _plain(text)
    if word in DosageForm.values:
        return word
    return next((form for form, words in FORM_WORDS.items() if word in words), "")


# ---- drug data ----

@transaction.atomic
def save_drug(item, data, user, lang="ar"):
    """Create or change the drug data of ``item`` (``remove`` takes it off). Returns the DrugInfo, or None once removed."""

    words = MESSAGES[lang]
    _require(user, EDIT, words)
    if not isinstance(item, Item):
        raise ValidationError(words["item"])
    ingredient = (data.get("active_ingredient") or "").strip()[:255]
    existing = DrugInfo.objects.select_for_update().filter(item=item).first()
    if data.get("remove"):
        if existing is not None:
            existing.delete()
            _audit(user, "remove_drug", item, {"item": item.item_code})
        return None
    if not ingredient:
        raise ValidationError(words["ingredient"])
    form = data.get("form") or ""
    form = form if form in DosageForm.values else form_from_text(form)
    strength = (data.get("strength") or "").strip()[:60]
    values = {"active_ingredient": ingredient, "ingredient_key": ingredient_key(ingredient), "strength": strength, "strength_key": strength_key(strength),
              "form": form, "manufacturer": (data.get("manufacturer") or "").strip()[:120],
              "prescription_only": data.get("prescription_only") in (True, "1", "on", "true", "yes"), "updated_by": user}
    before = {"active_ingredient": existing.active_ingredient, "strength": existing.strength} if existing else {}
    info, created = DrugInfo.objects.update_or_create(item=item, defaults=values)
    _audit(user, "create_drug" if created else "update_drug", item, {"item": item.item_code, "active_ingredient": ingredient, "strength": strength},
           AuditEventType.CREATE if created else AuditEventType.UPDATE, before)
    return info


def from_import(item, data):
    """IMPORT: the drug columns of an items file (active ingredient, strength, form, maker, prescription only).

    Called for each applied item row; a row without an active ingredient leaves the item's drug data as it was."""

    ingredient = (data.get("active_ingredient") or "").strip()[:255]
    if not ingredient:
        return None
    strength = (data.get("strength") or "").strip()[:60]
    flag = str(data.get("prescription_only") or "").strip().lower()
    info, _created = DrugInfo.objects.update_or_create(item=item, defaults={
        "active_ingredient": ingredient, "ingredient_key": ingredient_key(ingredient), "strength": strength, "strength_key": strength_key(strength),
        "form": form_from_text(data.get("dosage_form")), "manufacturer": (data.get("manufacturer") or "").strip()[:120],
        "prescription_only": flag in ("true", "1", "yes", "y")})
    return info


def on_hand(item_ids):
    """{item_id: quantity on the shelf} in the locations the current entity sees."""

    from batches.services import stock_on_hand
    from master_data.models import Location

    locations = entity_scope.locations(Location.objects) if entity_scope.is_scoped() else None
    return stock_on_hand(list(item_ids), locations)


def _price(item, customer=None):
    from pricing.services import price_for

    return price_for(item, customer)


def alternatives(item, customer=None, limit=30):
    """The other active items with the same active ingredient, the same strength and form first, then those in stock, then the cheapest.

    Each row: item, drug, match ("same" for the same strength and form, "other" for another strength or form), on_hand, price."""

    drug = getattr(item, "drug", None) if item is not None else None
    if drug is None or not drug.ingredient_key:
        return []
    others = list(Item.objects.filter(active=True, drug__ingredient_key=drug.ingredient_key).exclude(pk=item.pk).select_related("drug")[:200])
    stock = on_hand(other.pk for other in others)
    rows = []
    for other in others:
        same = other.drug.strength_key == drug.strength_key and (other.drug.form or "") == (drug.form or "")
        rows.append({"item": other, "drug": other.drug, "match": "same" if same else "other", "on_hand": stock.get(other.pk, ZERO),
                     "price": _price(other, customer)})
    rows.sort(key=lambda row: (row["match"] != "same", row["on_hand"] <= 0, row["price"], row["item"].item_name))
    return rows[:limit]


def search(query="", *, missing=False, limit=60):
    """Items that match a name, a barcode, a code or an active ingredient, each with its stock, price and how many alternatives are in stock.

    ``missing`` lists the stocked items that have no drug data yet."""

    items = Item.objects.filter(active=True).select_related("drug")
    if missing:
        items = items.filter(drug__isnull=True, is_stock_tracked=True)
    query = (query or "").strip()
    if query:
        key = ingredient_key(query)
        condition = Q(item_name__icontains=query) | Q(barcode=query) | Q(item_code__iexact=query) | Q(drug__active_ingredient__icontains=query)
        if key:
            condition |= Q(drug__ingredient_key__contains=key)
        items = items.filter(condition)
    items = list(items.order_by("item_name")[:limit])
    keys = {item.drug.ingredient_key for item in items if getattr(item, "drug", None) is not None}
    family = list(Item.objects.filter(active=True, drug__ingredient_key__in=keys).values_list("pk", "drug__ingredient_key")) if keys else []
    stock = on_hand({item.pk for item in items} | {pk for pk, _key in family})
    in_stock = {}
    for pk, key in family:
        if stock.get(pk, ZERO) > 0:
            in_stock.setdefault(key, set()).add(pk)
    rows = []
    for item in items:
        drug = getattr(item, "drug", None)
        alternatives_in_stock = len(in_stock.get(drug.ingredient_key, set()) - {item.pk}) if drug else 0
        rows.append({"item": item, "drug": drug, "on_hand": stock.get(item.pk, ZERO), "price": _price(item), "alternatives": alternatives_in_stock})
    return rows


def missing_count():
    return Item.objects.filter(active=True, is_stock_tracked=True, drug__isnull=True).count()


def ingredients(limit=500):
    """The active ingredients already written, so the form suggests one spelling."""

    return list(DrugInfo.objects.order_by("active_ingredient").values_list("active_ingredient", flat=True).distinct()[:limit])


# ---- prescriptions ----

def _date(raw, words, default=None):
    if raw in (None, ""):
        return default
    if hasattr(raw, "year"):
        return raw
    from datetime import date

    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise ValidationError(words["date"])


def _refill(raw, words):
    if raw in (None, ""):
        return None
    try:
        days = int(str(raw).strip())
    except ValueError:
        raise ValidationError(words["refill"])
    if not 1 <= days <= 365:
        raise ValidationError(words["refill"])
    return days


def _header(data, words, default_date):
    from master_data.models import Customer

    patient = (data.get("patient_name") or "").strip()[:120]
    customer = data.get("customer") if isinstance(data.get("customer"), Customer) else None
    if not patient and customer is not None:
        patient = customer.name[:120]
    if not patient:
        raise ValidationError(words["patient"])
    return {"customer": customer, "patient_name": patient, "patient_phone": (data.get("patient_phone") or (customer.phone if customer else "") or "").strip()[:30],
            "doctor_name": (data.get("doctor_name") or "").strip()[:120], "issued_on": _date(data.get("issued_on"), words, default_date),
            "refill_days": _refill(data.get("refill_days"), words), "notes": (data.get("notes") or "").strip()[:255]}


@transaction.atomic
def create_prescription(data, user, lang="ar"):
    """A prescription as the pharmacy receives it. Returns the Prescription, waiting for its drugs."""

    from config.numbering import next_in_series

    words = MESSAGES[lang]
    _require(user, MANAGE, words)
    values = {**_header(data, words, timezone.localdate()), "entity": entity_scope.entity() or None, "created_by": user}
    for attempt in range(5):
        number = next_in_series(Prescription, "number", "RX-")
        try:
            with transaction.atomic():
                prescription = Prescription.objects.create(number=number, **values)
            break
        except IntegrityError:
            if attempt == 4 or not Prescription.objects.filter(number=number).exists():
                raise
    _audit(user, "create_prescription", prescription, {"number": number, "patient": prescription.patient_name}, AuditEventType.CREATE)
    return prescription


def _locked(prescription, words):
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if not editable(prescription):
        raise ValidationError(words["locked"])
    return prescription


@transaction.atomic
def update_header(prescription, data, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user, MANAGE, words)
    prescription = _locked(prescription, words)
    values = _header({**data, "customer": data.get("customer", prescription.customer)}, words, prescription.issued_on)
    if dispenses(prescription):
        values.pop("customer")  # its invoices were made for this customer
    for key, value in values.items():
        setattr(prescription, key, value)
    prescription.save(update_fields=list(values))
    _audit(user, "update_prescription", prescription, {"number": prescription.number, "refill_days": prescription.refill_days})
    return prescription


def _quantity(raw, words):
    try:
        value = Decimal(str(raw if raw not in (None, "") else "1").strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError(words["quantity"])
    if not value.is_finite() or value <= 0 or value >= Decimal("1000000"):
        raise ValidationError(words["quantity"])
    return value.quantize(Decimal("0.001"))


@transaction.atomic
def add_line(prescription, data, user, lang="ar"):
    """One drug on the prescription: what the doctor wrote, the item dispensed for it (if chosen), how many and how to take it."""

    words = MESSAGES[lang]
    _require(user, MANAGE, words)
    prescription = _locked(prescription, words)
    if prescription.lines.count() >= INVOICE_LINES:  # one invoice dispenses the whole prescription, never part of it (Codex on #262)
        raise ValidationError(words["too_many"].format(limit=INVOICE_LINES))
    item = data.get("item") if isinstance(data.get("item"), Item) and data.get("item").active else None
    name = (data.get("prescribed_name") or "").strip()[:255] or (item.item_name if item else "")
    if not name:
        raise ValidationError(words["line"])
    line = PrescriptionLine.objects.create(prescription=prescription, prescribed_name=name, item=item, quantity=_quantity(data.get("quantity"), words),
                                           dosage=(data.get("dosage") or "").strip()[:255])
    _audit(user, "add_line", prescription, {"number": prescription.number, "drug": name, "item": item.item_code if item else ""})
    return line


@transaction.atomic
def remove_line(line, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user, MANAGE, words)
    prescription = _locked(line.prescription, words)
    _audit(user, "remove_line", prescription, {"number": prescription.number, "drug": line.prescribed_name})
    line.delete()


@transaction.atomic
def choose_item(line, item, user, lang="ar"):
    """Dispense ``item`` for a line: the drug itself, or an alternative with the same active ingredient (marked as substituted)."""

    words = MESSAGES[lang]
    _require(user, MANAGE, words)
    _locked(line.prescription, words)
    if not isinstance(item, Item) or not item.active:
        raise ValidationError(words["item"])
    line = PrescriptionLine.objects.select_for_update().get(pk=line.pk)
    current = line.item
    substituted = line.substituted
    if current is not None and current.pk != item.pk:
        current_drug, chosen_drug = getattr(current, "drug", None), getattr(item, "drug", None)
        if current_drug is None or chosen_drug is None or current_drug.ingredient_key != chosen_drug.ingredient_key:
            raise ValidationError(words["not_alternative"])
        substituted = True
    line.item, line.substituted = item, substituted
    line.save(update_fields=["item", "substituted"])
    _audit(user, "choose_item", line.prescription, {"number": line.prescription.number, "drug": line.prescribed_name, "item": item.item_code,
                                                    "substituted": substituted})
    return line


@transaction.atomic
def cancel(prescription, user, lang="ar"):
    words = MESSAGES[lang]
    _require(user, MANAGE, words)
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if dispenses(prescription):
        raise ValidationError(words["has_dispenses"])
    prescription.status = Status.CANCELLED
    prescription.save(update_fields=["status"])
    _audit(user, "cancel_prescription", prescription, {"number": prescription.number})
    return prescription


# ---- where a prescription stands ----

def dispenses(prescription):
    """The invoices that dispensed it and were not cancelled, oldest first."""

    from sales.models import SalesInvoice, SalesInvoiceStatus

    return list(SalesInvoice.objects.filter(prescription_link__prescription=prescription).exclude(status=SalesInvoiceStatus.CANCELLED)
                .order_by("invoice_date", "pk"))


def dispenses_by_prescription(prescriptions):
    """{prescription pk: its dispensing invoices, oldest first} for many prescriptions in one read (the lists)."""

    from sales.models import SalesInvoiceStatus

    result = {prescription.pk: [] for prescription in prescriptions}
    links = (PrescriptionInvoice.objects.filter(prescription_id__in=list(result)).exclude(invoice__status=SalesInvoiceStatus.CANCELLED)
             .select_related("invoice").order_by("invoice__invoice_date", "invoice__pk"))
    for link in links:
        result[link.prescription_id].append(link.invoice)
    return result


def next_refill(prescription, done=None):
    done = dispenses(prescription) if done is None else done
    if not prescription.refill_days or not done:
        return None
    return done[-1].invoice_date + timedelta(days=prescription.refill_days)


def state(prescription, today=None, done=None):
    today = today or timezone.localdate()
    if prescription.status == Status.CANCELLED:
        return "cancelled"
    done = dispenses(prescription) if done is None else done
    if not done:
        return "open"
    if not prescription.refill_days:
        return "dispensed"
    return "due" if next_refill(prescription, done) <= today + timedelta(days=DUE_AHEAD_DAYS) else "waiting"


def editable(prescription, done=None):
    """Lines and details change until the prescription is dispensed; a chronic one stays open for dose changes."""

    if prescription.status == Status.CANCELLED:
        return False
    return bool(prescription.refill_days) or not (dispenses(prescription) if done is None else done)


def can_dispense(prescription, done=None):
    done = dispenses(prescription) if done is None else done
    if prescription.status == Status.CANCELLED or (done and not prescription.refill_days):
        return False
    return prescription.lines.filter(item__isnull=False, item__active=True).exists()


def waiting(queryset):
    """The active prescriptions no invoice has dispensed yet (a cancelled invoice does not count), filtered in the database."""

    from sales.models import SalesInvoiceStatus

    return queryset.filter(status=Status.ACTIVE).exclude(dispenses__invoice__status__in=(SalesInvoiceStatus.DRAFT, SalesInvoiceStatus.POSTED))


def due(queryset, today=None):
    """The chronic prescriptions due for their refill, oldest refill date first: every one is checked, none cut off (Codex on #262)."""

    from sales.models import SalesInvoiceStatus

    today = today or timezone.localdate()
    chronic = list(queryset.filter(status=Status.ACTIVE, refill_days__isnull=False,
                                   dispenses__invoice__status__in=(SalesInvoiceStatus.DRAFT, SalesInvoiceStatus.POSTED)).distinct())
    done = dispenses_by_prescription(chronic)
    rows = [(next_refill(prescription, done[prescription.pk]), prescription) for prescription in chronic
            if state(prescription, today, done[prescription.pk]) == "due"]
    return [prescription for _refill, prescription in sorted(rows, key=lambda row: (row[0], row[1].pk))]


def counts(today=None):
    """How many are waiting to be dispensed and how many chronic ones are due for a refill."""

    return {"open": waiting(scoped()).count(), "due": len(due(scoped(), today))}


# ---- dispensing on the ordinary invoice form ----

def prescription_for_invoice(user, raw_pk, saving=False):
    """The prescription a new invoice dispenses (``?prescription=<pk>``), or None.

    When the invoice is being saved it is returned whatever its state, so :func:`link_dispense`
    refuses one that another invoice dispensed in the meantime instead of the sale going through unlinked."""

    if not raw_pk or not enabled() or not can_manage(user):
        return None
    try:
        pk = int(raw_pk)
    except (TypeError, ValueError):
        return None
    prescription = scoped().filter(pk=pk).select_related("customer").first()
    if prescription is None or saving:
        return prescription
    return prescription if can_dispense(prescription) else None


def invoice_initial(prescription, lang="ar"):
    """(header initial, line initials) for the ordinary invoice form: each drug chosen, at the customer's price."""

    from sales.pos import walk_in_customer

    customer = prescription.customer or walk_in_customer()
    lines = [{"item": line.item, "quantity": line.quantity, "unit_sale_price": _price(line.item, customer)}
             for line in prescription.lines.filter(item__isnull=False, item__active=True).select_related("item")[:INVOICE_LINES]]
    note = MESSAGES[lang]["note_invoice"].format(number=prescription.number)
    if prescription.doctor_name:
        note = f"{note} — {prescription.doctor_name}"
    return {"customer": customer, "notes": f"{note} — {prescription.patient_name}"[:255]}, lines


@transaction.atomic
def link_dispense(prescription, invoice, user, lang="ar"):
    """Remember that ``invoice`` dispensed ``prescription``. Called inside the transaction that saved the draft."""

    words = MESSAGES[lang]
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if prescription.status == Status.CANCELLED:
        raise ValidationError(words["cancelled"])
    if dispenses(prescription) and not prescription.refill_days:
        raise ValidationError(words["dispensed"].format(number=prescription.number))
    if prescription.customer_id and invoice.customer_id != prescription.customer_id:
        raise ValidationError(words["other_customer"].format(number=prescription.number))
    link = PrescriptionInvoice.objects.create(prescription=prescription, invoice=invoice)
    _audit(user, "dispense", prescription, {"number": prescription.number, "invoice": invoice.invoice_number})
    return link
