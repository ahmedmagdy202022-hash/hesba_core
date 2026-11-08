"""S1 endpoints (no new screen): upload and signature on a work order, a JSON list, and the file itself.

Everything answers JSON except the file. Errors are bilingual (`?lang=en`, else Arabic). Login is enforced by the
site's LoginRequiredMiddleware (an anonymous request is redirected to login); permission and entity scope are
enforced by the service layer, through the owner's own visibility rules.
"""

import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseNotModified, JsonResponse
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.http import content_disposition_header
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from . import services
from .handlers import CapUploadHandler, too_big_for_the_cap
from .models import Kind, Stage

WORK_ORDER = "workorders.workorder"


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _fail(lang, key_or_text, status):
    words = services.MESSAGES[lang]
    return JsonResponse({"ok": False, "error": words.get(key_or_text, key_or_text)}, status=status)


def _describe(attachment):
    return {"id": attachment.pk, "kind": attachment.kind, "stage": attachment.stage, "filename": attachment.original_filename,
            "content_type": attachment.content_type_name, "size": attachment.size, "sha256": attachment.sha256,
            "uploaded_by": getattr(attachment.uploaded_by, "get_username", lambda: "")(),
            "created_at": attachment.created_at.isoformat(), "url": reverse("attachments:file", args=[attachment.pk])}


def _denied(exc, lang):
    """403 with the service's own reason when it gave one in the user's language (read-only, delete), else the general one."""

    text = str(exc)
    return _fail(lang, text if text in services.MESSAGES[lang].values() else "denied", 403)


def _owner(request, pk, lang):
    """(owner, None) or (None, error response)."""

    try:
        return services.get_owner(WORK_ORDER, pk, request.user), None
    except services.OwnerNotFound:
        return None, _fail(lang, "not_found", 404)
    except PermissionDenied:
        return None, _fail(lang, "denied", 403)


def _guarded(request, pk, action):
    lang = _lang(request)
    owner, error = _owner(request, pk, lang)
    if error is not None:
        return error
    try:
        return action(owner, lang)
    except PermissionDenied as exc:
        return _denied(exc, lang)
    except ValidationError as exc:
        return _fail(lang, " ".join(exc.messages), 400)


def _too_big(request, lang=None):
    lang = lang or ("en" if request.GET.get("lang") == "en" else "ar")
    limit = services._human(services.max_bytes(), lang)
    return JsonResponse({"ok": False, "error": services.MESSAGES[lang]["size"].format(limit=limit)}, status=413)


@csrf_exempt  # CSRF is checked by ``_upload`` below: the cap handler must be in place before anything reads the body
@require_POST
def work_order_upload(request, pk):
    """A photo or document on a work order. Fields: ``file``, ``kind`` (photo|document), ``stage`` (receive|deliver|other).

    An upload larger than ATTACHMENTS_MAX_BYTES is refused (413) from its Content-Length, or stopped while it streams."""

    if too_big_for_the_cap(request.META.get("CONTENT_LENGTH")):
        return _too_big(request)
    handler = CapUploadHandler(request)
    request.upload_handlers.insert(0, handler)
    return _upload(request, pk, handler)


@csrf_protect
def _upload(request, pk, handler):
    request.POST  # parse the body now, so the cap handler has run before anything is decided

    def action(order, lang):
        if handler.exceeded:
            return _too_big(request, lang)
        kind = request.POST.get("kind") or Kind.PHOTO
        if kind == Kind.SIGNATURE:  # the signature has its own endpoint and its own checks
            raise ValidationError(services.MESSAGES[lang]["kind"])
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError(services.MESSAGES[lang]["no_file"])
        attachment = services.add_attachment(order, upload, request.user, kind=kind, stage=request.POST.get("stage") or Stage.RECEIVE, lang=lang)
        return JsonResponse({"ok": True, "attachment": _describe(attachment)}, status=201)

    return _guarded(request, pk, action)


@require_POST
def work_order_signature(request, pk):
    """The customer's signature as a canvas data URL. Fields: ``data_url`` (form or JSON body), ``stage``."""

    def action(order, lang):
        data = request.POST
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body or b"{}")
            except ValueError:
                raise ValidationError(services.MESSAGES[lang]["data_url"])
            if not isinstance(data, dict):
                raise ValidationError(services.MESSAGES[lang]["data_url"])
        attachment = services.add_signature_png(order, data.get("data_url"), request.user, stage=data.get("stage") or Stage.RECEIVE, lang=lang)
        return JsonResponse({"ok": True, "attachment": _describe(attachment)}, status=201)

    return _guarded(request, pk, action)


@require_http_methods(["GET", "HEAD"])
def work_order_list(request, pk):
    def action(order, lang):
        return JsonResponse({"ok": True, "attachments": [_describe(row) for row in services.attachments_for(order, request.user, lang)]})

    return _guarded(request, pk, action)


@require_http_methods(["GET", "HEAD"])
def file(request, pk):
    """The bytes. Images show inline, a PDF downloads; never sniffed, never shared by a cache."""

    lang = _lang(request)
    try:
        attachment = services.get_attachment(pk, request.user)
    except services.OwnerNotFound:
        return _fail(lang, "not_found", 404)
    except PermissionDenied:
        return _fail(lang, "denied", 403)
    etag = f'"{attachment.sha256}"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
    else:
        name = attachment.original_filename or f"attachment-{attachment.pk}"
        response = HttpResponse(bytes(attachment.data), content_type=attachment.content_type_name)  # bytes(): Postgres gives a memoryview
        response["Content-Disposition"] = content_disposition_header(as_attachment=not attachment.content_type_name.startswith("image/"), filename=name)
    response["ETag"] = etag
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    patch_cache_control(response, private=True, no_cache=True, max_age=0)  # revalidated on every use, kept by no shared cache
    return response


@require_POST
def delete(request, pk):
    """Delete one attachment (managers only; a delivered order's signature stays). Audited."""

    lang = _lang(request)
    try:
        services.delete_attachment(pk, request.user, lang)
    except services.OwnerNotFound:
        return _fail(lang, "not_found", 404)
    except PermissionDenied as exc:
        return _denied(exc, lang)
    except ValidationError as exc:
        return _fail(lang, " ".join(exc.messages), 400)
    return JsonResponse({"ok": True})
