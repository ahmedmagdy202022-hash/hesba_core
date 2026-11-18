"""Manufacturing F4 (HG-137) screens: «تشغيل لدى الغير» (the stages that are at outside suppliers, those back and
waiting for their bill, the bills not posted yet), the outside section of the order page (send the current stage,
record its return, make or withdraw the supplier's draft bill) and the line on the production board's card.

Every rule is in ``manufacturing.outside``; the views read, call and render. Seeing needs ``inventory.view_stock`` (as
every manufacturing screen). Sending a stage, recording its return and cancelling a sending need
``inventory.adjust_stock`` (as closing a stage does). The agreed price is typed, and changed until the bill, only with
``inventory.view_cost``. The bill needs ``purchases.create_purchase_invoice``. Prices and amounts show only with
``inventory.view_cost``; the bill's link only with ``purchases.view_purchase_invoices``. Everything is narrowed to the
entity being worked in, and another entity's order or job returns 404."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from entities import scope as entity_scope
from master_data.models import Supplier
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import outside
from .models import OrderStatus, OutsideJob, OutsideStatus, ProductionOrder
from .views import VIEW, _lang, _pick

SEE_PURCHASES = "purchases.view_purchase_invoices"

WORDS = {
    "ar": {
        "title": "تشغيل لدى الغير", "back": "التصنيع", "orders_link": "أوامر الإنتاج",
        "intro": "مرحلة من أمر الإنتاج بتتعمل عند مورّد خارج المصنع. إرسال المرحلة ورجوعها مبيحرّكوش المخزون، وفاتورة المورّد بتترحّل من المشتريات وقيمتها بتدخل في تكلفة المنتج عند إنهاء الأمر.",
        "tile_out": "مراحل عند جهات التشغيل", "tile_bill": "رجعت ومحتاجة فاتورة", "tile_draft": "فواتير لسه مترحّلتش",
        "open_title": "المفتوح دلوقتي", "closed_title": "آخر ما اتقفل",
        "empty_open": "مفيش مراحل مفتوحة عند جهات تشغيل دلوقتي.", "empty_closed": "لسه مفيش تشغيل لدى الغير اتقفل.",
        "order": "أمر الإنتاج", "product": "المنتج", "stage": "المرحلة", "supplier": "جهة التشغيل", "sent": "الكمية المرسلة", "sent_on": "تاريخ الإرسال",
        "good": "سليم", "defect": "معيب", "returned_on": "تاريخ الرجوع", "status": "الحالة", "price": "سعر الوحدة", "amount": "قيمة التشغيل",
        "bill": "الفاتورة", "actions": "الإجراء",
        "state_sent": "عند جهة التشغيل", "state_back": "رجعت ومحتاجة فاتورة", "state_back_none": "رجعت من غير وحدات سليمة",
        "state_draft": "الفاتورة مسودة", "state_posted": "الفاتورة اترحّلت", "state_cancelled": "الإرسال اتلغى",
        "card_title": "تشغيل لدى الغير",
        "card_hint": "لو المرحلة دي بتتعمل عند مورّد خارج المصنع، سجّل إرسالها من هنا. المخزون مبيتحركش، والوحدات بتفضل في الإنتاج تحت التشغيل لحد ما ترجع.",
        "send": "ابعت المرحلة دي لجهة تشغيل", "supplier_field": "المورّد (جهة التشغيل)", "choose": "اختار المورّد", "price_field": "سعر الوحدة المتفق عليه",
        "price_hint": "السعر للوحدة السليمة اللي بترجع، قبل الضريبة.", "price_later": "لو السعر لسه متحددش، سيب الخانة فاضية وحدّده قبل فاتورة التشغيل.", "note": "ملاحظات", "send_save": "سجّل الإرسال",
        "will_send": "هتتبعت كل الوحدات السليمة الداخلة المرحلة، وعددها", "no_suppliers": "سجّل المورّد الأول من شاشة الموردين، وبعدين ابعت المرحلة.",
        "set_price": "سعر التشغيل", "save_price": "احفظ السعر", "price_saved": "سعر التشغيل اتحفظ.", "not_priced": "لسه متحددش",
        "needs_price": "في مرحلة رجعت وسعر الوحدة المتفق عليه لسه متحددش؛ صاحب صلاحية رؤية التكلفة بيحدده، وبعدها فاتورة التشغيل تتعمل.",
        "sent_ok": "المرحلة اتسجلت عند جهة التشغيل.",
        "at_supplier": "المرحلة دي عند", "since": "من يوم", "back_on": "رجعت يوم", "receive_title": "رجوع المرحلة من جهة التشغيل", "receive_save": "سجّل الرجوع",
        "receive_hint": "المعيب بيتسجل هالك زي أي مرحلة، وفاتورة التشغيل بتتحسب على السليم بس. المخزون مبيتحركش.",
        "received_ok": "رجوع المرحلة اتسجل.", "cancel_sending": "إلغاء الإرسال", "cancelled_ok": "الإرسال اتلغى، والمرحلة رجعت تتسجل في المصنع.",
        "make_bill": "اعمل فاتورة التشغيل", "bill_date": "تاريخ الفاتورة", "bill_ok": "فاتورة التشغيل {number} اتعملت مسودة؛ رحّلها من شاشة المشتريات.",
        "withdraw": "اسحب الفاتورة المسودة", "withdrawn_ok": "الفاتورة المسودة اتسحبت، وتقدر تعمل فاتورة جديدة.",
        "open_bill": "افتح الفاتورة", "post_hint": "الفاتورة لسه مسودة؛ رحّلها من شاشة المشتريات عشان قيمتها تدخل في تكلفة المنتج.",
        "expected": "تكلفة التشغيل لدى الغير المتوقعة", "expected_note": "بتدخل في تكلفة المنتج عند إنهاء الأمر، ومن الفواتير المترحّلة بس.",
        "absorbed": "دخل في تكلفة المنتج", "finish_blocked": "قبل إنهاء الأمر",
        "drift": "فاتورة التشغيل اتلغت أو اترجّع منها بعد إنهاء الأمر؛ تكلفة المنتج في المخزون فضلت زي ما هي.",
        "at": "عند", "units": "وحدة", "outside_link": "كل مراحل التشغيل لدى الغير",
    },
    "en": {
        "title": "Outside processing", "back": "Manufacturing", "orders_link": "Production orders",
        "intro": "A stage of a production order done at a supplier outside the factory. Sending a stage and taking it back move no stock; the supplier's bill is posted from purchases, and its amount joins the product's cost when the order finishes.",
        "tile_out": "Stages at outside suppliers", "tile_bill": "Back, waiting for a bill", "tile_draft": "Bills not posted yet",
        "open_title": "Open now", "closed_title": "Latest closed",
        "empty_open": "No stage is open at an outside supplier right now.", "empty_closed": "No outside processing has been closed yet.",
        "order": "Production order", "product": "Product", "stage": "Stage", "supplier": "Supplier", "sent": "Quantity sent", "sent_on": "Sent on",
        "good": "Good", "defect": "Defective", "returned_on": "Returned on", "status": "Status", "price": "Price per unit", "amount": "Processing amount",
        "bill": "Bill", "actions": "Action",
        "state_sent": "At the supplier", "state_back": "Back, waiting for a bill", "state_back_none": "Back with no good units",
        "state_draft": "Bill is a draft", "state_posted": "Bill posted", "state_cancelled": "Sending cancelled",
        "card_title": "Outside processing",
        "card_hint": "When this stage is done at a supplier outside the factory, record sending it here. No stock moves; the units stay in work in progress until they come back.",
        "send": "Send this stage to an outside supplier", "supplier_field": "Supplier (who processes it)", "choose": "Choose the supplier", "price_field": "Agreed price per unit",
        "price_hint": "The price per good unit that comes back, before tax.", "price_later": "If the price is not agreed yet, leave the field empty and set it before the processing bill.", "note": "Notes", "send_save": "Record the sending",
        "will_send": "Every good unit entering the stage goes out:", "no_suppliers": "Add the supplier on the suppliers screen first, then send the stage.",
        "set_price": "Processing price", "save_price": "Save the price", "price_saved": "The processing price was saved.", "not_priced": "Not set yet",
        "needs_price": "A stage is back and its agreed price per unit is not set yet; someone who may see costs sets it, then the processing bill can be made.",
        "sent_ok": "The stage is recorded at the outside supplier.",
        "at_supplier": "This stage is at", "since": "since", "back_on": "back on", "receive_title": "The stage is back from the supplier", "receive_save": "Record the return",
        "receive_hint": "Defective units are scrap, as at any stage, and the processing bill counts the good units only. No stock moves.",
        "received_ok": "The stage's return was recorded.", "cancel_sending": "Cancel the sending", "cancelled_ok": "The sending was cancelled; the stage is recorded in the factory again.",
        "make_bill": "Make the processing bill", "bill_date": "Bill date", "bill_ok": "Processing bill {number} was made as a draft; post it from the purchases screen.",
        "withdraw": "Withdraw the draft bill", "withdrawn_ok": "The draft bill was withdrawn; you can make a new one.",
        "open_bill": "Open the bill", "post_hint": "The bill is still a draft; post it from the purchases screen so its amount joins the product's cost.",
        "expected": "Expected outside processing cost", "expected_note": "It joins the product's cost when the order finishes, from posted bills only.",
        "absorbed": "In the product's cost", "finish_blocked": "Before finishing the order",
        "drift": "The processing bill was cancelled or returned after the order finished; the product's stock cost stayed as it was.",
        "at": "At", "units": "units", "outside_link": "All outside processing",
    },
}


def _translated(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(exc.messages), lang)


def state_label(job, lang):
    """(words, CSS class, key) of where a job stands, read from the job and its bill."""

    words = WORDS[lang]
    where = outside.state(job)
    if where == OutsideStatus.SENT:
        return words["state_sent"], "op-status-draft", "sent"
    if where == OutsideStatus.CANCELLED:
        return words["state_cancelled"], "op-status-cancelled", "cancelled"
    if where == OutsideStatus.BACK:
        if job.returned_good <= 0:
            return words["state_back_none"], "op-status-cancelled", "back_none"
        return words["state_back"], "op-status-draft", "back"
    if job.purchase_invoice.status == outside.POSTED:
        return words["state_posted"], "op-status-posted", "posted"
    return words["state_draft"], "op-status-draft", "draft"


def job_row(job, lang, *, can_cost, see_purchases):
    """One job as the tables show it. Money is filled only for someone who may see cost."""

    label, css, key = state_label(job, lang)
    order = job.order
    invoice = job.purchase_invoice if key in ("draft", "posted") else None
    row = {"job": job, "order": order, "stage": outside.stage_name(order, job.stage_key, lang), "state": label, "state_class": css, "state_key": key,
           "returned": job.status in (OutsideStatus.BACK, OutsideStatus.BILLED), "invoice": invoice,
           "invoice_url": reverse("purchases:detail", args=[invoice.pk]) if invoice is not None and see_purchases else "",
           "absorbed": job.cost_line_id is not None, "price": None, "price_plain": "", "amount": None, "drift": False, "priced": job.unit_price > 0,
           "can_price": can_cost and key in ("sent", "back")}
    if can_cost:
        row["price"] = job.unit_price if job.unit_price > 0 else None
        row["price_plain"] = f"{job.unit_price.normalize():f}" if job.unit_price > 0 else ""  # as the field takes it back: no separator, no trailing zeros
        if job.cost_line_id:
            row["amount"] = job.cost_line.amount
            row["drift"] = bool(outside.drift(job))
        elif key == "posted":
            row["amount"] = outside.bill_cost(invoice)
        elif key in ("back", "draft") and job.unit_price > 0:
            row["amount"] = outside.bill_amount(job)
    return row


# ---- the order page ----

def order_context(request, order, lang):
    """The order page's outside section: its jobs, the stage that is out now (its return form), whether the current
    stage may be sent, and what stops the finish."""

    from .orders import good_units

    user = request.user
    can_produce, can_cost = user_has_permission(user, outside.PRODUCE), user_has_permission(user, outside.COST)
    can_bill, see_purchases = user_has_permission(user, outside.BILL), user_has_permission(user, SEE_PURCHASES)
    jobs = list(order.outside_jobs.select_related("supplier", "purchase_invoice", "cost_line").order_by("pk"))
    for job in jobs:
        job.order = order  # the row reads the order's stages; no query per job
    rows = [job_row(job, lang, can_cost=can_cost, see_purchases=see_purchases) for job in jobs]
    current = next((row for row in rows if row["state_key"] == "sent"), None)
    sendable = outside.can_send(order)
    ready = order.status == OrderStatus.IN_PROGRESS and order.stage_index >= len(order.stages)
    live = [row for row in rows if row["state_key"] != "cancelled"]
    card = {"rows": rows, "current": current, "can_send": sendable and can_produce, "units": good_units(order) if sendable else None, "can_bill": can_bill,
            "suppliers": list(Supplier.objects.filter(active=True).order_by("name")[:300]) if sendable and can_produce else [],
            "needs_price": any(row["state_key"] == "back" and not row["priced"] for row in rows),
            "expected": outside.expected(order) if can_cost and live and not order.run_id and order.status != OrderStatus.CANCELLED else None,
            "blockers": outside.blockers(order, lang) if ready else [], "today": timezone.localdate().isoformat(),
            "drift": any(row["drift"] for row in rows), "has_draft": any(row["state_key"] == "draft" for row in rows)}
    card["show"] = bool(rows or sendable)
    return {"ow": WORDS[lang], "outside": card, "outside_url": reverse("manufacturing:outside_action", args=[order.pk])}


@require_permission(VIEW)
@require_POST
def order_action(request, pk):
    """The outside section's posts: send the current stage, record its return, cancel a sending, make or withdraw the bill."""

    lang = _lang(request)
    words = WORDS[lang]
    order = get_object_or_404(entity_scope.scope(ProductionOrder.objects, "location__entity").select_related("recipe", "location"), pk=pk)
    action = request.POST.get("action")
    if action not in ("send", "receive", "cancel_sending", "price", "bill", "withdraw"):
        raise Http404("Unknown outside processing action.")
    needed = outside.BILL if action in ("bill", "withdraw") else outside.COST if action == "price" else outside.PRODUCE
    if not user_has_permission(request.user, needed):
        raise PermissionDenied(f"This outside processing action needs {needed}.")
    job = None
    if action != "send":
        raw = str(request.POST.get("job", ""))
        job = get_object_or_404(OutsideJob, pk=raw if raw.isdigit() else 0, order=order)
    try:
        if action == "send":
            outside.send(order, request.user, supplier=_pick(Supplier, request.POST.get("supplier"), active=True), unit_price=request.POST.get("unit_price"),
                         sent_on=request.POST.get("sent_on"), note=request.POST.get("note", ""), lang=lang)
            done = words["sent_ok"]
        elif action == "receive":
            outside.receive(job, request.user, good=request.POST.get("good"), defect=request.POST.get("defect"),
                            returned_on=request.POST.get("returned_on"), note=request.POST.get("note", ""), lang=lang)
            done = words["received_ok"]
        elif action == "cancel_sending":
            outside.cancel_sending(job, request.user, lang)
            done = words["cancelled_ok"]
        elif action == "price":
            outside.set_price(job, request.user, request.POST.get("unit_price"), lang)
            done = words["price_saved"]
        elif action == "bill":
            invoice = outside.make_bill(job, request.user, bill_date=request.POST.get("bill_date"), lang=lang)
            done = words["bill_ok"].format(number=invoice.invoice_number)
        else:
            outside.withdraw_bill(job, request.user, lang)
            done = words["withdrawn_ok"]
    except ValidationError as exc:
        messages.error(request, _translated(exc, lang))
    else:
        messages.success(request, done)
    return redirect(f"{reverse('manufacturing:order', args=[order.pk])}?lang={lang}")


# ---- the production board ----

def mark_board(orders):
    """Each order on the board gets ``at_supplier``: the job of its stage that is out now, or None (one query)."""

    jobs = {job.order_id: job for job in OutsideJob.objects.filter(order__in=orders, status=OutsideStatus.SENT).select_related("supplier")}
    for order in orders:
        order.at_supplier = jobs.get(order.pk)


# ---- the screen ----

@require_permission(VIEW)
def outside_screen(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_cost, see_purchases = user_has_permission(request.user, outside.COST), user_has_permission(request.user, SEE_PURCHASES)
    jobs = outside.scoped_jobs().select_related("order__recipe__product", "supplier", "purchase_invoice", "cost_line")
    closed_q = Q(status=OutsideStatus.CANCELLED) | Q(status=OutsideStatus.BILLED, purchase_invoice__status=outside.POSTED) | Q(status=OutsideStatus.BACK, returned_good=0)
    open_rows = [job_row(job, lang, can_cost=can_cost, see_purchases=see_purchases) for job in jobs.exclude(closed_q).order_by("sent_on", "pk")]
    open_rows = [row for row in open_rows if row["state_key"] in ("sent", "back", "draft")]
    closed_rows = [job_row(job, lang, can_cost=can_cost, see_purchases=see_purchases) for job in jobs.filter(closed_q).order_by("-pk")[:20]]
    tiles = {key: sum(1 for row in open_rows if row["state_key"] == key) for key in ("sent", "back", "draft")}
    return render(request, "manufacturing/outside.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "ow": words, "page_title": words["title"], "can_cost": can_cost,
        "open_rows": open_rows, "closed_rows": closed_rows, "tiles": tiles})
