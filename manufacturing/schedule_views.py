"""Manufacturing F6b (HG-134) screens: «جدول الإنتاج» (work centres by day, «رتّب», moving an order) and the order page's plan.

A plain form moves an order (a day, and another work centre of the same kind for one stage); the S10 appointment calendar is
built on time slots and employee JSON endpoints, so it is not reused."""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from entities import scope as entity_scope
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import schedule, workcentres
from .models import OrderStatus, ProductionOrder
from .views import PRODUCE, VIEW, _lang

WORDS = {
    "ar": {
        "title": "جدول الإنتاج", "intro": "كل مركز عمل في صف، والأيام في الأعمدة، والأوامر شرايط عليها المرحلة والدقايق. «رتّب» بيوزّع الأوامر المفتوحة من يوم تختاره، والأقرب تسليمًا الأول.",
        "back": "التصنيع", "orders_link": "أوامر الإنتاج", "centres_link": "مراكز العمل", "start": "ابدأ من يوم", "arrange": "رتّب",
        "arrange_hint": "بيعيد ترتيب كل الأوامر المفتوحة ويلغي أي نقل يدوي قبله. المراحل اللي خلصت مبتتحركش.",
        "arranged": "اترتّب {orders} أمر ({steps} مرحلة).", "grid": "مراكز العمل والأيام", "days": "الأيام", "no_centre": "من غير مركز عمل", "all_centres": "كل المراكز",
        "centre": "مركز العمل", "down_now": "متوقف للصيانة", "down_day": "عطل", "holiday": "إجازة", "over": "فوق الطاقة", "of": "من", "min": "دقيقة", "empty_day": "فاضي", "capacity": "الطاقة",
        "late": "متأخر", "late_overdue": "عدّى ميعاده", "late_planned": "التخطيط بيعدّي ميعاده", "orders": "الأوامر", "order": "الأمر", "product": "المنتج",
        "due": "المطلوب", "planned_span": "من - إلى", "rank": "الأولوية", "rank_hint": "رقم أصغر = أول، عند نفس ميعاد التسليم.", "save_rank": "احفظ",
        "rank_saved": "اتحفظت الأولوية.", "not_planned": "لسه مترتبش", "move": "انقل", "move_to": "انقل ليوم", "move_centre": "انقل المرحلة لمركز تاني",
        "stage": "المرحلة", "no_move": "مفيش حاجة تتنقل.", "pick_stage": "اختار المرحلة", "same_kind": "من نفس النوع بس", "moved": "اتنقل الأمر.",
        "warning": "تنبيه: {centre} يوم {day} عليه {minutes} دقيقة من {capacity}.", "week_hint": "التنقل على الموبايل: كل يوم قايمة لوحده.", "none": "—",
        "plan": "جدول الأمر", "planned_min": "دقايق مخططة", "start_day": "يبدأ", "end_day": "ينتهي", "finished": "خلصت", "not_yet": "مخططة", "order_late_overdue": "الأمر عدّى ميعاد تسليمه.",
        "order_late_planned": "آخر مرحلة في الجدول بتنتهي يوم {end}، بعد المطلوب يوم {due}.", "unplanned_note": "الأمر ده لسه مترتبش؛ اضغط «رتّب» من جدول الإنتاج أو انقله ليوم.",
        "late_count": "أوامر متأخرة", "day_list": "قايمة الأيام", "no_orders": "مفيش أوامر مفتوحة.", "open_schedule": "افتح جدول الإنتاج", "date_bad": "اكتب تاريخ صحيح.",
    },
    "en": {
        "title": "Production schedule", "intro": "One row per work centre, days across, and orders as bars with their stage and minutes. «Arrange» lays the open orders out from a day you choose, the earliest delivery first.",
        "back": "Manufacturing", "orders_link": "Production orders", "centres_link": "Work centres", "start": "Start from", "arrange": "Arrange",
        "arrange_hint": "Re-plans every open order and replaces any earlier move by hand. Stages already finished do not move.",
        "arranged": "Arranged {orders} orders ({steps} stages).", "grid": "Work centres by day", "days": "Days", "no_centre": "No work centre", "all_centres": "All centres",
        "centre": "Work centre", "down_now": "Down for repair", "down_day": "Breakdown", "holiday": "Holiday", "over": "Over capacity", "of": "of", "min": "min", "empty_day": "Free", "capacity": "Capacity",
        "late": "Late", "late_overdue": "Past its due date", "late_planned": "Planned past its due date", "orders": "Orders", "order": "Order", "product": "Product",
        "due": "Due", "planned_span": "From - to", "rank": "Priority", "rank_hint": "A smaller number goes first, among orders with the same delivery date.", "save_rank": "Save",
        "rank_saved": "The priority was saved.", "not_planned": "Not planned yet", "move": "Move", "move_to": "Move to day", "move_centre": "Move the stage to another centre",
        "stage": "Stage", "no_move": "Nothing to move.", "pick_stage": "Choose the stage", "same_kind": "Same kind only", "moved": "The order was moved.",
        "warning": "Warning: {centre} on {day} has {minutes} minutes of {capacity}.", "week_hint": "On a phone each day is its own list.", "none": "—",
        "plan": "The order's plan", "planned_min": "Planned minutes", "start_day": "Starts", "end_day": "Ends", "finished": "Finished", "not_yet": "Planned", "order_late_overdue": "This order is past its delivery date.",
        "order_late_planned": "The last planned stage ends on {end}, after the due date {due}.", "unplanned_note": "This order is not planned yet; press «Arrange» on the production schedule or move it to a day.",
        "late_count": "Late orders", "day_list": "Day list", "no_orders": "No open orders.", "open_schedule": "Open the production schedule", "date_bad": "Enter a valid date.",
    },
}


def _date(raw):
    try:
        return date.fromisoformat(str(raw or "").strip()) if str(raw or "").strip() else None
    except ValueError:
        return None


def _id(raw):
    return int(raw) if str(raw or "").isdigit() else 0


def _scoped_orders():
    return entity_scope.scope(ProductionOrder.objects, "location__entity")


def schedule_context(request, order, lang, late_reason=""):
    """The order page's plan: the stage rows, and for someone who may produce, the move forms (other centres of the kind)."""

    words = WORDS[lang]
    rows = schedule.order_plan(order, lang)
    can_move = user_has_permission(request.user, PRODUCE) and order.status in schedule.OPEN
    if can_move:
        kinds = {row["centre"].kind for row in rows if row["centre"] and row["open"]}
        others = {kind: list(workcentres.scoped().filter(active=True, kind=kind)) for kind in kinds}
        for row in rows:
            row["choices"] = others.get(row["centre"].kind, []) if row["centre"] and row["open"] else []
    last, due = max((row["end"] for row in rows if row["end"]), default=None), schedule.effective_due(order)
    # Manufacturing video finding F3: a finished or cancelled order is never «not planned yet; press Arrange» (arrange only takes
    # open orders), so the note is for an open order with no planned day only.
    planned = any(row["start"] for row in rows) or order.status not in schedule.OPEN
    return {"plan_rows": rows, "plan_words": words, "can_move": can_move, "plan_planned": planned,
            "plan_last": last, "plan_due": due,
            "plan_late_text": (words["order_late_overdue"] if late_reason == "overdue"
                               else words["order_late_planned"].format(end=last.isoformat(), due=due.isoformat()) if late_reason == "planned" and last and due else "")}


def _back(request, lang, order=None):
    if request.POST.get("back") == "order" and order is not None:
        return redirect(f"{reverse('manufacturing:order', args=[order.pk])}?lang={lang}")
    return redirect(f"{reverse('manufacturing:schedule')}?lang={lang}")


@require_permission(VIEW)
@require_http_methods(["GET", "POST"])
def schedule_screen(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        if not user_has_permission(request.user, PRODUCE):
            raise PermissionDenied("The production schedule needs inventory.adjust_stock.")
        action = request.POST.get("action")
        order = get_object_or_404(_scoped_orders(), pk=_id(request.POST.get("order"))) if action in ("move", "rank") else None
        try:
            if action == "arrange":
                start = _date(request.POST.get("start")) if request.POST.get("start") else timezone.localdate()
                if start is None:
                    raise ValidationError(words["date_bad"])
                done = schedule.arrange(start, request.user, lang)
                messages.success(request, words["arranged"].format(**done))
            elif action == "move":
                day = _date(request.POST.get("day"))
                if request.POST.get("day") and day is None:
                    raise ValidationError(words["date_bad"])
                centre = get_object_or_404(workcentres.scoped(), pk=_id(request.POST.get("centre"))) if request.POST.get("centre") else None
                result = schedule.move_order(order, request.user, day=day, step_index=int(request.POST["step"]) if str(request.POST.get("step", "")).isdigit() else None,
                                             centre=centre, lang=lang)
                messages.success(request, words["moved"])
                for warning in result["warnings"]:
                    messages.warning(request, words["warning"].format(centre=warning["centre"].name(lang), day=warning["day"].isoformat(),
                                                                       minutes=f"{warning['minutes'].normalize():f}", capacity=f"{warning['capacity'].normalize():f}"))
            elif action == "rank":
                schedule.set_rank(order, request.POST.get("rank"), request.user, lang)
                messages.success(request, words["rank_saved"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return _back(request, lang, order)
    start = _date(request.GET.get("start")) or timezone.localdate()
    centre = get_object_or_404(workcentres.scoped(), pk=_id(request.GET.get("centre"))) if request.GET.get("centre") else None
    data = schedule.grid(start, schedule.DAYS, centre, lang)
    return render(request, "manufacturing/schedule.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "data": data, "centre": centre,
        "centres": workcentres.scoped().filter(active=True).order_by("code"), "can_move": user_has_permission(request.user, PRODUCE),
        "prev": start - timedelta(days=schedule.DAYS), "next": start + timedelta(days=schedule.DAYS), "today": timezone.localdate()})
