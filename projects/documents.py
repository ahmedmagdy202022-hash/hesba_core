"""Contracting C2a (HG-095): a project's documents: what the «المستندات» tab shows.

The files are ordinary attachments (``attachments.services``) owned by the project; there is no second upload path and no table
of their own. The kind of a document (contract, drawing, letter, permit, photo, other) rides on the attachment's ``stage``,
mapped onto six of the stages the attachments table already allows, so no attachments migration is needed. The mapping lives
here and nowhere else: the upload form posts the stored value, and this module turns it back into a word.

Rules (who sees, who adds, who deletes, the limits, the entity) are the registry's, in ``attachments.services.OWNERS``; this
module only reads them, so the tab and the endpoints can never disagree.
"""

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from attachments import services
from attachments.models import Attachment, Kind, Stage

KINDS = ("contract", "drawing", "letter", "permit", "photo", "other")
#: the stored stage of each kind (the stage column's words are the attachments table's; only this map knows what they mean here)
KIND_STAGE = {"contract": Stage.RECEIVE, "drawing": Stage.SCAN, "letter": Stage.REPORT, "permit": Stage.DELIVER, "photo": Stage.PHOTO, "other": Stage.OTHER}
STAGE_KIND = {stage.value: kind for kind, stage in KIND_STAGE.items()}

WORDS = {
    "ar": {
        "title": "المستندات", "intro": "العقد والرسومات والمراسلات والتصاريح وصور الموقع. كل ملف بعنوانه ومين ضافه وامتى.",
        "contract": "عقود", "drawing": "رسومات", "letter": "مراسلات", "permit": "تصاريح", "photo": "صور الموقع", "other": "أخرى",
        "kind_one": {"contract": "عقد", "drawing": "رسم", "letter": "مراسلة", "permit": "تصريح", "photo": "صورة موقع", "other": "أخرى"},
        "kind": "نوع المستند", "doc_title": "العنوان", "title_hint": "اختياري. من غيره الملف بيتسمّى باسمه.", "title_ph": "مثلاً: رسم الدور الأول",
        "camera": "صوّر", "choose": "اختار ملف", "none": "لسه مفيش مستندات على المشروع ده.", "none_kind": "مفيش.", "open": "افتح", "pdf": "PDF",
        "by": "ضافه", "delete": "امسح", "photo_of": "صورة",
        "formats": "صور JPG أو PNG أو WEBP وملفات PDF بس، لحد {limit} للملف. الرسم الكبير اتقسّمه أو صغّره قبل ما ترفعه PDF.",
        "space": "المستخدم {used} من {max}، و{files} من {max_files} ملف.",
        "space_low": "المساحة الفاضلة {left}. مساحة المرفقات كلها في قاعدة البيانات محدودة، ومسّح اللي مش محتاجه.",
        "space_full": "مفيش مساحة فاضلة لمستندات جديدة. امسح مستند أو كلّم صاحب الحساب.",
        "blocked": "إضافة المستندات لمهندس الموقع أو لصاحب صلاحية الفواتير بس.", "count": "{n} ملف",
    },
    "en": {
        "title": "Documents", "intro": "The contract, drawings, letters, permits and site photos. Each file with its title, who added it and when.",
        "contract": "Contracts", "drawing": "Drawings", "letter": "Letters", "permit": "Permits", "photo": "Site photos", "other": "Other",
        "kind_one": {"contract": "Contract", "drawing": "Drawing", "letter": "Letter", "permit": "Permit", "photo": "Site photo", "other": "Other"},
        "kind": "Document type", "doc_title": "Title", "title_hint": "Optional. Without it the file keeps its own name.", "title_ph": "e.g. First floor plan",
        "camera": "Take photo", "choose": "Choose a file", "none": "No documents on this project yet.", "none_kind": "None.", "open": "Open", "pdf": "PDF",
        "by": "Added by", "delete": "Delete", "photo_of": "Photo",
        "formats": "JPG, PNG or WEBP images and PDF files only, up to {limit} each. Split or shrink a large drawing before uploading it as a PDF.",
        "space": "{used} used of {max}, and {files} of {max_files} files.",
        "space_low": "{left} of room left. The space for all attachments in the database is limited: delete what you no longer need.",
        "space_full": "No room left for new documents. Delete a document or tell the account owner.",
        "blocked": "Only the site engineer, or someone who may create invoices, can add documents.", "count": "{n} files",
    },
}

#: below this much room the tab warns before the next upload refuses
LOW_BYTES = 8 * 1024 * 1024


def _who(user):
    return (user.get_full_name() or user.get_username()) if user is not None else ""


def when_text(moment, lang):
    """When a file was added, in words: «9 نوفمبر 2026، 14:05» (the day as every screen writes it, then the time)."""

    from django.utils import timezone

    from settings_core.templatetags.hesba_format import day_text

    local = timezone.localtime(moment)
    return f"{day_text(local.date(), lang)}, {local:%H:%M}" if lang == "en" else f"{day_text(local.date(), lang)}، {local:%H:%M}"


def file_url(project, attachment, lang):
    return f"{reverse('projects:file', args=[project.pk, attachment.pk])}?lang={lang}"


def delete_url(project, attachment, lang):
    return f"{reverse('projects:file_delete', args=[project.pk, attachment.pk])}?lang={lang}"


def _item(project, row, lang, can_delete):
    words = WORDS[lang]
    kind = STAGE_KIND.get(row.stage, "other")
    return {"row": row, "kind": kind, "category": words["kind_one"][kind], "url": file_url(project, row, lang), "delete_url": delete_url(project, row, lang),
            "is_image": row.content_type_name.startswith("image/"), "can_delete": can_delete, "who": _who(row.uploaded_by),
            "when": when_text(row.created_at, lang)}


def card(project, user, lang):
    """Everything the documents tab needs, or None when this user may not see the project's documents (the tab is then a 403 anyway)."""

    lang = "en" if lang == "en" else "ar"
    rules = services.OWNERS[services.owner_label(project)]
    try:
        rows = list(services.attachments_for(project, user, lang))
    except (PermissionDenied, services.OwnerNotFound):
        return None
    words = WORDS[lang]
    can_delete = bool(rules["can_delete"](user, project))
    can_add = bool(rules["can_attach"](user, project)) and not rules["closed"](project)
    room = services.room(project, lang)
    items = [_item(project, row, lang, can_delete) for row in reversed(rows)]  # newest first
    groups = [{"kind": kind, "label": words[kind], "items": [i for i in items if i["kind"] == kind]} for kind in KINDS]
    return {
        "words": dict(words, formats=words["formats"].format(limit=services._human(services.max_bytes(), lang))), "groups": [g for g in groups if g["items"]],
        "all_groups": groups, "total": len(items), "can_add": can_add, "blocked": "" if can_add else words["blocked"], "room": room,
        "space": words["space"].format(used=room["used_text"], max=room["max_text"], files=room["files"], max_files=room["max_files"]),
        "space_note": (words["space_full"] if room["bytes_left"] <= 0 or room["files_left"] <= 0 else
                       words["space_low"].format(left=room["left_text"]) if room["bytes_left"] < LOW_BYTES else ""),
        "kinds": [{"value": KIND_STAGE[kind].value, "label": words["kind_one"][kind]} for kind in KINDS],
        "upload_url": f"{reverse('projects:document_upload', args=[project.pk])}?lang={lang}",
    }


def attachment_belongs(project, attachment_pk):
    """The attachment, if it is on this project, on one of its daily reports or on one of its variation orders, else None
    (another project's file id finds nothing)."""

    from django.contrib.contenttypes.models import ContentType

    from .models import Project, SiteReport, VariationOrder

    raw = str(attachment_pk or "")
    if not raw.isdigit():
        return None
    row = Attachment.objects.filter(pk=raw).defer("data").first()
    if row is None:
        return None
    if row.content_type_id == ContentType.objects.get_for_model(Project).pk:
        return row if row.object_id == project.pk else None
    if row.content_type_id == ContentType.objects.get_for_model(SiteReport).pk:
        return row if SiteReport.objects.filter(pk=row.object_id, project=project).exists() else None
    if row.content_type_id == ContentType.objects.get_for_model(VariationOrder).pk:  # Contracting C1 (HG-103): the signed copy
        return row if VariationOrder.objects.filter(pk=row.object_id, project=project).exists() else None
    return None
