"""Manufacturing F10 (HG-141): machine maintenance, planned and breakdowns, with its cost.

Rules, stated once:

* **The machine is a work centre** (F6a). A plan and a job belong to the work centre's entity (NULL is the main
  entity); another entity neither lists nor opens them.
* **A plan comes due** by days or by running hours, whichever comes first when it has both:
  - *days*: it is due when more than ``every_days`` days have passed since the day it was last done (a plan of 30 days
    is not due on day 30; it is due on day 31);
  - *running hours*: it is due when the stage minutes recorded on the work centre since it was last done reach
    ``every_hours`` x 60. The minutes are ``OrderStageLog.minutes`` of the stages whose schedule step
    (``OrderStep``) is on that work centre, so a stage moved to another machine on the schedule counts where it ran.
    A stage with no minutes recorded counts nothing.
* **A job** is planned or a breakdown. It is opened at a clock time (typed, or now) and closed at one; the downtime is
  the whole minutes between the two, written at the close. A work centre has one job open at a time. Closing a planned
  job that carries out a plan stamps the plan's «last done» with the job's closing time.
* **A breakdown takes capacity out of the schedule** (``downtime``): each day loses the minutes the breakdown was
  open on that calendar day, never more than the day's capacity (the schedule has no shift clock, so every minute of a
  breakdown counts as a working minute). A breakdown that is still open also takes the whole of today, because nobody
  knows when the machine comes back; the days after today are left as they are. ``workcentres.load``,
  ``schedule.grid`` and ``schedule.warnings_for`` read it.
* **A work centre with an open breakdown is down** (``down_centres``; manufacturing video findings F4 and F12, approved
  23 Nov 2026). A breakdown has no expected end, so nothing new is put on a down centre until its job is closed: «رتّب»
  and a move to a day leave a stage on it, and the stages after it on the same order, waiting with no planned day; a
  stage cannot be moved onto it; the load table counts a waiting stage apart; «ابدأ» is not offered and ``floor.start``
  refuses it; the order page records a stage on it only when it was started before (work in progress is never trapped).
  A planned job takes no capacity and stops nothing.
* **Spare parts** leave stock through the unchanged ``inventory.services.adjust_stock`` (out, at the average of that
  moment), from a location of the job's entity, while the job is open; a ``MaintenancePart`` row links the operation
  to the job. That link is what makes the ledger book the part to maintenance expense instead of stock-count loss
  (``ledger.projector``). Taking a part back is the engine's own ``cancel_stock_operation``; the stock screen leaves a
  part's operation to the job's page (``guards``).
* **An outside technician's bill** is an ordinary **draft** purchase invoice made by the unchanged
  ``taxes.services.create_purchase_draft_with_tax`` (one service line), or a purchase invoice that is already there,
  linked by its number. It is posted, cancelled and returned from the purchases screen; nothing here posts it.
* **The machine's cost history**, per month: the spare parts still out (quantity x the cost they left at, in the month
  of their operation), the posted bills net of recoverable VAT and of their posted returns (in the month of the bill),
  and the downtime of the jobs opened that month.

Nothing here changes the inventory engine, purchase posting, average cost or the permission core.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, ProtectedError, Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission
from settings_core.templatetags.hesba_format import qty as fmt_qty

from . import workcentres
from .models import MaintenanceJob, MaintenanceKind, MaintenancePart, MaintenancePlan, MaintenancePurchase, OrderStageLog

ZERO = Decimal("0")
VIEW, MANAGE, PRODUCE, COST, BILL = ("inventory.view_stock", "master_data.manage_items", "inventory.adjust_stock", "inventory.view_cost",
                                     "purchases.create_purchase_invoice")
REVERSAL_ACTION = "inventory.reverse_operation"  # FB-27: the stock reversal an owner may hold for a second person's approval
SERVICE_CODE = "MFG-MNT"
SERVICE_NAME = "صيانة وإصلاح مكن"
DRAFT, POSTED, CANCELLED = "draft", "posted", "cancelled"
DAY_MINUTES = Decimal("1440")
MONTHS = 12

MESSAGES = {
    "ar": {
        "plan_permission": "خطط الصيانة محتاجة صلاحية إدارة الأصناف.",
        "job_permission": "فتح أمر الصيانة وقفله محتاجين صلاحية تسوية المخزون.",
        "part_permission": "صرف قطع الغيار ورجوعها محتاجين صلاحية تسوية المخزون.",
        "bill_permission": "فاتورة الصيانة محتاجة صلاحية تسجيل فواتير الشراء.",
        "title": "اكتب اسم خطة الصيانة.",
        "every": "حدّد الصيانة كل كام يوم أو كل كام ساعة تشغيل، برقم صحيح أكبر من صفر.",
        "date": "التاريخ مش صحيح.", "future": "التاريخ مينفعش يكون بعد النهارده.",
        "moment": "اكتب التاريخ والساعة بشكل صحيح.", "moment_future": "الوقت مينفعش يكون بعد دلوقتي.",
        "kind": "اختار نوع الأمر: صيانة مخططة أو عطل.",
        "cause": "اكتب العطل اللي حصل في كلمتين.",
        "plan": "خطة الصيانة دي مش على مركز العمل ده أو متوقفة.",
        "already": "مركز العمل ده عليه أمر صيانة مفتوح؛ اقفله الأول.",
        "number_busy": "أكتر من أمر صيانة بيتفتح في نفس اللحظة؛ جرّب تاني.",
        "closed": "أمر الصيانة ده اتقفل قبل كده.",
        "before_open": "وقت الرجوع للشغل مينفعش يكون قبل وقت التوقف.",
        "employee": "اختار موظف شغال.", "supplier": "اختار مورّد شغال.",
        "part_open": "قطع الغيار بتتصرف وأمر الصيانة مفتوح بس.",
        "item": "اختار قطعة الغيار.", "item_stock": "قطعة الغيار لازم تكون صنف متتبع في المخزون.",
        "location": "اختار مخزن من نفس كيان مركز العمل.",
        "quantity": "الكمية لازم تكون رقم أكبر من صفر.",
        "short": "الكمية مش كفاية من «{item}»: المطلوب {need} والموجود {have} بس.",
        "part_gone": "القطعة دي رجعت المخزن قبل كده.",
        "part_other": "القطعة دي مش على أمر الصيانة ده.",
        "held": "رجوع القطعة عكس لحركة مخزون، والعكس هنا محتاج موافقة؛ اللي معاه صلاحية الموافقة يرجّعها من الصفحة دي.",
        "amount": "قيمة الفاتورة قبل الضريبة لازم تكون رقم أكبر من صفر.",
        "no_place": "مفيش مخزن استلام شغال في كيان مركز العمل عشان الفاتورة تتسجل عليه.",
        "invoice": "مفيش فاتورة شراء بالرقم ده في الكيان ده.",
        "invoice_cancelled": "الفاتورة دي ملغية.",
        "invoice_taken": "الفاتورة دي مربوطة قبل كده بأمر صيانة أو بأمر إنتاج أو بمشروع.",
        "invoice_stock": "الفاتورة دي فيها أصناف مخزون؛ قطع الغيار بتتحسب على المكنة لما تتصرف من المخزن.",
        "bill_other": "الفاتورة دي مش على أمر الصيانة ده.",
        "unlink_blocked": "الفاتورة المسودة مرتبطة بمستندات تانية، فمينفعش تتسحب.",
        "down_note": "المركز واقف للصيانة من {since} بسبب «{cause}» لحد ما أمر الصيانة يتقفل.",
        "down_start": "المركز «{centre}» واقف للصيانة من {since} بسبب «{cause}»، فالمرحلة دي مش هتبدأ عليه لحد ما أمر الصيانة يتقفل.",
        "down_wait": "مستنية المكنة: «{centre}» واقف للصيانة من {since} بسبب «{cause}»، والمرحلة دي واللي بعدها هيترتبوا لما أمر الصيانة يتقفل.",
        "down_move": "المركز ده واقف للصيانة دلوقتي؛ اختار مركز شغال.",
    },
    "en": {
        "plan_permission": "Maintenance plans need the permission to manage items.",
        "job_permission": "Opening and closing a maintenance job need the stock adjustment permission.",
        "part_permission": "Taking spare parts and returning them need the stock adjustment permission.",
        "bill_permission": "A maintenance bill needs the permission to create purchase invoices.",
        "title": "Enter the plan's name.",
        "every": "Say every how many days or every how many running hours, as a whole number above zero.",
        "date": "The date is not valid.", "future": "The date cannot be after today.",
        "moment": "Enter a valid date and time.", "moment_future": "The time cannot be later than now.",
        "kind": "Choose the kind of job: planned maintenance or a breakdown.",
        "cause": "Say in a few words what broke.",
        "plan": "That plan is not on this work centre, or it is switched off.",
        "already": "This work centre already has an open maintenance job; close it first.",
        "number_busy": "Several maintenance jobs are being opened at the same moment; try again.",
        "closed": "This maintenance job is already closed.",
        "before_open": "The time it came back cannot be before the time it stopped.",
        "employee": "Choose an active employee.", "supplier": "Choose an active supplier.",
        "part_open": "Spare parts are taken only while the job is open.",
        "item": "Choose the spare part.", "item_stock": "A spare part must be a stock-tracked item.",
        "location": "Choose a location of the work centre's entity.",
        "quantity": "The quantity must be a number above zero.",
        "short": "Not enough of “{item}”: {need} needed, {have} on hand.",
        "part_gone": "This part is already back in stock.",
        "part_other": "That part is not on this maintenance job.",
        "held": "Returning a part reverses a stock operation, and reversals here need approval; someone who may approve returns it from this page.",
        "amount": "The bill's amount before tax must be a number above zero.",
        "no_place": "The work centre's entity has no active receiving location to record the bill on.",
        "invoice": "No purchase invoice with that number in this entity.",
        "invoice_cancelled": "That invoice is cancelled.",
        "invoice_taken": "That invoice is already linked to a maintenance job, a production order or a project.",
        "invoice_stock": "That invoice has stock items; spare parts count on the machine when they are taken from stock.",
        "bill_other": "That bill is not on this maintenance job.",
        "unlink_blocked": "The draft bill is linked to other documents, so it cannot be withdrawn.",
        "down_note": "The work centre is down for maintenance since {since} (“{cause}”) until its maintenance job is closed.",
        "down_start": "The work centre “{centre}” is down for maintenance since {since} (“{cause}”), so this stage cannot start on it until the maintenance job is closed.",
        "down_wait": "Waiting for the machine: “{centre}” is down for maintenance since {since} (“{cause}”); this stage and the ones after it are planned once the maintenance job is closed.",
        "down_move": "That work centre is down for maintenance right now; choose one that is running.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _audit(action, user, obj, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="manufacturing", action=action, object_type=f"manufacturing.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


# ---- the entity ----

def scoped_plans():
    """The plans of the entity being worked in: a plan belongs to its work centre's entity."""

    return entity_scope.scope(MaintenancePlan.objects, "work_centre__entity")


def scoped_jobs():
    """The jobs of the entity being worked in: a job belongs to its work centre's entity."""

    return entity_scope.scope(MaintenanceJob.objects, "work_centre__entity")


def _own_locations(centre):
    """Every location of the work centre's entity (the main entity also owns the locations made before entities existed)."""

    from master_data.models import Location

    key = workcentres._entity_key(centre.entity_id)
    own = Q(entity_id=key)
    if key == workcentres._entity_key(None):
        own |= Q(entity__isnull=True)
    return Location.objects.filter(own)


def places(centre):
    """The active locations spare parts may leave from: those of the work centre's entity, never a quality hold place."""

    from . import quality

    return quality.production_places(_own_locations(centre).filter(active=True)).order_by("-is_default", "pk")


# ---- typed values ----

def _whole(raw):
    """A typed whole number above zero; None when blank; False when it is not one."""

    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    return int(text) if text.isdigit() and 0 < int(text) <= 100000 else False


def _day(value, words):
    """A typed date (a ``date``, an ISO text, or blank for today); never after today."""

    today = timezone.localdate()
    if value in (None, ""):
        return today
    if not isinstance(value, date):
        try:
            value = date.fromisoformat(str(value))
        except ValueError:
            raise ValidationError(words["date"])
    if value > today:
        raise ValidationError(words["future"])
    return value


def _moment(value, words, now=None):
    """A typed clock time (an aware or naive ``datetime``, an ISO text as a form sends it, or blank for now), read in the
    business's own time zone; never later than now."""

    now = now or timezone.now()
    if value in (None, ""):
        return now
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).strip())
        except ValueError:
            raise ValidationError(words["moment"])
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    if value > now + timedelta(minutes=1):
        raise ValidationError(words["moment_future"])
    return value


# ---- plans ----

@transaction.atomic
def save_plan(centre, data, user, plan=None, lang="ar"):
    """Create or change a plan of ``centre``: ``title``, ``every_days`` and/or ``every_hours`` (whole numbers),
    ``last_done`` (a date; blank keeps the stored one, or is today for a new plan) and ``active``."""

    words = _words(lang)
    if not user_has_permission(user, MANAGE):
        raise PermissionDenied(words["plan_permission"])
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError(words["title"])
    days, hours = _whole(data.get("every_days")), _whole(data.get("every_hours"))
    if days is False or hours is False or (days is None and hours is None):
        raise ValidationError(words["every"])
    before = {}
    if plan is None:
        plan = MaintenancePlan(work_centre=centre, created_by=user)
    else:
        before = {"title": plan.title, "every_days": plan.every_days, "every_hours": plan.every_hours, "last_done_at": plan.last_done_at.isoformat(),
                  "active": plan.active}
    if data.get("last_done") not in (None, "") or plan.pk is None:
        day = _day(data.get("last_done"), words)
        if plan.pk is None or day != timezone.localtime(plan.last_done_at).date():  # the same day typed again keeps the exact time a job stamped
            plan.last_done_at = timezone.make_aware(datetime.combine(day, time.min))
    plan.title, plan.every_days, plan.every_hours = title[:120], days, hours
    plan.active = data.get("active", True) not in (False, "", "0", None)
    new = plan.pk is None
    plan.save()
    _audit("save_maintenance_plan", user, plan, {"work_centre": centre.code, "title": plan.title, "every_days": days, "every_hours": hours,
                                                 "last_done_at": plan.last_done_at.isoformat(), "active": plan.active},
           before, AuditEventType.CREATE if new else AuditEventType.UPDATE)
    return plan


def running_minutes(centre, since):
    """The stage minutes recorded on ``centre`` after ``since``: the logs of the stages whose schedule step is on it."""

    total = (OrderStageLog.objects.filter(minutes__isnull=False, done_at__gt=since, order__steps__work_centre_id=centre.pk,
                                          order__steps__stage_key=F("stage_key")).aggregate(total=Sum("minutes"))["total"])
    return total or ZERO


def plan_state(plan, now=None):
    """Where a plan stands: the days since it was last done and the day after which it is due (``every_days``), the
    running minutes since and the minutes it is due at (``every_hours``), and ``due``: by days, by hours, or not."""

    now = now or timezone.now()
    last_day = timezone.localtime(plan.last_done_at).date()
    days = (timezone.localtime(now).date() - last_day).days
    state = {"plan": plan, "last_day": last_day, "days": days, "next_day": None, "minutes": None, "hours": None, "due_minutes": None,
             "by_days": False, "by_hours": False}
    if plan.every_days is not None:
        state["next_day"] = last_day + timedelta(days=plan.every_days)
        state["by_days"] = days > plan.every_days
    if plan.every_hours is not None:
        minutes = running_minutes(plan.work_centre, plan.last_done_at)
        state["minutes"], state["due_minutes"] = minutes, Decimal(plan.every_hours * 60)
        state["hours"] = (minutes / 60).quantize(Decimal("0.1"))
        state["by_hours"] = minutes >= state["due_minutes"]
    state["due"] = plan.active and (state["by_days"] or state["by_hours"])
    return state


def plan_states(centre=None, now=None):
    """The states of the entity's active plans (or of one work centre's plans, switched off ones included)."""

    if centre is not None:
        plans = centre.maintenance_plans.select_related("work_centre").order_by("-active", "pk")
    else:
        plans = scoped_plans().filter(active=True, work_centre__active=True).select_related("work_centre").order_by("work_centre__code", "pk")
    return [plan_state(plan, now) for plan in plans]


# ---- jobs ----

def open_job_of(centre):
    return centre.maintenance_jobs.filter(closed_at__isnull=True).select_related("plan", "employee", "supplier").first()


#: how many numbers ``open_job`` tries when another work centre's job takes the one it picked at the same moment
NUMBER_TRIES = 5


def _number(day):
    from .services import _next

    return _next(MaintenanceJob, "number", f"MJ-{day:%Y%m%d}-", 3)


def _locked_centre(centre):
    """The work centre's row, locked: two people opening a job on one machine at once take turns."""

    from .models import WorkCentre

    return WorkCentre.objects.select_for_update().get(pk=centre.pk)


def _locked_job(job):
    return MaintenanceJob.objects.select_for_update(of=("self",)).select_related("work_centre", "plan").get(pk=job.pk)


@transaction.atomic
def open_job(centre, user, *, kind, plan=None, opened_at=None, cause="", note="", lang="ar"):
    """«افتح أمر صيانة»: a planned job (optionally for one of the centre's plans) or a breakdown (its cause is said),
    from ``opened_at`` (blank: now). One job is open on a work centre at a time."""

    words = _words(lang)
    if not user_has_permission(user, PRODUCE):
        raise PermissionDenied(words["job_permission"])
    if kind not in MaintenanceKind.values:
        raise ValidationError(words["kind"])
    cause = (cause or "").strip()
    if kind == MaintenanceKind.BREAKDOWN:
        plan = None
        if not cause:
            raise ValidationError(words["cause"])
    elif plan is not None and (plan.work_centre_id != centre.pk or not plan.active):
        raise ValidationError(words["plan"])
    moment = _moment(opened_at, words)
    centre = _locked_centre(centre)
    if centre.maintenance_jobs.filter(closed_at__isnull=True).exists():
        raise ValidationError(words["already"])
    job = None
    for _attempt in range(NUMBER_TRIES):
        try:
            with transaction.atomic():
                job = MaintenanceJob.objects.create(number=_number(timezone.localtime(moment).date()), work_centre=centre, plan=plan, kind=kind, opened_at=moment,
                                                    cause=cause[:255], note=(note or "").strip()[:255], created_by=user)
            break
        except IntegrityError:
            # This centre's row is locked, so nobody else opened a job on it meanwhile. What collided is the number: a job
            # opened on another work centre at the same moment took it (numbers are per day, across centres). Take the next.
            if centre.maintenance_jobs.filter(closed_at__isnull=True).exists():
                raise ValidationError(words["already"])
    if job is None:
        raise ValidationError(words["number_busy"])
    _audit("open_maintenance_job", user, job, {"number": job.number, "work_centre": centre.code, "kind": kind, "plan": plan.title if plan else "",
                                               "opened_at": moment.isoformat(), "cause": job.cause}, event=AuditEventType.CREATE)
    return job


def _minutes_between(start, end):
    return max(int((end - start).total_seconds() // 60), 0)


@transaction.atomic
def close_job(job, user, *, closed_at=None, employee=None, supplier=None, note=None, lang="ar"):
    """«اقفل أمر الصيانة»: the machine is back at ``closed_at`` (blank: now). The downtime is the whole minutes since it
    stopped. ``employee`` and ``supplier`` say who did the work. A planned job stamps its plan's «last done»."""

    words = _words(lang)
    if not user_has_permission(user, PRODUCE):
        raise PermissionDenied(words["job_permission"])
    job = _locked_job(job)
    if job.closed_at is not None:
        raise ValidationError(words["closed"])
    if employee is not None and not employee.active:
        raise ValidationError(words["employee"])
    if supplier is not None and not supplier.active:
        raise ValidationError(words["supplier"])
    moment = _moment(closed_at, words)
    if moment < job.opened_at:
        raise ValidationError(words["before_open"])
    job.closed_at, job.downtime_minutes, job.employee, job.supplier, job.closed_by = moment, _minutes_between(job.opened_at, moment), employee, supplier, user
    if note is not None and note.strip():
        job.note = note.strip()[:255]
    job.save(update_fields=["closed_at", "downtime_minutes", "employee", "supplier", "closed_by", "note"])
    stamped = False
    if job.kind == MaintenanceKind.PLANNED and job.plan_id:
        MaintenancePlan.objects.filter(pk=job.plan_id).update(last_done_at=moment)
        stamped = True
    _audit("close_maintenance_job", user, job, {"number": job.number, "closed_at": moment.isoformat(), "downtime_minutes": job.downtime_minutes,
                                                "employee": employee.code if employee else "", "supplier": supplier.supplier_code if supplier else "",
                                                "plan_stamped": stamped})
    return job


def minutes_down(job, now=None):
    """A job's downtime in whole minutes: what its close wrote, or the minutes so far while it is open."""

    if job.downtime_minutes is not None:
        return job.downtime_minutes
    return _minutes_between(job.opened_at, now or timezone.now())


# ---- the schedule ----

def open_breakdowns(centre_ids=None):
    """{work centre id: its open breakdown} (every work centre of the entity when no ids are given)."""

    jobs = MaintenanceJob.objects.filter(kind=MaintenanceKind.BREAKDOWN, closed_at__isnull=True)
    jobs = scoped_jobs().filter(kind=MaintenanceKind.BREAKDOWN, closed_at__isnull=True) if centre_ids is None else jobs.filter(work_centre_id__in=list(centre_ids))
    return {job.work_centre_id: job for job in jobs}


def downtime(centre_ids, first, last, now=None):
    """{(work centre id, day): minutes} the breakdowns of these work centres take out of each day from ``first`` to
    ``last``: the minutes a breakdown was open on that calendar day; a breakdown still open also takes the whole of
    today (``DAY_MINUTES``). The reader caps it at the day's capacity. A ``defaultdict``: a day with none reads zero."""

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    taken = defaultdict(lambda: ZERO)
    centre_ids = [pk for pk in centre_ids if pk]
    if not centre_ids or last < first:
        return taken
    window_start = timezone.make_aware(datetime.combine(first, time.min))
    window_end = timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min))
    jobs = (MaintenanceJob.objects.filter(kind=MaintenanceKind.BREAKDOWN, work_centre_id__in=centre_ids, opened_at__lt=window_end)
            .filter(Q(closed_at__isnull=True) | Q(closed_at__gt=window_start)))
    for job in jobs:
        end = job.closed_at or now
        day = max(timezone.localtime(job.opened_at).date(), first)
        final = min(timezone.localtime(end).date(), last)
        while day <= final:
            if job.closed_at is None and day == today:
                taken[(job.work_centre_id, day)] += DAY_MINUTES
            else:
                day_start = timezone.make_aware(datetime.combine(day, time.min))
                day_end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
                seconds = (min(end, day_end) - max(job.opened_at, day_start)).total_seconds()
                if seconds > 0:
                    taken[(job.work_centre_id, day)] += (Decimal(int(seconds)) / 60).quantize(Decimal("0.01"))
            day += timedelta(days=1)
    return taken


def down_centres(centre_ids):
    """{work centre id: its open breakdown} for the given work centres: the ones that are down now (see the module's rules).
    Read by id, whatever the entity: the caller has already scoped the orders and centres it asks about."""

    ids = {pk for pk in centre_ids if pk}
    if not ids:
        return {}
    jobs = MaintenanceJob.objects.filter(kind=MaintenanceKind.BREAKDOWN, closed_at__isnull=True, work_centre_id__in=ids).select_related("work_centre")
    return {job.work_centre_id: job for job in jobs}


def down_text(job, key, lang="ar"):
    """One of the down sentences (``down_note``, ``down_start``, ``down_wait``) for an open breakdown: the centre's name,
    since when (the business's own clock) and the cause typed when it was opened."""

    since = timezone.localtime(job.opened_at).strftime("%Y-%m-%d %H:%M")
    return _words(lang)[key].format(centre=job.work_centre.name(lang), since=since, cause=job.cause)


def stage_down(order):
    """F12: the open breakdown that keeps the order's current stage from starting, or None. It is there when the stage's work
    centre (its schedule step's, else the one its routing gave it) is down and the stage was not started on the floor before:
    a stage already started is never held, so work in progress is always finished. A stage sent to an outside supplier (F4,
    HG-137) is not done on the centre at all, so its return still closes it."""

    from .models import OrderStep, OutsideStatus

    if order.stage_index >= len(order.stages):
        return None
    if order.outside_jobs.filter(stage_key=order.stages[order.stage_index][0]).exclude(status=OutsideStatus.CANCELLED).exists():
        return None
    step = OrderStep.objects.filter(order=order, index=order.stage_index).first()
    if step is not None and step.started_at is not None:
        return None
    centre_id = step.work_centre_id if step is not None else workcentres.stage_plan(order.stages[order.stage_index])[0]
    return down_centres([centre_id]).get(centre_id) if centre_id else None


def capacity_left(capacity, down):
    """A day's capacity after its breakdowns: never below zero."""

    return max(capacity - down, ZERO)


# ---- spare parts ----

def part_value(operation):
    """What a part's operation took out of stock, as the journal values it."""

    return money_round(operation.quantity * operation.unit_cost)


def _reversals_held(user):
    """FB-27: True when the owner holds stock reversals for a second person's approval and ``user`` is not one who approves."""

    from approvals.services import can_approve, held_actions

    return REVERSAL_ACTION in held_actions() and not can_approve(user)


@transaction.atomic
def take_part(job, user, *, item, location, quantity, taken_on=None, lang="ar"):
    """«اصرف قطعة غيار»: ``quantity`` of ``item`` leaves ``location`` for the job, at the average of this moment, through
    the engine's own adjustment out. Only while the job is open, and only from a location of the job's entity.
    ``taken_on`` dates the operation (blank: today; never after today; the engine refuses a closed period)."""

    from inventory.models import StockAdjustmentDirection, StockOperation
    from inventory.services import adjust_stock, get_item_location_stock_quantity, lock_items_for_stock_check

    from .services import _decimal, _next

    words = _words(lang)
    if not user_has_permission(user, PRODUCE):
        raise PermissionDenied(words["part_permission"])
    job = _locked_job(job)
    if job.closed_at is not None:
        raise ValidationError(words["part_open"])
    if item is None or not item.active:
        raise ValidationError(words["item"])
    if not item.is_stock_tracked:
        raise ValidationError(words["item_stock"])
    if location is None or not places(job.work_centre).filter(pk=location.pk).exists():
        raise ValidationError(words["location"])
    amount = _decimal(quantity)
    if amount is None or amount <= 0:
        raise ValidationError(words["quantity"])
    day = _day(taken_on, words)
    lock_items_for_stock_check([item.pk])
    have = get_item_location_stock_quantity(item, location)
    if have < amount:
        raise ValidationError(words["short"].format(item=item.item_name, need=fmt_qty(amount), have=fmt_qty(have)))
    centre = job.work_centre
    reason = f"قطعة غيار لصيانة {centre.name_ar} {job.number} / Spare part for maintenance {job.number}"[:255]
    operation = adjust_stock(_next(StockOperation, "reference_number", f"{job.number}-P", 2), day, item, location,
                             StockAdjustmentDirection.OUT, amount, reason, user)
    part = MaintenancePart.objects.create(job=job, operation=operation, created_by=user)
    _audit("take_maintenance_part", user, job, {"number": job.number, "item": item.item_code, "quantity": str(amount), "location": location.location_code,
                                                "operation": operation.reference_number})
    return part


@transaction.atomic
def return_part(part, user, *, reason="", lang="ar"):
    """«رجّع القطعة»: the part is back in the location it left, by the engine's own reversal of its operation, dated
    today. Where the owner holds stock reversals for approval, only someone who may approve does it."""

    from inventory.models import StockOperationStatus
    from inventory.services import cancel_stock_operation

    words = _words(lang)
    if not user_has_permission(user, PRODUCE):
        raise PermissionDenied(words["part_permission"])
    if _reversals_held(user):
        raise ValidationError(words["held"])
    job = _locked_job(part.job)
    part = MaintenancePart.objects.select_related("operation__item").get(pk=part.pk)
    if part.operation.status != StockOperationStatus.POSTED:
        raise ValidationError(words["part_gone"])
    typed = (reason or "").strip()
    text = f"رجوع قطعة غيار {job.number} / Spare part returned {job.number}{': ' + typed if typed else ''}"[:255]
    cancel_stock_operation(part.operation_id, timezone.localdate(), text, user)
    _audit("return_maintenance_part", user, job, {"number": job.number, "item": part.operation.item.item_code, "quantity": str(part.operation.quantity),
                                                  "operation": part.operation.reference_number, "reason": typed[:200]})
    return part


def part_rows(job):
    """The job's parts with their operation: item, quantity, unit cost, value, and whether the part is still out."""

    rows = []
    for part in job.parts.select_related("operation__item", "operation__source_location").order_by("pk"):
        operation = part.operation
        rows.append({"part": part, "operation": operation, "item": operation.item, "quantity": operation.quantity, "location": operation.source_location,
                     "unit_cost": operation.unit_cost, "value": part_value(operation), "out": operation.status == POSTED})
    return rows


# ---- an outside technician's bill ----

def service_item():
    """The reserved service line a maintenance bill uses: never a stock item, so the bill moves no stock."""

    from projects.services import service_item as reserved

    return reserved(SERVICE_CODE, SERVICE_NAME)


def _bill_place(centre):
    return places(centre).filter(is_receiving_location=True).first()


def _bill_number(job):
    from purchases.models import PurchaseInvoice

    stem = f"MNT-{job.number.removeprefix('MJ-')}"  # MNT-20261119-001-01: the job's own date and serial, then the bill's
    sequence = 1
    while PurchaseInvoice.objects.filter(invoice_number=f"{stem}-{sequence:02d}").exists():
        sequence += 1
    return f"{stem}-{sequence:02d}"


@transaction.atomic
def make_bill(job, user, *, supplier, amount, bill_date=None, description="", lang="ar"):
    """«اعمل فاتورة الصيانة»: a **draft** purchase invoice from ``supplier`` with one service line of ``amount`` before
    tax, through the unchanged ``create_purchase_draft_with_tax``. It posts from the purchases screen."""

    from taxes.services import create_purchase_draft_with_tax

    from .services import _decimal

    words = _words(lang)
    if not user_has_permission(user, BILL):
        raise PermissionDenied(words["bill_permission"])
    job = _locked_job(job)
    if supplier is None or not supplier.active:
        raise ValidationError(words["supplier"])
    value = _decimal(amount)
    if value is None or money_round(value) <= 0:
        raise ValidationError(words["amount"])
    day = _day(bill_date, words)
    place = _bill_place(job.work_centre)
    if place is None:
        raise ValidationError(words["no_place"])
    typed = (description or "").strip()
    label = f"أمر الصيانة {job.number}: صيانة {job.work_centre.name_ar}{' · ' + typed if typed else ''}"[:255]
    invoice = create_purchase_draft_with_tax(
        {"invoice_number": _bill_number(job), "invoice_date": day, "supplier": supplier, "receiving_location": place, "cashbox": None,
         "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO, "notes": label},
        [{"item": service_item(), "quantity": Decimal("1"), "unit_purchase_price": money_round(value), "line_discount_amount": ZERO, "description": label}], user)
    link = MaintenancePurchase.objects.create(job=job, purchase_invoice=invoice, made_here=True, created_by=user)
    _audit("bill_maintenance_job", user, job, {"number": job.number, "invoice": invoice.invoice_number, "supplier": supplier.supplier_code,
                                               "amount": str(money_round(value)), "total": str(invoice.total_amount)})
    return link


@transaction.atomic
def link_bill(job, user, *, invoice_number, lang="ar"):
    """«اربط فاتورة شراء»: a purchase invoice that is already there (draft or posted) is the job's bill. It must be in
    the job's entity, carry no stock item, and belong to no other job, production order or project."""

    from purchases.models import PurchaseInvoice

    words = _words(lang)
    if not user_has_permission(user, BILL):
        raise PermissionDenied(words["bill_permission"])
    job = _locked_job(job)
    number = (invoice_number or "").strip()
    own = _own_locations(job.work_centre).values("pk")
    invoice = PurchaseInvoice.objects.select_for_update(of=("self",)).filter(invoice_number__iexact=number, receiving_location__in=own).first() if number else None
    if invoice is None:
        raise ValidationError(words["invoice"])
    if invoice.status == CANCELLED:
        raise ValidationError(words["invoice_cancelled"])
    if any(hasattr(invoice, name) for name in ("maintenance_purchase", "outside_job", "project_purchase", "subcontract_bill")):
        raise ValidationError(words["invoice_taken"])
    if invoice.lines.filter(item__is_stock_tracked=True).exists():
        raise ValidationError(words["invoice_stock"])
    link = MaintenancePurchase.objects.create(job=job, purchase_invoice=invoice, made_here=False, created_by=user)
    _audit("link_maintenance_bill", user, job, {"number": job.number, "invoice": invoice.invoice_number, "status": invoice.status})
    return link


@transaction.atomic
def unlink_bill(link, user, lang="ar"):
    """«شيل الفاتورة من الأمر»: the bill no longer counts on the machine. A draft that was made here and is still a
    draft is deleted with its link (as a draft made by mistake); any other invoice stays in purchases as it is."""

    from purchases.models import PurchaseInvoice

    words = _words(lang)
    if not user_has_permission(user, BILL):
        raise PermissionDenied(words["bill_permission"])
    job = _locked_job(link.job)
    invoice = PurchaseInvoice.objects.select_for_update(of=("self",)).get(pk=link.purchase_invoice_id)
    number, withdrawn = invoice.invoice_number, link.made_here and invoice.status == DRAFT
    link.delete()
    if withdrawn:
        try:
            with transaction.atomic():
                invoice.delete()
        except ProtectedError:
            raise ValidationError(words["unlink_blocked"])
    _audit("unlink_maintenance_bill", user, job, {"number": job.number, "invoice": number, "draft_deleted": withdrawn})
    return withdrawn


def bill_cost(invoice):
    """What a bill left in expenses: nothing until it is posted, then its total less its recoverable VAT and less what
    its posted purchase returns took back (``outside.bill_cost``, the figures the ledger books for it)."""

    from . import outside

    return outside.bill_cost(invoice) if invoice.status == POSTED else ZERO


def bill_rows(job):
    rows = []
    for link in job.purchases.select_related("purchase_invoice__supplier").order_by("pk"):
        invoice = link.purchase_invoice
        rows.append({"link": link, "invoice": invoice, "supplier": invoice.supplier, "status": invoice.status, "cost": bill_cost(invoice),
                     "draft": invoice.status == DRAFT, "posted": invoice.status == POSTED, "cancelled": invoice.status == CANCELLED})
    return rows


# ---- what a job and a machine cost ----

def job_cost(job):
    """{parts, bills, total} of one job: its parts still out, and its posted bills net of VAT and returns."""

    parts = sum((row["value"] for row in part_rows(job) if row["out"]), ZERO)
    bills = sum((row["cost"] for row in bill_rows(job)), ZERO)
    return {"parts": money_round(parts), "bills": money_round(bills), "total": money_round(parts + bills)}


def _month(day):
    return day.replace(day=1)


def history(centre, months=MONTHS, now=None):
    """The machine's last ``months`` months, newest first, each {month, parts, bills, total, minutes, jobs}:

    * ``parts``: the spare parts still out, at the value they left stock at, in the month of their operation;
    * ``bills``: the posted bills net of recoverable VAT and of their posted returns, in the month of the bill;
    * ``minutes``: the downtime of the jobs opened that month (an open job counts the minutes so far);
    * ``jobs``: how many jobs were opened that month.

    Months with nothing are left out."""

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    first = _month(today)
    for _ in range(months - 1):
        first = _month(first - timedelta(days=1))
    rows = defaultdict(lambda: {"parts": ZERO, "bills": ZERO, "minutes": 0, "jobs": 0})
    for job in centre.maintenance_jobs.filter(opened_at__gte=timezone.make_aware(datetime.combine(first, time.min))):
        month = _month(timezone.localtime(job.opened_at).date())
        rows[month]["jobs"] += 1
        rows[month]["minutes"] += minutes_down(job, now)
    parts = (MaintenancePart.objects.filter(job__work_centre=centre, operation__status=POSTED, operation__operation_date__gte=first)
             .values_list("operation__operation_date", "operation__quantity", "operation__unit_cost"))
    for day, quantity, unit_cost in parts:
        rows[_month(day)]["parts"] += money_round(quantity * unit_cost)
    links = (MaintenancePurchase.objects.filter(job__work_centre=centre, purchase_invoice__status=POSTED, purchase_invoice__invoice_date__gte=first)
             .select_related("purchase_invoice"))
    for link in links:
        rows[_month(link.purchase_invoice.invoice_date)]["bills"] += bill_cost(link.purchase_invoice)
    result = []
    for month in sorted(rows, reverse=True):
        row = rows[month]
        hours = (Decimal(row["minutes"]) / 60).quantize(Decimal("0.1"))
        result.append({"month": month, "parts": money_round(row["parts"]), "bills": money_round(row["bills"]),
                       "total": money_round(row["parts"] + row["bills"]), "minutes": row["minutes"], "hours": hours, "jobs": row["jobs"]})
    return result


# ---- what the screens mark ----

def marks(centres=None, now=None):
    """{work centre id: {"down": its open breakdown or None, "open": its open job or None, "due": how many plans are due}}
    for the entity's work centres (or the given ones)."""

    ids = None if centres is None else [centre.pk for centre in centres]
    jobs = scoped_jobs().filter(closed_at__isnull=True) if ids is None else MaintenanceJob.objects.filter(closed_at__isnull=True, work_centre_id__in=ids)
    found = defaultdict(lambda: {"down": None, "open": None, "due": 0})
    for job in jobs.select_related("work_centre"):
        found[job.work_centre_id]["open"] = job
        if job.kind == MaintenanceKind.BREAKDOWN:
            found[job.work_centre_id]["down"] = job
    for state in plan_states(now=now):
        if state["due"] and (ids is None or state["plan"].work_centre_id in ids):
            found[state["plan"].work_centre_id]["due"] += 1
    return found


def counts(now=None):
    """(work centres down now, plans due) in the entity: the manufacturing home's tile."""

    down = scoped_jobs().filter(kind=MaintenanceKind.BREAKDOWN, closed_at__isnull=True, work_centre__active=True).count()
    due = sum(1 for state in plan_states(now=now) if state["due"])
    return down, due
