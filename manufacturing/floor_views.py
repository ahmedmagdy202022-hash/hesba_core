"""Manufacturing F8 (HG-140) screens: the worker's floor screen («شغلي»), the upload of a defect photo, and the defect photos
shown on the order page. No cost is read or shown here."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from permissions.decorators import require_any_permission
from permissions.services import user_has_permission

from . import floor
from .views import _lang

WORDS = {
    "ar": {
        "title": "شغلي", "intro": "المراحل اللي عليك دلوقتي، والأقرب في ميعاده الأول.",
        "empty": "مفيش مراحل عليك دلوقتي.", "centres": "مراكز العمل اللي عليك",
        "quantity": "الكمية", "stage_of": "المرحلة {n} من {count} مراحل", "due": "مطلوب يوم", "late": "متأخر", "centre": "مركز العمل",
        "ready": "لسه مبدأتش", "running": "شغال", "waiting": "مستني",
        "start": "ابدأ", "done": "خلصت", "started_at": "بدأت الساعة",
        "good": "السليم", "defect": "المعيب", "note": "ملاحظة (اختياري)",
        "good_hint": "سيب السليم فاضي لو كل الباقي سليم.",
        "first_hint": "لما تسجّل إن المرحلة دي خلصت، خامات الأمر بتتصرف من المخزن.",
        "last_hint": "لو السليم طلع أكتر من كمية الأمر، اكتب السبب في الملاحظة.",
        "photo": "صوّر العيب (اختياري)", "photo_alt": "صورة عيب", "photo_open": "افتح الصورة", "photos": "صور العيوب",
        "photos_hint": "الصور اللي اتاخدت من شاشة العامل، ومحفوظة مع الأمر.", "delete": "امسح", "added_by": "صوّرها",
        "started_ok": "المرحلة بدأت.", "done_ok": "المرحلة اتسجلت.", "open_floor": "شاشة العامل",
    },
    "en": {
        "title": "My work", "intro": "The stages on you now, the soonest due first.",
        "empty": "No stages are on you right now.", "centres": "Your work centres",
        "quantity": "Quantity", "stage_of": "Stage {n} of {count}", "due": "Due on", "late": "Late", "centre": "Work centre",
        "ready": "Not started", "running": "In progress", "waiting": "Waiting",
        "start": "Start", "done": "Done", "started_at": "Started at",
        "good": "Good", "defect": "Defective", "note": "Note (optional)",
        "good_hint": "Leave Good empty when all the rest is good.",
        "first_hint": "When you record this stage as done, the order's materials are issued from stock.",
        "last_hint": "If good units are more than the order, write the reason in the note.",
        "photo": "Photograph the defect (optional)", "photo_alt": "Photo of a defect", "photo_open": "Open the photo", "photos": "Defect photos",
        "photos_hint": "Photos taken on the floor screen and kept with the order.", "delete": "Delete", "added_by": "Taken by",
        "started_ok": "The stage has started.", "done_ok": "The stage was recorded.", "open_floor": "Floor screen",
    },
}

#: the attachments script's own messages (uploading, network...), as the other attachment cards word them
ATT_MESSAGES = {
    "ar": {"failed": "حصلت مشكلة، جرّب تاني.", "network": "مفيش اتصال دلوقتي، جرّب تاني.", "uploading": "جاري الرفع…", "uploaded": "اتحفظ.",
           "deleting": "جاري المسح…", "confirm": "تمسح الصورة دي؟"},
    "en": {"failed": "Something went wrong, try again.", "network": "No connection right now, try again.", "uploading": "Uploading…", "uploaded": "Saved.",
           "deleting": "Deleting…", "confirm": "Delete this photo?"},
}


def _photo_rows(rows, lang):
    return [{"row": row, "url": f"{reverse('attachments:file', args=[row.pk])}?lang={lang}",
             "delete_url": f"{reverse('attachments:delete', args=[row.pk])}?lang={lang}"} for row in rows]


@require_any_permission(floor.RECORD, floor.PRODUCE)
def floor_screen(request):
    lang = _lang(request)
    words = WORDS[lang]
    error, error_order, posted = "", None, {}
    if request.method == "POST":
        raw = request.POST.get("order", "")
        order = get_object_or_404(floor.scoped_orders(), pk=raw if raw.isdigit() else 0)  # another entity's order is not found
        action, stage = request.POST.get("action"), request.POST.get("stage")
        try:
            if action == "start":
                floor.start(order, request.user, stage=stage, lang=lang)
                messages.success(request, words["started_ok"])
            elif action == "done":
                floor.done(order, request.user, stage=stage, good=request.POST.get("good"), defect=request.POST.get("defect"),
                           note=request.POST.get("note", ""), lang=lang)
                messages.success(request, words["done_ok"])
            else:
                raise ValidationError(floor.MESSAGES[lang]["moved"])
        except ValidationError as exc:
            from settings_core.ui_messages import translate

            error, error_order, posted = translate(" ".join(exc.messages), lang), order.pk, request.POST
        else:
            return redirect(f"{reverse('manufacturing:floor')}?lang={lang}")
    cards = floor.cards(request.user, lang, timezone.localdate())
    running = [card["pk"] for card in cards if card["state"] == floor.RUNNING]
    photos = floor.photos(running, request.user) if running else {}
    for card in cards:
        card["stage_of"] = words["stage_of"].format(n=card["stage_number"], count=card["stage_count"])
        card["photos"] = _photo_rows(photos.get(card["pk"], []), lang)
        card["photo_url"] = f"{reverse('manufacturing:floor_photo', args=[card['pk']])}?lang={lang}"
        card["error"] = error if card["pk"] == error_order else ""
        card["posted"] = posted if card["pk"] == error_order else {}
    shown = {card["pk"] for card in cards}
    return render(request, "manufacturing/floor.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "cards": cards, "att": ATT_MESSAGES[lang],
        "centres": [centre.name(lang) for centre in floor.my_centres(request.user)],
        # a refusal about an order that is no longer on this user's screen (it moved on, or to another centre) is still said
        "error": error if error_order not in shown else "",
    })


@csrf_exempt  # CSRF is checked inside, after the upload cap handler is installed (the attachments views' own rule)
@require_POST
def photo_upload(request, pk):
    """A photo of a defect on a production order. Field: ``file``. Answers JSON, as every attachment upload."""

    from attachments import services as attachments, views as attachment_views

    lang = "en" if request.GET.get("lang") == "en" else "ar"  # not ``_lang``: reading POST here would close the upload handlers before the cap handler is installed
    if not floor.can_use(request.user):
        return attachment_views._fail(lang, "denied", 403)
    try:
        attachments.get_owner(floor.ORDER_OWNER, pk, request.user)
    except attachments.OwnerNotFound:
        return attachment_views._fail(lang, "not_found", 404)
    except PermissionDenied:
        return attachment_views._fail(lang, "denied", 403)
    return attachment_views._capped_upload(request, pk, floor.ORDER_OWNER)


def order_photos_context(request, order, lang):
    """The order page's «صور العيوب» section: the photos taken on the floor, and whether this user may remove one."""

    rows = floor.photos([order.pk], request.user).get(order.pk, [])
    return {"fw": WORDS[lang], "floor_att": ATT_MESSAGES[lang], "floor_photos": _photo_rows(rows, lang),
            "floor_photos_delete": order.status in floor.OPEN and user_has_permission(request.user, floor.PRODUCE),
            "can_floor": floor.can_use(request.user)}
