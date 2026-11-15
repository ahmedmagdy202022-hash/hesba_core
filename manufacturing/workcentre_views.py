"""Manufacturing F6a (HG-133) screens: «مراكز العمل» (list, 14-day load, form) and the routing helpers of the recipe page."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.display_labels import choice_label

from . import workcentres
from .models import WorkCentre, WorkCentreKind
from .views import COST, RECIPES, VIEW, _lang

WORDS = {
    "ar": {
        "title": "مراكز العمل", "intro": "الماكينات والخطوط وترابيزات الشغل اليدوي: كل مركز ليه ساعات عمل في اليوم وسعر ساعة، وبتتحط عليه مراحل الوصفة.",
        "back": "التصنيع", "new": "مركز عمل جديد", "edit": "تعديل مركز العمل", "code": "الكود", "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي (اختياري)",
        "kind": "النوع", "asset": "الماكينة في سجل الأصول (اختياري)", "no_asset": "—", "hours": "ساعات العمل في اليوم", "rate": "سعر الساعة (شامل العمالة والكهرباء وإهلاك الماكينة)",
        "rate_short": "سعر الساعة", "active": "شغال", "save": "حفظ", "saved": "اتحفظ مركز العمل.", "empty": "لسه مفيش مراكز عمل.", "inactive": "متوقف",
        "rate_hint": "سعر واحد شامل للساعة. الإهلاك داخل فيه، فمتسجلوش تاني على الأمر.", "no_rate_perm": "سعر الساعة ظاهر لصاحب صلاحية رؤية التكلفة بس.",
        "load": "الحِمل على مراكز العمل — الـ 14 يوم الجايين", "load_hint": "دقايق المراحل المتبقية على الأوامر المفتوحة، قصاد طاقة اليوم: على اليوم المخطط للمرحلة لو اتعملها «رتّب» أو نقل، وإلا على يوم الاستحقاق. الأمر المتأخر بيتحسب النهارده. الإجازة طاقتها صفر.",
        "date": "اليوم", "planned": "دقايق مخططة", "capacity": "الطاقة (دقيقة)", "state": "الحالة", "over": "فوق الطاقة", "ok": "تمام", "holiday": "إجازة",
        "unscheduled": "من غير تاريخ استحقاق ولا ترتيب", "later": "بعد الـ 14 يوم", "min": "دقيقة", "no_load": "مفيش مراكز عمل شغالة.", "assets_none": "—",
        "routing": "المراحل", "routing_hint": "مراحل الوصفة بالترتيب، كل مرحلة على مركز عمل. الأوامر الجديدة بتاخد المراحل دي؛ لو مفيش مراحل بتاخد مراحل المصنع المعتادة. الأوامر المفتوحة متتغيرش.",
        "stage": "المرحلة", "stage_ar": "اسم المرحلة", "stage_en": "الاسم بالإنجليزي", "centre": "مركز العمل", "per_batch": "دقايق الدفعة", "setup": "دقايق التجهيز",
        "routing_save": "حفظ المراحل", "routing_saved": "اتحفظت المراحل.", "routing_none": "الوصفة دي من غير مراحل: أوامرها بتاخد مراحل المصنع المعتادة.", "routing_other": "مراحل الوصفة دي متسجّلة على مراكز عمل كيان تاني، وتتعدّل من الكيان ده. الأوامر هنا بتاخد مراحل المصنع المعتادة.",
        "row": "صف", "centres_link": "مراكز العمل", "no_centres": "ضيف مركز عمل الأول من صفحة مراكز العمل.", "routing_cost": "تكلفة الدفعة بسعر المراكز",
        "routing_view_only": "تعديل المراحل لصاحب الصلاحية بس.",
    },
    "en": {
        "title": "Work centres", "intro": "Machines, lines and manual tables: each has hours it works in a day and an hourly rate, and takes the stages of a recipe.",
        "back": "Manufacturing", "new": "New work centre", "edit": "Edit the work centre", "code": "Code", "name_ar": "Arabic name", "name_en": "English name (optional)",
        "kind": "Kind", "asset": "The machine in the asset register (optional)", "no_asset": "—", "hours": "Hours per day", "rate": "Hourly rate (labour, power and the machine's wear, all in)",
        "rate_short": "Hourly rate", "active": "Active", "save": "Save", "saved": "The work centre was saved.", "empty": "No work centres yet.", "inactive": "Inactive",
        "rate_hint": "One all-in rate per hour. The machine's wear is inside it, so do not record it again on the order.", "no_rate_perm": "The hourly rate shows only to those who may see costs.",
        "load": "Load on the work centres — the next 14 days", "load_hint": "The minutes of the stages still ahead on open orders, against the day's capacity: on the stage's planned day when «Arrange» or a move has planned it, otherwise on the order's due date. An overdue order counts today. A holiday has no capacity.",
        "date": "Day", "planned": "Planned minutes", "capacity": "Capacity (min)", "state": "Status", "over": "Over capacity", "ok": "OK", "holiday": "Holiday",
        "unscheduled": "With no due date and not planned", "later": "After the 14 days", "min": "min", "no_load": "No active work centres.", "assets_none": "—",
        "routing": "Stages", "routing_hint": "The recipe's stages in order, each on a work centre. New orders take these stages; with none they take the factory's usual stages. Open orders do not change.",
        "stage": "Stage", "stage_ar": "Stage name", "stage_en": "English name", "centre": "Work centre", "per_batch": "Minutes per batch", "setup": "Setup minutes",
        "routing_save": "Save the stages", "routing_saved": "The stages were saved.", "routing_none": "This recipe has no stages: its orders take the factory's usual stages.", "routing_other": "This recipe's stages are on another entity's work centres and are edited from that entity. Orders here take the factory's usual stages.",
        "row": "Row", "centres_link": "Work centres", "no_centres": "Add a work centre first from the work centres page.", "routing_cost": "Cost of one batch at the centres' rates",
        "routing_view_only": "Only users with the permission can change the stages.",
    },
}


def _ctx(request, **extra):
    lang = _lang(request)
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"],
            "can_manage": user_has_permission(request.user, RECIPES), "can_cost": user_has_permission(request.user, COST), **extra}


@require_permission(VIEW)
def centres(request):
    lang = _lang(request)
    rows = [{"centre": c, "kind_label": choice_label(c, "kind", lang)} for c in workcentres.scoped().select_related("fixed_asset")]
    return render(request, "manufacturing/work_centres.html", _ctx(request, rows=rows, load=workcentres.load(timezone.localdate())))


@require_permission(VIEW)
def centre_form(request, pk=None):
    lang = _lang(request)
    words = WORDS[lang]
    if not user_has_permission(request.user, RECIPES):
        raise PermissionDenied("Work centres need master_data.manage_items.")
    centre = get_object_or_404(workcentres.scoped(), pk=pk) if pk else None
    can_cost = user_has_permission(request.user, COST)
    error = ""
    form = ({"code": centre.code, "name_ar": centre.name_ar, "name_en": centre.name_en, "kind": centre.kind, "fixed_asset": str(centre.fixed_asset_id or ""),
             "hours_per_day": f"{centre.hours_per_day.normalize():f}", "rate_per_hour": f"{centre.rate_per_hour:.2f}", "active": centre.active}
            if centre else {"kind": WorkCentreKind.MACHINE, "hours_per_day": "8", "rate_per_hour": "0", "active": True})
    if request.method == "POST":
        form = {"code": request.POST.get("code", ""), "name_ar": request.POST.get("name_ar", ""), "name_en": request.POST.get("name_en", ""),
                "kind": request.POST.get("kind", ""), "fixed_asset": request.POST.get("fixed_asset", ""), "hours_per_day": request.POST.get("hours_per_day", ""),
                "active": request.POST.get("active") == "on"}
        data = dict(form)
        if can_cost:  # no cost permission, no rate field: the stored rate stays
            form["rate_per_hour"] = data["rate_per_hour"] = request.POST.get("rate_per_hour", "")
        try:
            saved = workcentres.save_work_centre(data, request.user, centre, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('manufacturing:work_centres')}?lang={lang}")
    from fixed_assets.models import FixedAsset

    return render(request, "manufacturing/work_centre_form.html", _ctx(
        request, centre=centre, form=form, error=error, can_cost=can_cost, kinds=[(value, choice_label(WorkCentre(kind=value), "kind", lang)) for value in WorkCentreKind.values],
        assets=FixedAsset.objects.filter(status="active").order_by("code")))


def routing_context(request, recipe, posted=None):
    """The routing section of the recipe page: its rows (the saved steps, or the posted ones after a refusal)."""

    can_cost = user_has_permission(request.user, COST)
    if not workcentres.routing_visible(recipe):  # another entity's machines and rates: neither shown nor replaceable here
        return {"steps": [], "route_rows": [], "route_centres": [], "route_batch_cost": None, "route_other": True, "rwords": WORDS[_lang(request)]}
    steps = list(recipe.steps.select_related("work_centre"))
    if posted is not None:
        rows = posted
    else:
        rows = [{"name_ar": s.name_ar, "name_en": s.name_en, "work_centre": str(s.work_centre_id), "minutes_per_batch": f"{s.minutes_per_batch.normalize():f}",
                 "setup_minutes": f"{s.setup_minutes.normalize():f}"} for s in steps]
    filled = [row for row in rows if any(str(v or "").strip() for v in row.values())] if posted is not None else rows
    rows = (filled + [{}] * workcentres.STEP_ROWS)[:min(max(len(filled) + 2, 4), workcentres.STEP_ROWS)]
    batch = None
    if can_cost and steps:
        from config.money import money_round

        batch = money_round(sum((s.work_centre.rate_per_hour * (s.setup_minutes + s.minutes_per_batch) / 60 for s in steps), 0))
    return {"steps": steps, "route_rows": rows, "route_centres": workcentres.scoped().filter(Q(active=True) | Q(pk__in=[s.work_centre_id for s in steps])), "route_batch_cost": batch, "rwords": WORDS[_lang(request)]}


def posted_rows(request):
    return [{"name_ar": request.POST.get(f"step_name_ar_{i}", ""), "name_en": request.POST.get(f"step_name_en_{i}", ""), "work_centre": request.POST.get(f"step_centre_{i}", ""),
             "minutes_per_batch": request.POST.get(f"step_minutes_{i}", ""), "setup_minutes": request.POST.get(f"step_setup_{i}", "")} for i in range(workcentres.STEP_ROWS)]


def routing_post(request, recipe, lang):
    return workcentres.save_routing(recipe, posted_rows(request), request.user, lang)
