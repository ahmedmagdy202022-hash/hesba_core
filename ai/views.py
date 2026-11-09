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
"""

from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET, require_http_methods

from settings_core.ui_messages import translate

from . import voice_summary
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
               "can_ask": ask_feature.can_ask(request.user)}
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
