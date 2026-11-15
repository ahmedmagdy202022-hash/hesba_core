"""Restaurants R8 (HG-118): «الحجوزات», the day's table bookings, and the guests' public request page.

Permissions reuse the floor's codes (no new one): the day view follows the board (``FLOOR``: the invoices' view right or
``restaurant.take_orders``); making, changing, confirming, seating, cancelling and no-show follow whoever can open orders
(``sales.create_sales_invoice`` or ``restaurant.take_orders``); a deposit follows the deposits' own codes (taking and refunding
``sales.receive_customer_payment``, forfeiting ``sales.create_sales_invoice``) and the «العربون» capability; the public page's
switch and its key follow the floor's set-up (``master_data.manage_items``), as the QR cards do.

The public page follows ``restaurant.views_qr`` and S3's booking page: ``login_not_required`` in the URLconf, CSRF-protected, a
per-address (and per-phone) throttle, ``no-store`` / ``noindex`` / ``Referrer-Policy`` on every answer, a honeypot field, and one
404 page for every invalid key. It writes a ``booked`` request with no table and reads nothing back.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from master_data.models import Customer
from permissions.decorators import require_any_permission
from permissions.services import user_has_permission
from printing.company import company_details
from sales.pos import WALK_IN_CODE

from . import branches, reservations
from .models import DiningTable, ReservationStatus, TableReservation
from .views import FLOOR, TABLES, TAKE, _base, _lang, _order_url, _takes_orders
from .views_qr import _address, _private, _throttled

RECEIVE = "sales.receive_customer_payment"

RES_WORDS = {
    "ar": {
        "title": "الحجوزات", "intro": "حجوزات الطاولات يوم بيوم. أكّد الطلبات اللي جاية من الموبايل، واختار للحجز طاولته، ولما الضيوف يوصلوا اضغط «وصل» يتفتح طلب الطاولة.",
        "back": "الطاولات", "day": "اليوم", "prev": "اليوم اللي قبله", "next": "اليوم اللي بعده", "today": "النهارده", "show": "اعرض",
        "none": "مفيش حجوزات في اليوم ده.", "new": "حجز جديد", "edit": "تعديل الحجز", "save": "احفظ الحجز",
        "name": "اسم الضيف", "phone": "الموبايل", "party": "عدد الأفراد", "time": "الساعة", "duration": "المدة بالدقايق", "table": "الطاولة",
        "no_table": "— لسه من غير طاولة —", "customer": "العميل", "no_customer": "— من غير عميل مسجل —", "notes": "ملاحظات", "branch": "الفرع",
        "choose_branch": "اختار الفرع", "override": "كمّل رغم العدد", "seats": "كراسي", "people": "أفراد", "minutes": "دقيقة", "until": "لحد",
        "confirm": "أكّد", "seat": "وصل", "seat_table": "طاولة الضيوف", "cancel": "إلغاء الحجز", "no_show": "ما حضرش", "reason": "السبب (اختياري)",
        "cancel_confirm": "تلغي الحجز ده؟", "no_show_confirm": "تسجّل إن الضيوف ما حضروش؟", "actions": "إجراءات", "open_order": "طلب الطاولة",
        "online": "من الموبايل", "saved": "اتحفظ الحجز {number}.", "confirmed": "اتأكد الحجز {number}.",
        "seated": "أهلاً بالضيوف. اتفتح طلب الطاولة {table}.", "cancelled": "اتلغى الحجز {number}.", "no_showed": "اتسجل إن ضيوف الحجز {number} ما حضروش.",
        "deposit": "العربون", "deposit_amount": "مبلغ العربون", "cashbox": "الخزنة", "take": "خد عربون", "held": "عربون محجوز",
        "deposit_taken": "اتاخد عربون {amount} بإيصال {payment}.", "deposit_status": "حالة العربون",
        "forfeit": "حوّل العربون لرسوم إلغاء", "refund": "رجّع العربون", "refund_reason": "سبب الرد",
        "policy": "الإلغاء قبل الميعاد بأقل من {hours} ساعة أو عدم الحضور بيحوّل العربون لرسوم إلغاء، وقبل كده بيترد.",
        "forfeited": "العربون اتحوّل لفاتورة رسوم إلغاء {number} بـ {amount}.", "refunded": "العربون اترد للعميل.",
        "refund_pending": "رد العربون مستني موافقة شخص تاني.", "not_allowed": "العربون فضل محجوز رصيد للعميل؛ التصرف فيه محتاج صلاحية تانية.",
        "needs_customer": "العربون محتاج عميل مسجل على الحجز.",
        "page_title": "صفحة الحجز للضيوف", "page_intro": "صفحة عامة الضيف بيبعت منها طلب حجز من موبايله. الطلب بيوصل هنا محجوز من غير طاولة، وانت اللي بتأكده.",
        "page_on": "الصفحة شغالة", "page_off": "الصفحة مقفولة", "turn_on": "شغّل الصفحة", "turn_off": "اقفل الصفحة", "page_link": "رابط الصفحة",
        "rotate": "غيّر الرابط", "rotate_confirm": "الرابط القديم هيبطّل يشتغل فورًا. تكمّل؟", "rotated": "اتغيّر رابط صفحة الحجز.",
        "page_saved": "اتحفظت صفحة الحجز.", "no_base": "عنوان الموقع مش متظبط، فالرابط الكامل مش ظاهر.", "path": "المسار",
    },
    "en": {
        "title": "Reservations", "intro": "Table bookings day by day. Confirm the requests that come from phones, give each booking its table, and tap “Seat” when the guests arrive to open the table's order.",
        "back": "Tables", "day": "Day", "prev": "Previous day", "next": "Next day", "today": "Today", "show": "Show",
        "none": "No reservations on this day.", "new": "New reservation", "edit": "Edit the reservation", "save": "Save the reservation",
        "name": "Guest name", "phone": "Mobile", "party": "Party size", "time": "Time", "duration": "Length in minutes", "table": "Table",
        "no_table": "— no table yet —", "customer": "Customer", "no_customer": "— no registered customer —", "notes": "Notes", "branch": "Branch",
        "choose_branch": "Choose the branch", "override": "Go on despite the party size", "seats": "seats", "people": "guests", "minutes": "min", "until": "until",
        "confirm": "Confirm", "seat": "Seat", "seat_table": "Guests' table", "cancel": "Cancel the reservation", "no_show": "No-show", "reason": "Reason (optional)",
        "cancel_confirm": "Cancel this reservation?", "no_show_confirm": "Record that the guests did not come?", "actions": "Actions", "open_order": "The table's order",
        "online": "From a phone", "saved": "Reservation {number} saved.", "confirmed": "Reservation {number} confirmed.",
        "seated": "Welcome to the guests. Table {table}'s order is open.", "cancelled": "Reservation {number} cancelled.", "no_showed": "Reservation {number}'s guests recorded as a no-show.",
        "deposit": "Deposit", "deposit_amount": "Deposit amount", "cashbox": "Cashbox", "take": "Take a deposit", "held": "Deposit held",
        "deposit_taken": "Deposit of {amount} taken on receipt {payment}.", "deposit_status": "Deposit status",
        "forfeit": "Turn the deposit into a cancellation fee", "refund": "Refund the deposit", "refund_reason": "Refund reason",
        "policy": "Cancelling fewer than {hours} hours before, or not coming, turns the deposit into a cancellation fee; earlier, it is refunded.",
        "forfeited": "The deposit became cancellation-fee invoice {number} for {amount}.", "refunded": "The deposit was refunded to the customer.",
        "refund_pending": "The deposit refund is waiting for a second person's approval.", "not_allowed": "The deposit stays held as the customer's credit; settling it needs another permission.",
        "needs_customer": "A deposit needs a registered customer on the reservation.",
        "page_title": "The guests' booking page", "page_intro": "A public page guests send a booking request from, on their phones. The request arrives here as booked, with no table, for you to confirm.",
        "page_on": "The page is on", "page_off": "The page is off", "turn_on": "Switch the page on", "turn_off": "Switch the page off", "page_link": "The page's address",
        "rotate": "Change the address", "rotate_confirm": "The old address stops working at once. Go on?", "rotated": "The booking page's address changed.",
        "page_saved": "Booking page saved.", "no_base": "The site address is not set, so the full address is not shown.", "path": "Path",
    },
}

PUBLIC_WORDS = {
    "ar": {
        "title": "احجز طاولة", "lead": "ابعت طلب الحجز، وهنكلمك نأكده معاك.", "name": "اسمك", "phone": "رقم الموبايل",
        "phone_hint": "هنتواصل مع حضرتك على الرقم ده علشان نأكد الحجز.", "party": "عدد الأفراد", "day": "اليوم", "time": "الساعة",
        "notes": "ملاحظات", "optional": "اختياري", "send": "ابعت طلب الحجز", "honeypot": "سيب الخانة دي فاضية",
        "sent_h": "طلبك وصل", "sent_p": "هنتواصل مع حضرتك قريب علشان نأكد الحجز.", "again": "ابعت طلب تاني",
        "too_many": "الطلبات كتير في وقت قصير. استنى شوية وجرّب تاني.", "failed": "مقدرناش نسجّل الطلب. جرّب تاني.",
        "foot": "الطلب بيوصل للمطعم مباشرة، والحجز بيتأكد بعد ما نكلمك.",
        "dead_title": "صفحة الحجز مش متاحة", "dead_body": "الرابط ده مش شغال دلوقتي. تواصل مع المطعم مباشرة.",
    },
    "en": {
        "title": "Book a table", "lead": "Send your booking request and we will call you to confirm it.", "name": "Your name", "phone": "Mobile number",
        "phone_hint": "We will contact you on this number to confirm the booking.", "party": "Party size", "day": "Day", "time": "Time",
        "notes": "Notes", "optional": "optional", "send": "Send the booking request", "honeypot": "Leave this field empty",
        "sent_h": "Your request is in", "sent_p": "We will contact you shortly to confirm the booking.", "again": "Send another request",
        "too_many": "Too many requests in a short time. Wait a little and try again.", "failed": "We could not record the request. Try again.",
        "foot": "Your request goes straight to the restaurant; the booking is confirmed once we call you.",
        "dead_title": "The booking page is not available", "dead_body": "This address is not working now. Please contact the restaurant directly.",
    },
}

RATE_WINDOW = 60
PAGE_LIMIT = 120  # page views per address per minute
POST_WINDOW = 3600
POST_IP_LIMIT = 10  # requests per address per hour (S3's booking limits)
POST_PHONE_LIMIT = 5  # requests per phone number per hour
ENTITY_LIMIT = 200  # requests per branch per hour, whoever sends them


# --- the staff screen ----------------------------------------------------------------------------------------------------

def _day(raw):
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        return timezone.localdate()


def _day_url(day, lang, **extra):
    query = "&".join(f"{key}={value}" for key, value in extra.items())
    return f"{reverse('restaurant:reservations')}?lang={lang}&day={day.isoformat()}" + (f"&{query}" if query else "")


def _deposits_on():
    from deposits import services as deposits

    return deposits.enabled()


def _outcome_message(request, result, words):
    outcome = result["outcome"]
    if outcome == "forfeited":
        invoice = result["invoice"]
        messages.success(request, words["forfeited"].format(number=invoice.invoice_number, amount=f"{invoice.total_amount:,.2f}"))
    elif outcome == "refunded":
        messages.success(request, words["refunded"])
    elif outcome == "refund_pending":
        messages.info(request, words["refund_pending"])
    elif outcome in ("forfeit_not_allowed", "refund_not_allowed"):
        messages.info(request, words["not_allowed"])
    elif outcome in ("forfeit_refused", "refund_blocked"):
        messages.warning(request, result["detail"])


def _cards(rows, user, lang):
    """The day view's rows, with what each may do and the deposit it holds."""

    from deposits import services as deposits

    words = RES_WORDS[lang]
    deposits_on = _deposits_on()
    takes, receives, sells = _takes_orders(user), user_has_permission(user, RECEIVE), user_has_permission(user, TAKE)
    cards = []
    for row in rows:
        live = row.status in reservations.OPEN
        deposit = deposits.latest_for(row)
        held = deposit if deposit is not None and deposit.status == "held" else None
        if not deposits_on and held is None:
            deposit = None  # with the feature off only a deposit still held is shown (S5 keeps booking it)
        cards.append({
            "row": row, "time": reservations.local_time(row.starts_at), "until": reservations.local_time(row.ends_at), "status": reservations.status_label(row, lang),
            "live": live, "can_act": live and takes, "can_seat": live and takes,
            "over": row.table_id is not None and row.party_size > row.table.seats,
            "deposit": deposit, "deposit_label": deposits.status_label(deposit, lang) if deposit is not None else "",
            "can_take_deposit": deposits_on and live and held is None and receives and row.customer_id is not None,
            "needs_customer": deposits_on and live and held is None and receives and row.customer_id is None,
            "can_forfeit": held is not None and not live and row.status != ReservationStatus.SEATED and sells,
            "can_refund": held is not None and not live and (row.status != ReservationStatus.SEATED or reservations.deposit_released(row)) and receives,
            "cashboxes": list(reservations.cashboxes(row)) if deposits_on and live and held is None and receives and row.customer_id else [],
            "can_order": row.order_id is not None, "branch": row.entity.display(lang),
        })
    return cards


def _page_rows(user, lang, grouped):
    """The public page's switch and address, for each branch on the screen (none is made on reading)."""

    entities = [choice["entity"] for choice in branches.choices(user, lang)] if grouped else [branches.working_entity()]
    rows = []
    for entity in entities:
        page = reservations.page_for(entity, make=False)
        rows.append({"entity": entity, "label": entity.display(lang), "page": page, "on": bool(page and page.accepting),
                     "url": reservations.public_url(page, lang) if page else None,
                     "path": reverse("book_table", args=[page.public_key]) if page else ""})
    return rows


def _form_from(post):
    return {"name": post.get("name", ""), "phone": post.get("phone", ""), "party_size": post.get("party_size", ""), "day": post.get("day", ""),
            "time": post.get("time", ""), "duration_minutes": post.get("duration_minutes", ""), "notes": post.get("notes", ""),
            "table": post.get("table", ""), "customer": post.get("customer", ""), "entity": post.get("entity", "")}


def _form_of(reservation):
    when = timezone.localtime(reservation.starts_at)
    return {"name": reservation.name, "phone": reservation.phone, "party_size": reservation.party_size, "day": when.date().isoformat(),
            "time": when.strftime("%H:%M"), "duration_minutes": reservation.duration_minutes, "notes": reservation.notes,
            "table": reservation.table_id or "", "customer": reservation.customer_id or ""}


@require_any_permission(*FLOOR)
def reservations_view(request):
    lang = _lang(request)
    words = RES_WORDS[lang]
    day = _day(request.GET.get("day") or request.POST.get("day_view"))
    grouped = branches.group_mode()
    editing = None
    raw = str(request.GET.get("edit") or request.POST.get("pk") or "")
    if raw.isdigit():
        editing = get_object_or_404(reservations.scoped(), pk=int(raw))  # another branch's reservation is a 404
    error, error_code = "", ""
    form = {"party_size": "2", "duration_minutes": str(reservations.DEFAULT_DURATION), "day": day.isoformat(), "time": "20:00"}
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action in ("page_on", "page_off", "rotate"):
            return _page_action(request, action, lang, day)
        if not _takes_orders(request.user):
            raise PermissionDenied("Reservations are changed by whoever can open orders.")
        try:
            if action == "save":
                form = _form_from(request.POST)
                entity = branches.pick(request.user, request.POST.get("entity")) if grouped and editing is None else None
                if grouped and editing is None and entity is None:
                    from . import services

                    raise ValidationError(services.MESSAGES[lang]["branch"])
                saved = reservations.save_reservation(form, request.user, editing, lang, entity=entity, override=request.POST.get("override") == "on")
                messages.success(request, words["saved"].format(number=saved.number))
                return redirect(_day_url(timezone.localtime(saved.starts_at).date(), lang))
            if editing is None:
                raise ValidationError(reservations.MESSAGES[lang]["foreign"])
            if action == "confirm":
                reservations.confirm(editing, request.user, lang)
                messages.success(request, words["confirmed"].format(number=editing.number))
            elif action == "seat":
                seated = reservations.seat(editing, request.user, table=_table_pick(request.POST.get("table"), lang), override=request.POST.get("override") == "on", lang=lang)
                messages.success(request, words["seated"].format(table=seated.table.name))
                return redirect(_order_url(seated.order, lang))
            elif action in ("cancel", "no_show"):
                status = ReservationStatus.CANCELLED if action == "cancel" else ReservationStatus.NO_SHOW
                result = reservations.cancel(editing, status, request.user, lang, request.POST.get("reason", ""))
                messages.success(request, words["cancelled" if action == "cancel" else "no_showed"].format(number=editing.number))
                _outcome_message(request, result, words)
            elif action in ("deposit", "deposit_forfeit", "deposit_refund"):
                _deposit_action(request, editing, action, lang, words)
            else:
                raise ValidationError(reservations.MESSAGES[lang]["bad_status"])
        except ValidationError as exc:
            error, error_code = " ".join(exc.messages), getattr(exc, "code", "") or ""
            if action != "save":
                messages.error(request, error)
                return redirect(_day_url(day, lang))
        else:
            return redirect(_day_url(day, lang))
    elif editing is not None:
        form = _form_of(editing)
    tables = [{"pk": t.pk, "entity_id": t.entity_id, "label": f"{t.name} ({t.seats} {words['seats']})" + (f" · {t.entity.display(lang)}" if grouped else "")}
              for t in branches.tables(DiningTable.objects.filter(active=True)).select_related("entity").order_by("entity_id", "sort_order", "name")]
    takes = _takes_orders(request.user)
    rows = list(reservations.for_day(day))
    return render(request, "restaurant/reservations.html", _base(
        request, page_title=words["title"], rw=words, day=day, prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1), today=timezone.localdate(),
        cards=_cards(rows, request.user, lang), form=form, editing=editing if editing is not None and editing.status in reservations.OPEN else None,
        error=error, seats_warning=error_code == "seats", can_edit=takes, tables=tables, grouped=grouped,
        customers=Customer.objects.filter(active=True).exclude(customer_code=WALK_IN_CODE).order_by("name") if takes else [],
        branch_choices=branches.choices(request.user, lang) if grouped else [], chosen_branch=str(form.get("entity") or ""),
        policy=words["policy"].format(hours=_cancel_hours()) if _deposits_on() else "",
        page_rows=_page_rows(request.user, lang, grouped) if user_has_permission(request.user, TABLES) else [],
    ))


def _cancel_hours():
    from deposits import services as deposits

    return deposits.cancel_hours()


def _table_pick(raw, lang):
    raw = str(raw or "").strip()
    if not raw:
        return None
    table = branches.tables().filter(pk=raw).first() if raw.isdigit() else None
    if table is None:
        raise ValidationError(reservations.MESSAGES[lang]["table_branch"])
    return table


def _deposit_action(request, reservation, action, lang, words):
    from approvals.gate import held
    from deposits import services as deposits

    if not deposits.enabled() and action == "deposit":
        raise PermissionDenied("Deposits are switched off.")
    if action == "deposit":
        if not user_has_permission(request.user, RECEIVE):
            raise PermissionDenied("Taking a deposit needs sales.receive_customer_payment.")
        raw = str(request.POST.get("cashbox") or "")
        cashbox = reservations.cashboxes(reservation).filter(pk=raw).first() if raw.isdigit() else None
        deposit = reservations.take_deposit(reservation, cashbox, request.POST.get("amount", ""), request.user, lang)
        messages.success(request, words["deposit_taken"].format(amount=f"{deposit.payment.amount:,.2f}", payment=deposit.payment.payment_number))
        return
    deposit = deposits.held_for(reservation)
    if deposit is None:
        raise ValidationError(deposits.MESSAGES[lang]["not_held"].format(status=""))
    released = action == "deposit_refund" and reservations.deposit_released(reservation)  # a cancelled order or bill: refund only
    if reservation.status in reservations.OPEN or (reservation.status == ReservationStatus.SEATED and not released):
        raise ValidationError(reservations.MESSAGES[lang]["state"].format(status=reservations.status_label(reservation, lang)))
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


def _page_action(request, action, lang, day):
    if not user_has_permission(request.user, TABLES):
        raise PermissionDenied("The public booking page is set up with master_data.manage_items.")
    words = RES_WORDS[lang]
    entity = branches.pick(request.user, request.POST.get("entity")) if branches.group_mode() else branches.working_entity()
    if entity is None:
        raise PermissionDenied("Choose one of your branches.")
    if action == "rotate":
        reservations.rotate(entity, request.user)
        messages.success(request, words["rotated"])
    else:
        reservations.set_accepting(entity, action == "page_on", request.user)
        messages.success(request, words["page_saved"])
    return redirect(_day_url(day, lang))


# --- the public page -----------------------------------------------------------------------------------------------------

def _shell(lang, **extra):
    other = "en" if lang == "ar" else "ar"
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": PUBLIC_WORDS[lang], "company": company_details(),
            "switch_url": f"?lang={other}", **extra}  # relative: the dead page never repeats the key it was asked for


def _too_many(lang):
    from django.http import HttpResponse

    return _private(HttpResponse(PUBLIC_WORDS[lang]["too_many"], status=429, content_type="text/plain; charset=utf-8"))


def _public_render(request, page, lang, error="", form=None, status=200, sent=False):
    from entities.services import is_multi_entity

    today = timezone.localdate()
    context = _shell(lang, entity_name=page.entity.display(lang) if is_multi_entity() else "", error=error, sent=sent,
                     form=form or {"party_size": "2", "day": today.isoformat(), "time": "20:00"}, min_day=today.isoformat(),
                     max_day=(today + timedelta(days=reservations.PUBLIC_DAYS_AHEAD)).isoformat())
    return _private(render(request, "restaurant/book_table.html", context, status=status))


@never_cache
@csrf_protect
def book_table(request, key):
    lang = "en" if (request.GET.get("lang") or request.POST.get("lang")) == "en" else "ar"
    if _throttled("book_page", _address(request), PAGE_LIMIT, RATE_WINDOW):  # before the key is looked at: guessing keys is throttled too
        return _too_many(lang)
    page = reservations.page_by_key(key)
    if page is None:
        return _private(render(request, "restaurant/table_dead.html", _shell(lang), status=404))
    if request.method != "POST":
        return _public_render(request, page, lang, sent=request.GET.get("sent") == "1")
    words = PUBLIC_WORDS[lang]
    form = {"name": request.POST.get("name", "")[:120], "phone": request.POST.get("phone", "")[:30], "party_size": request.POST.get("party_size", "")[:3],
            "day": request.POST.get("day", "")[:10], "time": request.POST.get("time", "")[:5], "notes": request.POST.get("notes", "")[:200]}
    if request.POST.get("website"):  # the honeypot: a person never sees it, a script fills it in
        return _public_render(request, page, lang, words["failed"], form, status=400)
    from .callcentre import latin_digits

    digits = "".join(ch for ch in latin_digits(form["phone"]) if ch.isdigit())
    if (_throttled("book_post", _address(request), POST_IP_LIMIT, POST_WINDOW) or (digits and _throttled("book_phone", digits[-10:], POST_PHONE_LIMIT, POST_WINDOW))
            or _throttled("book_entity", page.entity_id, ENTITY_LIMIT, POST_WINDOW)):
        return _too_many(lang)
    try:
        reservations.request_online(page, form, lang)
    except ValidationError as exc:
        return _public_render(request, page, lang, " ".join(exc.messages), form, status=400)
    return _private(HttpResponseRedirect(f"{reverse('book_table', args=[page.public_key])}?lang={lang}&sent=1", status=303))
