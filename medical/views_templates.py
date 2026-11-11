"""M1 (HG-089): the visit templates' screens and the two JSON answers the visit form uses.

Every view is gated like a clinical screen (``views._guard(write=True)``: 404 outside a medical install, 403 without a
clinical permission; an employee link alone gives nothing). Who may change which template is ``visit_templates.can_edit``.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from . import prescribing, services, visit_templates
from .models import VisitTemplate
from .views import _base, _guard, _lang, _tabs
from .views_rx import _keys

FILTER_KEYS = ("scope", "specialty", "q", "tl")


def _filters(source, user):
    scope = source.get("scope") if source.get("scope") in ("mine", "shared", "others") else "mine"
    if scope == "others" and not visit_templates.is_manager(user):
        scope = "mine"
    return {"scope": scope, "specialty": source.get("specialty") if source.get("specialty") in visit_templates.SPECIALTIES else "",
            "q": (source.get("q") or "").strip()[:100], "tl": source.get("tl") if source.get("tl") in ("ar", "en", "all") else ""}


def _back(request, lang, **override):
    """The list address again with the filters it was opened with (only the known keys, from a posted form)."""

    kept = {key: request.POST.get(key, "") for key in FILTER_KEYS if request.POST.get(key)}
    kept.update(override)
    return f"{reverse('medical:visit_templates')}?{urlencode({'lang': lang, **{k: v for k, v in kept.items() if v}})}"


def _tab_context(request, lang):
    return {"tw": visit_templates.words(lang), **_tabs(request, "templates")}


def template_list(request):
    _guard(request, write=True)
    lang = _lang(request)
    tw = visit_templates.words(lang)
    visit_templates.seed()
    user = request.user
    filters = _filters(request.GET, user)
    text_lang = lang if filters["tl"] == "" else ("" if filters["tl"] == "all" else filters["tl"])
    rows = visit_templates.listing(user, filters["scope"], filters["specialty"], filters["q"], text_lang)
    groups = {}
    for row in rows:
        groups.setdefault((row.owner_id, row.specialty, row.lang), []).append(row.pk)
    for row in rows:
        siblings = groups[(row.owner_id, row.specialty, row.lang)]
        row.can_edit = visit_templates.can_edit(user, row)
        row.specialty_label = visit_templates.label(row.specialty, lang)
        row.first, row.last = siblings[0] == row.pk, siblings[-1] == row.pk
        row.lines_count = len(row.lines or [])
        row.lines_text, row.delete_text = tw["lines_n"].format(n=row.lines_count), tw["delete_q"].format(name=row.name)
        row.owner_label = tw["shared"] if row.owner_id is None else (tw["mine"] if row.owner_id == user.pk else (row.owner.get_full_name() or row.owner.get_username()))
    filtered = bool(filters["q"] or filters["specialty"] or filters["tl"])
    query = urlencode({"lang": lang, **{key: value for key, value in filters.items() if value}})
    return render(request, "medical/visit_templates.html", _base(
        lang, page_title=tw["title"], **_tab_context(request, lang), rows=rows, filters=filters, filtered=filtered, text_lang=text_lang, query=query,
        scopes=[("mine", tw["scope_mine"]), ("shared", tw["scope_shared"])] + ([("others", tw["scope_others"])] if visit_templates.is_manager(user) else []),
        specialties=visit_templates.specialty_options(lang), manager=visit_templates.is_manager(user)))


def template_edit(request, pk=None):
    _guard(request, write=True)
    lang = _lang(request)
    tw = visit_templates.words(lang)
    user = request.user
    template = get_object_or_404(VisitTemplate, pk=pk) if pk else None
    if template is not None and not (visit_templates.can_edit(user, template) or template.owner_id in (None, user.pk)):
        raise PermissionDenied(tw["not_yours"])  # another doctor's personal template is not even shown to a doctor
    read_only = template is not None and not visit_templates.can_edit(user, template)
    if read_only and request.method == "POST":
        raise PermissionDenied(tw["not_yours"])
    manager = visit_templates.is_manager(user)
    errors, post = [], None
    if request.method == "POST":
        post = request.POST
        text_lang = post.get("text_lang") if post.get("text_lang") in visit_templates.LANGS else ("en" if lang == "en" else "ar")
        data = {field: post.get(field) for field in ("name", "specialty", *visit_templates.TEXT_FIELDS)}
        data["lang"] = text_lang
        data["active"] = post.get("active") == "1"
        if manager:
            data["scope"] = post.get("scope")
        data["lines"] = visit_templates.lines_from_post(post, text_lang, lang, errors)
        if not errors:
            try:
                saved = visit_templates.save(data, user, template, lang)
            except ValidationError as exc:
                errors = list(exc.messages)
            else:
                messages.success(request, tw["saved"])
                return redirect(_back(request, lang, scope="shared" if saved.owner_id is None else ("mine" if saved.owner_id == user.pk else "others")))
    raw = prescribing.lines_from_post(post) if post is not None else (visit_templates.raw_lines(template) if template is not None else [])
    shown = {"name": "", "lang": "en" if lang == "en" else "ar", "specialty": visit_templates.default_specialty() or "general", "complaint": "", "examination": "",
             "diagnosis": "", "treatment": "", "active": True, "scope": "mine"}
    if template is not None:
        shown.update({field: getattr(template, field) for field in ("name", "lang", "specialty", "complaint", "examination", "diagnosis", "treatment", "active")})
        shown["scope"] = "shared" if template.owner_id is None else "mine"
    if post is not None:
        shown.update({field: post.get(field, "") for field in ("name", "specialty", "complaint", "examination", "diagnosis", "treatment")})
        shown["lang"] = post.get("text_lang") or shown["lang"]
        shown["active"] = post.get("active") == "1"
        shown["scope"] = post.get("scope") or shown["scope"]
    options = visit_templates.specialty_options(lang)
    if template is not None and template.specialty not in [o["code"] for o in options]:
        options.append({"code": template.specialty, "label": visit_templates.label(template.specialty, lang)})
    owner_name = ""
    if template is not None and template.owner_id not in (None, user.pk):
        owner_name = template.owner.get_full_name() or template.owner.get_username()
    return render(request, "medical/visit_template_form.html", _base(
        lang, page_title=tw["edit"] if template else tw["new"], **_tab_context(request, lang), template=template, shown=shown, errors=errors, read_only=read_only,
        manager=manager, specialties=options, owner_name=owner_name, back=_back_url(request, lang), **visit_templates.editor_context(raw, lang)))


def _back_url(request, lang):
    filters = _filters(request.GET, request.user)
    return f"{reverse('medical:visit_templates')}?{urlencode({'lang': lang, **{k: v for k, v in filters.items() if v}})}"


def template_action(request, pk, action):
    """POST: move up or down, switch on or off, copy to mine, delete."""

    _guard(request, write=True)
    lang = _lang(request)
    tw = visit_templates.words(lang)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)
    template = get_object_or_404(VisitTemplate, pk=pk)
    user = request.user
    if action in ("up", "down"):
        visit_templates.move(template, action, user, lang)
        messages.success(request, tw["moved"])
    elif action in ("on", "off"):
        visit_templates.set_active(template, action == "on", user, lang)
        messages.success(request, tw["turned_on"] if action == "on" else tw["turned_off"])
    elif action == "delete":
        visit_templates.delete(template, user, lang)
        messages.success(request, tw["deleted"])
    elif action == "copy":
        try:
            visit_templates.copy(template, user, lang)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(_back(request, lang))
        messages.success(request, tw["copied"])
        return redirect(_back(request, lang, scope="mine"))
    else:
        raise Http404("Unknown action.")
    return redirect(_back(request, lang))


@never_cache  # a doctor's own templates: never kept by a browser or a proxy
def templates_data(request):
    """GET (``patient`` and ``animal`` optional): the templates this person may apply on a visit, as JSON for the visit form's sheet.

    Same guard as ``medical:drug_search`` (404 outside a medical install, 403 without a clinical permission). Own templates
    first, then shared ones; another doctor's personal template is never in it. No patient data: ``patient`` and ``animal`` only
    select the allergy words, and a medicine line carries just its own drug's matching word."""

    _guard(request, write=True)
    lang = _lang(request)
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "GET only"}, status=405)
    visit_templates.seed()
    keys = _keys(request)
    found = visit_templates.payload(request.user, lang, keys)
    return JsonResponse({"ok": True, "count": len(found), "default_specialty": visit_templates.default_specialty(),
                         "specialties": visit_templates.specialty_options(lang), "templates": found},
                        json_dumps_params={"ensure_ascii": False})


@never_cache
def template_from_visit(request):
    """POST (the visit form's current texts and medicine lines, ``name`` and ``specialty``): a personal template, as JSON."""

    _guard(request, write=True)
    lang = _lang(request)
    tw = visit_templates.words(lang)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)
    try:
        saved = visit_templates.save_from_form(request.POST, request.user, lang)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=400, json_dumps_params={"ensure_ascii": False})
    return JsonResponse({"ok": True, "id": saved.pk, "name": saved.name, "message": tw["save_done"].format(name=saved.name),
                         "url": f"{reverse('medical:visit_template_edit', args=[saved.pk])}?lang={lang}"}, json_dumps_params={"ensure_ascii": False})
