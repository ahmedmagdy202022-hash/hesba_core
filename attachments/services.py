"""S1 (MARKET-PARITY, HG-072): the service layer for photos, signatures and files kept on a record.

Rules, all enforced here and not in the views:
- you can attach to, list or read an owner only if you can see that owner (for a work order: the work-order view
  permission, the technician scoping and the entity scope, exactly ``workorders.services.visible_to``);
- the file's real type comes from its first bytes, never from the client's name or mime; only jpeg, png, webp and
  pdf can pass, so SVG and HTML never do whatever the settings say;
- a size cap per file (``ATTACHMENTS_MAX_BYTES``; a signature has its own smaller cap);
- every upload writes an audit entry.
Nothing here posts money, stock, a cashbox movement or a ledger row.
"""

import base64
import binascii
import hashlib
import re

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope

from .models import Attachment, Kind, Stage

PNG = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
DATA_URL = re.compile(r"^data:image/png;base64,", re.IGNORECASE)
#: What the code can verify by magic bytes. The settings list can only narrow this.
SNIFFABLE = ("image/jpeg", "image/png", "image/webp", "application/pdf")
IMAGES = ("image/jpeg", "image/png", "image/webp")
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}
#: Refused by name even before the bytes are read: they can carry script.
MARKUP_TYPES = ("image/svg+xml", "text/html", "application/xhtml+xml", "text/xml", "application/xml")
MARKUP_EXTENSIONS = (".svg", ".svgz", ".html", ".htm", ".xhtml", ".xml")

MESSAGES = {
    "ar": {"denied": "مش مسموح لك بالمرفقات على السجل ده.", "not_found": "المرفق أو السجل مش موجود.", "kind": "نوع المرفق لازم يكون صورة أو مستند أو توقيع.",
           "stage": "مرحلة المرفق لازم تكون استلام أو تسليم أو أخرى.", "empty": "الملف فاضي أو مش اتبعت.",
           "size": "حجم الملف أكبر من المسموح ({limit}).", "type": "نوع الملف مش مسموح. المسموح: صور JPG أو PNG أو WEBP وملفات PDF.",
           "markup": "ملفات SVG وHTML مش مسموحة.", "photo": "الصورة لازم تكون ملف صورة (JPG أو PNG أو WEBP).",
           "signature": "التوقيع لازم يكون صورة PNG حقيقية.", "data_url": "بيانات التوقيع مش صحيحة؛ لازم تكون صورة PNG مشفرة base64.",
           "no_file": "اختار ملف الأول.", "no_signature": "ارسم التوقيع الأول."},
    "en": {"denied": "You may not use attachments on this record.", "not_found": "The attachment or the record was not found.",
           "kind": "The attachment kind must be photo, document or signature.", "stage": "The attachment stage must be receive, deliver or other.",
           "empty": "The file is empty or was not sent.", "size": "The file is larger than allowed ({limit}).",
           "type": "This file type is not allowed. Allowed: JPG, PNG or WEBP images and PDF files.", "markup": "SVG and HTML files are not allowed.",
           "photo": "A photo must be an image file (JPG, PNG or WEBP).", "signature": "A signature must be a real PNG image.",
           "data_url": "The signature data is not valid; it must be a base64 PNG image.", "no_file": "Choose a file first.",
           "no_signature": "Draw the signature first."},
}


class OwnerNotFound(LookupError):
    """The record does not exist for this user (missing, another entity, or another technician's)."""


# ---- who owns what: a small registry, so appointments and customers can join later ----

def _work_orders(user):
    from workorders import services as work
    from workorders.models import WorkOrder

    if not work.is_workshop():
        return WorkOrder.objects.none()
    if not (work.can_view(user) or work.technician_of(user) is not None):
        raise PermissionDenied("Work orders need sales.view_sales_invoices.")
    return work.visible_to(user)


#: ``app_label.modelname`` -> function(user) giving the owners that user may see (it raises PermissionDenied for no right at all).
OWNERS = {"workorders.workorder": _work_orders}


def owner_label(owner_or_model):
    meta = owner_or_model._meta
    return f"{meta.app_label}.{meta.model_name}"


def visible_owners(label, user):
    if label not in OWNERS:
        raise OwnerNotFound(label)
    return OWNERS[label](user)


def get_owner(label, pk, user):
    """The owner this user may see, else OwnerNotFound (404) or PermissionDenied (403)."""

    owner = visible_owners(label, user).filter(pk=pk).first()
    if owner is None:
        raise OwnerNotFound(f"{label} {pk}")
    return owner


def _check_owner(owner, user, words):
    try:
        allowed = visible_owners(owner_label(owner), user).filter(pk=owner.pk).exists()
    except OwnerNotFound:
        allowed = False
    if not allowed:
        raise PermissionDenied(words["denied"])


# ---- reading the file ----

def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _human(limit, lang):
    if limit >= 1024 * 1024:
        number = f"{limit / (1024 * 1024):.1f}".rstrip("0").rstrip(".")
        return f"{number} MB" if lang == "en" else f"{number} ميجا"
    number = max(1, limit // 1024)
    return f"{number} KB" if lang == "en" else f"{number} كيلو"


def max_bytes(kind=None):
    cap = int(settings.ATTACHMENTS_MAX_BYTES)
    if kind == Kind.SIGNATURE:
        cap = min(cap, int(settings.ATTACHMENTS_SIGNATURE_MAX_BYTES))
    return cap


def allowed_types():
    narrowed = {str(value).strip().lower() for value in (settings.ATTACHMENTS_ALLOWED_TYPES or ())}
    return [value for value in SNIFFABLE if value in narrowed]


def sniff(raw):
    """The real type from the first bytes, or None."""

    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == PNG:
        return "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:5] == b"%PDF-":
        return "application/pdf"
    return None


def _looks_like_markup(raw):
    head = raw[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith(b"<") or b"<svg" in head or b"<html" in head or b"<script" in head


def _valid_png(raw):
    """A whole PNG, not just its signature: IHDR first, IEND last."""

    return len(raw) > 33 and raw[:8] == PNG and raw[8:16] == b"\x00\x00\x00\rIHDR" and raw.endswith(PNG_END)


def _clean_name(name):
    name = re.split(r"[\\/]", str(name or ""))[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:255]


def _read(upload, limit, words, lang):
    """(bytes, filename, declared mime) from bytes or an uploaded file, never reading past the cap."""

    if isinstance(upload, (bytes, bytearray, memoryview)):
        raw, name, declared = bytes(upload[: limit + 1]), "", ""
    elif upload is not None and hasattr(upload, "read"):
        size = getattr(upload, "size", None)
        if size is not None and size > limit:
            raise ValidationError(words["size"].format(limit=_human(limit, lang)))
        raw = upload.read(limit + 1)
        name, declared = getattr(upload, "name", "") or "", getattr(upload, "content_type", "") or ""
    else:
        raise ValidationError(words["no_file"])
    if len(raw) > limit:
        raise ValidationError(words["size"].format(limit=_human(limit, lang)))
    if not raw:
        raise ValidationError(words["empty"])
    return raw, _clean_name(name), declared.split(";")[0].strip().lower()


def _check_kind_stage(kind, stage, words):
    if kind not in Kind.values:
        raise ValidationError(words["kind"])
    if stage not in Stage.values:
        raise ValidationError(words["stage"])


def _store(owner, raw, name, user, kind, stage, words):
    real = sniff(raw)
    if real is None or real not in allowed_types():
        raise ValidationError(words["markup"] if _looks_like_markup(raw) else words["type"])
    if kind == Kind.PHOTO and real not in IMAGES:
        raise ValidationError(words["photo"])
    if kind == Kind.SIGNATURE and (real != "image/png" or not _valid_png(raw)):
        raise ValidationError(words["signature"])
    sha = hashlib.sha256(raw).hexdigest()
    with transaction.atomic():
        attachment = Attachment.objects.create(
            content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk, entity=getattr(owner, "entity", None),
            kind=kind, stage=stage, original_filename=name or f"{kind}-{sha[:8]}.{EXTENSIONS[real]}", content_type_name=real,
            size=len(raw), sha256=sha, data=raw, uploaded_by=user)
        AuditLog.objects.create(
            event_type=AuditEventType.CREATE, actor=user, module="attachments", action="add_attachment", object_type="attachments.Attachment",
            object_id=str(attachment.pk), before_data={},
            after_data={"owner": owner_label(owner), "owner_id": owner.pk, "kind": kind, "stage": stage, "filename": attachment.original_filename,
                        "content_type": real, "size": attachment.size, "sha256": sha})
    return attachment


# ---- the service functions ----

def add_attachment(owner, uploaded_file_or_bytes, user, kind=Kind.PHOTO, stage=Stage.OTHER, lang="ar"):
    """Keep a photo, a document or a signature on ``owner``. Returns the Attachment."""

    words = _words(lang)
    _check_owner(owner, user, words)
    _check_kind_stage(kind, stage, words)
    raw, name, declared = _read(uploaded_file_or_bytes, max_bytes(kind), words, lang)
    if declared in MARKUP_TYPES or name.lower().endswith(MARKUP_EXTENSIONS):
        raise ValidationError(words["markup"])
    return _store(owner, raw, name, user, kind, stage, words)


def add_signature_png(owner, data_url, user, stage=Stage.OTHER, lang="ar"):
    """Keep the customer's signature, drawn on a canvas and sent as a ``data:image/png;base64,`` URL."""

    words = _words(lang)
    _check_owner(owner, user, words)
    _check_kind_stage(Kind.SIGNATURE, stage, words)
    text = str(data_url or "").strip()
    if not text:
        raise ValidationError(words["no_signature"])
    head = DATA_URL.match(text)
    if head is None:
        raise ValidationError(words["data_url"])
    encoded = "".join(text[head.end():].split())
    limit = max_bytes(Kind.SIGNATURE)
    if len(encoded) > (limit * 4) // 3 + 8:  # refuse before decoding something huge
        raise ValidationError(words["size"].format(limit=_human(limit, lang)))
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError(words["data_url"])
    if len(raw) > limit:
        raise ValidationError(words["size"].format(limit=_human(limit, lang)))
    if not raw:
        raise ValidationError(words["no_signature"])
    return _store(owner, raw, "", user, Kind.SIGNATURE, stage, words)


def attachments_for(owner, user, lang="ar"):
    """The owner's attachments, oldest first, without their bytes (``data`` is deferred)."""

    _check_owner(owner, user, _words(lang))
    rows = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk)
    return entity_scope.scope(rows, "entity").select_related("uploaded_by").defer("data")


def get_attachment(pk, user):
    """One attachment with its bytes, only if the user may see its owner and its entity. Else OwnerNotFound / PermissionDenied."""

    attachment = entity_scope.scope(Attachment.objects.filter(pk=pk), "entity").select_related("content_type").first()
    if attachment is None:
        raise OwnerNotFound(f"attachment {pk}")
    model = attachment.content_type.model_class()
    if model is None or not visible_owners(owner_label(model), user).filter(pk=attachment.object_id).exists():
        raise OwnerNotFound(f"attachment {pk}")
    return attachment
