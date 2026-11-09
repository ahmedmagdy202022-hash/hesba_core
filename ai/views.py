"""A1 (HG-079): the voice summary's two endpoints.

* ``voice_summary``:
  * GET returns the listener's summary as JSON: the text, the figures with their links, and the audio URL when the audio
    is already cached. It never calls the provider, so opening the dashboard never spends a call.
  * POST (CSRF-protected, sent on the first press of play) voices the summary through the AI layer, or reuses the cached
    audio, and returns the same JSON with the audio URL. A refusal (feature off, kill switch, not connected, daily cap,
    being prepared) comes back as a friendly message in the listener's language.
* ``voice_summary_audio`` streams the cached MP3. It rebuilds the listener's own summary and serves the audio only when
  the requested hash is that summary's hash, in the entity being worked in: a user who may not see a figure, or works in
  another entity, has a different text and so gets a 404. The answer is private and never stored by the browser.

Both answer 404 while the capability is off, and 403 to a listener who may see none of the figures.

A2 (HG-080): ``ask`` is the «اسأل حسبة» page (``ai.ask``).

A3 (HG-081): ``invoice_photo`` (upload), ``invoice_photo_review`` (check, map and save as a draft),
``invoice_photo_discard`` and ``invoice_photo_image`` are «فاتورة شراء من صورة» (``ai.invoice_photo``).
"""

from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from permissions.decorators import require_permission
from settings_core.ui_messages import translate

from . import voice_summary
from .sample import notice
from .summary import build_summary


def _lang(request):
    return "en" if (request.GET.get("lang") or request.POST.get("lang")) == "en" else "ar"


def _private(response):
    patch_cache_control(response, private=True, no_store=True, max_age=0)
    return response


def _json(data, status=200):
    return _private(JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False}))


@require_http_methods(["GET", "POST"])
def summary(request):
    lang = _lang(request)
    if not voice_summary.is_on():
        return _json({"ok": False, "error": translate("This AI feature is not switched on.", lang)}, status=404)
    built = build_summary(request.user, lang)
    if not built.has_figures:
        return _json({"ok": False, "error": translate("There is nothing to summarise for you yet.", lang)}, status=403)
    ready = voice_summary.is_cached(built)  # an EXISTS query: the bytes are read only by ``audio`` below
    if request.method == "POST" and not ready:
        try:
            voice_summary.speak(built, request.user)
        except ValidationError as exc:
            return _json({"ok": False, **built.as_dict(), "audio_url": "", "error": translate(exc.messages[0], lang)}, status=409)
        ready = True
    return _json({"ok": True, **built.as_dict(), "audio_url": voice_summary.audio_url(built) if ready else ""})


@require_http_methods(["GET", "POST"])
def ask(request):
    """A2 (HG-080): «اسأل حسبة». GET shows the box and the suggestions and never calls the model. POST (CSRF) asks one
    question and shows its card. 404 while the capability is off (the module gate also closes ``/ai/ask/``)."""

    from . import ask as ask_feature
    from .services import QUESTION_MAX_CHARS, clean_question

    lang = _lang(request)
    if not ask_feature.is_on():
        raise Http404("Ask Hesba is not switched on.")
    words = ask_feature.WORDS[lang]
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "page_title": words["title"], "w": words,
               "suggestions": ask_feature.suggestions(request.user, lang), "max_chars": QUESTION_MAX_CHARS,
               "can_ask": ask_feature.can_ask(request.user), "sample": notice(ask_feature.FEATURE, lang)}
    if not context["can_ask"]:  # no tool this person may use: no box, and a POST never reaches the model
        context["answer"] = ask_feature.answer("", request.user, lang)
        return _private(render(request, "ai/ask.html", context, status=403))
    if request.method == "POST":
        question = request.POST.get("q", "")
        context["answer"] = ask_feature.answer(question, request.user, lang)
        # shown back to its own asker only
        context["question"] = clean_question(question[:ask_feature.RAW_MAX_CHARS])[:QUESTION_MAX_CHARS]
    return _private(render(request, "ai/ask.html", context))


@require_GET
def audio(request, digest):
    if not voice_summary.is_on():
        raise Http404("The voice summary is not switched on.")
    built = build_summary(request.user, _lang(request))
    if not built.has_figures or digest != built.text_sha256:
        raise Http404("No audio for this summary.")
    row = voice_summary.cached_audio(built)
    if row is None:
        raise Http404("No audio for this summary.")
    response = HttpResponse(bytes(row.audio), content_type=row.content_type or "audio/mpeg")
    response["Content-Length"] = str(row.size)
    response["Content-Disposition"] = 'inline; filename="hesba-summary.mp3"'
    response["X-Content-Type-Options"] = "nosniff"
    return _private(response)


# ---- A3 (HG-081): «فاتورة شراء من صورة» ----

PHOTO_TEMPLATE = "ai/invoice_photo.html"


def _photo_lang(request):
    """The language from the query string only: the upload's body must not be parsed before its cap handler is in place."""

    return "en" if request.GET.get("lang") == "en" else "ar"


def _photo_page(request, lang, status=200, **extra):
    from . import invoice_photo as feature

    w = feature.words(lang)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "page_title": w["title"], "w": w,
               "upload_hint": w["upload_hint"].format(limit=feature.human_size(feature.max_photo_bytes(), lang)),
               "accept": ",".join(feature.photo_types()), "sample": notice(feature.FEATURE, lang), **extra}
    return _private(render(request, PHOTO_TEMPLATE, context, status=status))


def _photo_gate(request, lang):
    """404 while the capability is off; 403 (with the reason on the page) to someone who may not create and see purchase
    invoices. Either way nothing is read and nothing is sent."""

    from . import invoice_photo as feature

    if not feature.is_on():
        raise Http404("The invoice photo is not switched on.")
    if not feature.can_use(request.user):
        w = feature.words(lang)
        return _photo_page(request, lang, status=403, refused={"title": w["not_allowed"], "message": w["need_permission"]})
    return None


@csrf_exempt  # CSRF is checked by ``_photo_upload``: the cap handler must be in place before anything reads the body
@require_http_methods(["GET", "POST"])
@require_permission("purchases.create_purchase_invoice")
def invoice_photo(request):
    """The upload page. GET shows the form and never calls the model. POST (CSRF, multipart ``photo``) reads one photo
    through A0's door and, when the reading is accepted, redirects to its review page (``invoice_photo_review``)."""

    from attachments.handlers import CapUploadHandler, too_big_for_the_cap

    from . import invoice_photo as feature

    lang = _photo_lang(request)
    closed = _photo_gate(request, lang)
    if closed is not None:
        return closed
    if request.method == "GET":
        return _photo_page(request, lang)
    limit = feature.max_photo_bytes()
    if too_big_for_the_cap(request.META.get("CONTENT_LENGTH"), limit):  # refused before the body is read
        w = feature.words(lang)
        return _photo_page(request, lang, status=413, error=w["too_big"].format(limit=feature.human_size(limit, lang)))
    handler = CapUploadHandler(request, limit=limit)
    request.upload_handlers.insert(0, handler)
    return _photo_upload(request, lang, handler)


@csrf_protect
def _photo_upload(request, lang, handler):
    from django.shortcuts import redirect

    from . import invoice_photo as feature

    request.POST  # parse the body now, so the cap handler has run before anything is decided
    w = feature.words(lang)
    if handler.exceeded:
        return _photo_page(request, lang, status=413, error=w["too_big"].format(limit=feature.human_size(feature.max_photo_bytes(), lang)))
    outcome = feature.read_photo(request.FILES.get("photo"), request.user, lang)
    if outcome.pending is None:
        return _photo_page(request, lang, status=outcome.status, error=outcome.message)
    return redirect(f"{reverse('ai:invoice_photo_review', args=[outcome.pending.token])}?lang={lang}")


@require_http_methods(["GET", "POST"])
@require_permission("purchases.create_purchase_invoice")
def invoice_photo_review(request, token):
    """The review page of one waiting photo (its own person only, in the entity it was read in). GET shows it; POST (CSRF)
    saves the draft and redirects to the purchase invoice's own screen, where the person posts it. Never calls the model."""

    from django.contrib import messages
    from django.shortcuts import redirect

    from . import invoice_photo as feature

    lang = _lang(request)
    closed = _photo_gate(request, lang)
    if closed is not None:
        return closed
    w = feature.words(lang)
    try:
        pending = feature.pending_for(token, request.user, lang)
    except feature.Refused as refused:
        return _photo_page(request, lang, status=refused.status, error=refused.message)
    if request.method == "POST":
        try:
            invoice = feature.save_draft(token, request.POST, request.user, lang)
        except feature.Refused as refused:
            if refused.status == 404:
                return _photo_page(request, lang, status=404, error=refused.message)
            return _photo_page(request, lang, status=refused.status, review=feature.review(pending, request.POST, lang),
                               choices=feature.choices(), error=refused.message)
        messages.success(request, w["saved"])
        return redirect(f"{reverse('purchases:detail', args=[invoice.pk])}?lang={lang}")
    return _photo_page(request, lang, review=feature.review(pending, lang=lang), choices=feature.choices())


@require_POST
@require_permission("purchases.create_purchase_invoice")
def invoice_photo_discard(request, token):
    """«صوّر تاني»: forget the waiting photo (its own person only) and go back to the upload page."""

    from django.shortcuts import redirect

    from . import invoice_photo as feature
    from .models import PendingInvoicePhoto

    lang = _lang(request)
    closed = _photo_gate(request, lang)
    if closed is not None:
        return closed
    PendingInvoicePhoto.objects.filter(token=str(token)[:64], user=request.user).delete()
    return redirect(f"{reverse('ai:invoice_photo')}?lang={lang}")


@require_GET
@require_permission("purchases.create_purchase_invoice")
def invoice_photo_image(request, token):
    """The waiting photo itself, for its own person's review page only: never sniffed, sandboxed, never cached."""

    from . import invoice_photo as feature

    lang = _lang(request)
    if not feature.is_on() or not feature.can_use(request.user):
        raise Http404("No photo.")
    try:
        pending = feature.pending_for(token, request.user, lang)
    except feature.Refused:
        raise Http404("No photo.") from None
    response = HttpResponse(bytes(pending.image), content_type=pending.content_type)
    response["Content-Length"] = str(pending.size)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return _private(response)
