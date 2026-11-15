"""Restaurants R5a (HG-115): the table's public QR page (no account) and, for staff, the QR cards and the requests' accept / decline.

The public page follows ``appointments.views_booking`` (S3): ``login_not_required`` in the URLconf, CSRF-protected, a per-address
throttle, ``no-store`` / ``noindex`` / ``Referrer-Policy`` on every answer, a honeypot field, and one dead page for every invalid
key. It is server-rendered and works without JavaScript: every dish has a quantity box in one form. What an anonymous visitor can
read: the menu (names, prices, choices), the service and minimum notice, and the status of the requests made from this browser
(a signed cookie, never an id in a URL). What they can write: a ``TableRequest`` (``restaurant.qr``), within its limits. Never an
order line, never anything about a bill, another table, the staff or any customer.
"""

import re
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from entities.services import is_multi_entity
from permissions.decorators import require_any_permission, require_permission
from printing.company import company_details

from . import branches, qr
from .models import DiningTable, RequestKind, TableRequest
from .views import FLOOR, TABLES, _lang, _order_url, _takes_orders

RATE_WINDOW = 60
PAGE_LIMIT = 120  # page views per address per minute
POST_WINDOW = 600
POST_IP_LIMIT = 20  # requests per address per ten minutes
TABLE_WINDOW = 3600
TABLE_LIMIT = 120  # requests per table per hour, whoever sends them
COOKIE = "tm_req"
COOKIE_SALT = "restaurant.table_request"
COOKIE_AGE = 6 * 3600
REMEMBERED = 8
SHOWN = 6

WORDS = {
    "ar": {
        "title": "المنيو", "lead": "اختار اللي يعجبك واطلب من مكانك. الجرسون هيأكد طلبك ويجيبه لحد الطاولة.", "table": "طاولة {name}",
        "vat_note": "الأسعار شاملة ضريبة القيمة المضافة.", "service_note": "بتضاف نسبة خدمة {percent}% على الحساب.",
        "minimum_note": "الحد الأدنى للفرد {amount}.", "pay_note": "الدفع بيتم عند الحساب مع الجرسون.",
        "all": "المنيو", "other": "أصناف تانية", "from": "من", "size": "الحجم", "choose_size": "اختار الحجم", "sizes": "أحجام",
        "qty": "الكمية", "plus": "زوّد", "minus": "قلّل", "details": "الاختيارات والملاحظات", "required": "لازم", "optional": "اختياري",
        "line_note": "ملاحظة للمطبخ", "line_note_hint": "مثلًا: من غير بصل", "order_note": "ملاحظة للجرسون (اختياري)",
        "order": "اطلب", "order_n": "اطلب ({n})", "call": "نادي الجرسون", "bill": "الحساب", "none": "المنيو مفيهوش أصناف دلوقتي.",
        "status_title": "طلباتك من الطاولة", "refresh": "حدّث الحالة", "ago": "الساعة {time}",
        "kind_order": "الطلب", "kind_call_waiter": "نداء الجرسون", "kind_bill": "طلب الحساب",
        "new_order": "وصل طلبك، والجرسون هيأكده معاك دلوقتي.", "accepted_order": "الجرسون استلم طلبك وهيتحضّر.",
        "declined_order": "الطلب ما اتقبلش. كلّم الجرسون أو اطلب من جديد.",
        "new_call_waiter": "نادينا الجرسون وهيكون عندك حالًا.", "accepted_call_waiter": "الجرسون وصلك.", "declined_call_waiter": "النداء اتشال. اطلب الجرسون تاني لو محتاجه.",
        "new_bill": "طلبنا الحساب والجرسون جاي بيه.", "accepted_bill": "الجرسون جاب الحساب.", "declined_bill": "طلب الحساب اتشال. اطلبه تاني لو محتاجه.",
        "failed": "مقدرناش نبعت الطلب. جرّب تاني.", "too_many": "الطلبات كتير في وقت قصير. استنى دقيقة وجرّب تاني.",
        "foot": "الطلب بيوصل للجرسون مباشرة.", "honeypot": "سيب الخانة دي فاضية",
        "dead_title": "الكود ده مش شغال", "dead_body": "جرّب تمسح الكود الموجود على الطاولة تاني، أو اسأل حد من الفريق.",
    },
    "en": {
        "title": "Menu", "lead": "Choose what you like and order from your seat. Your waiter confirms the order and brings it to the table.", "table": "Table {name}",
        "vat_note": "Prices include VAT.", "service_note": "A {percent}% service charge is added to the bill.",
        "minimum_note": "Minimum charge per guest: {amount}.", "pay_note": "You pay at the bill, with your waiter.",
        "all": "Menu", "other": "Other", "from": "from", "size": "Size", "choose_size": "Choose the size", "sizes": "sizes",
        "qty": "Quantity", "plus": "More", "minus": "Less", "details": "Choices and notes", "required": "Required", "optional": "Optional",
        "line_note": "Note for the kitchen", "line_note_hint": "For example: no onions", "order_note": "Note for the waiter (optional)",
        "order": "Order", "order_n": "Order ({n})", "call": "Call the waiter", "bill": "Bill", "none": "The menu has no items right now.",
        "status_title": "Your requests from this table", "refresh": "Refresh status", "ago": "At {time}",
        "kind_order": "Order", "kind_call_waiter": "Waiter call", "kind_bill": "Bill request",
        "new_order": "Your order is in; the waiter will confirm it with you shortly.", "accepted_order": "The waiter has your order and it is being prepared.",
        "declined_order": "The order was not accepted. Talk to the waiter or order again.",
        "new_call_waiter": "We called the waiter; they will be with you shortly.", "accepted_call_waiter": "The waiter is with you.", "declined_call_waiter": "The call was cleared. Call the waiter again if you need them.",
        "new_bill": "We asked for the bill; the waiter is bringing it.", "accepted_bill": "The waiter brought the bill.", "declined_bill": "The bill request was cleared. Ask again if you need it.",
        "failed": "We could not send the request. Try again.", "too_many": "Too many requests in a short time. Wait a minute and try again.",
        "foot": "Your request goes straight to the waiter.", "honeypot": "Leave this field empty",
        "dead_title": "This code is not working", "dead_body": "Try scanning the code on the table again, or ask a member of the team.",
    },
}


# --- the public page ---------------------------------------------------------------------------------------------------

def _private(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"  # the stricter "no referrer" policy makes browsers post "Origin: null" (CSRF 403)
    patch_vary_headers(response, ("Accept-Language",))
    return response


def _address(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _throttled(bucket, who, limit, window):
    """A counter per bucket, caller and window (the booking and payments pattern); enough to blunt a flood."""

    key = f"tablemenu:{bucket}:{who}:{int(timezone.now().timestamp()) // window}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window * 2)
        count = 1
    return count > limit


def _too_many(lang):
    return _private(HttpResponse(WORDS[lang]["too_many"], status=429, content_type="text/plain; charset=utf-8"))


def _shell(request, lang, **extra):
    company = company_details()
    other = "en" if lang == "ar" else "ar"
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "company": company,
            "switch_url": f"?lang={other}", **extra}  # relative: the dead page never repeats the key it was asked for


def _dead(request, lang):
    """The one answer for a key that is wrong, rotated, of an inactive table or branch, or of a module that is off."""

    return _private(render(request, "restaurant/table_dead.html", _shell(request, lang), status=404))


def _remembered(request, table):
    """The requests made from this browser at this table (a signed cookie), newest first."""

    raw = request.get_signed_cookie(COOKIE, default="", salt=COOKIE_SALT, max_age=COOKIE_AGE)
    ids = [int(part) for part in str(raw).split(",") if part.isdigit()][-REMEMBERED:]
    if not ids:
        return []
    return list(TableRequest.objects.filter(pk__in=ids, table=table).order_by("-created_at", "-pk")[:SHOWN])


def _remember(request, response, table, request_id):
    raw = request.get_signed_cookie(COOKIE, default="", salt=COOKIE_SALT, max_age=COOKIE_AGE)
    ids = [part for part in str(raw).split(",") if part.isdigit()][-(REMEMBERED - 1):] + [str(request_id)]
    response.set_signed_cookie(COOKIE, ",".join(ids), salt=COOKIE_SALT, max_age=COOKIE_AGE, httponly=True, samesite="Lax",
                               secure=request.is_secure(), path=reverse("table_menu", args=[table.qr_key]))


def _notices(text):
    """The VAT note and R3's service and minimum notice, from the same settings the bill reads."""

    from settings_core.templatetags.hesba_format import money
    from taxes.services import vat_enabled

    from . import charges
    from .models import OrderKind

    config = charges.settings()
    notes = []
    if vat_enabled():
        notes.append(text["vat_note"])
    if config.percent is not None and OrderKind.DINE_IN in config.kinds:
        notes.append(text["service_note"].format(percent=charges._shown_percent(config.percent)))
    if config.minimum is not None:
        notes.append(text["minimum_note"].format(amount=money(config.minimum)))
    return notes + [text["pay_note"]]


def menu(lang, posted=None):
    """The public menu: [{key, name, entries: [dish | family]}], each dish with its price as paid (VAT in) and its choices.

    A size family (``variants``) is one tile asking the size; a dish with required or optional choices lists them (R1a). Combos
    are not on this page. The cart's posted state (after a refused request) is put back on each dish."""

    from .views_modifiers import menu_entries, sheets

    posted = posted or {"q": {}, "o": {}, "n": {}}
    items = qr.public_items()
    asked = {sheet["item"].pk: sheet for sheet in sheets(items, lang)}
    entries, _families = menu_entries(items, set(asked), lang)
    rates = {}
    by_category = {item.category_id: item.category for item in items if item.category_id}

    def dish(item, label=None):
        sheet = asked.get(item.pk)
        picked = posted["o"].get(item.pk, set())
        groups = []
        for group in (sheet["groups"] if sheet else []):
            groups.append({"pk": group["group"].pk, "label": group["label"], "rule": group["rule"], "required": group["required"], "radio": group["radio"],
                           "options": [{"pk": option["option"].pk, "label": option["label"], "checked": option["option"].pk in picked,
                                        "price": qr.shown_price(option["option"].item, option["option"].item.default_sale_price, rates) if option["option"].item_id else None}
                                       for option in group["options"]]})
        quantity = posted["q"].get(item.pk, 0)
        return {"pk": item.pk, "name": label or item.item_name, "price": qr.shown_price(item, item.default_sale_price, rates), "groups": groups,
                "required": any(group["required"] for group in groups), "qty": quantity, "note": posted["n"].get(item.pk, ""), "open": bool(quantity and groups)}

    sections, index = [], {}

    def section(category_id):
        if category_id not in index:
            category = by_category.get(category_id)
            name = (category.name_en if lang == "en" and category and category.name_en else category.name_ar) if category else ""
            index[category_id] = {"key": category_id or 0, "name": name, "entries": []}
            sections.append(index[category_id])
        return index[category_id]

    for entry in entries:
        if "sizes" in entry:
            family = entry["sizes"]
            sizes = [dish(size["item"], size["label"]) for size in family["sizes"]]
            section(family["category_id"] or None)["entries"].append({"family": {"name": family["name"], "sizes": sizes, "low": min(size["price"] for size in sizes),
                                                                              "same_price": len({size["price"] for size in sizes}) == 1}})
        else:
            section(entry["item"].category_id)["entries"].append({"dish": dish(entry["item"])})
    sections.sort(key=lambda found: found["key"] == 0)  # items with no category last
    for found in sections:
        found["name"] = found["name"] or WORDS[lang]["other"]
    return sections


def parse_cart(post):
    """The cart as the form sends it: ``q-<item>`` the quantity, ``o-<item>`` / ``o-<item>-<group>`` the option pks, ``n-<item>`` a note."""

    lines = {}
    for key in post:
        found = re.fullmatch(r"([qon])-(\d{1,9})(?:-\d{1,9})?", key, re.ASCII)
        if not found:
            continue
        kind, pk = found.groups()
        line = lines.setdefault(pk, {"item": pk, "quantity": "0", "options": [], "note": ""})
        if kind == "q":
            line["quantity"] = post.get(key, "0")
        elif kind == "o":
            line["options"].extend(post.getlist(key))
        else:
            line["note"] = post.get(key, "")
    return list(lines.values())


def _posted_state(lines):
    state = {"q": {}, "o": {}, "n": {}}
    for line in lines:
        pk = int(line["item"])
        if qr._ascii_digits(str(line["quantity"]).strip()):
            state["q"][pk] = int(line["quantity"])
        state["o"][pk] = {int(value) for value in line["options"] if qr._ascii_digits(str(value))}
        state["n"][pk] = line["note"][:qr.LINE_NOTE]
    return state


def _render(request, table, lang, error="", posted=None, note="", status=200):
    text = WORDS[lang]
    sections = menu(lang, posted)
    mine = [{"request": found, "text": text[f"{found.status}_{found.kind}"], "kind": text[f"kind_{found.kind}"],
             "time": timezone.localtime(found.created_at).strftime("%H:%M")} for found in _remembered(request, table)]
    context = _shell(request, lang, table=table, table_label=text["table"].format(name=table.name), sections=sections, notices=_notices(text),
                     mine=mine, error=error, note=note, entity_name=table.entity.display(lang) if is_multi_entity() else "",
                     dish_count=sum(len(found["entries"]) for found in sections))
    return _private(render(request, "restaurant/table_menu.html", context, status=status))


@never_cache
@csrf_protect
def page(request, key):
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    if _throttled("page", _address(request), PAGE_LIMIT, RATE_WINDOW):  # before the key is looked at: guessing keys is throttled too
        return _too_many(lang)
    table = qr.table_by_key(key)
    if table is None:
        return _dead(request, lang)
    if request.method == "POST":
        return _submit(request, table, lang)
    return _render(request, table, lang)


def _submit(request, table, lang):
    text = WORDS[lang]
    if request.POST.get("website"):  # the honeypot: a person never sees it, a script fills it in
        return _render(request, table, lang, text["failed"], status=400)
    if _throttled("post_ip", _address(request), POST_IP_LIMIT, POST_WINDOW) or _throttled("post_table", table.pk, TABLE_LIMIT, TABLE_WINDOW):
        return _too_many(lang)
    action = request.POST.get("action", "")
    kind = {"order": RequestKind.ORDER, "call": RequestKind.CALL, "bill": RequestKind.BILL}.get(action)
    if kind is None:
        return _render(request, table, lang, text["failed"], status=400)
    lines = parse_cart(request.POST) if kind == RequestKind.ORDER else []
    note = request.POST.get("note", "")[:qr.NOTE]
    try:
        made = qr.create_request(table, kind, lines, note, lang)
    except ValidationError as exc:
        return _render(request, table, lang, " ".join(exc.messages), _posted_state(lines) if lines else None, note, status=400)
    response = HttpResponseRedirect(f"{reverse('table_menu', args=[table.qr_key])}?lang={lang}#status", status=303)
    _remember(request, response, table, made.pk)
    return _private(response)


# --- the staff side ------------------------------------------------------------------------------------------------------

QR_WORDS = {
    "ar": {
        "cards_title": "كروت QR للطاولات", "cards_link": "اطبع كروت QR", "print": "اطبع الكروت", "tip": "كل طاولة ليها كارت بكودها. اطبع على ورق A4، أو احفظ PDF من نافذة الطباعة.",
        "back": "الطاولات", "scan_ar": "امسح المنيو واطلب من مكانك", "scan_en": "Scan the menu and order from your seat", "table": "طاولة",
        "no_cards": "لسه مفيش طاولات شغالة. ضيف طاولات الصالة الأول.", "no_base": "عنوان الموقع (PUBLIC_BASE_URL) مش متظبط، فالكروت هتتطبع من غير QR. ظبّطه الأول.",
        "manage": "كود كل طاولة", "rotate": "غيّر الكود", "rotate_confirm": "الكارت المطبوع ليه هيبطّل يشتغل فورًا. تكمّل؟", "rotated": "اتغيّر كود الطاولة {table}. الكارت القديم بطّل يشتغل، اطبع الجديد.",
        "branch": "الفرع",
        "badge": "طلب جديد", "requests": "طلبات من الطاولات", "request_n": "{n} طلب", "kind_order": "طلب", "kind_call_waiter": "نداء جرسون", "kind_bill": "طلب الحساب",
        "accept": "قبول", "decline": "رفض", "done": "تمام", "from_table": "من الطاولة {table}", "at": "الساعة {time}", "note": "ملاحظة", "gone": "غير متاح",
        "accepted": "اتقبل الطلب واتضافت أصنافه لطلب الطاولة {table} (لسه ما راحتش المطبخ).", "declined": "اترفض الطلب.", "handled": "تمام.",
        "bad_action": "الإجراء ده مش مظبوط.", "unsent_hint": "الأصناف بتتضاف من غير ما تروح المطبخ؛ ابعتها من صفحة الطلب.",
    },
    "en": {
        "cards_title": "Table QR cards", "cards_link": "Print QR cards", "print": "Print the cards", "tip": "Each table has a card with its own code. Print on A4, or save a PDF from the print window.",
        "back": "Tables", "scan_ar": "امسح المنيو واطلب من مكانك", "scan_en": "Scan the menu and order from your seat", "table": "Table",
        "no_cards": "No active tables yet. Add the hall's tables first.", "no_base": "The site address (PUBLIC_BASE_URL) is not set, so the cards print without a QR. Set it first.",
        "manage": "Each table's code", "rotate": "Change the code", "rotate_confirm": "The printed card will stop working at once. Continue?", "rotated": "Table {table}'s code was changed. The old card no longer works; print the new one.",
        "branch": "Branch",
        "badge": "new", "requests": "Requests from the tables", "request_n": "{n} requests", "kind_order": "Order", "kind_call_waiter": "Waiter call", "kind_bill": "Bill request",
        "accept": "Accept", "decline": "Decline", "done": "Done", "from_table": "From table {table}", "at": "At {time}", "note": "Note", "gone": "unavailable",
        "accepted": "Request accepted and its items added to table {table}'s order (not sent to the kitchen yet).", "declined": "Request declined.", "handled": "Done.",
        "bad_action": "That action is not valid.", "unsent_hint": "Items are added without going to the kitchen; send them from the order page.",
    },
}


def request_rows(requests, lang):
    """What the staff's blocks draw: each waiting request with its table, kind, time and (for an order) its lines."""

    text = QR_WORDS[lang]
    rows = []
    for found in requests:
        clock = timezone.localtime(found.created_at).strftime("%H:%M")
        rows.append({"request": found, "kind_label": text[f"kind_{found.kind}"], "title": text["from_table"].format(table=found.table.name), "at": text["at"].format(time=clock),
                     "is_order": found.kind == RequestKind.ORDER, "lines": qr.lines_text(found, lang) if found.kind == RequestKind.ORDER else []})
    return rows


def requests_context(requests, lang, can_act):
    """The context ``_requests.html`` needs, for the board (every waiting request) or one order's table."""

    return {"qr_rows": request_rows(requests, lang), "qw": QR_WORDS[lang], "can_act_requests": can_act}


@require_permission(TABLES)
def qr_cards(request):
    """«كروت QR للطاولات»: one card per active table of the entity being worked in, the QR carrying its full public address."""

    lang = _lang(request)
    words = QR_WORDS[lang]
    tables = list(branches.tables(DiningTable.objects.filter(active=True)).select_related("entity"))
    base = bool(getattr(settings, "PUBLIC_BASE_URL", ""))
    grouped = is_multi_entity()
    cards = [{"table": table, "url": qr.public_url(table), "branch": table.entity.display(lang) if grouped else ""} for table in tables]
    per_page = 4
    pages = [cards[start:start + per_page] for start in range(0, len(cards), per_page)]
    return render(request, "restaurant/qr_cards.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["cards_title"],
                                                        "pages": pages, "count": len(cards), "has_base": base, "company": company_details(),
                                                        "back_url": f"{reverse('restaurant:tables')}?lang={lang}"})


@require_permission(TABLES)
@require_POST
def qr_rotate(request, pk):
    lang = _lang(request)
    table = get_object_or_404(branches.tables(DiningTable.objects.filter(active=True)), pk=pk)  # R12: another branch's table is a 404
    qr.rotate_key(table, request.user)
    messages.success(request, QR_WORDS[lang]["rotated"].format(table=table.name))
    return redirect(f"{reverse('restaurant:qr_cards')}?lang={lang}")


@require_any_permission(*FLOOR)
@require_POST
def request_act(request, pk):
    """«قبول» / «رفض» on an order request, «تمام» on a call or a bill. The request is looked up in the entity being worked in only (R12)."""

    lang = _lang(request)
    words = QR_WORDS[lang]
    if not _takes_orders(request.user):
        raise PermissionDenied("Handling a table's requests needs sales.create_sales_invoice or restaurant.take_orders.")
    found = get_object_or_404(branches.requests(TableRequest.objects.select_related("table")), pk=pk)
    action = request.POST.get("action", "")
    back = f"{reverse('restaurant:board')}?lang={lang}"
    stay = request.POST.get("order", "")
    if stay.isdigit():
        order = branches.orders().filter(pk=int(stay), status="open").first()
        if order is not None:
            back = _order_url(order, lang)
    try:
        if action == "accept":
            _done, order = qr.accept(found, request.user, lang)
            messages.success(request, words["accepted"].format(table=found.table.name))
            return redirect(_order_url(order, lang))
        if action == "decline":
            qr.decline(found, request.user, lang)
            messages.success(request, words["declined"])
        elif action == "done":
            qr.handle(found, request.user, lang)
            messages.success(request, words["handled"])
        else:
            messages.error(request, words["bad_action"])
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(back)
