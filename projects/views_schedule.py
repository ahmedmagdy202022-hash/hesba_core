"""Contracting C3 (HG-124): the «الجدول الزمني» screens: the tab (summary, alerts, the Gantt and the task list, and the form
that adds a task) and one task (its plan, the office's edit form, and the percent done typed on site).

Every rule is in ``projects.schedule``; the views read, call and render. A task is looked up THROUGH its project, so another
project's task id answers 404, and so does any task from an entity that does not hold the project's money. Reading needs the
project right; the plan (adding, editing, deleting, dates, predecessor, weight and links) needs ``sales.create_sales_invoice``,
the right that edits the project itself (403 without); the percent done needs the right to record progress.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from settings_core.templatetags.hesba_format import day_text
from settings_core.ui_messages import translate

from . import schedule
from .contract_views import _context
from .models import Project
from .services import OPEN

MONTHS = {
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}

WORDS = {
    "ar": {
        "page_title": "الجدول الزمني",
        "intro": "بنود التنفيذ بمواعيدها المخططة، وكل بند بيبدأ بعد ما اللي قبله يخلص. نسبة إنجاز البند المربوط ببنود المقايسة بتتحسب من المستخلصات المعتمدة، والباقي بيكتبه مهندس الموقع.",
        "planned": "المخطط لحد النهارده", "actual": "المنفّذ فعلاً", "gap": "الفرق", "points": "{n} نقطة", "planned_end": "نهاية التنفيذ المخططة",
        "extension": "شاملة تمديد {n} يوم من أوامر التغيير", "alerts": "تنبيهات", "no_alerts": "مفيش تأخير",
        "alert_late": "«{name}» عدّى ميعاد نهايته بـ {days} يوم وإنجازه {pct}%.",
        "alert_waiting": "«{name}» كان المفروض يبدأ، واللي قبله «{before}» لسه ما خلصش.",
        "alert_behind": "المشروع متأخر {points} نقطة عن المخطط (المخطط {planned}% والمنفّذ {actual}%).",
        "legend_progress": "اللي اتنفّذ من البند", "legend_late": "بند متأخر", "chart": "مخطط التنفيذ (Gantt)", "chart_hint": "اسحب المخطط يمين وشمال لو المدة طويلة. الخط الرأسي هو النهارده.", "today": "النهارده",
        "task": "البند", "start": "البداية", "end": "النهاية", "predecessor": "بعد بند", "weight": "الوزن", "share": "من المشروع",
        "progress": "الإنجاز", "planned_short": "المخطط", "boq": "بنود المقايسة", "days": "{n} يوم", "none_dash": "—",
        "source_boq": "من المستخلصات", "source_manual": "بيكتبه الموقع", "late_chip": "متأخر {n} يوم", "waiting_chip": "مستني «{name}»", "done_chip": "خلص",
        "empty": "لسه مفيش بنود في الجدول الزمني للمشروع ده.", "empty_hint": "ضيف أول بند من النموذج اللي تحت.",
        "add": "إضافة بند للجدول", "f_name": "اسم البند", "f_name_ph": "مثلاً: أعمال الحفر", "f_start": "تاريخ البداية", "f_end": "تاريخ النهاية",
        "f_predecessor": "يبدأ بعد", "f_no_predecessor": "من غير بند سابق", "f_weight": "الوزن (اختياري)",
        "f_weight_hint": "البند المربوط ببنود مقايسة وزنه قيمتها. من غير ربط ومن غير وزن، وزنه عدد أيامه.",
        "f_boq": "بنود المقايسة المربوطة (اختياري)", "f_boq_hint": "لو ربطت بنود، نسبة الإنجاز هتتحسب من الكميات اللي اتعمل بيها مستخلصات معتمدة. البند الواحد يتربط ببند جدول واحد.",
        "f_sort": "الترتيب", "save": "حفظ", "saved": "اتحفظ البند «{name}» في الجدول.", "back": "الجدول الزمني",
        "edit_title": "تعديل «{name}»", "delete": "امسح البند", "deleted": "اتمسح البند «{name}» من الجدول.",
        "progress_title": "نسبة الإنجاز", "progress_label": "نسبة الإنجاز (%)", "progress_save": "سجّل النسبة", "progress_saved": "اتسجّلت نسبة إنجاز «{name}»: {pct}%.",
        "progress_boq": "نسبة إنجاز البند ده بتتحسب من المستخلصات المعتمدة لبنود المقايسة المربوطة بيه.",
        "plan_only": "تعديل الخطة (المواعيد والترتيب والربط) للمكتب بس؛ مهندس الموقع بيسجّل نسبة الإنجاز.",
        "view_only": "الجدول للقراءة بس.", "closed": "المشروع اتقفل أو اتلغى؛ الجدول للقراءة بس.",
        "other_entity": "الجدول الزمني محفوظ في الكيان اللي بتتعمل منه مستخلصات المشروع ده؛ اشتغل من الكيان ده عشان تشوفه.",
        "header": "متأخر عن الجدول", "header_late": "{n} بند عدّى ميعاده", "header_waiting": "{n} بند مستني اللي قبله",
        "header_behind": "المخطط {planned}% والمنفّذ {actual}%", "header_open": "افتح الجدول الزمني",
        "created_by": "أضافه {who}",
    },
    "en": {
        "page_title": "Schedule",
        "intro": "The tasks of the work with their planned dates; each starts once the one before it is done. A task linked to items of the bill of quantities reads its progress from the posted certificates; the site engineer types the rest.",
        "planned": "Planned to date", "actual": "Done", "gap": "Gap", "points": "{n} points", "planned_end": "Planned end",
        "extension": "including {n} days from variation orders", "alerts": "Alerts", "no_alerts": "Nothing late",
        "alert_late": "«{name}» passed its end by {days} days at {pct}% done.",
        "alert_waiting": "«{name}» should have started, and the task before it, «{before}», is not finished.",
        "alert_behind": "The project is {points} points behind its plan (planned {planned}%, done {actual}%).",
        "legend_progress": "Done of the task", "legend_late": "Late task", "chart": "Gantt chart", "chart_hint": "Swipe the chart sideways for a long project. The vertical line is today.", "today": "Today",
        "task": "Task", "start": "Start", "end": "End", "predecessor": "After", "weight": "Weight", "share": "Of the project",
        "progress": "Progress", "planned_short": "Planned", "boq": "Bill of quantities items", "days": "{n} days", "none_dash": "—",
        "source_boq": "from certificates", "source_manual": "typed on site", "late_chip": "{n} days late", "waiting_chip": "waiting for «{name}»", "done_chip": "Done",
        "empty": "No tasks on this project's schedule yet.", "empty_hint": "Add the first task with the form below.",
        "add": "Add a task", "f_name": "Task name", "f_name_ph": "e.g. Excavation", "f_start": "Start date", "f_end": "End date",
        "f_predecessor": "Starts after", "f_no_predecessor": "No predecessor", "f_weight": "Weight (optional)",
        "f_weight_hint": "A task linked to items of the bill of quantities weighs their value. With no link and no weight, it weighs its days.",
        "f_boq": "Linked items of the bill of quantities (optional)", "f_boq_hint": "With linked items, progress is read from the quantities on posted certificates. An item is linked to one task only.",
        "f_sort": "Order", "save": "Save", "saved": "The task «{name}» is saved on the schedule.", "back": "Schedule",
        "edit_title": "Edit «{name}»", "delete": "Delete the task", "deleted": "The task «{name}» was deleted from the schedule.",
        "progress_title": "Progress", "progress_label": "Progress (%)", "progress_save": "Record progress", "progress_saved": "Progress of «{name}» recorded: {pct}%.",
        "progress_boq": "This task's progress is read from the posted certificates of the items linked to it.",
        "plan_only": "Only the office changes the plan (dates, order and links); the site engineer records progress.",
        "view_only": "The schedule is read-only for you.", "closed": "This project is closed or cancelled; its schedule is read-only.",
        "other_entity": "The schedule is kept in the entity that bills this project's certificates; work in that entity to see it.",
        "header": "Behind schedule", "header_late": "{n} tasks past their end", "header_waiting": "{n} tasks waiting for the one before",
        "header_behind": "planned {planned}%, done {actual}%", "header_open": "Open the schedule",
        "created_by": "Added by {who}",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _need_view(request):
    if not schedule.can_view(request.user):
        raise PermissionDenied("Projects need projects.view_projects or sales.view_sales_invoices.")


def _project(pk):
    return get_object_or_404(Project.objects.select_related("customer"), pk=pk)


def _url(name, project, task=None, lang="ar"):
    args = [project.pk] + ([task.pk] if task is not None else [])
    return f"{reverse(f'projects:{name}', args=args)}?lang={lang}"


def _pct(value):
    """A percent as the screens show it: 42.2, 100 (never 100.0), with a Latin point whatever the page's language."""

    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _frac(value):
    """A fraction for a CSS custom property: always a Latin point, never the page's number format."""

    return f"{max(min(value, 1), 0):.4f}"


def _who(user):
    from reports.navigation import display_name

    return display_name(user) if user is not None else ""


def header_flag(project, user, lang):
    """The schedule line under the project's title: None when it has no tasks, nothing is late, or the money is elsewhere."""

    if not schedule.can_view(user):
        return None
    data = schedule.header_alerts(project, timezone.localdate())
    if not data or not data["count"]:
        return None
    words = WORDS["en" if lang == "en" else "ar"]
    parts = []
    if data["late"]:
        parts.append(words["header_late"].format(n=data["late"]))
    if data["waiting"]:
        parts.append(words["header_waiting"].format(n=data["waiting"]))
    if data["behind"]:
        parts.append(words["header_behind"].format(planned=_pct(data["planned"]), actual=_pct(data["actual"])))
    return {"title": words["header"], "text": " · ".join(parts), "url": _url("schedule", project, lang=lang), "open": words["header_open"]}


def _alert_text(alert, rows, words):
    if alert["kind"] == "behind":
        return words["alert_behind"].format(points=_pct(alert["points"]), planned=_pct(rows["planned"]), actual=_pct(rows["actual"]))
    task = alert["task"]
    if alert["kind"] == "late":
        actual = next(row["actual"] for row in rows["rows"] if row["task"].pk == task.pk)
        return words["alert_late"].format(name=task.name, days=alert["days"], pct=_pct(actual))
    return words["alert_waiting"].format(name=task.name, before=alert["predecessor"].name)


def _chart(data, today, lang):
    """The Gantt's geometry: each bar's start, span and done share as fractions of the chart, the month marks and today's line."""

    if not data["rows"]:
        return None
    first = data["start"]
    last = max(data["end"], data["planned_end"] or data["end"])
    days = (last - first).days + 1
    marks, mark = [], date(first.year, first.month, 1)
    while mark <= last:
        if mark >= first:
            marks.append({"label": f"{MONTHS[lang][mark.month - 1]} {mark.year}" if mark.month == 1 or not marks else MONTHS[lang][mark.month - 1],
                          "at": _frac((mark - first).days / days)})
        mark = date(mark.year + (mark.month == 12), mark.month % 12 + 1, 1)
    if not marks or marks[0]["at"] != _frac(0):
        marks.insert(0, {"label": f"{MONTHS[lang][first.month - 1]} {first.year}", "at": _frac(0)})
    bars = []
    for row in data["rows"]:
        task = row["task"]
        bars.append({"task": task, "from": _frac((task.start_date - first).days / days), "span": _frac(schedule.duration(task) / days),
                     "done": _frac(float(row["actual"]) / 100), "late": bool(row["late_days"]), "finished": row["actual"] >= 100,
                     "waiting": row["blocked_by"] is not None})
    shown = first <= today <= last
    return {"bars": bars, "marks": marks, "today": _frac(((today - first).days + 0.5) / days) if shown else None,
            "width": f"{min(max(days * 3, 640), 2400)}px"}


def _boq_choices(project, task=None):
    """Every item of the bill of quantities, each with whether this task links it and which other task already does."""

    from .models import ProjectTask

    linked = set(task.boq_lines.values_list("pk", flat=True)) if task is not None else set()
    taken = {}
    for row in ProjectTask.boq_lines.through.objects.filter(boqline__project=project).select_related("projecttask"):
        if task is None or row.projecttask_id != task.pk:
            taken[row.boqline_id] = row.projecttask.name
    return [{"line": line, "checked": line.pk in linked, "taken": taken.get(line.pk, "")} for line in project.boq.all()]


def _typed(post):
    return {"name": post.get("name", ""), "start_date": post.get("start_date", ""), "end_date": post.get("end_date", ""),
            "predecessor": post.get("predecessor", ""), "weight": post.get("weight", ""), "sort": post.get("sort", ""),
            "boq_lines": set(post.getlist("boq_lines"))}


def _stored(task):
    return {"name": task.name, "start_date": task.start_date.isoformat(), "end_date": task.end_date.isoformat(),
            "predecessor": str(task.predecessor_id or ""), "weight": str(task.weight) if task.weight is not None else "", "sort": str(task.sort),
            "boq_lines": {str(pk) for pk in task.boq_lines.values_list("pk", flat=True)}}


def _blank(project):
    start = project.start_date or timezone.localdate()
    return {"name": "", "start_date": start.isoformat(), "end_date": (start + timedelta(days=13)).isoformat(), "predecessor": "", "weight": "", "sort": "",
            "boq_lines": set()}


def _context_for(request, project, **extra):
    lang = _lang(request)
    return _context(request, project, "schedule", sw=WORDS[lang], can_plan=schedule.can_plan(request.user),
                    can_progress=schedule.can_update_progress(request.user), **extra)


# ---- the tab ----

def schedule_tab(request, pk):
    _need_view(request)
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    reach = schedule.in_reach(project)
    is_open = project.status in OPEN and reach
    error, form = "", _blank(project)
    if request.method == "POST":
        if not schedule.can_plan(request.user):
            raise PermissionDenied("Changing the schedule needs sales.create_sales_invoice.")
        if not reach:
            raise Http404("This project's money is in another entity.")
        form = _typed(request.POST)
        try:
            task = schedule.save_task(project, request.user, request.POST, lang=lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["saved"].format(name=task.name))
            return redirect(_url("schedule", project, lang=lang))
    today = timezone.localdate()
    data = schedule.snapshot(project, today) if reach else None
    rows = []
    if data:
        for row in data["rows"]:
            task = row["task"]
            rows.append({"task": task, "start": day_text(task.start_date, lang), "end": day_text(task.end_date, lang),
                         "days": words["days"].format(n=schedule.duration(task)), "actual": _pct(row["actual"]), "planned": _pct(row["planned"]),
                         "share": _pct(row["share"]), "source": words["source_" + row["source"]], "is_boq": row["source"] == "boq",
                         "late": words["late_chip"].format(n=row["late_days"]) if row["late_days"] else "",
                         "waiting": words["waiting_chip"].format(name=row["blocked_by"].name) if row["blocked_by"] else "",
                         "done": row["actual"] >= 100, "boq": ", ".join(str(line.code or line.line_number) for line in task.boq_lines.all()),
                         "url": _url("schedule_task", project, task, lang)})
    summary = None
    if data and data["rows"]:
        from .variations import extension_days

        days = extension_days(project)
        summary = {"planned": _pct(data["planned"]), "actual": _pct(data["actual"]), "gap": words["points"].format(n=_pct(max(data["gap"], 0))),
                   "planned_end": day_text(data["planned_end"], lang), "extension": words["extension"].format(n=days) if days else "",
                   "alerts": [_alert_text(alert, data, words) for alert in data["alerts"]], "behind": data["behind"]}
    return render(request, "projects/schedule.html", _context_for(
        request, project, rows=rows, summary=summary, chart=_chart(data, today, lang) if data else None, other_entity=not reach,
        is_open=is_open, form=form, error=error, tasks=[row["task"] for row in rows], boq_choices=_boq_choices(project) if reach else [],
        page_title=f"{project.name} — {words['page_title']}"))


# ---- one task ----

def task_page(request, pk, tid):
    _need_view(request)
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    task = schedule.get_task(project, tid)
    if task is None:
        raise Http404("No such task on this project.")
    is_open = project.status in OPEN
    error, form = "", None
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "progress":
            if not schedule.can_update_progress(request.user):
                raise PermissionDenied("Recording progress needs projects.record_progress or sales.create_sales_invoice.")
        elif action in ("save", "delete"):
            if not schedule.can_plan(request.user):
                raise PermissionDenied("Changing the schedule needs sales.create_sales_invoice.")
        else:
            raise Http404
        try:
            if action == "progress":
                task = schedule.set_progress(project, task, request.user, request.POST.get("progress"), lang)
                messages.success(request, words["progress_saved"].format(name=task.name, pct=task.progress))
                return redirect(_url("schedule", project, lang=lang))
            if action == "delete":
                name = task.name
                schedule.delete_task(project, task, request.user, lang)
                messages.success(request, words["deleted"].format(name=name))
                return redirect(_url("schedule", project, lang=lang))
            form = _typed(request.POST)
            task = schedule.save_task(project, request.user, request.POST, task, lang)
            messages.success(request, words["saved"].format(name=task.name))
            return redirect(_url("schedule", project, lang=lang))
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
            task = schedule.get_task(project, tid)
    form = form or _stored(task)
    actual, _, source = schedule.task_figures(task)
    from .models import ProjectTask

    others = ProjectTask.objects.filter(project=project).exclude(pk=task.pk)
    return render(request, "projects/schedule_task.html", _context_for(
        request, project, task=task, form=form, error=error, is_open=is_open, tasks=others, boq_choices=_boq_choices(project, task),
        actual=_pct(schedule.tenth(actual)), is_boq=source == "boq", start=day_text(task.start_date, lang), end=day_text(task.end_date, lang),
        days=words["days"].format(n=schedule.duration(task)), back_url=_url("schedule", project, lang=lang),
        created=words["created_by"].format(who=_who(task.created_by)), heading=words["edit_title"].format(name=task.name),
        page_title=f"{project.name} — {task.name}"))
