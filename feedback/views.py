"""DEMO-FEEDBACK: the tester's note in, Ahmed's inbox out."""

import csv
import hmac
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.decorators import login_not_required
from django.core.mail import send_mail
from django.db import DatabaseError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .models import Feedback

log = logging.getLogger("hesba.feedback")

MAX_PER_HOUR = 20            # per tester
MAX_ANONYMOUS_PER_HOUR = 60  # all notes from visitors without a demo copy, together
LIMITS = {"message": 2000, "contact": 120, "path": 500, "viewport": 20, "user_agent": 300}


def _demo_only():
    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")


def _clip(value, field):
    return (value or "").strip()[: LIMITS.get(field, 40)]


def _context_of(request):
    """Which activity and role the tester was in, read from their own demo copy."""

    activity = sub_activity = role = ""
    try:
        from settings_core.models import ClientProfile

        profile = ClientProfile.get_active()
        if profile is not None:
            activity, sub_activity = profile.activity_slug or "", profile.sub_activity_slug or ""
        if request.user.is_authenticated:
            profile = getattr(request.user, "hesba_profile", None)
            role = getattr(getattr(profile, "role", None), "code", "") or ("owner" if request.user.is_superuser else "")
    except Exception:  # the note matters more than its context
        pass
    return activity[:40], sub_activity[:40], role[:40]


def _sandbox_of(request):
    """The tester's demo copy (DEMO-SANDBOX); empty for a visitor without one."""

    return getattr(request, "demo_sandbox", None) or ""


def _recent_in_memory(request, sandbox):
    """Notes in the last hour from this tester (or all visitors), counted in the cache."""

    key = f"feedback-rate:{sandbox or 'anonymous'}"
    cache.add(key, 0, 3600)
    try:
        return cache.incr(key) - 1
    except ValueError:  # expired between the two calls
        return 0


@login_not_required
@require_POST
def send(request):
    _demo_only()
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    message = _clip(request.POST.get("message"), "message")
    if len(message) < 3:
        return JsonResponse({"ok": False, "error": "Write a few words first." if lang == "en" else "اكتب ملاحظتك الأول."}, status=400)
    sandbox = _sandbox_of(request)
    try:
        recent = Feedback.objects.filter(sandbox=sandbox, created_at__gte=timezone.now() - timedelta(hours=1)).count()
    except DatabaseError:
        # The feedback database is down; the note still reaches the log below.
        # This server's own memory keeps the hourly limit meanwhile.
        recent = _recent_in_memory(request, sandbox)
    if recent >= (MAX_PER_HOUR if sandbox else MAX_ANONYMOUS_PER_HOUR):
        return JsonResponse({"ok": False, "error": "Thanks! That is plenty for this hour." if lang == "en" else "شكرًا! كده كفاية الساعة دي، كمّل بعدين."}, status=429)
    mood = request.POST.get("mood", "")
    activity, sub_activity, role = _context_of(request)
    note = Feedback(
        message=message,
        mood=mood if mood in dict(Feedback.MOODS) else "",
        contact=_clip(request.POST.get("contact"), "contact"),
        path=_clip(request.POST.get("path") or request.META.get("HTTP_REFERER", ""), "path"),
        lang=lang,
        activity=activity,
        sub_activity=sub_activity,
        role=role,
        sandbox=sandbox[:64],
        viewport=_clip(request.POST.get("viewport"), "viewport"),
        user_agent=_clip(request.META.get("HTTP_USER_AGENT", ""), "user_agent"),
    )
    # R2: every note also goes to the server log (Render -> Logs), so a missing
    # inbox key or a lost database can never lose what a tester wrote again.
    log.warning("FEEDBACK %s", json.dumps({
        "message": note.message, "mood": note.mood, "contact": note.contact, "page": note.path, "activity": note.activity,
        "sub_activity": note.sub_activity, "role": note.role, "lang": note.lang, "viewport": note.viewport, "tester": note.sandbox[:8],
    }, ensure_ascii=False))
    try:
        note.save()
    except DatabaseError:
        log.error("FEEDBACK not stored: the feedback database is unreachable; the note above is the only copy.")
    notify = getattr(settings, "FEEDBACK_NOTIFY_EMAIL", "")
    if notify and getattr(settings, "EMAIL_HOST", ""):
        send_mail(
            f"Hesba feedback: {note.message[:60]}",
            f"{note.message}\n\nPage: {note.path}\nActivity: {note.activity} / {note.sub_activity}\nRole: {note.role}\n"
            f"Mood: {note.mood}\nContact: {note.contact}\nDevice: {note.viewport} {note.user_agent}",
            None, [notify], fail_silently=True,
        )
    return JsonResponse({"ok": True})


def _allowed(request):
    expected = getattr(settings, "FEEDBACK_VIEW_TOKEN", "")
    given = request.GET.get("key", "")
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


@never_cache
@login_not_required
def inbox(request):
    _demo_only()
    if not _allowed(request):
        raise Http404("Not found.")
    notes = Feedback.objects.all()
    query = request.GET.get("q", "").strip()
    if query:
        notes = notes.filter(message__icontains=query)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="hesba-feedback.csv"'
        response.write("﻿")
        writer = csv.writer(response)
        writer.writerow(["time", "message", "mood", "contact", "page", "activity", "sub_activity", "role", "lang", "viewport", "tester"])
        for note in notes:
            writer.writerow([timezone.localtime(note.created_at).strftime("%Y-%m-%d %H:%M"), note.message, note.mood, note.contact,
                             note.path, note.activity, note.sub_activity, note.role, note.lang, note.viewport, note.sandbox[:8]])
        return response
    total = Feedback.objects.count()
    testers = Feedback.objects.exclude(sandbox="").values("sandbox").distinct().count()
    return render(request, "feedback/inbox.html", {
        "notes": notes[:500], "query": query, "key": request.GET.get("key", ""),
        "total": total, "testers": testers,
    })


# DEMO-TRACK ---------------------------------------------------------------

@login_not_required
@require_POST
def hello(request):
    """The visitor's name and phone, if they chose to give them."""

    _demo_only()
    from .models import Visit
    from .tracking import visitor_of

    lang = "en" if request.POST.get("lang") == "en" else "ar"
    visitor = visitor_of(request)
    name = (request.POST.get("name") or "").strip()[:120]
    phone = (request.POST.get("phone") or "").strip()[:40]
    if visitor is None:
        return JsonResponse({"ok": False, "error": "Reload the page first." if lang == "en" else "حدّث الصفحة الأول."}, status=400)
    if not name and not phone:
        return JsonResponse({"ok": False, "error": "Write your name or phone." if lang == "en" else "اكتب اسمك أو رقمك."}, status=400)
    log.warning("DEMO-VISITOR %s", json.dumps({"visitor": visitor[:8], "name": name, "phone": phone}, ensure_ascii=False))
    try:
        Visit.objects.filter(visitor=visitor).update(**{k: v for k, v in (("name", name), ("phone", phone)) if v})
    except DatabaseError:
        log.error("DEMO-VISITOR not stored: the feedback database is unreachable; the line above is the only copy.")
    return JsonResponse({"ok": True})


def _activity_name(activity, sub_activity):
    """The activity in Ahmed's words, e.g. "طبي / عيادة"."""

    from settings_core.setup_catalog import activity_label, sub_activity_label

    if not activity:
        return ""
    return activity_label(activity) + (f" / {sub_activity_label(activity, sub_activity)}" if sub_activity else "")


def _cairo(moment):
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M") if moment else ""


@never_cache
@login_not_required
def visits(request):
    """Ahmed's view of who visited the demo and what they opened."""

    _demo_only()
    if not _allowed(request):
        raise Http404("Not found.")
    from django.db.models import Count, Max, Sum

    from .models import PageView, Visit

    key = request.GET.get("key", "")
    chosen = request.GET.get("visit", "")
    if chosen.isdigit():
        visit = Visit.objects.filter(pk=int(chosen)).first()
        if visit is None:
            raise Http404("Not found.")
        views = list(visit.views.all()[:500])
        for view in views:
            view.activity_name = _activity_name(view.activity, view.sub_activity)
        return render(request, "feedback/visit.html", {
            "visit": visit, "views": views, "key": key,
            "notes": Feedback.objects.filter(sandbox=visit.sandbox).order_by("created_at") if visit.sandbox else [],
        })
    all_visits = Visit.objects.all()
    activity = request.GET.get("activity", "").strip()
    shown = all_visits.filter(activity=activity) if activity else all_visits
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="hesba-demo-visits.csv"'
        response.write("﻿")
        writer = csv.writer(response)
        writer.writerow(["started", "last_seen", "minutes", "name", "phone", "activity", "sub_activity", "role", "pages",
                         "first_page", "last_page", "lang", "device", "visitor"])
        for visit in shown:
            writer.writerow([_cairo(visit.started_at), _cairo(visit.last_seen_at), visit.minutes, visit.name, visit.phone,
                             visit.activity, visit.sub_activity, visit.role, visit.pages, visit.first_path, visit.last_path,
                             visit.lang, visit.user_agent, visit.visitor[:8]])
        return response
    # Which activities were tried, counted from the pages themselves: one visitor
    # may try several activities by starting over.
    tried = (PageView.objects.exclude(activity="").values("activity", "sub_activity")
             .annotate(visitors=Count("visit", distinct=True), pages=Count("id"), last=Max("at"))
             .order_by("-visitors", "-pages"))
    totals = all_visits.aggregate(pages=Sum("pages"))
    tried = [dict(row, label=_activity_name(row["activity"], row["sub_activity"])) for row in tried]
    listed = list(shown[:500])
    for visit in listed:
        visit.activity_name = _activity_name(visit.activity, visit.sub_activity)
    activities = sorted({(row["activity"], _activity_name(row["activity"], "")) for row in tried}, key=lambda pair: pair[1])
    return render(request, "feedback/visits.html", {
        "visits": listed, "tried": tried, "activities": activities, "key": key, "activity": activity,
        "total": all_visits.count(), "named": all_visits.exclude(name="", phone="").count(),
        "pages": totals["pages"] or 0,
        "today": all_visits.filter(last_seen_at__gte=timezone.now() - timedelta(hours=24)).count(),
    })
