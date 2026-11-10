"""S8 screens: the campaign list, the composer (live audience count and message preview) and a queued campaign."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from permissions.decorators import require_any_permission
from reports.navigation import CUSTOMER_AUDIENCE

from . import campaigns, services
from .models import Campaign, CampaignAudience, CampaignStatus, MessageKind

WORDS = {
    "ar": {
        "title": "حملات الولاء والرسائل", "intro": "اكتب رسالة لشريحة من العملاء وحطها في صندوق الرسائل. مفيش حاجة بتتبعت من هنا: كل رسالة بتتبعت من الصندوق بضغطة واتساب، أو أوتوماتيك لما واتساب بيزنس يتوصل.",
        "new": "حملة جديدة", "outbox": "افتح صندوق الرسائل", "campaigns": "الحملات", "none": "مفيش حملات لسه.", "name": "اسم الحملة", "audience": "الشريحة", "day": "اليوم", "status": "الحالة", "count": "الرسائل",
        "by": "اتعملت بواسطة", "draft": "مسودة", "queued": "في الصندوق", "done": "خلصت", "open": "افتح", "delete": "امسح المسودة", "delete_confirm": "تمسح المسودة دي؟",
        "step1": "اختار مين هيستلم", "step2": "الرسالة", "text": "نص الرسالة", "insert": "أدرج:", "chars": "حرف", "category": "الفئة", "choose": "اختار...", "when": "اليوم اللي تبدأ فيه",
        "who": "مين هيستلم بالظبط", "reach": "عميل هيستلموا الرسالة", "match": "مطابقين للشريحة", "no_phone": "ناقص: من غير رقم موبايل صالح", "dups": "ناقص: رقم متكرر (بيوصل مرة واحدة)",
        "preview": "معاينة الرسالة", "preview_note": "المعاينة بأول عميل في الشريحة.", "save": "احفظ كمسودة", "saved": "اتحفظت المسودة.",
        "cta": "حط {n} رسالة في صندوق الرسائل", "confirm": "هتتحط {n} رسالة في صندوق الرسائل ومينفعش تتعدل بعد كده. تكمّل؟",
        "fine1": "مفيش حاجة بتتبعت دلوقتي. كل رسالة بتتبعت من الصندوق بضغطة واتساب، أو أوتوماتيك لما واتساب بيزنس يتوصل.",
        "fine2": "العميل مابيوصلوش نفس الحملة مرتين، حتى لو في يوم تاني.",
        "fine3": "العميل ممكن يوصله أكتر من حملة؛ مفيش ميزة لإلغاء الاشتراك في بيانات العميل لسه.",
        "cap_under": "العدد تحت الحد اليومي ({cap} رسالة). كل الرسائل هتتحط في الصندوق النهاردة.",
        "cap_under_later": "العدد تحت الحد اليومي ({cap} رسالة). كل الرسائل هتتحط يوم {day}.",
        "cap_over": "الحد اليومي للحملات {cap} رسالة. هنحط {first} في الصندوق يوم {day} والباقي ({rest}) على الأيام اللي بعده.",
        "queued_title": "اتحطت {n} رسالة في صندوق الرسائل", "queued_note": "لسه ماتبعتش ولا رسالة. كل رسالة بتتبعت من الصندوق بضغطة واتساب، أو أوتوماتيك لما واتساب بيزنس يتوصل.",
        "schedule": "هتظهر في الصندوق إمتى", "details": "تفاصيل الحملة", "today": "النهاردة", "tomorrow": "بكرة", "sent": "اتبعت", "waiting": "مستني", "text_k": "الرسالة",
        "settings": "إعدادات رسائل الحملات", "on": "رسائل الحملات شغالة", "wa_template": "اسم القالب المتوافق عليه عند Meta (فاضي: بضغطة بس)",
        "cap_k": "الحد اليومي", "cap_hint": "الحد اليومي ({cap} رسالة) بيتظبط من إعداد الخادم MESSAGING_CAMPAIGN_DAILY_CAP، ومشترك بين كل حملات الكيان.",
        "save_settings": "حفظ الإعدادات", "deleted": "اتمسحت المسودة.", "loyalty_off": "الولاء مقفول في الإعدادات.", "no_cat": "مفيش فئات لسه.", "off_note": "رسائل الحملات مقفولة، ومفيش حاجة هتتحط في الصندوق.",
        "placeholders": "الكلمات اللي بتتغير لوحدها: {name} الاسم، {company} اسم الشركة، {points} نقاط الولاء، {days} الأيام من آخر شراء.", "excluded": "مستبعدين",
        "aud": {
            "all": ("كل العملاء", "أي عميل ليه رقم موبايل", "", ()),
            "points": ("عملاء نقاطهم كتير", "عندهم نقاط مستنياهم", "نقاطهم على الأقل", (100, 300, 500)),
            "inactive": ("عملاء بطّلوا يشتروا", "ماشتروش من فترة", "آخر شراء من أكتر من (يوم)", (30, 60, 90)),
            "category": ("اشتروا من فئة", "اشتروا من الفئة دي مؤخرًا", "اشتروا خلال آخر (يوم)", (30, 60, 90)),
            "lapsed": ("محتاجين يكرروا", "اشتروا الفئة دي بس من فترة", "آخر شراء للفئة من أكتر من (يوم)", (30, 60, 90)),
        },
    },
    "en": {
        "title": "Loyalty and message campaigns", "intro": "Write one message to a slice of your customers and put it on the message outbox. Nothing is sent from here: each message goes from the outbox with one WhatsApp click, or by itself once WhatsApp Business is connected.",
        "new": "New campaign", "outbox": "Open the outbox", "campaigns": "Campaigns", "none": "No campaigns yet.", "name": "Campaign name", "audience": "Audience", "day": "Day", "status": "Status", "count": "Messages",
        "by": "Made by", "draft": "Draft", "queued": "On the outbox", "done": "Done", "open": "Open", "delete": "Delete draft", "delete_confirm": "Delete this draft?",
        "step1": "Choose who receives it", "step2": "The message", "text": "Message text", "insert": "Insert:", "chars": "characters", "category": "Category", "choose": "Choose...", "when": "Start on",
        "who": "Exactly who receives it", "reach": "customers will get the message", "match": "Matching the audience", "no_phone": "Minus: no valid mobile number", "dups": "Minus: repeated number (it gets one)",
        "preview": "Message preview", "preview_note": "Previewed with the first customer of the audience.", "save": "Save as draft", "saved": "Draft saved.",
        "cta": "Put {n} messages on the outbox", "confirm": "{n} messages will be put on the outbox and can no longer be edited. Continue?",
        "fine1": "Nothing is sent now. Each message goes from the outbox with one WhatsApp click, or by itself once WhatsApp Business is connected.",
        "fine2": "A customer never gets the same campaign twice, even on another day.",
        "fine3": "A customer can get more than one campaign; the customer record has no opt-out field yet.",
        "cap_under": "The count is under the daily cap ({cap} messages). All of them reach the outbox today.",
        "cap_under_later": "The count is under the daily cap ({cap} messages). All of them reach the outbox on {day}.",
        "cap_over": "The daily cap for campaigns is {cap} messages. {first} reach the outbox on {day} and the rest ({rest}) on the days after.",
        "queued_title": "{n} messages are on the outbox", "queued_note": "No message has gone yet. Each goes from the outbox with one WhatsApp click, or by itself once WhatsApp Business is connected.",
        "schedule": "When they reach the outbox", "details": "Campaign details", "today": "Today", "tomorrow": "Tomorrow", "sent": "Sent", "waiting": "Waiting", "text_k": "Message",
        "settings": "Campaign message settings", "on": "Campaign messages are on", "wa_template": "The template Meta approved (blank: by a click only)",
        "cap_k": "Daily cap", "cap_hint": "The daily cap ({cap} messages) is the server setting MESSAGING_CAMPAIGN_DAILY_CAP, shared by all the entity's campaigns.",
        "save_settings": "Save settings", "deleted": "Draft deleted.", "loyalty_off": "Loyalty is off in Settings.", "no_cat": "No categories yet.", "off_note": "Campaign messages are switched off; nothing will reach the outbox.",
        "placeholders": "Words filled in by themselves: {name} the name, {company} your company, {points} the loyalty points, {days} the days since the last purchase.", "excluded": "Excluded",
        "aud": {
            "all": ("Every customer", "Anyone with a mobile number", "", ()),
            "points": ("Customers with many points", "They have points waiting", "At least this many points", (100, 300, 500)),
            "inactive": ("Customers who stopped buying", "No purchase for a while", "Last purchase more than (days) ago", (30, 60, 90)),
            "category": ("Bought from a category", "Bought from this category lately", "Bought within the last (days)", (30, 60, 90)),
            "lapsed": ("Due to buy again", "Bought this category, but a while ago", "Last bought the category more than (days) ago", (30, 60, 90)),
        },
    },
}
AUDIENCE_ORDER = (CampaignAudience.ALL, CampaignAudience.POINTS, CampaignAudience.INACTIVE, CampaignAudience.CATEGORY, CampaignAudience.LAPSED)


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(getattr(exc, "messages", [str(exc)])), lang)


def _url(name, lang, *args):
    return f"{reverse(name, args=args)}?lang={lang}"


def _day_label(day, today, words):
    if day == today:
        return words["today"]
    if (day - today).days == 1:
        return words["tomorrow"]
    return day.isoformat()


def _audience_label(campaign, words):
    title = words["aud"][campaign.audience][0]
    if campaign.audience in campaigns.CATEGORY_AUDIENCES and campaign.category_id:
        title += f" · {campaign.category.name_ar or campaign.category.category_code}"
    return f"{title} · {campaign.threshold}" if campaign.threshold else title


def cap_note(entity, count, start, lang, today):
    """The sentence about the daily cap and whether it is exceeded, for a campaign of ``count`` messages."""

    words = WORDS[lang]
    cap = campaigns.daily_cap()
    days = campaigns.plan(entity, count, start, cap) if count else []
    if len(days) > 1:
        return words["cap_over"].format(cap=cap, first=days[0][1], day=_day_label(days[0][0], today, words), rest=count - days[0][1]), True
    if days and days[0][0] != today:
        return words["cap_under_later"].format(cap=cap, day=_day_label(days[0][0], today, words)), False
    return words["cap_under"].format(cap=cap), False


def _draft_from(data, entity, today, lang):
    """An unsaved campaign from form values, or ``(None, error)``."""

    try:
        fields = campaigns.clean(data, lang, today)
    except ValidationError as exc:
        return None, _error(exc, lang)
    return Campaign(entity=entity, **fields), ""


def _summary(draft, entity, lang, today):
    """What the composer shows beside the form for ``draft``: counts, preview and the cap note."""

    result = campaigns.audience(draft, today)
    note, over = cap_note(entity, result.count, campaigns.start_day(draft, today), lang, today)
    return {"count": result.count, "match": result.match, "no_phone": result.no_phone, "duplicates": result.duplicates,
            "preview": campaigns.preview(draft, result, lang, today), "cap_note": note, "cap_over": over}


@require_any_permission(*CUSTOMER_AUDIENCE)
def audience_count(request):
    """The composer's live numbers (counts and one preview only; never the customers)."""

    lang = _lang(request)
    today = timezone.localdate()
    campaign = None
    if (request.GET.get("campaign") or "").isdecimal():
        campaign = campaigns.visible(request.user).filter(pk=int(request.GET["campaign"])).first()
    entity = campaign.entity if campaign and campaign.entity_id else campaigns.new_entity()
    draft, error = _draft_from({**request.GET.dict(), "name": request.GET.get("name") or "-"}, entity, today, lang)  # the numbers do not wait for a name
    if draft is None:
        return JsonResponse({"ok": False, "error": error})
    data = _summary(draft, entity, lang, today)
    data.update(ok=True, cta=WORDS[lang]["cta"].format(n=data["count"]), confirm=WORDS[lang]["confirm"].format(n=data["count"]))
    return JsonResponse(data)


@require_any_permission(*CUSTOMER_AUDIENCE)
def campaign_list(request):
    lang = _lang(request)
    words = WORDS[lang]
    user = request.user
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") != "settings":
                raise Http404("Unknown action.")
            campaigns.save_settings(request.POST, user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            flash.success(request, words["saved"])
            return redirect(_url("messaging:campaigns", lang))
    rows = list(campaigns.visible(user).select_related("category", "created_by")[:200])
    campaigns.sync_status(rows)
    for row in rows:
        row.audience_label = _audience_label(row, words)
        row.status_label = words[row.status]
    template = services.template(MessageKind.CAMPAIGN)
    return render(request, "messaging/campaigns.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "rows": rows, "can_edit": campaigns.can_edit(user),
        "settings_row": template, "cap": campaigns.daily_cap(), "error": error, "outbox_url": _url("messaging:outbox", lang),
        "new_url": _url("messaging:campaign_new", lang), "cap_hint": words["cap_hint"].format(cap=campaigns.daily_cap())})


def _segments(words, selected):
    return [{"value": value, "title": words["aud"][value][0], "desc": words["aud"][value][1], "label": words["aud"][value][2], "chips": words["aud"][value][3],
             "selected": value == selected} for value in AUDIENCE_ORDER]


def _composer(request, campaign=None):
    """The composer for a new campaign or an unqueued draft. Saving and queuing go through ``campaigns``."""

    from master_data.models import Category

    lang = _lang(request)
    words = WORDS[lang]
    user = request.user
    today = timezone.localdate()
    error = ""
    if not campaigns.can_edit(user):  # a viewer reads the list and queued campaigns; the composer is for whoever edits the outbox wording
        raise PermissionDenied("Campaigns need settings.manage_settings.")
    if request.method == "POST":
        try:
            saved = campaigns.save_draft(request.POST, user, lang, campaign, today)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            if request.POST.get("action") != "queue":
                flash.success(request, words["saved"])
                return redirect(_url("messaging:campaign", lang, saved.pk))
            try:
                campaigns.queue(saved, user, lang, today)
            except ValidationError as exc:
                flash.error(request, _error(exc, lang))  # the draft is kept; the screen shows why nothing was queued
                return redirect(_url("messaging:campaign", lang, saved.pk))
            return redirect(_url("messaging:campaign", lang, saved.pk))
    if request.method == "POST":
        form = request.POST
    elif campaign is not None:
        form = {"name": campaign.name, "text": campaign.text, "audience": campaign.audience, "threshold": campaign.threshold or "",
                "category": campaign.category_id or "", "scheduled_day": campaign.scheduled_day.isoformat()}
    else:
        form = {"name": "", "text": services.DEFAULT_TEXTS_EN[MessageKind.CAMPAIGN] if lang == "en" else services.DEFAULT_TEXTS[MessageKind.CAMPAIGN],
                "audience": CampaignAudience.ALL, "threshold": "", "category": "", "scheduled_day": today.isoformat()}
    selected = form.get("audience") if form.get("audience") in CampaignAudience.values else CampaignAudience.ALL
    entity = campaign.entity if campaign is not None and campaign.entity_id else campaigns.new_entity()
    values = form.dict() if hasattr(form, "dict") else dict(form)
    draft, _err = _draft_from({**values, "name": values.get("name") or "-"}, entity, today, lang)  # the numbers do not wait for a name
    summary = _summary(draft, entity, lang, today) if draft is not None else {"count": 0, "match": 0, "no_phone": 0, "duplicates": 0, "preview": "", "cap_note": "", "cap_over": False}
    return render(request, "messaging/campaign_composer.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["new"] if campaign is None else campaign.name, "campaign": campaign,
        "form": form, "segments": _segments(words, selected), "selected": selected, "categories": list(Category.objects.order_by("category_code")), "summary": summary,
        "cta": words["cta"].format(n=summary["count"]), "confirm": words["confirm"].format(n=summary["count"]), "error": error, "can_edit": campaigns.can_edit(user),
        "kind_on": campaigns.active(), "placeholders": campaigns.PLACEHOLDERS, "audience_url": reverse("messaging:campaign_audience"), "list_url": _url("messaging:campaigns", lang),
        "needs_param": selected != CampaignAudience.ALL, "needs_category": selected in campaigns.CATEGORY_AUDIENCES,
        "chips": words["aud"][selected][3], "param_label": words["aud"][selected][2]})


@require_any_permission(*CUSTOMER_AUDIENCE)
def campaign_new(request):
    return _composer(request)


@require_any_permission(*CUSTOMER_AUDIENCE)
def campaign_detail(request, pk):
    campaign = campaigns.visible(request.user).select_related("category", "created_by", "queued_by").filter(pk=pk).first()
    if campaign is None:
        if Campaign.objects.filter(pk=pk).exists():
            raise Http404("Another entity's campaign.")
        raise Http404("No such campaign.")
    if campaign.status == CampaignStatus.DRAFT:
        return _composer(request, campaign)
    lang = _lang(request)
    words = WORDS[lang]
    today = timezone.localdate()
    campaigns.sync_status([campaign])
    schedule = [{"day": row["day"], "label": _day_label(row["day"], today, words), "iso": row["day"].isoformat(), "n": row["n"], "sent": row["sent"], "waiting": row["waiting"]}
                for row in campaigns.schedule_of(campaign)]
    details = [(words["name"], campaign.name), (words["audience"], _audience_label(campaign, words)), (words["day"], campaign.scheduled_day.isoformat()),
               (words["by"], (campaign.created_by.get_username() if campaign.created_by else "")), (words["cap_k"], campaigns.daily_cap()),
               (words["text_k"], campaign.text)]
    return render(request, "messaging/campaign_queued.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": campaign.name, "campaign": campaign, "schedule": schedule, "details": details,
        "status_label": words[campaign.status], "outbox_url": _url("messaging:outbox", lang), "new_url": _url("messaging:campaign_new", lang), "list_url": _url("messaging:campaigns", lang),
        "title": words["queued_title"].format(n=campaign.queued_count)})


@require_any_permission(*CUSTOMER_AUDIENCE)
def campaign_delete(request, pk):
    lang = _lang(request)
    if request.method != "POST":
        raise Http404("POST only.")
    campaign = get_object_or_404(Campaign, pk=pk)
    try:
        campaigns.delete_draft(campaign, request.user, lang)
    except ValidationError as exc:
        flash.error(request, _error(exc, lang))
        return redirect(_url("messaging:campaign", lang, pk))
    flash.success(request, WORDS[lang]["deleted"])
    return redirect(_url("messaging:campaigns", lang))
