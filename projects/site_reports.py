"""Contracting C2b (HG-095): the daily site report.

One report per project per day; saving a day that already has one edits it. A report records what happened on site:
the weather (chosen by hand), the head count by trade, the equipment, what was done, what was delayed and why, the
materials that arrived, the visitors and instructions, and photos (attachments on the report).

It is a record only. It writes no quantity of the bill of quantities, no certificate, no cost, no stock movement, no
cashbox movement and no journal entry: the quantities executed on a certificate are typed on the certificate screen, never
read from here. Every rule lives in this module; the views only call it.
"""

from datetime import date as date_type
from datetime import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import access
from .models import Project, ProjectStatus, SiteReport, SiteReportRow, SiteReportRowKind, Weather

OPEN = (ProjectStatus.PLANNED, ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD)
MAX_ROWS = 15
MAX_COUNT = 999
MAX_TEXT = 3000
MAX_LABEL = 80

MESSAGES = {
    "ar": {"denied": "كتابة التقرير اليومي لمهندس الموقع أو لصاحب صلاحية الفواتير بس.", "view_denied": "مش مسموح لك تشوف تقارير المشروع.",
           "not_open": "المشروع ده اتقفل أو اتلغى؛ مينفعش يتكتب له تقرير.", "no_date": "اختار تاريخ التقرير.",
           "future": "التاريخ لسه ما جاش. التقرير لليوم أو لأيام فاتت بس.", "weather": "اختار الطقس من القايمة.",
           "empty": "اكتب اللي اتعمل في اليوم أو اللي اتأخر.", "text_long": "النص أطول من المسموح ({limit} حرف).",
           "row_label": "اكتب الاسم في الصف {n} من {section}.", "row_count": "العدد في الصف {n} من {section} لازم يكون رقم من 1 لـ {max}.",
           "rows_many": "{section}: الصفوف كتير؛ الحد {n} صف.", "label_long": "الاسم في الصف {n} من {section} أطول من المسموح ({limit} حرف).",
           "section_crew": "فريق الموقع", "section_equipment": "المعدات"},
    "en": {"denied": "Only the site engineer, or someone who may create invoices, can write the daily report.",
           "view_denied": "You may not see this project's reports.",
           "not_open": "This project is closed or cancelled; a report cannot be written for it.", "no_date": "Choose the report's date.",
           "future": "That date has not come yet. A report is for today or a past day.", "weather": "Choose the weather from the list.",
           "empty": "Write what was done that day, or what was delayed.", "text_long": "The text is longer than allowed ({limit} characters).",
           "row_label": "Write the name in row {n} of {section}.", "row_count": "The count in row {n} of {section} must be a number from 1 to {max}.",
           "rows_many": "{section}: too many rows; the limit is {n}.", "label_long": "The name in row {n} of {section} is longer than allowed ({limit} characters).",
           "section_crew": "Workers", "section_equipment": "Equipment"},
}

WEATHER = {
    "ar": {"clear": "صافي", "hot": "حر", "cloudy": "غيم", "rain": "مطر", "wind": "رياح", "dust": "عاصفة ترابية", "cold": "برد"},
    "en": {"clear": "Clear", "hot": "Hot", "cloudy": "Cloudy", "rain": "Rain", "wind": "Windy", "dust": "Dust storm", "cold": "Cold"},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def weather_choices(lang):
    return [(value, WEATHER["en" if lang == "en" else "ar"][value]) for value in Weather.values]


def weather_label(value, lang):
    return WEATHER["en" if lang == "en" else "ar"].get(value, "")


def can_view(user):
    return access.can_view(user)


def can_write(user):
    return access.can_write(user)


def parse_date(raw):
    """A posted day (YYYY-MM-DD) or a date, else None."""

    if isinstance(raw, date_type) and not isinstance(raw, datetime):
        return raw
    try:
        return date_type.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None


def _rows(pairs, kind, words, lang):
    """The typed rows of one section as (label, count) pairs, blank rows dropped. A crew row needs a count; an equipment row
    with none counts as one."""

    section = words["section_crew" if kind == SiteReportRowKind.CREW else "section_equipment"]
    typed = [(str(label or "").strip(), str(count or "").strip()) for label, count in pairs]
    typed = [(label, count) for label, count in typed if label or count]
    if len(typed) > MAX_ROWS:
        raise ValidationError(words["rows_many"].format(section=section, n=MAX_ROWS))
    result = []
    for n, (label, count) in enumerate(typed, start=1):
        if not label:
            raise ValidationError(words["row_label"].format(n=n, section=section))
        if len(label) > MAX_LABEL:
            raise ValidationError(words["label_long"].format(n=n, section=section, limit=MAX_LABEL))
        if not count and kind == SiteReportRowKind.EQUIPMENT:
            count = "1"
        if not count.isascii() or not count.isdigit() or not 1 <= int(count) <= MAX_COUNT:
            raise ValidationError(words["row_count"].format(n=n, section=section, max=MAX_COUNT))
        result.append((label, int(count)))
    return result


def _text(value, words):
    value = str(value or "").strip()
    if len(value) > MAX_TEXT:
        raise ValidationError(words["text_long"].format(limit=MAX_TEXT))
    return value


def save_report(project, user, *, report_date, weather="", work_done="", delayed="", materials="", visitors="", crew=(), equipment=(), lang="ar"):
    """Write the report of ``report_date`` on ``project``: a new one, or the edit of the day's existing one.

    Returns ``(report, created)``. Needs the right to record progress on a project (``projects.access.can_write``); the
    project must be open; the day is today or earlier (``timezone.localdate()``); the weather, when given, is one of the
    list; something must be said (work done or a delay); rows are replaced as typed. Audited; moves nothing else."""

    words = _words(lang)
    if not access.can_view(user) or not access.can_write(user):
        raise PermissionDenied(words["denied"])
    day = parse_date(report_date)
    if day is None:
        raise ValidationError(words["no_date"])
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    weather = str(weather or "").strip()
    if weather and weather not in Weather.values:
        raise ValidationError(words["weather"])
    fields = {"work_done": _text(work_done, words), "delayed": _text(delayed, words), "materials": _text(materials, words), "visitors": _text(visitors, words)}
    crew_rows = _rows(crew, SiteReportRowKind.CREW, words, lang)
    equipment_rows = _rows(equipment, SiteReportRowKind.EQUIPMENT, words, lang)
    if not fields["work_done"] and not fields["delayed"]:
        raise ValidationError(words["empty"])
    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.pk)  # two saves of one day, or a close, settle one at a time
        if locked.status not in OPEN:
            raise ValidationError(words["not_open"])
        report = SiteReport.objects.filter(project=locked, report_date=day).first()
        before = _snapshot(report) if report is not None else {}
        created = report is None
        if created:
            report = SiteReport(project=locked, report_date=day, created_by=user)
        else:
            report.updated_by = user
        report.weather = weather
        for name, value in fields.items():
            setattr(report, name, value)
        report.save()
        report.rows.all().delete()
        SiteReportRow.objects.bulk_create(
            [SiteReportRow(report=report, kind=SiteReportRowKind.CREW, label=label, count=count, position=n) for n, (label, count) in enumerate(crew_rows)]
            + [SiteReportRow(report=report, kind=SiteReportRowKind.EQUIPMENT, label=label, count=count, position=n) for n, (label, count) in enumerate(equipment_rows)])
        AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="projects", action="save_site_report",
                                object_type="projects.SiteReport", object_id=str(report.pk), before_data=before, after_data=_snapshot(report))
    return report, created


def _snapshot(report):
    """What the audit row keeps: the day, the weather, the head count and the flags; never the text itself."""

    rows = list(report.rows.all())
    crew = [row for row in rows if row.kind == SiteReportRowKind.CREW]
    return {"project": report.project.code, "date": report.report_date.isoformat(), "weather": report.weather,
            "workers": sum(row.count for row in crew), "crew_rows": len(crew), "equipment_rows": len(rows) - len(crew),
            "has_work": bool(report.work_done), "has_delay": bool(report.delayed), "has_materials": bool(report.materials), "has_visitors": bool(report.visitors)}


def head_count(report):
    """The workers on site that day: the sum of the crew rows."""

    return report.rows.filter(kind=SiteReportRowKind.CREW).aggregate(n=Sum("count"))["n"] or 0


def get_report(project, pk):
    """A report of this project by id, else None (another project's id finds nothing)."""

    raw = str(pk or "")
    return SiteReport.objects.select_related("project", "created_by", "updated_by").filter(project=project, pk=raw).first() if raw.isdigit() else None


def reports_for(project):
    """The project's reports, newest day first, with the head count and the number of equipment rows.

    A queryset of the whole diary, never cut here: the list screen shows it a page at a time (``config.paging``), so a
    project that runs for years still reaches its first day.
    """

    return (SiteReport.objects.filter(project=project)
            .annotate(workers=Sum("rows__count", filter=Q(rows__kind=SiteReportRowKind.CREW)), equipment_rows=Count("rows", filter=Q(rows__kind=SiteReportRowKind.EQUIPMENT)))
            .select_related("created_by").order_by("-report_date", "-pk"))


def photo_counts(reports):
    """{report id: how many photos}, in one query."""

    from attachments.models import Attachment
    from django.contrib.contenttypes.models import ContentType

    ids = [report.pk for report in reports]
    if not ids:
        return {}
    found = (Attachment.objects.filter(content_type=ContentType.objects.get_for_model(SiteReport), object_id__in=ids)
             .values("object_id").annotate(n=Count("id")))
    return {row["object_id"]: row["n"] for row in found}


def rows_of(report):
    """(crew rows, equipment rows) of a report, in the order typed."""

    rows = list(report.rows.all())
    return ([row for row in rows if row.kind == SiteReportRowKind.CREW], [row for row in rows if row.kind == SiteReportRowKind.EQUIPMENT])
