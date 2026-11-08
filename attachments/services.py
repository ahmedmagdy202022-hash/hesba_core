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
           "no_file": "اختار ملف الأول.", "no_signature": "ارسم التوقيع الأول.",
           "read_only": "إضافة المرفقات للكاونتر أو للفني صاحب الأمر بس.", "closed": "الأمر ده اتقفل؛ مينفعش يتضاف عليه مرفقات.",
           "max_count": "وصلت للحد الأقصى من المرفقات على السجل ده ({count}).", "max_owner_bytes": "مجموع حجم مرفقات السجل ده هيعدّي الحد ({limit}).",
           "global_full": "مساحة المرفقات في قاعدة البيانات امتلت؛ كلّم صاحب الحساب.", "pdf_active": "ملف PDF فيه سكربت أو ملف مدمج أو أمر تشغيل تلقائي، ومش مسموح.",
           "delete_denied": "مسح المرفقات للمدير أو الكاونتر بس.", "signature_kept": "توقيع أمر اتسلّم مينفعش يتمسح."},
    "en": {"denied": "You may not use attachments on this record.", "not_found": "The attachment or the record was not found.",
           "kind": "The attachment kind must be photo, document or signature.", "stage": "The attachment stage must be receive, deliver or other.",
           "empty": "The file is empty or was not sent.", "size": "The file is larger than allowed ({limit}).",
           "type": "This file type is not allowed. Allowed: JPG, PNG or WEBP images and PDF files.", "markup": "SVG and HTML files are not allowed.",
           "photo": "A photo must be an image file (JPG, PNG or WEBP).", "signature": "A signature must be a real PNG image.",
           "data_url": "The signature data is not valid; it must be a base64 PNG image.", "no_file": "Choose a file first.",
           "no_signature": "Draw the signature first.",
           "read_only": "Only the counter, or the technician who owns the order, may add attachments.", "closed": "This order is closed; attachments can no longer be added.",
           "max_count": "This record has reached the most attachments allowed ({count}).", "max_owner_bytes": "The attachments on this record would pass the size limit ({limit}).",
           "global_full": "The space for attachments in the database is full; tell the account owner.",
           "pdf_active": "This PDF contains a script, an embedded file or an auto-run action, and is not allowed.",
           "delete_denied": "Only a manager or the counter may delete attachments.", "signature_kept": "The signature of a delivered order cannot be deleted."},
}


class OwnerNotFound(LookupError):
    """The record does not exist for this user (missing, another entity, or another technician's)."""


# ---- who owns what: a small registry, so appointments and customers can join later ----
# Each owner type gives: who SEES it, who may ADD/DELETE (the rule its own screens use for changes), whether it is CLOSED
# (no more attachments) and whether a signature on it is final.

def _work_orders(user):
    from workorders import services as work
    from workorders.models import WorkOrder

    if not work.is_workshop():
        return WorkOrder.objects.none()
    if not (work.can_view(user) or work.technician_of(user) is not None):
        raise PermissionDenied("Work orders need sales.view_sales_invoices.")
    return work.visible_to(user)


def _work_order_can_attach(user, order):
    """The work orders' own rule for any change: the counter, or the technician who owns the order."""

    from workorders import services as work

    technician = work.technician_of(user)
    return work.can_manage(user) or (technician is not None and order.technician_id == technician.pk)


def _work_order_closed(order):
    """Delivered or cancelled. An order still open, a ready one waiting to be handed over included, takes attachments,
    so the delivery-stage photo and signature are taken before the hand-over."""

    from workorders.models import OPEN, WorkOrder

    return not WorkOrder.objects.filter(pk=order.pk, status__in=OPEN).exists()  # fresh from the database, not the caller's copy


def _work_order_signature_final(order):
    from workorders.models import Status, WorkOrder

    return WorkOrder.objects.filter(pk=order.pk, status=Status.DELIVERED).exists()


def _work_order_can_delete(user, order):
    from workorders import services as work

    return work.can_manage(user)


OWNERS = {"workorders.workorder": {"visible": _work_orders, "can_attach": _work_order_can_attach, "closed": _work_order_closed,
                                   "can_delete": _work_order_can_delete, "signature_final": _work_order_signature_final}}


def owner_label(owner_or_model):
    meta = owner_or_model._meta
    return f"{meta.app_label}.{meta.model_name}"


def _rules(label):
    if label not in OWNERS:
        raise OwnerNotFound(label)
    return OWNERS[label]


def visible_owners(label, user):
    return _rules(label)["visible"](user)


def get_owner(label, pk, user):
    """The owner this user may see, else OwnerNotFound (404) or PermissionDenied (403)."""

    owner = visible_owners(label, user).filter(pk=pk).first()
    if owner is None:
        raise OwnerNotFound(f"{label} {pk}")
    return owner


def _check_owner(owner, user, words, write=False):
    """Seeing is not enough to change: a write also needs the owner's own change right."""

    try:
        allowed = visible_owners(owner_label(owner), user).filter(pk=owner.pk).exists()
    except OwnerNotFound:
        allowed = False
    if not allowed:
        raise PermissionDenied(words["denied"])
    if write and not _rules(owner_label(owner))["can_attach"](user, owner):
        raise PermissionDenied(words["read_only"])


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


def _final_name(name, real, sha, kind):
    """The stored name always carries the extension of the DETECTED type, whatever the client called it."""

    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = stem.strip(". ")[:200] or f"{kind}-{sha[:8]}"
    return f"{stem}.{EXTENSIONS[real]}"


#: PDF names that run something or hide a file. A plain, case-sensitive bytes search (the PDF spec's names are case-sensitive).
PDF_ACTIVE = (b"/JavaScript", b"/JS", b"/OpenAction", b"/Launch", b"/EmbeddedFile")


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


def _setting(name, default):
    return getattr(settings, name, default)


def _check_room(owner, size, words, lang):
    """Per-record count and bytes, then the share of the database plan all attachments together may take."""

    from django.db.models import Count, Sum

    mine = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk).aggregate(n=Count("id"), total=Sum("size"))
    max_count = int(_setting("ATTACHMENTS_MAX_PER_OWNER", 20))
    if mine["n"] >= max_count:
        raise ValidationError(words["max_count"].format(count=max_count))
    max_owner = int(_setting("ATTACHMENTS_MAX_BYTES_PER_OWNER", 25 * 1024 * 1024))
    if (mine["total"] or 0) + size > max_owner:
        raise ValidationError(words["max_owner_bytes"].format(limit=_human(max_owner, lang)))
    limit_mb, share = _setting("DATABASE_SIZE_LIMIT_MB", None), float(_setting("ATTACHMENTS_GLOBAL_SHARE", 0.2))
    if limit_mb and share > 0:
        used = Attachment.objects.aggregate(total=Sum("size"))["total"] or 0
        if used + size > int(limit_mb) * 1024 * 1024 * share:
            raise ValidationError(words["global_full"])


def _store(owner, raw, name, user, kind, stage, words, lang="ar"):
    real = sniff(raw)
    if real is None or real not in allowed_types():
        raise ValidationError(words["markup"] if _looks_like_markup(raw) else words["type"])
    if kind == Kind.PHOTO and real not in IMAGES:
        raise ValidationError(words["photo"])
    if kind == Kind.SIGNATURE and (real != "image/png" or not _valid_png(raw)):
        raise ValidationError(words["signature"])
    if real == "application/pdf" and any(token in raw for token in PDF_ACTIVE):
        raise ValidationError(words["pdf_active"])
    sha = hashlib.sha256(raw).hexdigest()
    with transaction.atomic():
        type(owner).objects.select_for_update().filter(pk=owner.pk).first()  # one upload at a time per record, so the caps hold
        if _rules(owner_label(owner))["closed"](owner):
            raise ValidationError(words["closed"])
        _check_room(owner, len(raw), words, lang)
        attachment = Attachment.objects.create(
            content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk, entity=getattr(owner, "entity", None),
            kind=kind, stage=stage, original_filename=_final_name(name, real, sha, kind), content_type_name=real,
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
    _check_owner(owner, user, words, write=True)
    _check_kind_stage(kind, stage, words)
    raw, name, declared = _read(uploaded_file_or_bytes, max_bytes(kind), words, lang)
    if declared in MARKUP_TYPES or name.lower().endswith(MARKUP_EXTENSIONS):
        raise ValidationError(words["markup"])
    return _store(owner, raw, name, user, kind, stage, words, lang)


def add_signature_png(owner, data_url, user, stage=Stage.OTHER, lang="ar"):
    """Keep the customer's signature, drawn on a canvas and sent as a ``data:image/png;base64,`` URL."""

    words = _words(lang)
    _check_owner(owner, user, words, write=True)
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
    return _store(owner, raw, "", user, Kind.SIGNATURE, stage, words, lang)


def attachments_for(owner, user, lang="ar"):
    """The owner's attachments, oldest first, without their bytes (``data`` is deferred)."""

    _check_owner(owner, user, _words(lang))
    rows = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk)
    return entity_scope.scope(rows, "entity").select_related("uploaded_by").defer("data")


def get_attachment(pk, user, with_data=True):
    """One attachment (with its bytes unless ``with_data`` is False), only if the user may see its owner and its entity.
    Else OwnerNotFound / PermissionDenied."""

    rows = entity_scope.scope(Attachment.objects.filter(pk=pk), "entity").select_related("content_type")
    attachment = (rows if with_data else rows.defer("data")).first()
    if attachment is None:
        raise OwnerNotFound(f"attachment {pk}")
    model = attachment.content_type.model_class()
    if model is None or not visible_owners(owner_label(model), user).filter(pk=attachment.object_id).exists():
        raise OwnerNotFound(f"attachment {pk}")
    return attachment


def delete_attachment(pk, user, lang="ar"):
    """Delete one attachment, audited. Managers only (the owner's own right for deleting); the signature of a
    delivered order is final. Returns the audit entry."""

    words = _words(lang)
    attachment = get_attachment(pk, user, with_data=False)
    model = attachment.content_type.model_class()
    owner = model.objects.get(pk=attachment.object_id)
    rules = _rules(owner_label(model))
    if not rules["can_delete"](user, owner):
        raise PermissionDenied(words["delete_denied"])
    with transaction.atomic():
        if attachment.kind == Kind.SIGNATURE and rules["signature_final"](owner):
            raise ValidationError(words["signature_kept"])
        before = {"owner": owner_label(model), "owner_id": owner.pk, "kind": attachment.kind, "stage": attachment.stage,
                  "filename": attachment.original_filename, "content_type": attachment.content_type_name, "size": attachment.size,
                  "sha256": attachment.sha256}
        entry = AuditLog.objects.create(event_type=AuditEventType.DELETE, actor=user, module="attachments", action="delete_attachment",
                                        object_type="attachments.Attachment", object_id=str(attachment.pk), before_data=before, after_data={})
        attachment.delete()
    return entry
