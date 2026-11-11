"""M8 (HG-088) screens: the bed board, the admission sheet, the stay's page and the ward set-up.

Who sees what: the board and a stay's page open for clinical staff and for reception (the booking permission);
the diagnosis and a charge's note show only to those who may read patient records (HG-084). Admitting, moving
and cancelling are the desk's or clinical staff's; charges and discharge are clinical; the draft invoice needs
the create-sales-invoice permission; the advance needs the collection permission. Every rule is checked again
in ``medical.inpatient``.
"""

from datetime import date
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission

from . import inpatient, services
from .models import Admission, AdmissionCharge, AdmissionState, Bed, ChargeKind

WORDS = {
    "ar": {
        "title": "الأسرّة", "intro": "كل عنبر بأسرّته: الفاضي تدخّل عليه مريض، والمشغول تفتح إقامته.",
        "admit": "دخول مريض", "setup": "العنابر والأسرّة", "free": "فاضي", "occupied": "مشغول", "out": "خارج الخدمة",
        "free_n": "فاضي", "occupied_n": "مشغول", "day_n": "اليوم {n}", "per_day": "في اليوم", "no_wards": "مفيش عنابر لسه.",
        "no_wards_setup": "ضيف أول عنبر وأسرّته من «العنابر والأسرّة».", "no_beds": "العنبر ده مفيهوش أسرّة لسه.",
        "admit_title": "دخول مريض", "admit_intro": "اختار المريض والسرير والدكتور. المقدم اختياري.",
        "patient": "المريض", "search": "دوّر بالاسم أو التليفون أو رقم الملف", "find": "بحث", "choose": "اختار", "change": "غيّر المريض",
        "no_match": "مفيش مريض بالاسم ده. المريض بيتسجل من «المرضى».", "pick_patient": "دوّر على المريض واختاره الأول.",
        "bed": "السرير", "doctor": "الدكتور المعالج", "diagnosis": "التشخيص / سبب الدخول", "diagnosis_hint": "بيظهر للدكاترة والإدارة بس.",
        "advance": "مقدم (اختياري)", "advance_amount": "مبلغ المقدم", "cashbox": "الخزنة أو الحساب البنكي",
        "advance_hint": "المقدم بيتسجل عربون باسم المريض ويدخل الخزنة النهارده، ويتخصم من فاتورة الإقامة لما تترحّل.",
        "advance_off": "عشان تاخد مقدم شغّل «العربون وسياسة الإلغاء» من الإعدادات ← الخصائص.",
        "save_admit": "سجّل الدخول", "cancel_link": "رجوع", "no_free": "مفيش سرير فاضي دلوقتي.", "none": "—",
        "admitted": "{name} دخل على سرير {bed} ({number}).", "back": "الأسرّة",
        "since": "دخل", "left": "خرج", "stay_day": "اليوم {n} من الإقامة", "state": "الحالة", "ward": "العنبر",
        "edit_diagnosis": "عدّل التشخيص", "save": "حفظ", "no_diagnosis": "مفيش تشخيص مكتوب.", "diagnosis_saved": "اتحفظ التشخيص.",
        "charges": "بنود الإقامة", "running": "الإجمالي لحد دلوقتي", "before_tax": "قبل الضريبة", "no_charges": "مفيش بنود لسه.",
        "add_procedure": "ضيف خدمة", "add_operation": "ضيف عملية", "add_supply": "ضيف مستلزم", "item": "البند", "service": "الخدمة",
        "operation": "العملية", "supply": "المستلزم", "quantity": "الكمية", "price": "السعر", "price_hint": "سيبه فاضي ياخد سعر البند.",
        "day": "اليوم", "note": "ملاحظة", "surgeon": "الجرّاح", "add": "ضيف", "remove": "شيل", "billed": "اتفوتر", "pending": "لسه ماتقفلش",
        "bed_days_note": "أيام الإقامة بتتحسب لوحدها: يوم الدخول بيتحسب ويوم الخروج لأ، وأقل إقامة يوم.",
        "no_items": "مفيش بنود من النوع ده. ضيفها من «الأصناف والخدمات» الأول.",
        "supply_note": "المستلزم بيتخصم من المخزن لما فاتورة الإقامة تترحّل، مش دلوقتي.",
        "charge_added": "اتضاف البند.", "charge_removed": "اتشال البند.",
        "move": "انقل لسرير تاني", "move_to": "السرير الجديد", "move_go": "انقل", "moved": "اتنقل لسرير {bed}.",
        "move_note": "أيام السرير القديم بتتقفل بسعره، ويوم النقل بيتحسب على السرير الجديد.",
        "discharge": "خروج وفاتورة", "discharge_only": "خروج", "review": "مراجعة قبل الخروج", "review_intro": "ده اللي هيتفوتر. مفيش حاجة هتترحّل: هتطلع فاتورة مسودة تراجعها وترحّلها من صفحة الفاتورة.",
        "line": "البند", "subtotal": "الإجمالي قبل الضريبة", "tax": "الضريبة", "total": "إجمالي الفاتورة", "advance_held": "المقدم المحجوز",
        "after_advance": "الباقي بعد المقدم", "credit_left": "يفضل رصيد للمريض", "advance_applies": "المقدم بيتخصم لوحده لما الفاتورة تترحّل.",
        "confirm_discharge": "أكّد الخروج واعمل الفاتورة المسودة", "confirm_discharge_only": "أكّد الخروج",
        "discharge_needs_bill": "الفاتورة هيعملها الاستقبال بعد الخروج (محتاجة صلاحية إنشاء فاتورة بيع).",
        "discharged": "اتسجّل الخروج.", "discharged_billed": "اتسجّل الخروج، وفاتورة الإقامة {number} مسودة.",
        "bill": "اعمل فاتورة الإقامة", "bill_ready": "الإقامة خلصت ولسه ماتفوترتش.", "billed_ok": "فاتورة الإقامة {number} مسودة.",
        "invoices": "فاتورة الإقامة", "open_invoice": "افتح الفاتورة", "draft": "مسودة", "posted": "مترحّلة", "cancelled": "ملغية",
        "insured": "المريض ده عليه تأمين ({insurer}). فاتورة الإقامة بتطلع على المريض كاملة، وتقسيمها على شركة التأمين لسه مش مدعوم.",
        "advance_box": "المقدم", "take_advance": "خد مقدم", "advance_taken": "اتاخد مقدم {amount} ({payment}).", "advance_status": "حالة المقدم",
        "attention": "إقامات محتاجة إجراء", "attention_intro": "مرضى خرجوا أو اتلغى دخولهم ولسه عليهم حاجة تتعمل.",
        "need_bill": "محتاجة فاتورة", "need_draft": "فاتورتها مسودة لسه ماترحّلتش", "need_advance": "مقدم لسه محجوز",
        "recent": "آخر الإقامات اللي خلصت", "open_stay": "افتح الإقامة",
        "advance_split": "اتخصم من الفاتورة {applied}، والباقي {credit} رصيد للمريض على حسابه.",
        "cancel_late": "الإقامة دي عدّى عليها {nights} ليلة، وأيام الإقامة ({amount}) مش هتتفوتر لو اتلغت. لو المريض أقام فعلاً اعمل خروج وفاتورة.",
        "cancel": "إلغاء الدخول", "cancel_hint": "للدخول اللي اتسجّل غلط ومفيش عليه بنود. السرير بيفضى، والمقدم بيترد.",
        "cancel_confirm": "أكّد إلغاء الدخول", "cancelled_ok": "اتلغى الدخول والسرير فضي.",
        "refund": "رد المقدم", "refunded": "المقدم {payment} اترد.", "refund_pending": "رد المقدم مستني موافقة.",
        "refund_not_allowed": "المقدم لسه محجوز؛ رده محتاج صلاحية التحصيل.", "refund_blocked": "المقدم لسه محجوز: {detail}",
        "wards_title": "العنابر والأسرّة", "wards_intro": "كل سرير له خدمة إقامة يومية بسعرها؛ دي اللي بتتحسب على المريض كل يوم.",
        "new_ward": "عنبر جديد", "ward_name": "اسم العنبر", "ward_name_en": "الاسم بالإنجليزي (اختياري)", "add_ward": "ضيف العنبر",
        "new_bed": "سرير جديد", "bed_code": "رقم السرير", "daily_service": "خدمة الإقامة اليومية", "add_bed": "ضيف السرير",
        "put_out": "خرّجه من الخدمة", "put_in": "رجّعه للخدمة", "saved": "اتحفظ.", "no_services": "ضيف خدمة «إقامة يوم» من «الأصناف والخدمات» الأول (خدمة مش صنف مخزون).",
        "file": "ملف المريض", "currency_day": "/ يوم",
    },
    "en": {
        "title": "Beds", "intro": "Each ward with its beds: admit a patient to a free one, open the stay of an occupied one.",
        "admit": "Admit a patient", "setup": "Wards & beds", "free": "Free", "occupied": "Occupied", "out": "Out of service",
        "free_n": "free", "occupied_n": "occupied", "day_n": "Day {n}", "per_day": "a day", "no_wards": "No wards yet.",
        "no_wards_setup": "Add the first ward and its beds from “Wards & beds”.", "no_beds": "This ward has no beds yet.",
        "admit_title": "Admit a patient", "admit_intro": "Choose the patient, the bed and the doctor. An advance is optional.",
        "patient": "Patient", "search": "Search by name, phone or file number", "find": "Search", "choose": "Choose", "change": "Change patient",
        "no_match": "No patient by that name. A patient is added from Patients.", "pick_patient": "Search for the patient and choose them first.",
        "bed": "Bed", "doctor": "Treating doctor", "diagnosis": "Diagnosis / reason for admission", "diagnosis_hint": "Shown to doctors and management only.",
        "advance": "Advance (optional)", "advance_amount": "Advance amount", "cashbox": "Cashbox or bank account",
        "advance_hint": "The advance is recorded as a deposit in the patient's name, reaches the cashbox today and comes off the stay's invoice when it is posted.",
        "advance_off": "To take an advance, switch on “Deposits & cancellation policy” in Settings → Features.",
        "save_admit": "Admit", "cancel_link": "Back", "no_free": "No free bed right now.", "none": "—",
        "admitted": "{name} was admitted to bed {bed} ({number}).", "back": "Beds",
        "since": "Admitted", "left": "Discharged", "stay_day": "Day {n} of the stay", "state": "Status", "ward": "Ward",
        "edit_diagnosis": "Edit the diagnosis", "save": "Save", "no_diagnosis": "No diagnosis written.", "diagnosis_saved": "Diagnosis saved.",
        "charges": "Stay charges", "running": "Total so far", "before_tax": "before tax", "no_charges": "No charges yet.",
        "add_procedure": "Add a service", "add_operation": "Add an operation", "add_supply": "Add a supply", "item": "Item", "service": "Service",
        "operation": "Operation", "supply": "Supply", "quantity": "Quantity", "price": "Price", "price_hint": "Leave it empty to take the item's price.",
        "day": "Day", "note": "Note", "surgeon": "Surgeon", "add": "Add", "remove": "Remove", "billed": "Billed", "pending": "Not closed yet",
        "bed_days_note": "Bed days are worked out by the system: the admission day counts, the discharge day does not, and the shortest stay is one day.",
        "no_items": "No items of this kind. Add them under Items & services first.",
        "supply_note": "A supply leaves stock when the stay's invoice is posted, not now.",
        "charge_added": "Charge added.", "charge_removed": "Charge removed.",
        "move": "Move to another bed", "move_to": "New bed", "move_go": "Move", "moved": "Moved to bed {bed}.",
        "move_note": "The old bed's days are closed at its price; the day of the move is billed to the new bed.",
        "discharge": "Discharge & bill", "discharge_only": "Discharge", "review": "Review before discharge", "review_intro": "This is what will be billed. Nothing is posted: a draft invoice is made for you to review and post from the invoice page.",
        "line": "Line", "subtotal": "Total before tax", "tax": "Tax", "total": "Invoice total", "advance_held": "Advance held",
        "after_advance": "Left after the advance", "credit_left": "Stays as the patient's credit", "advance_applies": "The advance comes off by itself when the invoice is posted.",
        "confirm_discharge": "Confirm discharge and make the draft invoice", "confirm_discharge_only": "Confirm discharge",
        "discharge_needs_bill": "Reception makes the invoice after discharge (it needs the create-sales-invoice permission).",
        "discharged": "Discharge recorded.", "discharged_billed": "Discharge recorded; the stay's invoice {number} is a draft.",
        "bill": "Make the stay's invoice", "bill_ready": "The stay has ended and is not billed yet.", "billed_ok": "The stay's invoice {number} is a draft.",
        "invoices": "The stay's invoice", "open_invoice": "Open the invoice", "draft": "Draft", "posted": "Posted", "cancelled": "Cancelled",
        "insured": "This patient is insured ({insurer}). The stay is billed to the patient in full; splitting it with the insurer is not supported yet.",
        "advance_box": "Advance", "take_advance": "Take an advance", "advance_taken": "Advance of {amount} taken ({payment}).", "advance_status": "Advance status",
        "attention": "Stays that need action", "attention_intro": "Patients who left or whose admission was cancelled, with something still to do.",
        "need_bill": "Needs its invoice", "need_draft": "Its invoice is a draft, not posted yet", "need_advance": "Advance still held",
        "recent": "Latest stays that ended", "open_stay": "Open the stay",
        "advance_split": "{applied} came off the invoice; the remaining {credit} is the patient's credit on account.",
        "cancel_late": "This stay has {nights} night(s) behind it, and its bed days ({amount}) will not be billed if it is cancelled. If the patient did stay, discharge and bill.",
        "cancel": "Cancel the admission", "cancel_hint": "For an admission entered by mistake with no charges. The bed is freed and the advance refunded.",
        "cancel_confirm": "Confirm cancelling the admission", "cancelled_ok": "The admission was cancelled and the bed is free.",
        "refund": "Refund the advance", "refunded": "Advance {payment} was refunded.", "refund_pending": "The refund is waiting for approval.",
        "refund_not_allowed": "The advance is still held; refunding it needs the collection permission.", "refund_blocked": "The advance is still held: {detail}",
        "wards_title": "Wards & beds", "wards_intro": "Each bed has a daily stay service with its price; that is what the patient is charged per day.",
        "new_ward": "New ward", "ward_name": "Ward name", "ward_name_en": "English name (optional)", "add_ward": "Add the ward",
        "new_bed": "New bed", "bed_code": "Bed number", "daily_service": "Daily stay service", "add_bed": "Add the bed",
        "put_out": "Take out of service", "put_in": "Back in service", "saved": "Saved.", "no_services": "Add a “stay day” service under Items & services first (a service, not a stock item).",
        "file": "Patient file", "currency_day": "/ day",
    },
}


def _id(raw):
    return int(raw) if str(raw or "").isdigit() else 0


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _guard(request):
    if not inpatient.has_inpatient():
        raise Http404("Beds are for medical businesses that switched inpatient stays on.")
    if not inpatient.can_see_board(request.user):
        raise PermissionDenied("Beds need the booking permission or a clinical permission.")


def _base(lang, **extra):
    words = WORDS[lang]
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "w": words, "page_title": words["title"], "section": "medical", **extra}


def _error(exc):
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _doctors():
    from staff.models import Employee

    return Employee.objects.filter(active=True).order_by("name")


def _services():
    """Service items a procedure, an operation or a bed's day can be billed on (never the deposit's fee item)."""

    from master_data.models import Item

    return Item.objects.filter(active=True, is_stock_tracked=False).exclude(item_code="CANCEL-FEE").order_by("item_name")[:500]


def _supplies():
    from master_data.models import Item

    return Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name")[:500]


def _stocks():
    from settings_core.setup_services import usable_modules

    return "inventory" in set(usable_modules())


def _can_advance(user):
    from deposits import services as deposits

    return deposits.enabled() and user_has_permission(user, inpatient.RECEIVE)


def _cashboxes():
    from cashboxes.models import Cashbox, CashboxKind

    return entity_scope.cashboxes(Cashbox.objects.filter(active=True, kind__in=(CashboxKind.CASH, CashboxKind.BANK))).order_by("-is_default", "pk")


# ---- the bed board -----------------------------------------------------------------


def beds(request):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    rows = inpatient.board()
    for row in rows:
        row["name"] = row["ward"].display(lang)
        for cell in row["beds"]:
            if cell["admission"] is not None:
                cell["day_label"] = words["day_n"].format(n=cell["admission"].day_number)
    # A stay that left its bed is reached from here: the ones that still need someone, then the latest that ended.
    attention = inpatient.needing_action()
    for row in attention:
        row["labels"] = [words[f"need_{reason}"] for reason in row["reasons"]]
    recent = inpatient.recent_closed()
    for admission in recent:
        admission.state_label = inpatient.STATE_LABELS[lang][admission.state]
    return render(request, "medical/beds.html", _base(lang, board=rows, can_admit=inpatient.can_manage_beds(request.user),
                                                      can_setup=inpatient.can_setup(request.user), attention=attention, recent=recent))


#: Settings offers «افتح» on the feature's row only to someone the board opens for, and only where it opens at all
#: (a medical business, or a medical entity of a group): ``settings_core.operational_views._opens_for``.
beds.opens_for = inpatient.can_see_board


# ---- the admission sheet -----------------------------------------------------------


def admit(request):
    from deposits import services as deposits

    _guard(request)
    if not inpatient.can_manage_beds(request.user):
        raise PermissionDenied("Admitting needs the booking permission or a clinical permission.")
    lang = _lang(request)
    words = WORDS[lang]
    source = request.POST if request.method == "POST" else request.GET
    patient = inpatient.patients().filter(pk=_id(source.get("patient"))).first()
    free = list(inpatient.free_beds())
    chosen_bed = _id(source.get("bed"))
    error = ""
    clinical = services.can_write(request.user)
    can_advance = _can_advance(request.user)
    if request.method == "POST":
        bed = next((b for b in free if b.pk == chosen_bed), None)
        try:
            amount = (request.POST.get("advance") or "").strip() if can_advance else ""
            admission = inpatient.admit(patient, bed, request.user, doctor=_doctors().filter(pk=_id(request.POST.get("doctor"))).first(),
                                        diagnosis=request.POST.get("diagnosis", "") if clinical else "",
                                        advance=amount or None, cashbox=_cashboxes().filter(pk=_id(request.POST.get("cashbox"))).first() if amount else None, lang=lang)
        except (ValidationError, PermissionDenied) as exc:
            error = _error(exc)
        else:
            messages.success(request, words["admitted"].format(name=admission.patient.name, bed=admission.bed.code, number=admission.number))
            return redirect(f"{reverse('medical:stay', args=[admission.pk])}?lang={lang}")
    query = (request.GET.get("q") or "").strip()
    found = []
    if patient is None and query:
        found = list(inpatient.patients().select_related("patient_file").filter(
            Q(name__icontains=query) | Q(phone__icontains=query) | Q(patient_file__file_number__icontains=query) | Q(customer_code__icontains=query)).order_by("name")[:12])
    own = getattr(request.user, "employee", None)
    for bed in free:
        bed.ward_name = bed.ward.display(lang)
    return render(request, "medical/admit.html", _base(
        lang, page_title=words["admit_title"], patient=patient, query=query, found=found, searched=bool(query), free=free, chosen_bed=chosen_bed,
        doctors=_doctors(), default_doctor=_id(request.POST.get("doctor")) or (own.pk if own is not None and clinical else 0), clinical=clinical,
        can_advance=can_advance, deposits_on=deposits.enabled(), cashboxes=_cashboxes() if can_advance else (), error=error, post=request.POST))


# ---- the stay ----------------------------------------------------------------------


def _refund_message(request, result, words):
    outcome = result.get("outcome")
    if outcome == "refunded":
        messages.success(request, words["refunded"].format(payment=result["deposit"].payment.payment_number))
    elif outcome == "refund_pending":
        messages.info(request, words["refund_pending"])
    elif outcome == "refund_not_allowed":
        messages.warning(request, words["refund_not_allowed"])
    elif outcome == "refund_blocked":
        messages.warning(request, words["refund_blocked"].format(detail=result.get("detail", "")))


def _parse_day(raw):
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def _act(request, admission, lang, words):
    """Run one POSTed action. Returns the URL to go to; raises ValidationError / PermissionDenied to show on the page."""

    from master_data.models import Item

    action = request.POST.get("action", "")
    here = f"{reverse('medical:stay', args=[admission.pk])}?lang={lang}"
    if action == "charge":
        kind = request.POST.get("kind", "")
        inpatient.add_charge(admission, {
            "kind": kind, "item": Item.objects.filter(pk=_id(request.POST.get("item"))).first(), "quantity": request.POST.get("quantity", ""),
            "price": request.POST.get("price", ""), "day": _parse_day(request.POST.get("day")), "note": request.POST.get("note", ""),
            "surgeon": _doctors().filter(pk=_id(request.POST.get("surgeon"))).first()}, request.user, lang)
        messages.success(request, words["charge_added"])
    elif action == "remove_charge":
        charge = get_object_or_404(AdmissionCharge, pk=_id(request.POST.get("charge")), admission=admission)
        inpatient.remove_charge(charge, request.user, lang)
        messages.success(request, words["charge_removed"])
    elif action == "move":
        bed = inpatient.scoped_beds().filter(pk=_id(request.POST.get("bed"))).first()
        moved = inpatient.move(admission, bed, request.user, lang=lang)
        messages.success(request, words["moved"].format(bed=moved.bed.code))
    elif action == "diagnosis":
        inpatient.set_diagnosis(admission, request.POST.get("diagnosis", ""), request.user, lang)
        messages.success(request, words["diagnosis_saved"])
    elif action == "advance":
        from deposits import services as deposits

        if not deposits.enabled():
            raise PermissionDenied("Deposits are switched off.")
        cashbox = inpatient.advance_cashboxes(admission).filter(pk=_id(request.POST.get("cashbox"))).first()
        deposit = inpatient.take_advance(admission, cashbox, request.POST.get("amount", ""), request.user, lang)
        messages.success(request, words["advance_taken"].format(amount=deposit.payment.amount, payment=deposit.payment.payment_number))
    elif action == "discharge":
        if user_has_permission(request.user, inpatient.BILL):
            _, invoice = inpatient.discharge_and_bill(admission, request.user, lang=lang)
            messages.success(request, words["discharged_billed"].format(number=invoice.invoice_number))
        else:
            inpatient.discharge(admission, request.user, lang=lang)
            messages.success(request, words["discharged"])
    elif action == "bill":
        invoice = inpatient.bill(admission, request.user, lang)
        messages.success(request, words["billed_ok"].format(number=invoice.invoice_number))
    elif action == "cancel":
        result = inpatient.cancel(admission, request.user, lang)
        messages.success(request, words["cancelled_ok"])
        _refund_message(request, result, words)
    elif action == "refund":
        _refund_message(request, inpatient.refund_advance(admission, request.user, lang), words)
    else:
        raise Http404("Unknown action.")
    return here


def stay(request, pk):
    from deposits import services as deposits

    from . import insurance

    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    # Another entity's admission answers 404 before any action runs, as every scoped document does (HG-034, HG-074).
    admission = entity_scope.get_or_404(Admission.objects.select_related("patient", "bed__ward", "bed__daily_service", "doctor", "entity"), "entity", pk=pk)
    user = request.user
    error = ""
    if request.method == "POST":
        try:
            return redirect(_act(request, admission, lang, words))
        except (ValidationError, PermissionDenied) as exc:
            error = _error(exc)
            admission.refresh_from_db()
    reads = services.can_view(user)
    writes = services.can_write(user)
    is_open = admission.state == AdmissionState.ADMITTED
    labels = inpatient.KIND_LABELS[lang]
    charges = list(admission.charges.select_related("item", "bed__ward", "surgeon", "invoice"))
    lines = [{"pk": c.pk, "day": c.day, "kind": c.kind, "kind_label": labels[c.kind], "name": c.item.item_name, "quantity": c.quantity, "price": c.price,
              "total": money_round(c.quantity * c.price), "bed": c.bed.code if c.bed_id else "", "surgeon": c.surgeon.name if c.surgeon_id else "",
              "note": c.note if reads else "", "billed": c.invoice_id is not None and c.invoice.status != "cancelled", "pending": False,
              "removable": writes and c.kind != ChargeKind.BED_DAY and admission.state != AdmissionState.CANCELLED
                           and not (c.invoice_id is not None and c.invoice.status != "cancelled")} for c in charges]
    for day, price in inpatient.pending_bed_days(admission):  # what a discharge now would add: shown, not saved
        lines.append({"pk": None, "day": day, "kind": ChargeKind.BED_DAY, "kind_label": labels["bed_day"], "name": admission.bed.daily_service.item_name,
                      "quantity": 1, "price": price, "total": price, "bed": admission.bed.code, "surgeon": "", "note": "", "billed": False, "pending": True,
                      "removable": False})
    lines.sort(key=lambda row: (row["day"], row["kind"] != ChargeKind.BED_DAY, row["pk"] or 0))
    days, running = [], 0
    for day, group in groupby(lines, key=lambda row: row["day"]):
        rows = list(group)
        subtotal = sum(row["total"] for row in rows)
        running += subtotal
        days.append({"day": day, "rows": rows, "subtotal": subtotal, "running": running})
    days.reverse()  # today first: what was just added is on top
    deposit = inpatient.advance_of(admission)
    held = deposit if deposit is not None and deposit.status == "held" else None
    advance_split = ""
    if deposit is not None and deposit.status == "applied":  # an advance larger than the bill: say what it settled and what is left
        from settlements.models import InvoiceSettlement

        settled = InvoiceSettlement.objects.filter(customer_payment_id=deposit.payment_id).first()
        if settled is not None and settled.amount < deposit.payment.amount:
            advance_split = words["advance_split"].format(applied=f"{settled.amount:,.2f}", credit=f"{deposit.payment.amount - settled.amount:,.2f}")
    nights = inpatient.nights_behind(admission) if is_open else 0
    add = request.GET.get("add") if request.GET.get("add") in inpatient.ADDED_KINDS and writes and is_open else ""
    if request.method == "POST" and request.POST.get("action") == "charge" and error:
        add = request.POST.get("kind") if request.POST.get("kind") in inpatient.ADDED_KINDS else ""
    stocks = _stocks()
    if add == ChargeKind.SUPPLY and not stocks:  # a supply is a stock item: offered only where the business keeps stock
        add = ""
    reviewing = is_open and writes and (request.GET.get("review") == "1" or (request.method == "POST" and request.POST.get("action") == "discharge"))
    moving = is_open and inpatient.can_manage_beds(user) and (request.GET.get("move") == "1" or (request.method == "POST" and request.POST.get("action") == "move"))
    free = []
    if moving:
        free = [bed for bed in inpatient.free_beds() if inpatient.same_entity(bed.ward.entity_id, admission.entity_id)]
        for bed in free:
            bed.ward_name = bed.ward.display(lang)
    can_bill = user_has_permission(user, inpatient.BILL)
    unbilled_count = inpatient.unbilled(admission).count()
    cover = insurance.cover_for(admission.patient) if insurance.is_insurance_install() else None
    stay_invoices = list(inpatient.invoices(admission))
    for invoice in stay_invoices:
        invoice.status_label = words.get(invoice.status, invoice.status)
    return render(request, "medical/stay.html", _base(
        lang, page_title=f"{admission.patient.name} — {admission.number}", admission=admission, ward_name=admission.bed.ward.display(lang),
        state_label=inpatient.STATE_LABELS[lang][admission.state], is_open=is_open, reads=reads, writes=writes, error=error,
        day_label=words["stay_day"].format(n=inpatient.stay_day(admission)), days=days, running=running, has_lines=bool(lines),
        add=add, add_items=(_supplies() if add == ChargeKind.SUPPLY else _services()) if add else (), doctors=_doctors() if add == ChargeKind.OPERATION else (),
        today=timezone.localdate(), first_day=timezone.localtime(admission.admitted_at).date(), stocks=stocks, post=request.POST if error else {},
        can_manage=inpatient.can_manage_beds(user), moving=moving, free=free,
        reviewing=reviewing, review=inpatient.review(admission, lang) if reviewing else None, can_bill=can_bill,
        needs_bill=admission.state == AdmissionState.DISCHARGED and unbilled_count > 0,
        invoices=stay_invoices, can_open_invoice=user_has_permission(user, "sales.view_sales_invoices"),
        insured_note=words["insured"].format(insurer=cover.contract.insurer.name) if cover is not None else "",
        deposits_on=deposits.enabled(), deposit=deposit, deposit_status=deposits.status_label(deposit, lang) if deposit is not None else "",
        can_take_advance=is_open and held is None and _can_advance(user), advance_boxes=inpatient.advance_cashboxes(admission) if is_open and held is None else (),
        can_refund=held is not None and inpatient.advance_refundable(admission) and user_has_permission(user, inpatient.RECEIVE),
        advance_split=advance_split,
        # A stay with nights behind it is undone by the owner or a manager only: it waives those nights (``inpatient.cancel``).
        can_cancel=is_open and inpatient.can_manage_beds(user) and not charges and (not nights or inpatient.can_setup(user)),
        cancel_late=words["cancel_late"].format(nights=nights, amount=f"{sum((price for _, price in inpatient.pending_bed_days(admission)), 0):,.2f}") if nights else "",
        cancelling=request.GET.get("cancel") == "1",
        shows_file=reads))


# ---- wards and beds ----------------------------------------------------------------


def wards(request):
    from master_data.models import Item

    _guard(request)
    if not inpatient.can_setup(request.user):
        raise PermissionDenied("Setting up wards and beds needs medical.write_records.")
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "ward":
                inpatient.save_ward({"name_ar": request.POST.get("name_ar", ""), "name_en": request.POST.get("name_en", "")}, request.user, lang=lang)
            elif action == "bed":
                ward = get_object_or_404(inpatient.scoped_wards(), pk=_id(request.POST.get("ward")))
                inpatient.save_bed(ward, {"code": request.POST.get("code", ""),
                                          "daily_service": Item.objects.filter(pk=_id(request.POST.get("daily_service"))).first()}, request.user, lang=lang)
            elif action == "bed_active":
                bed = get_object_or_404(inpatient.scoped_beds(), pk=_id(request.POST.get("bed")))
                inpatient.save_bed(bed.ward, {"active": request.POST.get("active") == "1"}, request.user, bed=bed, lang=lang)
            else:
                raise Http404("Unknown action.")
        except (ValidationError, PermissionDenied) as exc:
            error = _error(exc)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('medical:wards')}?lang={lang}")
    rows = []
    all_beds = list(Bed.objects.filter(ward__in=inpatient.scoped_wards()).select_related("daily_service"))
    for ward in inpatient.scoped_wards():
        rows.append({"ward": ward, "name": ward.display(lang), "beds": [bed for bed in all_beds if bed.ward_id == ward.pk]})
    return render(request, "medical/wards.html", _base(lang, page_title=words["wards_title"], rows=rows, services=_services(), error=error, post=request.POST))
