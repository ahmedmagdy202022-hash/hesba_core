"""Manufacturing F10 (HG-141) screens: «صيانة المكن» (what is down, what is due, every work centre), one work centre's
maintenance page (its open job, its plans, its cost month by month, its latest jobs) and one job's page (spare parts,
technicians' bills, the close).

Every rule is in ``manufacturing.maintenance``; the views read, call and render.

* Seeing needs ``inventory.view_stock`` (as every manufacturing screen).
* Opening and closing a job, taking a spare part and returning it need ``inventory.adjust_stock``.
* A plan needs ``master_data.manage_items`` (as the work centre it hangs on).
* Making, linking or removing a technician's bill needs ``purchases.create_purchase_invoice``; the way into the bill
  shows only with ``purchases.view_purchase_invoices``.
* Money (a part's value, a bill's amount, a job's cost, the machine's months) shows only with ``inventory.view_cost``.

Everything is narrowed to the entity being worked in: another entity's work centre or job returns 404."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from master_data.models import Item, Location, Supplier
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import maintenance, workcentres
from .models import MaintenanceKind, MaintenancePart, MaintenancePlan, MaintenancePurchase
from .views import VIEW, _lang, _pick

SEE_PURCHASES = "purchases.view_purchase_invoices"
LATEST_JOBS = 10

WORDS = {
    "ar": {
        "title": "صيانة المكن", "back": "التصنيع", "centres_link": "مراكز العمل",
        "intro": "الصيانة المخططة والأعطال لكل مركز عمل، وتكلفتها شهر بشهر.",
        "tile_down": "مكن متوقف دلوقتي", "tile_due": "صيانة عليها الدور", "tile_open": "أوامر صيانة مفتوحة",
        "down_title": "متوقف دلوقتي", "due_title": "صيانة عليها الدور", "all_title": "كل مراكز العمل",
        "empty_down": "مفيش مكن متوقف دلوقتي.", "empty_due": "مفيش صيانة عليها الدور.", "empty_centres": "لسه مفيش مراكز عمل؛ ضيفها من صفحة مراكز العمل.",
        "centre": "مركز العمل", "state": "الحالة", "state_down": "متوقف", "state_open": "في الصيانة", "state_due": "عليها الدور", "state_ok": "تمام",
        "state_off": "متوقفة", "since": "من", "minutes": "دقيقة", "hours": "ساعة", "so_far": "لحد دلوقتي", "plan": "الخطة", "why": "السبب",
        "due_days": "آخر مرة من {n} يوم", "due_hours": "اشتغل {n} ساعة من آخر صيانة", "plans_due": "خطط عليها الدور",
        "open_page": "افتح صفحة الصيانة",
        # one work centre
        "page_intro": "أمر الصيانة المفتوح، وخطط الصيانة، وتكلفة المكنة شهر بشهر.",
        "open_new": "افتح أمر صيانة", "kind": "النوع", "kind_planned": "صيانة مخططة", "kind_breakdown": "عطل",
        "plan_field": "خطة الصيانة (للصيانة المخططة)", "no_plan": "من غير خطة", "cause": "العطل", "cause_hint": "اكتب اللي حصل في كلمتين. مطلوب للعطل.",
        "opened_at": "وقت التوقف", "opened_hint": "سيبه فاضي لو المكنة واقفة دلوقتي.", "note": "ملاحظات", "open_save": "افتح الأمر",
        "opened_ok": "أمر الصيانة اتفتح.", "down_note": "العطل المفتوح بيشيل طاقة المركز من جدول الإنتاج لحد ما الأمر يتقفل.",
        "plans_title": "خطط الصيانة", "plan_new": "خطة صيانة جديدة", "plan_edit": "تعديل الخطة", "plan_title": "اسم الخطة",
        "every_days": "كل كام يوم", "every_hours": "كل كام ساعة تشغيل", "last_done": "آخر مرة اتعملت", "plan_active": "الخطة شغالة",
        "plan_save": "احفظ الخطة", "plan_saved": "خطة الصيانة اتحفظت.", "plans_empty": "لسه مفيش خطط صيانة للمركز ده.",
        "days_line": "كل {n} يوم", "hours_line": "كل {n} ساعة تشغيل", "next_day": "ميعادها يوم", "ran": "اشتغل", "of_hours": "ساعة من",
        "plan_hint": "ساعات التشغيل بتتحسب من دقايق المراحل المتسجلة على المركز من آخر صيانة.",
        "history_title": "تكلفة المكنة شهر بشهر", "month": "الشهر", "jobs": "أوامر", "downtime": "ساعات التوقف", "parts": "قطع غيار", "bills": "فواتير فنيين",
        "total": "الإجمالي", "history_empty": "لسه مفيش صيانة متسجلة على المركز ده.",
        "history_note": "قطع الغيار بقيمتها وقت ما اتصرفت، وفواتير الفنيين المترحّلة من غير الضريبة ومن غير المرتجع.",
        "no_cost": "التكلفة ظاهرة لصاحب صلاحية رؤية التكلفة بس.",
        "jobs_title": "آخر أوامر الصيانة", "jobs_empty": "لسه مفيش أوامر صيانة اتقفلت.", "view_only": "التسجيل لصاحب الصلاحية بس.",
        # one job
        "job": "أمر الصيانة", "job_open": "مفتوح", "job_closed": "اتقفل", "stopped_at": "وقف", "back_at": "رجع", "down_for": "مدة التوقف",
        "done_by": "اللي عمل الصيانة", "job_cost": "تكلفة الأمر", "back_to_centre": "صفحة صيانة المركز",
        "parts_title": "قطع الغيار", "part": "القطعة", "qty": "الكمية", "from": "من مخزن", "value": "القيمة", "take": "اصرف قطعة غيار",
        "choose_item": "اختار القطعة", "location": "المخزن", "take_save": "اصرف القطعة", "taken_ok": "القطعة اتصرفت من المخزن على أمر الصيانة.",
        "return": "رجّع القطعة للمخزن", "return_reason": "السبب (اختياري)", "return_save": "رجّع القطعة", "returned_ok": "القطعة رجعت المخزن.",
        "part_back": "رجعت المخزن", "parts_empty": "مفيش قطع غيار على الأمر ده.",
        "parts_note": "القطعة بتخرج من المخزن بمتوسط تكلفتها، وبتتسجل مصروف صيانة مش عجز جرد.",
        "parts_closed": "قطع الغيار بتتصرف والأمر مفتوح بس.",
        "bills_title": "فواتير فنيين الصيانة", "bill_make": "فاتورة فني صيانة من برّه", "supplier": "المورّد (فني أو مركز صيانة)", "choose_supplier": "اختار المورّد",
        "amount": "القيمة قبل الضريبة", "bill_date": "تاريخ الفاتورة", "bill_desc": "البيان (اختياري)", "bill_save": "اعمل الفاتورة مسودة",
        "bill_ok": "فاتورة الصيانة {number} اتعملت مسودة؛ رحّلها من شاشة المشتريات.",
        "bill_link": "اربط فاتورة شراء موجودة", "invoice_number": "رقم فاتورة الشراء", "link_save": "اربط الفاتورة", "linked_ok": "الفاتورة اتربطت بأمر الصيانة.",
        "unlink": "شيل الفاتورة من الأمر", "unlinked_ok": "الفاتورة اتشالت من أمر الصيانة.", "withdrawn_ok": "الفاتورة المسودة اتسحبت.",
        "bill_draft": "مسودة", "bill_posted": "اترحّلت", "bill_cancelled": "ملغية", "bill": "الفاتورة", "bill_status": "حالتها",
        "post_hint": "الفاتورة المسودة مبتتحسبش على المكنة لحد ما تترحّل من شاشة المشتريات.", "bills_empty": "مفيش فواتير على الأمر ده.",
        "no_suppliers": "سجّل المورّد الأول من شاشة الموردين.", "open_bill": "افتح الفاتورة",
        "close": "اقفل أمر الصيانة", "closed_at": "وقت الرجوع للشغل", "closed_hint": "سيبه فاضي لو المكنة رجعت دلوقتي.",
        "employee": "موظف من عندنا (اختياري)", "tech": "فني أو مركز صيانة من برّه (اختياري)", "nobody": "—", "close_save": "اقفل الأمر",
        "closed_ok": "أمر الصيانة اتقفل، ومدة التوقف {minutes} دقيقة.",
    },
    "en": {
        "title": "Machine maintenance", "back": "Manufacturing", "centres_link": "Work centres",
        "intro": "Planned maintenance and breakdowns for each work centre, and what they cost month by month.",
        "tile_down": "Machines down now", "tile_due": "Maintenance due", "tile_open": "Open maintenance jobs",
        "down_title": "Down now", "due_title": "Maintenance due", "all_title": "Every work centre",
        "empty_down": "No machine is down right now.", "empty_due": "No maintenance is due.", "empty_centres": "No work centres yet; add them on the work centres page.",
        "centre": "Work centre", "state": "Status", "state_down": "Down", "state_open": "In maintenance", "state_due": "Due", "state_ok": "OK",
        "state_off": "Off", "since": "since", "minutes": "min", "hours": "h", "so_far": "so far", "plan": "Plan", "why": "Why",
        "due_days": "Last done {n} days ago", "due_hours": "Ran {n} hours since the last maintenance", "plans_due": "plans due",
        "open_page": "Open the maintenance page",
        "page_intro": "The open maintenance job, the maintenance plans, and the machine's cost month by month.",
        "open_new": "Open a maintenance job", "kind": "Kind", "kind_planned": "Planned maintenance", "kind_breakdown": "Breakdown",
        "plan_field": "Maintenance plan (for planned maintenance)", "no_plan": "No plan", "cause": "What broke", "cause_hint": "A few words on what happened. Needed for a breakdown.",
        "opened_at": "Stopped at", "opened_hint": "Leave it empty if the machine stopped just now.", "note": "Notes", "open_save": "Open the job",
        "opened_ok": "The maintenance job was opened.", "down_note": "An open breakdown takes the work centre's capacity out of the production schedule until the job is closed.",
        "plans_title": "Maintenance plans", "plan_new": "New maintenance plan", "plan_edit": "Edit the plan", "plan_title": "Plan name",
        "every_days": "Every how many days", "every_hours": "Every how many running hours", "last_done": "Last done on", "plan_active": "The plan is active",
        "plan_save": "Save the plan", "plan_saved": "The maintenance plan was saved.", "plans_empty": "No maintenance plans for this work centre yet.",
        "days_line": "Every {n} days", "hours_line": "Every {n} running hours", "next_day": "Due after", "ran": "Ran", "of_hours": "h of",
        "plan_hint": "Running hours are counted from the stage minutes recorded on the work centre since the last maintenance.",
        "history_title": "The machine's cost month by month", "month": "Month", "jobs": "Jobs", "downtime": "Hours down", "parts": "Spare parts", "bills": "Technicians' bills",
        "total": "Total", "history_empty": "No maintenance is recorded on this work centre yet.",
        "history_note": "Spare parts at the value they left stock at, and posted technicians' bills net of tax and of returns.",
        "no_cost": "Costs show only to those who may see costs.",
        "jobs_title": "Latest maintenance jobs", "jobs_empty": "No maintenance job has been closed yet.", "view_only": "Only users with the permission can record here.",
        "job": "Maintenance job", "job_open": "Open", "job_closed": "Closed", "stopped_at": "Stopped", "back_at": "Back", "down_for": "Downtime",
        "done_by": "Done by", "job_cost": "Cost of the job", "back_to_centre": "The work centre's maintenance page",
        "parts_title": "Spare parts", "part": "Part", "qty": "Quantity", "from": "From", "value": "Value", "take": "Take a spare part",
        "choose_item": "Choose the part", "location": "Location", "take_save": "Take the part", "taken_ok": "The part left stock for the maintenance job.",
        "return": "Return the part to stock", "return_reason": "Reason (optional)", "return_save": "Return the part", "returned_ok": "The part is back in stock.",
        "part_back": "Back in stock", "parts_empty": "No spare parts on this job.",
        "parts_note": "A part leaves stock at its average cost and is booked as maintenance expense, not as a stock-count loss.",
        "parts_closed": "Spare parts are taken only while the job is open.",
        "bills_title": "Technicians' bills", "bill_make": "An outside technician's bill", "supplier": "Supplier (a technician or a service centre)", "choose_supplier": "Choose the supplier",
        "amount": "Amount before tax", "bill_date": "Bill date", "bill_desc": "Description (optional)", "bill_save": "Make the draft bill",
        "bill_ok": "Maintenance bill {number} was made as a draft; post it from the purchases screen.",
        "bill_link": "Link an existing purchase invoice", "invoice_number": "Purchase invoice number", "link_save": "Link the invoice", "linked_ok": "The invoice was linked to the maintenance job.",
        "unlink": "Remove the bill from the job", "unlinked_ok": "The bill was removed from the maintenance job.", "withdrawn_ok": "The draft bill was withdrawn.",
        "bill_draft": "Draft", "bill_posted": "Posted", "bill_cancelled": "Cancelled", "bill": "Bill", "bill_status": "Status",
        "post_hint": "A draft bill does not count on the machine until it is posted from the purchases screen.", "bills_empty": "No bills on this job.",
        "no_suppliers": "Add the supplier on the suppliers screen first.", "open_bill": "Open the bill",
        "close": "Close the maintenance job", "closed_at": "Back at work at", "closed_hint": "Leave it empty if the machine came back just now.",
        "employee": "One of our employees (optional)", "tech": "An outside technician or service centre (optional)", "nobody": "—", "close_save": "Close the job",
        "closed_ok": "The maintenance job was closed; the downtime was {minutes} minutes.",
    },
}


def _translated(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(exc.messages), lang)


def _rights(user):
    return {"can_produce": user_has_permission(user, maintenance.PRODUCE), "can_manage": user_has_permission(user, maintenance.MANAGE),
            "can_cost": user_has_permission(user, maintenance.COST), "can_bill": user_has_permission(user, maintenance.BILL),
            "see_purchases": user_has_permission(user, SEE_PURCHASES)}


def _ctx(request, **extra):
    lang = _lang(request)
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "mw": WORDS[lang], "page_title": WORDS[lang]["title"],
            **_rights(request.user), **extra}


def _local_input(moment):
    """A moment as a ``datetime-local`` field takes it."""

    return timezone.localtime(moment).strftime("%Y-%m-%dT%H:%M")


def kind_label(kind, lang):
    return WORDS[lang]["kind_breakdown" if kind == MaintenanceKind.BREAKDOWN else "kind_planned"]


def plan_row(state, lang):
    """A plan as the pages show it: its rule in words, and why it is due."""

    words, plan = WORDS[lang], state["plan"]
    every = []
    if plan.every_days is not None:
        every.append(words["days_line"].format(n=plan.every_days))
    if plan.every_hours is not None:
        every.append(words["hours_line"].format(n=plan.every_hours))
    reasons = []
    if state["by_days"]:
        reasons.append(words["due_days"].format(n=state["days"]))
    if state["by_hours"]:
        reasons.append(words["due_hours"].format(n=f"{state['hours'].normalize():f}"))
    return {**state, "every": every, "reasons": reasons, "last_input": state["last_day"].isoformat()}


# ---- the list ----

@require_permission(VIEW)
def maintenance_screen(request):
    lang = _lang(request)
    now = timezone.now()
    centres = list(workcentres.scoped().order_by("-active", "code"))
    marks = maintenance.marks(now=now)
    open_jobs = list(maintenance.scoped_jobs().filter(closed_at__isnull=True).select_related("work_centre", "plan").order_by("opened_at"))
    down = [{"job": job, "centre": job.work_centre, "name": job.work_centre.name(lang), "minutes": maintenance.minutes_down(job, now)}
            for job in open_jobs if job.kind == MaintenanceKind.BREAKDOWN]
    due = [{**plan_row(state, lang), "name": state["plan"].work_centre.name(lang)} for state in maintenance.plan_states(now=now) if state["due"]]
    rows = [{"centre": centre, "name": centre.name(lang), **marks[centre.pk]} for centre in centres]
    return render(request, "manufacturing/maintenance.html", _ctx(
        request, down=down, due=due, rows=rows, tiles={"down": len(down), "due": len(due), "open": len(open_jobs)}))


# ---- one job, as the pages show it ----

def job_context(request, job, lang, rights=None):
    """What the job card needs: the job, its parts and bills, what the person may do, and the lists its forms offer."""

    rights = rights or _rights(request.user)
    now = timezone.now()
    is_open = job.closed_at is None
    parts, bills = maintenance.part_rows(job), maintenance.bill_rows(job)
    for row in bills:
        row["url"] = reverse("purchases:detail", args=[row["invoice"].pk]) if rights["see_purchases"] else ""
    card = {"job": job, "open": is_open, "kind_label": kind_label(job.kind, lang), "minutes": maintenance.minutes_down(job, now), "parts": parts, "bills": bills,
            "cost": maintenance.job_cost(job) if rights["can_cost"] else None, "has_draft": any(row["draft"] for row in bills),
            "now": _local_input(now), "opened_input": _local_input(job.opened_at), "today": timezone.localdate().isoformat(),
            "url": reverse("manufacturing:maintenance_job", args=[job.pk]), "items": [], "locations": [], "suppliers": [], "employees": []}
    if is_open and rights["can_produce"]:
        from staff.models import Employee

        card["items"] = list(Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name")[:500])
        card["locations"] = list(maintenance.places(job.work_centre))
        card["employees"] = list(Employee.objects.filter(active=True).order_by("name")[:300])
    if rights["can_bill"] or (is_open and rights["can_produce"]):
        card["suppliers"] = list(Supplier.objects.filter(active=True).order_by("name")[:300])
    return card


# ---- one work centre ----

@require_permission(VIEW)
@require_http_methods(["GET", "POST"])
def centre_page(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    centre = get_object_or_404(workcentres.scoped(), pk=pk)
    rights = _rights(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action not in ("open", "plan"):
            raise Http404("Unknown maintenance action.")
        needed = maintenance.MANAGE if action == "plan" else maintenance.PRODUCE
        if not user_has_permission(request.user, needed):
            raise PermissionDenied(f"This maintenance action needs {needed}.")
        try:
            if action == "open":
                raw = str(request.POST.get("plan", ""))
                plan = MaintenancePlan.objects.filter(pk=raw, work_centre=centre).first() if raw.isdigit() else None
                maintenance.open_job(centre, request.user, kind=request.POST.get("kind"), plan=plan, opened_at=request.POST.get("opened_at"),
                                     cause=request.POST.get("cause", ""), note=request.POST.get("note", ""), lang=lang)
                done = words["opened_ok"]
            else:
                raw = str(request.POST.get("plan", ""))
                plan = get_object_or_404(MaintenancePlan, pk=raw, work_centre=centre) if raw.isdigit() else None
                maintenance.save_plan(centre, {"title": request.POST.get("title", ""), "every_days": request.POST.get("every_days", ""),
                                               "every_hours": request.POST.get("every_hours", ""), "last_done": request.POST.get("last_done", ""),
                                               "active": request.POST.get("active") == "on" if plan is not None else True}, request.user, plan, lang)
                done = words["plan_saved"]
        except ValidationError as exc:
            messages.error(request, _translated(exc, lang))
        else:
            messages.success(request, done)
        return redirect(f"{reverse('manufacturing:maintenance_centre', args=[centre.pk])}?lang={lang}")
    now = timezone.now()
    job = maintenance.open_job_of(centre)
    plans = [plan_row(state, lang) for state in maintenance.plan_states(centre, now)]
    closed = list(centre.maintenance_jobs.filter(closed_at__isnull=False).select_related("employee", "supplier", "plan")[:LATEST_JOBS])
    latest = [{"job": row, "kind_label": kind_label(row.kind, lang), "cost": maintenance.job_cost(row)["total"] if rights["can_cost"] else None} for row in closed]
    return render(request, "manufacturing/maintenance_centre.html", _ctx(
        request, centre=centre, centre_name=centre.name(lang), card=job_context(request, job, lang, rights) if job is not None else None, plans=plans,
        active_plans=[row for row in plans if row["plan"].active], history=maintenance.history(centre, now=now), latest=latest,
        due_count=sum(1 for row in plans if row["due"]), now_input=_local_input(now), today=timezone.localdate().isoformat(), next="centre"))


# ---- one job ----

ACTIONS = {"close": maintenance.PRODUCE, "part": maintenance.PRODUCE, "return_part": maintenance.PRODUCE,
           "bill": maintenance.BILL, "link": maintenance.BILL, "unlink": maintenance.BILL}


@require_permission(VIEW)
@require_http_methods(["GET", "POST"])
def job_page(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    job = get_object_or_404(maintenance.scoped_jobs().select_related("work_centre", "plan", "employee", "supplier"), pk=pk)
    if request.method == "GET":
        return render(request, "manufacturing/maintenance_job.html", _ctx(request, centre=job.work_centre, centre_name=job.work_centre.name(lang),
                                                                          card=job_context(request, job, lang), next="job"))
    action = request.POST.get("action")
    if action not in ACTIONS:
        raise Http404("Unknown maintenance action.")
    if not user_has_permission(request.user, ACTIONS[action]):
        raise PermissionDenied(f"This maintenance action needs {ACTIONS[action]}.")
    try:
        if action == "close":
            from staff.models import Employee

            closed = maintenance.close_job(job, request.user, closed_at=request.POST.get("closed_at"), employee=_pick(Employee, request.POST.get("employee")),
                                           supplier=_pick(Supplier, request.POST.get("supplier")), note=request.POST.get("note", ""), lang=lang)
            done = words["closed_ok"].format(minutes=closed.downtime_minutes)
        elif action == "part":
            maintenance.take_part(job, request.user, item=_pick(Item, request.POST.get("item"), active=True),
                                  location=_pick(Location, request.POST.get("location"), active=True), quantity=request.POST.get("quantity"), lang=lang)
            done = words["taken_ok"]
        elif action == "return_part":
            raw = str(request.POST.get("part", ""))
            part = get_object_or_404(MaintenancePart, pk=raw if raw.isdigit() else 0, job=job)
            maintenance.return_part(part, request.user, reason=request.POST.get("reason", ""), lang=lang)
            done = words["returned_ok"]
        elif action == "bill":
            link = maintenance.make_bill(job, request.user, supplier=_pick(Supplier, request.POST.get("supplier"), active=True), amount=request.POST.get("amount"),
                                         bill_date=request.POST.get("bill_date"), description=request.POST.get("description", ""), lang=lang)
            done = words["bill_ok"].format(number=link.purchase_invoice.invoice_number)
        elif action == "link":
            maintenance.link_bill(job, request.user, invoice_number=request.POST.get("invoice_number", ""), lang=lang)
            done = words["linked_ok"]
        else:
            raw = str(request.POST.get("bill", ""))
            link = get_object_or_404(MaintenancePurchase, pk=raw if raw.isdigit() else 0, job=job)
            done = words["withdrawn_ok"] if maintenance.unlink_bill(link, request.user, lang) else words["unlinked_ok"]
    except ValidationError as exc:
        messages.error(request, _translated(exc, lang))
    else:
        messages.success(request, done)
    # Back to where the form was: the work centre's page while the job is open there, else the job's own page.
    if request.POST.get("next") == "centre" and action != "close":
        return redirect(f"{reverse('manufacturing:maintenance_centre', args=[job.work_centre_id])}?lang={lang}")
    return redirect(f"{reverse('manufacturing:maintenance_job', args=[job.pk])}?lang={lang}")


# ---- marks for the other manufacturing screens ----

def home_tile():
    """The manufacturing home's tile: (work centres down now, plans due)."""

    return maintenance.counts()


def mark_centres(rows):
    """Each row of the work centres page gets ``down``, ``open`` and ``due`` (three queries for the whole list)."""

    marks = maintenance.marks()
    for row in rows:
        row.update(marks[row["centre"].pk])
