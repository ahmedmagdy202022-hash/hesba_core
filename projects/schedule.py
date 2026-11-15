"""Contracting C3 (HG-124): the project's schedule (الجدول الزمني) and its delay alerts.

A task is a plan: a name, a planned start and end (both days count), an optional finish-to-start predecessor, and how much
of the project it is. Nothing here holds or moves money, stock or a certificate; the schedule only reads them.

Rules, stated once:

* **Actual progress of a task.** With items of the bill of quantities linked to it: the value their *posted* certificates
  certified (less posted returns) ÷ the value of those items, at most 100%. A draft certificate moves nothing. With no item
  linked: the percent typed on site.
* **Weight of a task:** the value of its linked items; else the weight the office typed; else its duration in days.
* **Planned progress of a task on a day:** the share of its days gone by the end of that day, in a straight line (0 before
  its start, 100% from its end on). The project's planned and actual progress are the weighted averages.
* **Alerts (on a given day):** a task whose end has passed and is not 100% done; a task that should have started while its
  predecessor is not finished; the project's planned progress more than ``insights.BEHIND_POINTS`` ahead of its actual.
* **Planned end of the project:** the last task's end plus the days approved variation orders added (C1).
* A project with tasks is "behind schedule" when its schedule has any alert; a project with none keeps the billing-based flag
  of ``insights.portfolio`` exactly as it was.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum

from audit.models import AuditEventType, AuditLog
from config.money import money_round

from . import access, contract, services
from .models import BoqLine, CertificateLine, ProjectTask

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TENTH = Decimal("0.1")
MAX_TASKS = 200
MAX_WEIGHT = Decimal("9999999.99")

MESSAGES = {
    "ar": {
        "name": "اكتب اسم البند في الجدول.", "name_long": "اسم البند أطول من المسموح (160 حرف).",
        "start": "اكتب تاريخ البداية.", "end": "اكتب تاريخ النهاية.", "dates": "تاريخ النهاية لازم يكون في يوم البداية أو بعده.",
        "weight": "الوزن لازم يكون رقم أكبر من صفر، أو سيبه فاضي.", "progress": "نسبة الإنجاز لازم تكون رقم صحيح من 0 لـ 100.",
        "predecessor": "اختار بند سابق من نفس المشروع.", "self": "البند مينفعش يكون سابق لنفسه.",
        "loop": "الاختيار ده بيعمل دايرة: «{name}» معتمد أصلاً على البند ده. اختار بند سابق تاني.",
        "boq": "اختار بنود مقايسة من نفس المشروع.", "boq_taken": "بند المقايسة «{line}» مربوط بالفعل ببند «{task}» في الجدول؛ البند الواحد يتربط ببند جدول واحد.",
        "boq_progress": "نسبة إنجاز البند ده بتتحسب من المستخلصات المعتمدة لبنود المقايسة المربوطة بيه، فمش بتتكتب باليد.",
        "has_successors": "مينفعش تمسح «{name}»: بنود تانية معتمدة عليه ({names}). غيّر البند السابق ليها الأول.",
        "too_many": "الجدول وصل للحد الأقصى ({max} بند).", "sort": "الترتيب لازم يكون رقم صحيح صفر أو أكبر.",
    },
    "en": {
        "name": "Type the task's name.", "name_long": "The task's name is longer than allowed (160 characters).",
        "start": "Type the start date.", "end": "Type the end date.", "dates": "The end date must be on or after the start date.",
        "weight": "The weight must be a number above zero, or left blank.", "progress": "Progress must be a whole number from 0 to 100.",
        "predecessor": "Choose a predecessor from the same project.", "self": "A task cannot be its own predecessor.",
        "loop": "That choice makes a loop: «{name}» already depends on this task. Choose another predecessor.",
        "boq": "Choose items of this project's bill of quantities.", "boq_taken": "Item «{line}» is already linked to the task «{task}»; an item is linked to one task only.",
        "boq_progress": "This task's progress is read from the posted certificates of the items linked to it, so it is not typed.",
        "has_successors": "«{name}» cannot be deleted: other tasks depend on it ({names}). Change their predecessor first.",
        "too_many": "The schedule has reached its limit ({max} tasks).", "sort": "The order must be a whole number, zero or more.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def tenth(value):
    """A percent to one decimal, as the screens show it: 42.2."""

    return Decimal(value).quantize(TENTH, rounding=ROUND_HALF_UP)


# ---- who ----

def can_view(user):
    return access.can_view(user)


def can_plan(user):
    """Add, edit and delete tasks, their dates, predecessor, weight and links: the office, the right that edits the project itself."""

    from permissions.services import user_has_permission

    return access.can_view(user) and user_has_permission(user, access.MANAGE)


def can_update_progress(user):
    """Type the percent done of a task with no linked item: whoever records progress on the project (the site engineer, the office)."""

    return access.can_view(user) and access.can_write(user)


def in_reach(project):
    """The schedule follows the project's money: from another entity it is neither shown nor changed (its progress reads the certificates)."""

    return contract.in_reach(project)


# ---- reading ----

def has_tasks(project):
    return ProjectTask.objects.filter(project=project).exists()


def tasks_for(project):
    return list(ProjectTask.objects.filter(project=project).select_related("predecessor").prefetch_related("boq_lines"))


def get_task(project, pk):
    """A task of this project by id, else None: another project's id finds nothing, and neither does any id from an entity that
    does not hold this project's money (the screens answer 404)."""

    raw = str(pk or "")
    if not raw.isdigit() or not in_reach(project):
        return None
    return ProjectTask.objects.select_related("predecessor").prefetch_related("boq_lines").filter(project=project, pk=raw).first()


def posted_quantities(project):
    """{bill-of-quantities item id: the quantity its POSTED certificates billed, less what posted returns took back}.

    ``contract.certified_quantity`` counts a draft certificate too (it reserves the quantity for billing); the schedule must not,
    because a draft is not yet work the owner accepted."""

    from sales.models import SalesReturnLine

    lines = CertificateLine.objects.filter(boq_line__project=project, certificate__invoice__status="posted")
    billed = {row["boq_line"]: row["total"] or ZERO for row in lines.values("boq_line").annotate(total=Sum("quantity"))}
    returns = (SalesReturnLine.objects.filter(source_line__certificate_line__in=lines, sales_return__status="posted")
               .values("source_line__certificate_line__boq_line").annotate(total=Sum("quantity")))
    for row in returns:
        key = row["source_line__certificate_line__boq_line"]
        billed[key] = billed.get(key, ZERO) - (row["total"] or ZERO)
    return billed


def duration(task):
    """The task's days, both ends counted: 1–15 January is 15 days."""

    return (task.end_date - task.start_date).days + 1


def planned_share(task, day):
    """Percent of the task planned to be done by the end of ``day`` (0–100), in a straight line over its days."""

    gone = min(max((day - task.start_date).days + 1, 0), duration(task))
    return Decimal(gone) * HUNDRED / Decimal(duration(task))


def _boq_value(lines):
    return money_round(sum((contract.line_amount(line) for line in lines), ZERO))


def task_figures(task, quantities=None):
    """(actual percent, weight, source) of one task. ``source`` is "boq" when the certificates say it, else "manual"."""

    lines = list(task.boq_lines.all())
    if lines:
        quantities = posted_quantities(task.project) if quantities is None else quantities
        value = _boq_value(lines)
        done = sum((Decimal(quantities.get(line.pk, ZERO)) * Decimal(line.rate) for line in lines), ZERO)
        actual = min(max(done * HUNDRED / value, ZERO), HUNDRED) if value > 0 else ZERO
        weight = value if value > 0 else (Decimal(task.weight) if task.weight else Decimal(duration(task)))
        return actual, weight, "boq"
    weight = Decimal(task.weight) if task.weight else Decimal(duration(task))
    return Decimal(task.progress), weight, "manual"


def planned_end(project, tasks=None):
    """The last task's end plus the days approved variation orders added; None without tasks."""

    from .variations import extension_days

    tasks = tasks_for(project) if tasks is None else tasks
    if not tasks:
        return None
    return max(task.end_date for task in tasks) + timedelta(days=extension_days(project))


def snapshot(project, today):
    """The schedule on ``today``: each task's row, the project's planned and actual progress, the gap, the alerts and the planned end.

    {rows: [{task, actual, planned, weight, share, source, late_days, blocked_by}], planned, actual, gap, alerts, planned_end,
    start, end, behind}. Percents are Decimals to one decimal; ``share`` is the task's part of the total weight."""

    from .insights import BEHIND_POINTS

    tasks = tasks_for(project)
    quantities = posted_quantities(project) if any(task.boq_lines.all() for task in tasks) else {}
    rows, by_id = [], {}
    for task in tasks:
        actual, weight, source = task_figures(task, quantities)
        row = {"task": task, "actual": actual, "raw_actual": actual, "planned": planned_share(task, today), "weight": weight, "source": source,
               "late_days": 0, "blocked_by": None}
        rows.append(row)
        by_id[task.pk] = row
    total = sum((row["weight"] for row in rows), ZERO)
    alerts = []
    for row in rows:
        task = row["task"]
        row["share"] = tenth(row["weight"] * HUNDRED / total) if total > 0 else ZERO
        if row["actual"] < HUNDRED and today > task.end_date:
            row["late_days"] = (today - task.end_date).days
            alerts.append({"kind": "late", "task": task, "days": row["late_days"]})
        before = by_id.get(task.predecessor_id)
        if before is not None and row["actual"] < HUNDRED and today >= task.start_date and before["actual"] < HUNDRED:
            row["blocked_by"] = before["task"]
            alerts.append({"kind": "waiting", "task": task, "predecessor": before["task"]})
        row["actual"], row["planned"] = tenth(row["actual"]), tenth(row["planned"])
    if total > 0:
        planned = tenth(sum((row["weight"] * planned_share(row["task"], today) for row in rows), ZERO) / total)
        actual = tenth(sum((row["weight"] * row.pop("raw_actual") for row in rows), ZERO) / total)
    else:
        planned = actual = ZERO
    gap = planned - actual
    if rows and gap > BEHIND_POINTS:
        alerts.insert(0, {"kind": "behind", "points": gap})
    end = planned_end(project, tasks)
    return {"rows": rows, "planned": planned, "actual": actual, "gap": gap, "alerts": alerts, "planned_end": end,
            "start": min((task.start_date for task in tasks), default=None), "end": max((task.end_date for task in tasks), default=None),
            "behind": bool(alerts)}


def behind(project, figures, share, today):
    """The portfolio's «متأخر عن الجدول» for one project: (flag, schedule) where ``schedule`` is the snapshot, or None.

    With tasks: the schedule has an alert on ``today``. Without: the billing-based rule, unchanged (billed % more than
    ``BEHIND_POINTS`` below the share of the planned span gone), and no snapshot."""

    from .insights import BEHIND_POINTS

    if has_tasks(project):
        data = snapshot(project, today)
        return data["behind"], data
    return share is not None and figures["contract"] > 0 and figures["progress"] + BEHIND_POINTS < share, None


def header_alerts(project, today):
    """What the project's header says about the schedule: None without tasks or from another entity, else the alert count and
    the gap. One ``exists()`` query when the project has no schedule."""

    if not has_tasks(project) or not in_reach(project):
        return None
    data = snapshot(project, today)
    late = sum(1 for alert in data["alerts"] if alert["kind"] == "late")
    waiting = sum(1 for alert in data["alerts"] if alert["kind"] == "waiting")
    return {"count": len(data["alerts"]), "late": late, "waiting": waiting, "behind": any(a["kind"] == "behind" for a in data["alerts"]),
            "planned": data["planned"], "actual": data["actual"], "gap": data["gap"]}


# ---- writing ----

def _audit(task, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.ProjectTask",
                            object_id=str(task.pk), before_data=before or {}, after_data=after)


def _state(task):
    """What the audit row keeps of a task: its plan and figures."""

    return {"project": task.project.code, "name": task.name, "start": task.start_date.isoformat(), "end": task.end_date.isoformat(),
            "predecessor": task.predecessor_id, "weight": str(task.weight) if task.weight is not None else "", "progress": task.progress,
            "boq_lines": sorted(task.boq_lines.values_list("pk", flat=True)) if task.pk else [], "sort": task.sort}


def _locked(project, lang):
    project = services._open(project, services.MESSAGES["en" if lang == "en" else "ar"])
    contract._reach(project, contract.MESSAGES["en" if lang == "en" else "ar"])
    return project


def _weight(raw, words):
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    number = contract._decimal(text)
    if number is None:
        raise ValidationError(words["weight"])
    number = number.quantize(Decimal("0.01"))  # checked as it is stored: 0.001 rounds to 0.00, which the table refuses (Codex)
    if number <= 0 or number > MAX_WEIGHT:
        raise ValidationError(words["weight"])
    return number


def _progress(raw, words):
    text = str(raw if raw is not None else "").strip()
    if not text.isdigit() or int(text) > 100:
        raise ValidationError(words["progress"])
    return int(text)


def _check_loop(task, predecessor, words):
    """Walk back from the chosen predecessor; meeting the task itself means a loop."""

    seen, current = set(), predecessor
    while current is not None:
        if task.pk is not None and current.pk == task.pk:
            raise ValidationError(words["loop"].format(name=predecessor.name))
        if current.pk in seen:  # a loop already stored would never end the walk; refuse rather than spin
            raise ValidationError(words["loop"].format(name=predecessor.name))
        seen.add(current.pk)
        current = ProjectTask.objects.filter(pk=current.predecessor_id).first() if current.predecessor_id else None


@transaction.atomic
def save_task(project, user, data, task=None, lang="ar"):
    """Add a task or change its plan. ``data``: name, start_date, end_date, predecessor (id or blank), weight (blank for none),
    boq_lines (ids), sort (blank keeps it, or puts a new task last). The percent typed on site is not part of the plan."""

    from .variations import parse_date

    words = _words(lang)
    project = _locked(project, lang)
    if task is not None:  # re-read under the project's lock, so a percent the site saved meanwhile is not written back (Codex)
        task = ProjectTask.objects.select_for_update().get(pk=task.pk, project=project)
    if task is None and ProjectTask.objects.filter(project=project).count() >= MAX_TASKS:
        raise ValidationError(words["too_many"].format(max=MAX_TASKS))
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValidationError(words["name"])
    if len(name) > 160:
        raise ValidationError(words["name_long"])
    start, end = parse_date(data.get("start_date")), parse_date(data.get("end_date"))
    if start is None:
        raise ValidationError(words["start"])
    if end is None:
        raise ValidationError(words["end"])
    if end < start:
        raise ValidationError(words["dates"])
    weight = _weight(data.get("weight"), words)
    raw = str(data.get("predecessor") or "").strip()
    predecessor = None
    if raw:
        predecessor = ProjectTask.objects.filter(project=project, pk=raw).first() if raw.isdigit() else None
        if predecessor is None:
            raise ValidationError(words["predecessor"])
        if task is not None and predecessor.pk == task.pk:
            raise ValidationError(words["self"])
        _check_loop(task or ProjectTask(project=project), predecessor, words)
    ids = data.getlist("boq_lines") if hasattr(data, "getlist") else (data.get("boq_lines") or [])
    ids = {str(pk).strip() for pk in ids if str(pk).strip()}
    if any(not pk.isdigit() for pk in ids):
        raise ValidationError(words["boq"])
    lines = list(BoqLine.objects.filter(project=project, pk__in=ids))
    if len(lines) != len(ids):
        raise ValidationError(words["boq"])
    taken = ProjectTask.boq_lines.through.objects.filter(boqline__in=lines).exclude(projecttask=task).select_related("projecttask", "boqline").first()
    if taken is not None:
        raise ValidationError(words["boq_taken"].format(line=taken.boqline.code or taken.boqline.line_number, task=taken.projecttask.name))
    sort_text = str(data.get("sort") or "").strip()
    if sort_text and not sort_text.isdigit():
        raise ValidationError(words["sort"])
    before = _state(task) if task is not None else None
    if task is None:
        last = ProjectTask.objects.filter(project=project).aggregate(top=Max("sort"))["top"]
        task = ProjectTask(project=project, created_by=user, sort=int(sort_text) if sort_text else (last or 0) + 1)
    elif sort_text:
        task.sort = int(sort_text)
    task.name, task.start_date, task.end_date, task.predecessor, task.weight = name, start, end, predecessor, weight
    task.save()
    task.boq_lines.set(lines)
    _audit(task, user, "save_project_task", _state(task), before, AuditEventType.CREATE if before is None else AuditEventType.UPDATE)
    return task


@transaction.atomic
def set_progress(project, task, user, progress, lang="ar"):
    """The percent done typed on site, for a task with no linked item (a linked one reads its certificates)."""

    words = _words(lang)
    _locked(project, lang)
    task = ProjectTask.objects.select_for_update().get(pk=task.pk, project=project)
    if task.boq_lines.exists():
        raise ValidationError(words["boq_progress"])
    value = _progress(progress, words)
    before = task.progress
    task.progress = value
    task.save(update_fields=["progress"])
    _audit(task, user, "project_task_progress", {"project": project.code, "progress": value}, {"progress": before})
    return task


@transaction.atomic
def delete_task(project, task, user, lang="ar"):
    words = _words(lang)
    _locked(project, lang)
    task = ProjectTask.objects.get(pk=task.pk, project=project)
    successors = list(task.successors.values_list("name", flat=True))
    if successors:
        raise ValidationError(words["has_successors"].format(name=task.name, names="، ".join(successors) if lang != "en" else ", ".join(successors)))
    before = _state(task)
    pk = task.pk
    task.boq_lines.clear()
    task.delete()
    AuditLog.objects.create(event_type=AuditEventType.DELETE, actor=user, module="projects", action="delete_project_task",
                            object_type="projects.ProjectTask", object_id=str(pk), before_data=before, after_data={})
