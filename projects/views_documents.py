"""Contracting C2a (HG-095): the «المستندات» tab and the three file endpoints of a project.

The tab reads ``projects.documents.card``; the endpoints are thin: each one finds the project through the attachments
registry (403 without the project right, 404 for a project that does not exist), then checks that the file asked for really
belongs to THAT project (or one of its daily reports), then hands over to the ordinary attachments views, which apply the
registry's own rules (type, size, caps, who may add or delete). No second upload path exists.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from attachments import services, views as attachment_views

from . import access, documents
from .contract_views import _context
from .models import Project

PROJECT = "projects.project"


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def documents_tab(request, pk):
    """The tab. 403 without the project right, as the other project tabs."""

    if not access.can_view(request.user):
        raise PermissionDenied("Projects need projects.view_projects or sales.view_sales_invoices.")
    from .views_reports import cta

    lang = _lang(request)
    project = get_object_or_404(Project.objects.select_related("customer"), pk=pk)
    card = documents.card(project, request.user, lang)
    if card is None:
        raise PermissionDenied("Projects need projects.view_projects or sales.view_sales_invoices.")
    return render(request, "projects/documents.html", _context(request, project, "documents", att=card, dw=card["words"], site_cta=cta(project, request.user, lang)))


@csrf_exempt  # CSRF is checked inside, after the upload cap handler is installed (the attachments views' own rule)
@require_POST
def document_upload(request, pk):
    """A document or a photo on the project. Fields: ``file``, ``kind`` (photo|document), ``stage`` (the document type), ``title`` (optional)."""

    return attachment_views._capped_upload(request, pk, PROJECT)


def _project_file(request, pk, attachment_pk):
    """(attachment, None) for a file of this project or of one of its reports, else (None, the refusal)."""

    lang = attachment_views._lang(request)
    try:
        project = services.get_owner(PROJECT, pk, request.user)
    except services.OwnerNotFound:
        return None, attachment_views._fail(lang, "not_found", 404)
    except PermissionDenied:
        return None, attachment_views._fail(lang, "denied", 403)
    row = documents.attachment_belongs(project, attachment_pk)
    if row is None:
        return None, attachment_views._fail(lang, "not_found", 404)
    return row, None


@require_http_methods(["GET", "HEAD"])
def file(request, pk, attachment_pk):
    """The bytes of a document or a report photo: images inline, a PDF as a download, never cached by a shared cache."""

    row, refusal = _project_file(request, pk, attachment_pk)
    return refusal if refusal is not None else attachment_views.file(request, row.pk)


@require_POST
def file_delete(request, pk, attachment_pk):
    """Delete one file (a document: the office; a report photo: those who write reports). Audited by the attachments service."""

    row, refusal = _project_file(request, pk, attachment_pk)
    return refusal if refusal is not None else attachment_views.delete(request, row.pk)
