"""T10 screens: find a drug and its alternatives, the drug data of an item, and prescriptions dispensed on ordinary sales invoices."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Item
from permissions.decorators import require_permission

from . import services
from .models import DosageForm, PrescriptionLine

WORDS = {
    "ar": {
        "title": "الأدوية والبدائل", "intro": "دوّر باسم الدوا أو المادة الفعالة أو الباركود؛ كل دوا قدامه رصيده وسعره، وعدد البدائل اللي عندك منها في المخزون.",
        "tab_drugs": "الأدوية", "tab_rx": "الروشتات", "tab_new": "+ روشتة جديدة", "search": "اسم الدوا أو المادة الفعالة أو الباركود", "find": "بحث",
        "missing_filter": "أصناف من غير بيانات دوا", "all_filter": "كل الأدوية", "missing_hint": "{count} صنف في المخزون لسه من غير مادة فعالة. البدائل بتتحسب من المادة الفعالة.",
        "import_hint": "عندك ملف أدوية؟ استورده من شاشة الاستيراد ← الأصناف، وفيه أعمدة «المادة الفعالة» و«التركيز» و«الشكل» و«الشركة» و«بروشتة».",
        "import": "استيراد ملف الأدوية", "item": "الدوا", "ingredient": "المادة الفعالة", "strength": "التركيز", "form": "الشكل", "maker": "الشركة",
        "stock": "الرصيد", "price": "السعر", "alternatives": "بدائل في المخزون", "rx_only": "بروشتة", "no_data": "من غير بيانات", "none": "مفيش أدوية بالاسم ده.",
        "type_more": "اكتب اسم دوا أو مادة فعالة.", "open": "افتح", "back": "الأدوية والبدائل",
        "drug_data": "بيانات الدوا", "rx_only_label": "مايتصرفش غير بروشتة", "save": "حفظ", "remove": "شيل بيانات الدوا", "saved": "اتحفظت بيانات الدوا.",
        "removed": "اتشالت بيانات الدوا.", "choose": "اختار", "ingredient_hint": "اكتبها بنفس الطريقة في كل الأصناف (مثلًا Paracetamol)، والتركيبة بعلامة +.",
        "alt_title": "البدائل", "alt_intro": "أصناف ليها نفس المادة الفعالة. المطابق في التركيز والشكل الأول، وبعده اللي في المخزون، وبعده الأرخص.",
        "same": "مطابق", "other": "تركيز أو شكل تاني", "no_alt": "مفيش بدائل مسجلة للمادة الفعالة دي.", "no_alt_data": "سجّل المادة الفعالة الأول علشان البدائل تظهر.",
        "sold_out": "خلصان",
    },
    "en": {
        "title": "Drugs & alternatives", "intro": "Search by drug name, active ingredient or barcode; each drug shows its stock and price, and how many alternatives you have in stock.",
        "tab_drugs": "Drugs", "tab_rx": "Prescriptions", "tab_new": "+ New prescription", "search": "Drug name, active ingredient or barcode", "find": "Search",
        "missing_filter": "Items with no drug data", "all_filter": "All drugs", "missing_hint": "{count} stocked items have no active ingredient yet. Alternatives come from the active ingredient.",
        "import_hint": "Have a drug file? Import it from Imports → Items, with the columns “Active ingredient”, “Strength”, “Form”, “Maker” and “Prescription only”.",
        "import": "Import the drug file", "item": "Drug", "ingredient": "Active ingredient", "strength": "Strength", "form": "Form", "maker": "Maker",
        "stock": "Stock", "price": "Price", "alternatives": "Alternatives in stock", "rx_only": "Rx", "no_data": "No data", "none": "No drugs by that name.",
        "type_more": "Type a drug name or an active ingredient.", "open": "Open", "back": "Drugs & alternatives",
        "drug_data": "Drug data", "rx_only_label": "Sold on prescription only", "save": "Save", "remove": "Remove the drug data", "saved": "Drug data saved.",
        "removed": "Drug data removed.", "choose": "Choose", "ingredient_hint": "Write it the same way on every item (Paracetamol, say); a combination with +.",
        "alt_title": "Alternatives", "alt_intro": "Items with the same active ingredient: the same strength and form first, then those in stock, then the cheapest.",
        "same": "Same", "other": "Other strength or form", "no_alt": "No alternatives recorded for this active ingredient.", "no_alt_data": "Record the active ingredient first to see alternatives.",
        "sold_out": "Sold out",
    },
}

RX_WORDS = {
    "ar": {
        "title": "الروشتات", "intro": "الروشتة بتتسجل زي ما المريض جابها، وبتتصرف بفاتورة بيع عادية. الروشتة المزمنة بتتجدد كل كام يوم وبتظهر هنا لما ميعادها يقرب.",
        "new": "+ روشتة جديدة", "search": "دوّر برقم الروشتة أو المريض أو الدكتور أو التليفون", "find": "بحث", "f_open": "مستنية الصرف", "f_due": "ميعاد التجديد",
        "f_all": "الكل", "number": "الرقم", "patient": "المريض", "doctor": "الدكتور", "date": "التاريخ", "status": "الحالة", "next_refill": "التجديد الجاي",
        "none": "مفيش روشتات هنا.", "back": "الروشتات", "customer": "حساب العميل", "customer_hint": "سيبه فاضي لو البيع نقدي.", "cash": "نقدي (من غير حساب)",
        "phone": "تليفون المريض", "issued_on": "تاريخ الروشتة", "refill": "تتجدد كل (يوم)", "refill_hint": "للأمراض المزمنة: مثلًا 30. سيبه فاضي لو مرة واحدة.",
        "notes": "ملاحظات", "create_btn": "سجّل الروشتة وضيف الأدوية", "created": "اتسجلت الروشتة {number}.", "saved": "اتحفظ.", "save": "حفظ", "lines": "الأدوية",
        "prescribed": "المكتوب في الروشتة", "dispensed_item": "اللي هيتصرف", "qty": "الكمية", "dosage": "الجرعة", "dosage_hint": "مثلًا: قرص كل 8 ساعات بعد الأكل",
        "stock": "الرصيد", "price": "السعر", "add": "ضيف", "remove": "شيل", "no_lines": "لسه مفيش أدوية على الروشتة.", "choose": "اختار الصنف",
        "pick": "اصرف ده", "alternatives": "بدائل في المخزون", "substituted": "بديل", "short": "الرصيد مش مكفي", "no_item": "لسه مااخترتش صنف",
        "dispense": "اصرف الروشتة بفاتورة بيع", "dispense_again": "اصرف التجديد بفاتورة بيع",
        "dispense_hint": "بتفتح فاتورة البيع العادية فيها أدوية الروشتة بسعر العميل. التشغيلات الأقرب انتهاءً بتطلع الأول.",
        "dispenses": "فواتير الصرف", "no_dispenses": "لسه ماتصرفتش.", "total": "الإجمالي", "print": "اطبع الجرعات للمريض", "cancel": "الغي الروشتة",
        "cancelled": "اتلغت الروشتة.", "details": "بيانات الروشتة", "doc": "إرشادات صرف الروشتة", "rx_only": "بروشتة", "whatsapp": "ابعت الجرعات واتساب",
        "share_text": "روشتة {number} من {company}:\n{lines}",
    },
    "en": {
        "title": "Prescriptions", "intro": "A prescription is recorded as the patient brings it and dispensed on an ordinary sales invoice. A chronic one is refilled every so many days and shows here when its date comes near.",
        "new": "+ New prescription", "search": "Search by number, patient, doctor or phone", "find": "Search", "f_open": "Waiting to be dispensed", "f_due": "Refill due",
        "f_all": "All", "number": "No.", "patient": "Patient", "doctor": "Doctor", "date": "Date", "status": "Status", "next_refill": "Next refill",
        "none": "No prescriptions here.", "back": "Prescriptions", "customer": "Customer account", "customer_hint": "Leave empty for a cash sale.", "cash": "Cash (no account)",
        "phone": "Patient's phone", "issued_on": "Prescription date", "refill": "Refill every (days)", "refill_hint": "For chronic conditions: 30, say. Leave empty for once.",
        "notes": "Notes", "create_btn": "Record the prescription and add the drugs", "created": "Prescription {number} recorded.", "saved": "Saved.", "save": "Save", "lines": "Drugs",
        "prescribed": "Written on the prescription", "dispensed_item": "Dispensed", "qty": "Qty", "dosage": "Dosage", "dosage_hint": "For example: one tablet every 8 hours after meals",
        "stock": "Stock", "price": "Price", "add": "Add", "remove": "Remove", "no_lines": "No drugs on the prescription yet.", "choose": "Choose the item",
        "pick": "Dispense this", "alternatives": "Alternatives in stock", "substituted": "Alternative", "short": "Not enough stock", "no_item": "No item chosen yet",
        "dispense": "Dispense on a sales invoice", "dispense_again": "Dispense the refill on a sales invoice",
        "dispense_hint": "Opens the ordinary sales invoice with the prescription's drugs at the customer's price. The batches expiring first go out first.",
        "dispenses": "Dispensing invoices", "no_dispenses": "Not dispensed yet.", "total": "Total", "print": "Print the dosage for the patient", "cancel": "Cancel the prescription",
        "cancelled": "Prescription cancelled.", "details": "Prescription details", "doc": "Prescription dispensing guide", "rx_only": "Rx", "whatsapp": "Send the dosage on WhatsApp",
        "share_text": "Prescription {number} from {company}:\n{lines}",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(getattr(exc, "messages", [str(exc)])), lang)


def _guard(request, can):
    if not services.enabled():
        raise Http404("The drug database is switched off.")
    if not can(request.user):
        raise PermissionDenied("The pharmacy screens need their permissions.")


def _base(request, lang, words, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "section": "inventory",
            "forms": services.FORM_LABELS[lang], "labels": services.STATE_LABELS[lang], "can_rx": services.can_view(request.user),
            "can_manage": services.can_manage(request.user), "can_edit": services.can_edit(request.user), **extra}


@require_permission(services.LOOKUP)
def lookup(request):
    _guard(request, services.can_lookup)
    lang = _lang(request)
    query = (request.GET.get("q") or "").strip()
    missing = request.GET.get("missing") == "1"
    rows = services.search(query, missing=missing)
    missing_count = services.missing_count()
    return render(request, "pharmacy/lookup.html", _base(request, lang, WORDS[lang], rows=rows, query=query, missing=missing, tab="drugs",
                                                         missing_count=missing_count, missing_text=WORDS[lang]["missing_hint"].format(count=missing_count)))


@require_permission(services.LOOKUP)
def drug(request, item_pk):
    _guard(request, services.can_lookup)
    lang = _lang(request)
    words = WORDS[lang]
    item = get_object_or_404(Item.objects.select_related("drug"), pk=item_pk, active=True)
    error = ""
    if request.method == "POST":
        try:
            info = services.save_drug(item, {key: request.POST.get(key) for key in request.POST}, request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, words["saved"] if info is not None else words["removed"])
            return redirect(f"{reverse('pharmacy:drug', args=[item.pk])}?lang={lang}")
        item = Item.objects.select_related("drug").get(pk=item.pk)
    stock = services.on_hand([item.pk]).get(item.pk, services.ZERO)
    return render(request, "pharmacy/drug.html", _base(request, lang, words, item=item, info=getattr(item, "drug", None), stock=stock, error=error,
                                                       price=services._price(item), alternatives=services.alternatives(item), post=request.POST,
                                                       form_choices=[(value, services.FORM_LABELS[lang][value]) for value in DosageForm.values],
                                                       ingredients=services.ingredients()))


@require_permission(services.VIEW)
def board(request):
    _guard(request, services.can_view)
    lang = _lang(request)
    today = timezone.localdate()
    show = request.GET.get("show") or "open"
    show = show if show in ("open", "due", "all") else "open"
    rows = services.scoped().select_related("customer")
    query = (request.GET.get("q") or "").strip()
    if query:
        from django.db.models import Q

        rows = rows.filter(Q(number__icontains=query) | Q(patient_name__icontains=query) | Q(doctor_name__icontains=query) | Q(patient_phone__icontains=query))
    # Codex on #262: the state is filtered before the list is cut, so nothing waiting or due is hidden behind newer ones.
    if show == "open":
        rows = list(services.waiting(rows)[:300])
    elif show == "due":
        rows = services.due(rows, today)[:300]
    else:
        rows = list(rows[:300])
    dispensed = services.dispenses_by_prescription(rows)
    shown = []
    for row in rows:
        done = dispensed[row.pk]
        row.state = services.state(row, today, done)
        row.next_refill = services.next_refill(row, done)
        shown.append(row)
    return render(request, "pharmacy/board.html", _base(request, lang, RX_WORDS[lang], rows=shown, show=show, query=query,
                                                        counts=services.counts(today), tab="rx"))


def new(request):
    _guard(request, services.can_view)
    if not services.can_manage(request.user):
        raise PermissionDenied("A prescription needs sales.create_sales_invoice.")
    lang = _lang(request)
    words = RX_WORDS[lang]
    error = ""
    if request.method == "POST":
        data = {key: request.POST.get(key) for key in request.POST}
        data["customer"] = Customer.objects.filter(pk=request.POST.get("customer") or 0, active=True).first()
        try:
            prescription = services.create_prescription(data, request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, words["created"].format(number=prescription.number))
            return redirect(f"{reverse('pharmacy:prescription', args=[prescription.pk])}?lang={lang}")
    return render(request, "pharmacy/new.html", _base(request, lang, words, customers=_customers(), post=request.POST, error=error,
                                                      today=timezone.localdate(), tab="new"))


def _customers():
    from sales.pos import WALK_IN_CODE

    return Customer.objects.filter(active=True).exclude(customer_code=WALK_IN_CODE).order_by("name")[:500]


def _lines(prescription, customer):
    """Each line with its stock, price and, when it falls short, the alternatives in stock."""

    lines = list(prescription.lines.select_related("item", "item__drug"))
    stock = services.on_hand(line.item_id for line in lines if line.item_id)
    rows = []
    for line in lines:
        row = {"line": line, "on_hand": stock.get(line.item_id, services.ZERO) if line.item_id else None,
               "price": services._price(line.item, customer) if line.item_id else None, "alternatives": []}
        row["short"] = line.item_id is not None and row["on_hand"] < line.quantity
        if row["short"]:
            row["alternatives"] = [alt for alt in services.alternatives(line.item, customer, limit=8) if alt["on_hand"] >= line.quantity][:4]
        rows.append(row)
    return rows


def detail(request, pk):
    _guard(request, services.can_view)
    lang = _lang(request)
    words = RX_WORDS[lang]
    prescription = get_object_or_404(services.scoped().select_related("customer"), pk=pk)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "details":
                data = {key: request.POST.get(key) for key in request.POST}
                data["customer"] = Customer.objects.filter(pk=request.POST.get("customer") or 0, active=True).first()
                services.update_header(prescription, data, request.user, lang)
                message = words["saved"]
            elif action == "add_line":
                item = Item.objects.filter(pk=request.POST.get("item") or 0, active=True).first()
                services.add_line(prescription, {"item": item, "prescribed_name": request.POST.get("prescribed_name"), "quantity": request.POST.get("quantity"),
                                                 "dosage": request.POST.get("dosage")}, request.user, lang)
                message = words["saved"]
            elif action == "remove_line":
                services.remove_line(get_object_or_404(PrescriptionLine, pk=request.POST.get("line") or 0, prescription=prescription), request.user, lang)
                message = words["saved"]
            elif action == "choose":
                line = get_object_or_404(PrescriptionLine, pk=request.POST.get("line") or 0, prescription=prescription)
                services.choose_item(line, Item.objects.filter(pk=request.POST.get("item") or 0, active=True).first(), request.user, lang)
                message = words["saved"]
            elif action == "cancel":
                services.cancel(prescription, request.user, lang)
                message = words["cancelled"]
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, message)
            return redirect(f"{reverse('pharmacy:prescription', args=[prescription.pk])}?lang={lang}")
        prescription.refresh_from_db()
    today = timezone.localdate()
    done = services.dispenses(prescription)
    can_manage = services.can_manage(request.user)
    editable = can_manage and services.editable(prescription, done)
    rows = _lines(prescription, prescription.customer)
    whatsapp = ""
    if prescription.patient_phone and rows:
        from printing.company import company_details
        from printing.share import whatsapp_link

        listing = "\n".join(f"- {(row['line'].item.item_name if row['line'].item_id else row['line'].prescribed_name)}: {row['line'].dosage or row['line'].quantity.normalize()}"
                            for row in rows)
        whatsapp = whatsapp_link(prescription.patient_phone, words["share_text"].format(number=prescription.number, company=company_details()["name"], lines=listing))
    return render(request, "pharmacy/prescription.html", _base(
        request, lang, words, prescription=prescription, rows=rows, error=error, state=services.state(prescription, today, done), dispenses=done,
        next_refill=services.next_refill(prescription, done), editable=editable, can_dispense=can_manage and services.can_dispense(prescription, done),
        can_cancel=can_manage and prescription.status == "active" and not done, whatsapp=whatsapp, customers=_customers() if editable else [],
        items=Item.objects.filter(active=True).select_related("drug").order_by("item_name")[:800] if editable else []))


def print_prescription(request, pk):
    from printing.views import WORDS as PRINT_WORDS, _render

    _guard(request, services.can_view)
    lang = _lang(request)
    prescription = get_object_or_404(services.scoped().select_related("customer"), pk=pk)
    words, own = PRINT_WORDS[lang], RX_WORDS[lang]
    lines = []
    for line in prescription.lines.select_related("item"):
        name = line.item.item_name if line.item_id else line.prescribed_name
        lines.append({"label": f"{name} — {line.dosage}" if line.dosage else name, "quantity": line.quantity})
    doc = {"kind": "prescription", "title": own["doc"], "number": prescription.number, "date": prescription.issued_on, "status": "posted",
           "party_label": own["patient"], "party": {"name": prescription.patient_name, "phone": prescription.patient_phone, "address": ""},
           "reference": (own["doctor"], prescription.doctor_name) if prescription.doctor_name else None, "lines": lines, "no_prices": True,
           "notes": prescription.notes}
    return _render(request, doc, f"/pharmacy/prescriptions/{pk}/")
