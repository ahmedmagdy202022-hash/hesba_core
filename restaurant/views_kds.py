"""Restaurants R4 (HG-097): the kitchen screen, its JSON feed and actions, and the setup of the kitchen stations.

The screen, the feed and the actions are open to whoever takes orders (``restaurant.take_orders``; a user who may only view
invoices does not get them). Station setup follows the tables' setup: seen with the invoices' view right, changed with
``master_data.manage_items``. Everything is scoped to the entity being worked in: another entity's station is a 404 and the
feed carries only its own tickets.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from entities import scope as entity_scope
from master_data.models import Category
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import stations
from .models import KitchenTicket, OrderLine
from .views import TABLES, TAKE_ORDERS, VIEW, WORDS, _base, _kind_label, _lang

KDS_WORDS = {
    "ar": {
        "title": "شاشة المطبخ", "all": "كل المحطات", "back": "الطاولات", "recall": "رجّع", "recall_hint": "رجّع آخر تذكرة خلصت",
        "empty": "مفيش طلبات دلوقتي.", "empty_hint": "أي طلب يتبعت من الصالة بيظهر هنا لوحده.", "done": "التذكرة خلصت",
        "hint": "اضغط على الصنف لما يجهز، واضغط «التذكرة خلصت» لما كل أصنافها تجهز.", "voided": "اتلغى بعد الإرسال", "seen": "تمام، شفته", "cancelled": "الطلب اتلغى",
        "offline": "الاتصال اتقطع، بنحاول تاني.", "signed_out": "الجلسة خلصت؛ سجّل الدخول تاني.", "min": "د", "ticket": "تذكرة", "waiter": "الويتر",
        "no_station": "من غير محطة", "setup": "جهّز المحطات", "no_stations": "لسه مفيش محطات؛ كل التذاكر بتظهر هنا.",
    },
    "en": {
        "title": "Kitchen screen", "all": "All stations", "back": "Tables", "recall": "Recall", "recall_hint": "Bring back the last finished ticket",
        "empty": "No orders right now.", "empty_hint": "An order sent from the floor appears here by itself.", "done": "Ticket done",
        "hint": "Tap a dish when it is ready; tap the ticket when it is all done.", "voided": "Voided after sending", "seen": "Seen", "cancelled": "Order cancelled",
        "offline": "The connection dropped; trying again.", "signed_out": "The session ended; sign in again.", "min": "min", "ticket": "Ticket", "waiter": "Waiter",
        "no_station": "No station", "setup": "Set up the stations", "no_stations": "No stations are set up; every ticket shows here.",
    },
}

STATION_WORDS = {
    "ar": {
        "title": "محطات المطبخ", "intro": "كل محطة بتاخد فئات من المنيو، وكل إرسال للمطبخ بيطلع تذكرة لكل محطة ليها أصناف فيه. الفئة بتروح لمحطة واحدة بس. المحطة الافتراضية بتاخد اللي مالوش محطة.",
        "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "sort": "الترتيب", "categories": "الفئات", "default": "افتراضية", "active": "شغالة", "save": "حفظ",
        "saved": "اتحفظت المحطة.", "new": "محطة جديدة", "edit": "تعديل", "none": "لسه مفيش محطات. من غيرها كل إرسال للمطبخ بيطلع تذكرة واحدة.",
        "default_hint": "تاخد كل صنف فئته مش عند محطة تانية.", "no_categories": "مفيش فئات في المنيو لسه.", "open_screen": "شاشة المطبخ", "back": "الطاولات",
        "all_items": "كل الباقي",
    },
    "en": {
        "title": "Kitchen stations", "intro": "Each station takes categories of the menu, and each send to the kitchen makes a ticket for every station that has items in it. A category goes to one station only. The default station takes whatever has none.",
        "name_ar": "Name in Arabic", "name_en": "Name in English", "sort": "Order", "categories": "Categories", "default": "Default", "active": "Active", "save": "Save",
        "saved": "Station saved.", "new": "New station", "edit": "Edit", "none": "No stations yet. Without them every send to the kitchen is one ticket.",
        "default_hint": "Takes every item whose category no other station holds.", "no_categories": "The menu has no categories yet.", "open_screen": "Kitchen screen", "back": "Tables",
        "all_items": "Everything else",
    },
}


def _station(request):
    """The station the screen is filtered to (``?station=``); another entity's, or one that does not exist, is a 404."""

    raw = request.GET.get("station") or request.POST.get("station") or ""
    return get_object_or_404(stations.scoped_stations(), pk=int(raw)) if raw.isdigit() else None


def _feed(station, lang):
    now = timezone.now()
    words = WORDS[lang]
    cards = [stations.ticket_card(ticket, now, _kind_label(ticket.order, lang), words["table"], lang) for ticket in stations.shown_tickets(station, now)]
    back = stations.last_done(station, now)
    recall = {"id": back.pk, "where": f"{words['table']} {back.order.home_table.name}" if back and back.order.home_table else _kind_label(back.order, lang)} if back else None
    # ``clock`` is the business's own time of day: the screen shows it beside tickets stamped the same way, whatever the tablet's clock says
    return {"now": now.isoformat(), "clock": timezone.localtime(now).strftime("%H:%M"), "tickets": cards, "recall": recall}


def _json(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


@require_permission(TAKE_ORDERS)
def kds(request):
    lang = _lang(request)
    station = _station(request)
    active = list(stations.scoped_stations().filter(active=True))
    words = KDS_WORDS[lang]
    js_words = {key: words[key] for key in ("empty", "empty_hint", "done", "voided", "seen", "cancelled", "offline", "signed_out", "min", "ticket", "waiter",
                                            "recall", "no_station")}
    return render(request, "restaurant/kds.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "station": station, "stations": active, "js_words": js_words,
        "tabs": [{"pk": one.pk, "label": one.label(lang)} for one in active], "can_setup": user_has_permission(request.user, VIEW),
        "page_title": words["title"] + (" · " + station.label(lang) if station else ""), "table_word": WORDS[lang]["table"],
    })


@require_permission(TAKE_ORDERS)
def kds_feed(request):
    return _json(_feed(_station(request), _lang(request)))


@require_permission(TAKE_ORDERS)
@require_POST
def kds_act(request):
    """One tap on the screen: ``ready`` (a line), ``done`` (a card) or ``recall`` (a finished card). Answers with the fresh feed."""

    lang = _lang(request)
    station = _station(request)
    op, raw = request.POST.get("op", ""), request.POST.get("id", "")
    if op not in ("ready", "done", "recall") or not raw.isdigit():
        return _json({"error": "bad request"}, status=400)
    if op == "ready":
        line = get_object_or_404(entity_scope.scope(OrderLine.objects.filter(ticket__isnull=False), "ticket__order__entity"), pk=int(raw))
        stations.mark_line(line)
    else:
        ticket = get_object_or_404(entity_scope.scope(KitchenTicket.objects.all(), "order__entity"), pk=int(raw))
        (stations.finish_ticket if op == "done" else stations.recall_ticket)(ticket)
    return _json({"ok": True, **_feed(station, lang)})


@require_permission(VIEW)
def station_setup(request):
    lang = _lang(request)
    words = STATION_WORDS[lang]
    error = ""
    editing = None
    raw = request.GET.get("edit") or request.POST.get("pk") or ""
    if raw.isdigit():
        editing = get_object_or_404(stations.scoped_stations(), pk=int(raw))
    form = {"sort": 0, "active": True, "categories": []}
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing the kitchen stations needs master_data.manage_items.")
        form = {"name_ar": request.POST.get("name_ar", ""), "name_en": request.POST.get("name_en", ""), "sort": request.POST.get("sort", ""),
                "active": request.POST.get("active") == "on", "is_default": request.POST.get("is_default") == "on",
                "categories": [int(one) for one in request.POST.getlist("categories") if one.isdigit()]}
        try:
            stations.save_station(form, request.user, editing, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('restaurant:stations')}?lang={lang}")
    elif editing:
        form = {"name_ar": editing.name_ar, "name_en": editing.name_en, "sort": editing.sort, "active": editing.active, "is_default": editing.is_default,
                "categories": list(editing.categories.values_list("pk", flat=True))}
    rows = [{"station": one, "label": one.label(lang), "categories": [(c.name_en if lang == "en" and c.name_en else c.name_ar) for c in one.categories.all()]}
            for one in stations.scoped_stations().prefetch_related("categories")]
    categories = [{"pk": c.pk, "label": c.name_en if lang == "en" and c.name_en else c.name_ar} for c in Category.objects.filter(active=True).order_by("category_code")]
    return render(request, "restaurant/stations.html", _base(request, page_title=words["title"], sw=words, rows=rows, form=form, editing=editing, error=error,
                                                            categories=categories, can_edit=user_has_permission(request.user, TABLES)))
