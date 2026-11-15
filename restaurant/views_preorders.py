"""Restaurants R10 (HG-119): «طلبات مسبقة», pre-orders by pick-up day, with the day's production list.

Permissions reuse the codes R8 uses (no new one): the screen follows the board (``FLOOR``: the invoices' view right or
``restaurant.take_orders``); making, changing, confirming, «جاهز», «اتسلّم», cancelling and no-show follow whoever can open
orders (``sales.create_sales_invoice`` or ``restaurant.take_orders``); a deposit follows the deposits' own codes (taking and
refunding ``sales.receive_customer_payment``, forfeiting ``sales.create_sales_invoice``) and the «العربون» capability; «حضّر»
follows ``kitchen.prepare``'s own two (``master_data.manage_items`` and ``inventory.adjust_stock``). Paying the collected
order's bill is the order page's, unchanged.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer
from permissions.decorators import require_any_permission
from permissions.services import user_has_permission
from sales.pos import WALK_IN_CODE

from . import branches, kitchen, preorders
from .models import PreOrderStatus
from .views import FLOOR, TAKE, _base, _lang, _menu, _order_url, _takes_orders
from .views_reservations import _cancel_hours, _deposits_on, _outcome_message

RECEIVE = "sales.receive_customer_payment"
BLANK_ROWS = 3

PRE_WORDS = {
    "ar": {
        "title": "طلبات مسبقة", "intro": "طلبات العملاء بميعاد استلام وعربون، يوم بيوم: التورتة والطلبات الكبيرة. لما العميل ييجي اضغط «اتسلّم» يتفتح حسابه بالأسعار المتفق عليها، والعربون يتخصم منه.",
        "back": "الطاولات", "day": "يوم الاستلام", "prev": "اليوم اللي قبله", "next": "اليوم اللي بعده", "today": "النهارده", "show": "اعرض",
        "none": "مفيش طلبات مسبقة للاستلام في اليوم ده.", "new": "طلب مسبق جديد", "edit": "تعديل الطلب", "save": "احفظ الطلب",
        "customer": "العميل", "choose_customer": "— اختار العميل —", "name": "الاسم على الطلب", "phone": "الموبايل", "time": "ساعة الاستلام",
        "notes": "الكتابة على التورتة والتفاصيل", "branch": "الفرع", "choose_branch": "اختار الفرع",
        "lines": "الأصناف", "item": "الصنف", "no_item": "— اختار صنف —", "quantity": "الكمية", "price": "السعر المتفق عليه",
        "price_hint": "سيب السعر فاضي ياخد سعر المنيو دلوقتي؛ السعر بيتثبت على الطلب ومش بيتغير لو المنيو اتغير.", "line_note": "ملاحظة",
        "total": "الإجمالي قبل الضريبة", "pickup": "الاستلام",
        "confirm": "أكّد", "ready": "جاهز", "collect": "اتسلّم", "cancel": "إلغاء الطلب", "no_show": "ما جاش", "reason": "السبب (اختياري)",
        "cancel_confirm": "تلغي الطلب ده؟", "no_show_confirm": "تسجّل إن العميل ما جاش يستلم؟", "collect_confirm": "العميل استلم الطلب؟ هيتفتح حسابه دلوقتي.",
        "open_order": "حساب الطلب", "saved": "الطلب {number} اتحفظ.", "confirmed": "الطلب {number} اتأكد.", "readied": "الطلب {number} جاهز للاستلام.",
        "collected": "اتسلّم الطلب، واتفتح حسابه. اقبض الباقي من هنا.", "cancelled": "الطلب {number} اتلغى.", "no_showed": "اتسجل إن عميل الطلب {number} ما جاش.",
        "deposit": "العربون", "deposit_amount": "مبلغ العربون", "cashbox": "الخزنة", "take": "خد عربون",
        "deposit_taken": "إيصال العربون {payment} اتسجل بمبلغ {amount}.",
        "forfeit": "حوّل العربون لرسوم إلغاء", "refund": "رجّع العربون", "refund_reason": "سبب الرد",
        "policy": "الإلغاء قبل ميعاد الاستلام بأقل من {hours} ساعة أو عدم الحضور بيحوّل العربون لرسوم إلغاء، وقبل كده بيترد.",
        "forfeited": "العربون اتحوّل لفاتورة رسوم إلغاء {number} بـ {amount}.", "refunded": "العربون اترد للعميل.",
        "refund_pending": "رد العربون مستني موافقة شخص تاني.", "not_allowed": "العربون فضل محجوز رصيد للعميل؛ التصرف فيه محتاج صلاحية تانية.",
        "make_title": "قائمة التحضير", "make_intro": "إجمالي كل صنف في طلبات اليوم اللي لسه ما اتسلمتش. حضّر الأصناف اللي بتتعمل في المطبخ من بدري، وتتباع من المخزن وقت الاستلام من غير ما تتعمل تاني.",
        "make_day": "يوم الاستلام", "make_none": "مفيش أصناف مطلوبة في اليوم ده.", "wanted": "المطلوب", "in_stock": "الجاهز في المخزن", "missing": "الناقص",
        "prepare": "حضّر", "prepared": "اتحضّر {quantity} من «{item}».", "not_kitchen": "مش من المطبخ",
    },
    "en": {
        "title": "Pre-orders", "intro": "Customers' orders with a pick-up time and a deposit, day by day: cakes and large orders. When the customer comes, tap “Collected” to open their bill at the agreed prices, with the deposit taken off.",
        "back": "Tables", "day": "Pick-up day", "prev": "Previous day", "next": "Next day", "today": "Today", "show": "Show",
        "none": "No pre-orders to collect on this day.", "new": "New pre-order", "edit": "Edit the order", "save": "Save the order",
        "customer": "Customer", "choose_customer": "— choose the customer —", "name": "Name on the order", "phone": "Mobile", "time": "Pick-up time",
        "notes": "Writing on the cake and details", "branch": "Branch", "choose_branch": "Choose the branch",
        "lines": "Items", "item": "Item", "no_item": "— choose an item —", "quantity": "Quantity", "price": "Agreed price",
        "price_hint": "Leave the price empty to take today's menu price; the price is fixed on the order and does not follow later menu changes.", "line_note": "Note",
        "total": "Total before VAT", "pickup": "Pick-up",
        "confirm": "Confirm", "ready": "Ready", "collect": "Collected", "cancel": "Cancel the order", "no_show": "No-show", "reason": "Reason (optional)",
        "cancel_confirm": "Cancel this order?", "no_show_confirm": "Record that the customer did not come?", "collect_confirm": "Did the customer collect the order? Their bill opens now.",
        "open_order": "The order's bill", "saved": "Pre-order {number} saved.", "confirmed": "Pre-order {number} confirmed.", "readied": "Pre-order {number} is ready to collect.",
        "collected": "Collected; the bill is open. Take the rest from here.", "cancelled": "Pre-order {number} cancelled.", "no_showed": "Pre-order {number} recorded as a no-show.",
        "deposit": "Deposit", "deposit_amount": "Deposit amount", "cashbox": "Cashbox", "take": "Take a deposit",
        "deposit_taken": "Deposit of {amount} taken on receipt {payment}.",
        "forfeit": "Turn the deposit into a cancellation fee", "refund": "Refund the deposit", "refund_reason": "Refund reason",
        "policy": "Cancelling fewer than {hours} hours before the pick-up, or not coming, turns the deposit into a cancellation fee; earlier, it is refunded.",
        "forfeited": "The deposit became cancellation-fee invoice {number} for {amount}.", "refunded": "The deposit was refunded to the customer.",
        "refund_pending": "The deposit refund is waiting for a second person's approval.", "not_allowed": "The deposit stays held as the customer's credit; settling it needs another permission.",
        "make_title": "Production list", "make_intro": "Each item's total across the day's orders not yet collected. Prepare the kitchen-made items ahead; at pick-up they are sold from stock, not made again.",
        "make_day": "Pick-up day", "make_none": "No items are due on this day.", "wanted": "Wanted", "in_stock": "Ready in store", "missing": "Still to make",
        "prepare": "Prepare", "prepared": "Prepared {quantity} of “{item}”.", "not_kitchen": "Not kitchen-made",
    },
}


def _day(raw, default):
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        return default


def _url(day, lang, make=None):
    url = f"{reverse('restaurant:preorders')}?lang={lang}&day={day.isoformat()}"
    return url + (f"&make={make.isoformat()}" if make is not None else "")


def _menu_items():
    """The items a pre-order line may be: the menu (``views._menu``) less the combos' own items (a line is a plain dish)."""

    from .models import Combo

    items, _categories = _menu()
    combos = set(Combo.objects.values_list("item_id", flat=True))
    return [item for item in items if item.pk not in combos]


def _cards(rows, user, lang):
    from deposits import services as deposits

    deposits_on = _deposits_on()
    takes, receives, sells = _takes_orders(user), user_has_permission(user, RECEIVE), user_has_permission(user, TAKE)
    cards = []
    for row in rows:
        live = row.status in (PreOrderStatus.BOOKED, PreOrderStatus.CONFIRMED, PreOrderStatus.READY)
        editable = row.status in preorders.EDITABLE
        deposit = deposits.latest_for(row)
        held = deposit if deposit is not None and deposit.status == "held" else None
        if not deposits_on and held is None:
            deposit = None  # with the feature off only a deposit still held is shown (S5 keeps booking it)
        closed = row.status in (PreOrderStatus.CANCELLED, PreOrderStatus.NO_SHOW)
        cards.append({
            "row": row, "time": timezone.localtime(row.starts_at).strftime("%H:%M"), "status": preorders.status_label(row, lang),
            "lines": list(row.lines.all()), "total": row.total, "live": live, "editable": editable and takes, "can_act": live and takes,
            "deposit": deposit, "deposit_label": deposits.status_label(deposit, lang) if deposit is not None else "",
            "can_take_deposit": deposits_on and editable and held is None and receives,
            "can_forfeit": held is not None and closed and sells, "can_refund": held is not None and closed and receives,
            "cashboxes": list(preorders.cashboxes(row)) if deposits_on and editable and held is None and receives else [],
            "can_order": row.order_id is not None, "branch": row.entity.display(lang),
        })
    return cards


def _rows_from(post):
    items, quantities, prices, notes = (post.getlist(key) for key in ("item", "quantity", "unit_price", "line_note"))
    count = max(len(items), len(quantities), len(prices), len(notes))
    pad = lambda values: list(values) + [""] * (count - len(values))  # noqa: E731
    return [{"item": i, "quantity": q, "unit_price": p, "note": n} for i, q, p, n in zip(pad(items), pad(quantities), pad(prices), pad(notes))]


def _form_from(post):
    return {"customer": post.get("customer", ""), "name": post.get("name", ""), "phone": post.get("phone", ""), "day": post.get("day", ""),
            "time": post.get("time", ""), "notes": post.get("notes", ""), "entity": post.get("entity", "")}


def _form_of(preorder):
    when = timezone.localtime(preorder.starts_at)
    return {"customer": preorder.customer_id, "name": preorder.name, "phone": preorder.phone, "day": when.date().isoformat(), "time": when.strftime("%H:%M"),
            "notes": preorder.notes}


def _shown_rows(rows):
    """The lines as the form shows them, plus empty rows to add more."""

    shown = [{"item": str(getattr(row["item"], "pk", row["item"]) or ""), "quantity": str(row["quantity"] or ""), "unit_price": str(row["unit_price"] if row["unit_price"] is not None else ""),
              "note": row["note"]}
             for row in rows if str(getattr(row["item"], "pk", row["item"]) or "").strip()]
    return shown + [{"item": "", "quantity": "", "unit_price": "", "note": ""}] * BLANK_ROWS


@require_any_permission(*FLOOR)
def preorders_view(request):
    lang = _lang(request)
    words = PRE_WORDS[lang]
    today = timezone.localdate()
    day = _day(request.GET.get("day") or request.POST.get("day_view"), today)
    make_day = _day(request.GET.get("make") or request.POST.get("make_view"), preorders.default_day())
    grouped = branches.group_mode()
    editing = None
    raw = str(request.GET.get("edit") or request.POST.get("pk") or "")
    if raw.isdigit():
        editing = get_object_or_404(preorders.scoped(), pk=int(raw))  # another branch's pre-order is a 404
    error = ""
    form = {"day": day.isoformat(), "time": "18:00"}
    rows = []
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "prepare":
            return _prepare(request, lang, words, day, make_day)
        if not _takes_orders(request.user):
            raise PermissionDenied("Pre-orders are changed by whoever can open orders.")
        try:
            if action == "save":
                form, rows = _form_from(request.POST), _rows_from(request.POST)
                entity = branches.pick(request.user, request.POST.get("entity")) if grouped and editing is None else None
                if grouped and editing is None and entity is None:
                    from . import services

                    raise ValidationError(services.MESSAGES[lang]["branch"])
                saved = preorders.save_preorder(form, rows, request.user, editing, lang, entity=entity)
                messages.success(request, words["saved"].format(number=saved.number))
                return redirect(_url(timezone.localtime(saved.starts_at).date(), lang))
            if editing is None:
                raise ValidationError(preorders.MESSAGES[lang]["foreign"])
            if action == "confirm":
                preorders.confirm(editing, request.user, lang)
                messages.success(request, words["confirmed"].format(number=editing.number))
            elif action == "ready":
                preorders.mark_ready(editing, request.user, lang)
                messages.success(request, words["readied"].format(number=editing.number))
            elif action == "collect":
                collected = preorders.collect(editing, request.user, lang)
                messages.success(request, words["collected"])
                return redirect(_order_url(collected.order, lang))
            elif action in ("cancel", "no_show"):
                status = PreOrderStatus.CANCELLED if action == "cancel" else PreOrderStatus.NO_SHOW
                result = preorders.cancel(editing, status, request.user, lang, request.POST.get("reason", ""))
                messages.success(request, words["cancelled" if action == "cancel" else "no_showed"].format(number=editing.number))
                _outcome_message(request, result, words)
            elif action in ("deposit", "deposit_forfeit", "deposit_refund"):
                _deposit_action(request, editing, action, lang, words)
            else:
                raise ValidationError(preorders.MESSAGES[lang]["bad_status"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
            if action != "save":
                messages.error(request, error)
                return redirect(_url(day, lang))
        else:
            return redirect(_url(day, lang))
    elif editing is not None:
        form = _form_of(editing)
        rows = [{"item": line.item_id, "quantity": format(line.quantity.normalize(), "f"), "unit_price": line.unit_price, "note": line.note} for line in editing.lines.all()]
    takes = _takes_orders(request.user)
    can_prepare = kitchen.may_cancel(request.user)
    return render(request, "restaurant/preorders.html", _base(
        request, page_title=words["title"], pw=words, day=day, prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1), today=today,
        cards=_cards(list(preorders.for_day(day)), request.user, lang), form=form, form_rows=_shown_rows(rows),
        editing=editing if editing is not None and editing.status in preorders.EDITABLE else None, error=error, can_edit=takes, grouped=grouped,
        customers=Customer.objects.filter(active=True).exclude(customer_code=WALK_IN_CODE).order_by("name") if takes else [],
        menu_items=_menu_items() if takes else [],
        branch_choices=branches.choices(request.user, lang) if grouped else [], chosen_branch=str(form.get("entity") or ""),
        policy=words["policy"].format(hours=_cancel_hours()) if _deposits_on() else "",
        make_day=make_day, production=[{**row, "branch": row["entity"].display(lang)} for row in preorders.production(make_day)], can_prepare=can_prepare,
    ))


def _deposit_action(request, preorder, action, lang, words):
    from approvals.gate import held
    from deposits import services as deposits

    if not deposits.enabled() and action == "deposit":
        raise PermissionDenied("Deposits are switched off.")
    if action == "deposit":
        if not user_has_permission(request.user, RECEIVE):
            raise PermissionDenied("Taking a deposit needs sales.receive_customer_payment.")
        raw = str(request.POST.get("cashbox") or "")
        cashbox = preorders.cashboxes(preorder).filter(pk=raw).first() if raw.isdigit() else None
        deposit = preorders.take_deposit(preorder, cashbox, request.POST.get("amount", ""), request.user, lang)
        messages.success(request, words["deposit_taken"].format(amount=f"{deposit.payment.amount:,.2f}", payment=deposit.payment.payment_number))
        return
    deposit = deposits.held_for(preorder)
    if deposit is None:
        raise ValidationError(deposits.MESSAGES[lang]["not_held"].format(status=""))
    if preorder.status not in (PreOrderStatus.CANCELLED, PreOrderStatus.NO_SHOW):
        raise ValidationError(preorders.MESSAGES[lang]["not_live"].format(status=preorders.status_label(preorder, lang)))
    if action == "deposit_forfeit":
        if not user_has_permission(request.user, TAKE):
            raise PermissionDenied("Forfeiting a deposit needs sales.create_sales_invoice.")
        invoice = deposits.forfeit(deposit, request.user, lang, request.POST.get("reason", ""))
        messages.success(request, words["forfeited"].format(number=invoice.invoice_number, amount=f"{invoice.total_amount:,.2f}"))
        return
    if not user_has_permission(request.user, RECEIVE):
        raise PermissionDenied("Refunding a deposit needs sales.receive_customer_payment.")
    reason = request.POST.get("reason", "")
    blocker = deposits.refund_blocker(deposit, lang)
    if blocker:
        raise ValidationError(blocker)
    if held(request, "sales.cancel_payment", deposit.payment_id, {"reason": reason}, deposit.payment.payment_number):  # HG-068
        return
    deposits.refund(deposit, request.user, reason, lang)
    messages.success(request, words["refunded"])


def _prepare(request, lang, words, day, make_day):
    """«حضّر» from the production list: ``kitchen.prepare``'s own permissions; the dish and the branch must be a row of the list
    here (another branch's, or a dish no live pre-order of the day wants, is refused)."""

    from settings_core.templatetags.hesba_format import qty

    if not kitchen.may_cancel(request.user):
        raise PermissionDenied(kitchen.MESSAGES["en" if lang == "en" else "ar"]["manage"])
    item_raw, entity_raw = str(request.POST.get("item") or ""), str(request.POST.get("entity") or "")
    row = next((r for r in preorders.production(make_day) if str(r["item"].pk) == item_raw and str(r["entity"].pk) == entity_raw and r["kitchen"]), None)
    if row is None:
        raise PermissionDenied("Not a kitchen dish of this production list.")
    try:
        run = preorders.prepare(row["item"], request.POST.get("quantity", ""), request.user, row["entity"], lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, words["prepared"].format(quantity=qty(run.output_quantity), item=row["item"].item_name))
    return redirect(_url(day, lang, make_day) + "#pre-make")
