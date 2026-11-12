"""Contracting C2b (HG-095): the daily site report screens: the list, the form (new and edit), one report, its photos and its print.

Every rule is in ``projects.site_reports`` and the attachments registry; the views read, call and render. Every screen looks the
report up THROUGH its project, so another project's report id answers 404. Reading needs the project right (403 without it);
writing needs the right to record progress (403 without it). Nothing here touches a quantity, a certificate or a cost.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from attachments import services, views as attachment_views
from settings_core.templatetags.hesba_format import day_text

from . import access, documents, site_reports
from .contract_views import _context
from .models import Project, SiteReport

REPORT = "projects.sitereport"
CREW_ROWS, EQUIPMENT_ROWS = 3, 2  # blank rows a new form offers (the page adds more with one tap)

WORDS = {
    "ar": {
        "title": "اليوميات", "page_title": "التقارير اليومية",
        "intro": "تقرير لكل يوم شغل: الطقس وفريق الموقع والمعدات واللي اتعمل واللي اتأخر وصور الموقع. ده تسجيل بس: مبيغيّرش كميات ولا مستخلصات ولا تكلفة.",
        "today": "تقرير النهارده", "day": "اليوم", "weather": "الطقس", "workers": "الفريق", "delay": "تأخير", "photos": "الصور", "author": "كتبه", "by": "ضافها", "pdf": "PDF",
        "has_delay": "فيه تأخير", "no_delay": "—", "workers_n": "{n} فرد", "photos_n": "{n} صورة", "print": "اطبع التقرير", "edit": "تعديل التقرير", "open": "افتح",
        "none": "لسه مفيش تقارير يومية على المشروع ده. «تقرير النهارده» يبدأ أول واحد.", "closed": "المشروع اتقفل أو اتلغى؛ التقارير للقراءة بس.",
        "view_only": "كتابة التقرير اليومي لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
        "form_title": "تقرير يوم", "form_new": "تقرير جديد", "form_intro": "اكتب اللي حصل في الموقع النهارده. الصور بتتضاف بعد ما تحفظ.",
        "date": "التاريخ", "date_hint": "لو فيه تقرير للتاريخ ده هيتعدّل.", "weather_q": "الطقس", "weather_none": "من غير طقس",
        "crew": "فريق الموقع حسب التخصص", "crew_ph": "التخصص (نجار، حداد، مبيّض…)", "count": "العدد", "equipment": "المعدات في الموقع",
        "equipment_ph": "المعدة (خلاطة، ونش…)", "add_row": "+ صف تاني", "work": "اللي اتعمل", "work_ph": "الشغل اللي اتنفذ النهارده في الموقع",
        "delayed": "اللي اتأخر وليه", "delayed_ph": "أي شغل اتأخر أو وقف، والسبب", "materials": "خامات وصلت الموقع", "materials_ph": "نوع الخامة والكمية",
        "visitors": "زيارات وتعليمات", "visitors_ph": "مين زار الموقع وإيه التعليمات اللي اتقالت", "save": "احفظ التقرير", "cancel": "رجوع",
        "saved": "اتحفظ تقرير يوم {day}. ضيف صور اليوم من تحت.", "edited": "اتعدّل تقرير يوم {day}.",
        "total_workers": "إجمالي الفريق", "none_rows": "مفيش.", "written": "كتبه {who}", "written_on": "يوم {when}", "edited_by": "آخر تعديل: {who} يوم {when}",
        "photos_title": "صور اليوم", "photos_intro": "صوّر من الموبايل أو اختار صور موجودة. الصورة الكبيرة بتتصغّر قبل الرفع.",
        "photos_none": "مفيش صور لليوم ده لسه.", "camera": "صوّر", "choose": "اختار صور", "delete": "امسح", "photo_of": "صورة اليوم",
        "formats": "صور JPG أو PNG أو WEBP بس، لحد {limit} للصورة.",
        "space": "{files} من {max_files} صورة، والمساحة الفاضلة {left}.", "blocked": "إضافة الصور لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
        "back": "اليوميات", "ph": "—",
        "p_title": "تقرير يومي للموقع", "p_project": "المشروع", "p_site": "الموقع", "p_owner": "صاحب المشروع", "p_date": "التاريخ", "p_weather": "الطقس",
        "p_workers": "إجمالي الفريق", "p_trade": "التخصص", "p_equipment_name": "المعدة", "p_written": "كتبه",
        "p_engineer": "مهندس الموقع", "p_consultant": "الاستشاري", "p_contractor": "المقاول", "p_back": "رجوع", "p_print": "اطبع",
        "p_hint": "عشان تحفظه PDF اختار «حفظ كـ PDF» من شاشة الطباعة.", "p_photos": "صور الموقع",
    },
    "en": {
        "title": "Daily reports", "page_title": "Daily site reports",
        "intro": "One report per working day: weather, workers, equipment, what was done, what was delayed and site photos. It only records: it changes no quantity, certificate or cost.",
        "today": "Today's report", "day": "Day", "weather": "Weather", "workers": "Workers", "delay": "Delay", "photos": "Photos", "author": "Written by", "by": "Added by", "pdf": "PDF",
        "has_delay": "Delayed", "no_delay": "—", "workers_n": "{n} workers", "photos_n": "{n} photos", "print": "Print the report", "edit": "Edit the report", "open": "Open",
        "none": "No daily reports on this project yet. “Today's report” starts the first one.", "closed": "This project is closed or cancelled; reports are read-only.",
        "view_only": "Only the site engineer, or someone who may create invoices, can write the daily report.",
        "form_title": "Report of", "form_new": "New report", "form_intro": "Write what happened on site today. Photos are added after you save.",
        "date": "Date", "date_hint": "If this date already has a report, it is edited.", "weather_q": "Weather", "weather_none": "No weather",
        "crew": "Workers by trade", "crew_ph": "Trade (carpenter, steel fixer, plasterer…)", "count": "Count", "equipment": "Equipment on site",
        "equipment_ph": "Equipment (mixer, crane…)", "add_row": "+ Another row", "work": "What was done", "work_ph": "The work carried out on site today",
        "delayed": "What was delayed and why", "delayed_ph": "Any work that slipped or stopped, and the reason", "materials": "Materials that arrived", "materials_ph": "What and how much",
        "visitors": "Visitors and instructions", "visitors_ph": "Who visited the site and the instructions given", "save": "Save the report", "cancel": "Back",
        "saved": "The report of {day} is saved. Add the day's photos below.", "edited": "The report of {day} was updated.",
        "total_workers": "Total workers", "none_rows": "None.", "written": "Written by {who}", "written_on": "on {when}", "edited_by": "Last edited: {who} on {when}",
        "photos_title": "The day's photos", "photos_intro": "Take photos with the phone or choose existing ones. A large photo is made smaller before it is uploaded.",
        "photos_none": "No photos for this day yet.", "camera": "Take photo", "choose": "Choose photos", "delete": "Delete", "photo_of": "Photo of the day",
        "formats": "JPG, PNG or WEBP images only, up to {limit} each.",
        "space": "{files} of {max_files} photos, and {left} of room left.", "blocked": "Only the site engineer, or someone who may create invoices, can add photos.",
        "back": "Daily reports", "ph": "—",
        "p_title": "Daily site report", "p_project": "Project", "p_site": "Site", "p_owner": "Project owner", "p_date": "Date", "p_weather": "Weather",
        "p_workers": "Total workers", "p_trade": "Trade", "p_equipment_name": "Equipment", "p_written": "Written by",
        "p_engineer": "Site engineer", "p_consultant": "Consultant", "p_contractor": "Contractor", "p_back": "Back", "p_print": "Print",
        "p_hint": "To save a PDF, choose \"Save as PDF\" in the print window.", "p_photos": "Site photos",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _who(user):
    return (user.get_full_name() or user.get_username()) if user is not None else ""


def _need_view(request):
    if not access.can_view(request.user):
        raise PermissionDenied("Projects need projects.view_projects or sales.view_sales_invoices.")


def _need_write(request):
    _need_view(request)
    if not access.can_write(request.user):
        raise PermissionDenied("Daily reports need projects.record_progress or sales.create_sales_invoice.")


def _project(pk):
    return get_object_or_404(Project.objects.select_related("customer"), pk=pk)


def _is_open(project):
    return project.status in site_reports.OPEN


def cta(project, user, lang):
    """The primary action of a project's pages, «تقرير النهارده»: for those who may write reports, on an open project. Else None."""

    if not access.can_write(user) or not _is_open(project):
        return None
    return {"url": f"{reverse('projects:diary_new', args=[project.pk])}?lang={lang}", "label": WORDS[lang]["today"]}


def _context_for(request, project, tab, **extra):
    lang = _lang(request)
    return _context(request, project, tab, rw=WORDS[lang], site_cta=cta(project, request.user, lang), **extra)


# ---- the list ----

def reports_list(request, pk):
    _need_view(request)
    lang = _lang(request)
    project = _project(pk)
    reports = site_reports.reports_for(project)
    photos = site_reports.photo_counts(reports)
    words = WORDS[lang]
    rows = [{"report": report, "day": day_text(report.report_date, lang), "weather": site_reports.weather_label(report.weather, lang), "workers": report.workers or 0,
             "delayed": bool(report.delayed.strip()), "photos": photos.get(report.pk, 0), "who": _who(report.created_by),
             "workers_text": words["workers_n"].format(n=report.workers or 0), "photos_text": words["photos_n"].format(n=photos.get(report.pk, 0)),
             "url": f"{reverse('projects:diary_day', args=[project.pk, report.pk])}?lang={lang}",
             "print_url": f"{reverse('projects:diary_print', args=[project.pk, report.pk])}?lang={lang}"} for report in reports]
    return render(request, "projects/reports.html", _context_for(request, project, "reports", rows=rows, today=day_text(timezone.localdate(), lang), page_title=f"{project.name} — {words['page_title']}"))


# ---- the form ----

def _padded(rows, blanks):
    rows = list(rows)
    return rows + [("", "")] * max(0, blanks - len(rows))


def _typed(post):
    """The form as typed, for showing it again after a refusal."""

    return {"date": post.get("report_date", ""), "weather": post.get("weather", ""), "work_done": post.get("work_done", ""), "delayed": post.get("delayed", ""),
            "materials": post.get("materials", ""), "visitors": post.get("visitors", ""),
            "crew": list(zip(post.getlist("crew_label"), post.getlist("crew_count"))), "equipment": list(zip(post.getlist("equipment_label"), post.getlist("equipment_count")))}


def _stored(report):
    crew, equipment = site_reports.rows_of(report)
    return {"date": report.report_date.isoformat(), "weather": report.weather, "work_done": report.work_done, "delayed": report.delayed, "materials": report.materials,
            "visitors": report.visitors, "crew": [(row.label, str(row.count)) for row in crew], "equipment": [(row.label, str(row.count)) for row in equipment]}


def report_form(request, pk, rid=None):
    """New (today, or ``?date=``; a day that already has a report opens that report) and edit, in one screen."""

    _need_write(request)
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    report = site_reports.get_report(project, rid) if rid is not None else None
    if rid is not None and report is None:
        raise Http404("No such report on this project.")
    today = timezone.localdate()
    error, form = "", None
    if request.method == "POST":
        post = request.POST
        form = _typed(post)
        day = report.report_date if report is not None else post.get("report_date")
        try:
            saved, created = site_reports.save_report(
                project, request.user, report_date=day, weather=post.get("weather"), work_done=post.get("work_done"), delayed=post.get("delayed"),
                materials=post.get("materials"), visitors=post.get("visitors"), crew=form["crew"], equipment=form["equipment"], lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            text = words["saved" if created and report is None else "edited"].format(day=day_text(saved.report_date, lang))
            messages.success(request, text)
            return redirect(f"{reverse('projects:diary_day', args=[project.pk, saved.pk])}?lang={lang}")
    elif report is not None:
        form = _stored(report)
    else:
        wanted = site_reports.parse_date(request.GET.get("date")) or today
        wanted = min(wanted, today)
        existing = SiteReport.objects.filter(project=project, report_date=wanted).first()
        if existing is not None:
            return redirect(f"{reverse('projects:diary_edit', args=[project.pk, existing.pk])}?lang={lang}")
        form = {"date": wanted.isoformat(), "weather": "", "work_done": "", "delayed": "", "materials": "", "visitors": "", "crew": [], "equipment": []}
    form["crew"] = _padded(form["crew"], CREW_ROWS)
    form["equipment"] = _padded(form["equipment"], EQUIPMENT_ROWS)
    title = f"{words['form_title']} {day_text(report.report_date, lang)}" if report is not None else words["form_new"]
    context = _context_for(request, project, "reports", form=form, report=report, error=error, weather=site_reports.weather_choices(lang), today=today, form_title=title,
                           closed=not _is_open(project), page_title=f"{project.name} — {title}")
    return render(request, "projects/report_form.html", context)


# ---- one report ----

def _photo_items(project, report, user, lang):
    rules = services.OWNERS[REPORT]
    can_delete = bool(rules["can_delete"](user, report))
    rows = list(services.attachments_for(report, user, lang))
    return [{"row": row, "url": documents.file_url(project, row, lang), "delete_url": documents.delete_url(project, row, lang), "can_delete": can_delete,
             "who": _who(row.uploaded_by), "when": documents.when_text(row.created_at, lang)} for row in rows]


def photos_card(project, report, user, lang):
    rules = services.OWNERS[REPORT]
    words = WORDS[lang]
    room = services.room(report, lang)
    can_add = bool(rules["can_attach"](user, report)) and not rules["closed"](report)
    return {"words": dict(words, formats=words["formats"].format(limit=services._human(services.max_bytes(), lang))), "items": _photo_items(project, report, user, lang), "can_add": can_add,
            "blocked": "" if can_add else words["blocked"], "room": room, "space": words["space"].format(files=room["files"], max_files=room["max_files"], left=room["left_text"]),
            "upload_url": f"{reverse('projects:diary_photos', args=[project.pk, report.pk])}?lang={lang}"}


def _report(project, rid):
    report = site_reports.get_report(project, rid)
    if report is None:
        raise Http404("No such report on this project.")
    return report


def report_detail(request, pk, rid):
    _need_view(request)
    lang = _lang(request)
    project = _project(pk)
    report = _report(project, rid)
    words = WORDS[lang]
    crew, equipment = site_reports.rows_of(report)
    day = day_text(report.report_date, lang)
    card = photos_card(project, report, request.user, lang)
    can_edit = access.can_write(request.user) and _is_open(project)
    return render(request, "projects/report_day.html", _context_for(
        request, project, "reports", report=report, day=day, weather=site_reports.weather_label(report.weather, lang), crew=crew, equipment=equipment,
        workers=sum(row.count for row in crew), delayed=bool(report.delayed.strip()), att=card, aw=card["words"], can_edit=can_edit,
        written=words["written"].format(who=_who(report.created_by)) + " " + words["written_on"].format(when=day_text(timezone.localtime(report.created_at).date(), lang)),
        edited=(words["edited_by"].format(who=_who(report.updated_by), when=day_text(timezone.localtime(report.updated_at).date(), lang)) if report.updated_by_id else ""),
        page_title=f"{project.name} — {day}"))


@csrf_exempt  # CSRF is checked inside, after the upload cap handler is installed (the attachments views' own rule)
@require_POST
def report_photos(request, pk, rid):
    """A photo on the report. Fields: ``file``, ``kind`` (photo), ``stage`` (photo). The report is looked up through its project."""

    lang = "en" if request.GET.get("lang") == "en" else "ar"  # not ``_lang``: reading POST here would close the upload handlers before the cap handler is installed
    try:
        project = services.get_owner("projects.project", pk, request.user)
    except services.OwnerNotFound:
        return attachment_views._fail(lang, "not_found", 404)
    except PermissionDenied:
        return attachment_views._fail(lang, "denied", 403)
    report = site_reports.get_report(project, rid)
    if report is None:
        return attachment_views._fail(lang, "not_found", 404)
    return attachment_views._capped_upload(request, report.pk, REPORT)


# ---- the print ----

def report_print(request, pk, rid):
    from printing.company import company_details

    _need_view(request)
    lang = _lang(request)
    project = _project(pk)
    report = _report(project, rid)
    crew, equipment = site_reports.rows_of(report)
    photos = [{"url": documents.file_url(project, row, lang), "name": row.original_filename} for row in services.attachments_for(report, request.user, lang)]
    return render(request, "projects/report_print.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "rw": WORDS[lang], "project": project, "report": report, "company": company_details(),
        "day": day_text(report.report_date, lang), "weather": site_reports.weather_label(report.weather, lang), "crew": crew, "equipment": equipment,
        "workers": sum(row.count for row in crew), "photos": photos, "who": _who(report.created_by),
        "printed": documents.when_text(timezone.now(), lang)})
