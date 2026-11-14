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
import struct
import zlib

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope

from .models import Attachment, Kind, Stage

PNG = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PNG_MAX_SIDE = 4000  # a signature pad is a few hundred pixels; refuse anything that would inflate to tens of MB
# bit depths each colour type may use, and its channel count (PNG spec table 11.1)
PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
GLOBAL_LOCK_KEY = 7207201  # fixed key of the PostgreSQL advisory lock that serializes the global storage check (HG-072)
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
           "delete_denied": "مسح المرفقات للمدير أو الكاونتر بس.", "signature_kept": "توقيع أمر اتسلّم مينفعش يتمسح.",
           "purchase_delete_denied": "صورة فاتورة الشراء بتتمسح بس طول ما الفاتورة مسودة، ولصاحب صلاحية إنشاء فواتير الشراء.",
           "category": "نوع المرفق لازم يكون أشعة أو تحاليل أو صور أو تقارير.", "medical_kind": "المرفق على الملف الطبي لازم يكون صورة أو ملف PDF.",
           "medical_read_only": "إضافة المرفقات على الملف الطبي للدكتور أو لصاحب صلاحية تسجيل الكشوفات بس.",
           "medical_delete_denied": "مسح مرفقات الملف الطبي لصاحب صلاحية تسجيل الكشوفات بس.",
           "project_read_only": "إضافة مستندات المشروع لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
           "project_delete_denied": "مسح مستندات المشروع لصاحب صلاحية الفواتير بس.",
           "project_stage": "نوع المستند لازم يكون عقد أو رسم أو مراسلة أو تصريح أو صورة أو أخرى.",
           "project_kind": "المستند لازم يكون صورة أو ملف PDF.",
           "report_read_only": "إضافة صور التقرير اليومي لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
           "report_delete_denied": "مسح صور التقرير اليومي لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
           "report_stage": "صور التقرير اليومي بتبقى صور موقع بس.", "report_closed": "المشروع ده اتقفل؛ مينفعش يتضاف صور على تقاريره.",
           "report_kept": "المشروع ده اتقفل؛ صور تقاريره بتفضل زي ما هي ومينفعش تتمسح.",
           "variation_read_only": "إضافة مرفقات أمر التغيير لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
           "variation_stage": "مرفق أمر التغيير بيتسجّل كنسخة موقّعة بس.", "variation_closed": "أمر التغيير ده اتقفل؛ مينفعش يتضاف عليه مرفقات.",
           "variation_kept": "أمر التغيير ده اتقفل؛ مرفقاته بتفضل زي ما هي ومينفعش تتمسح."},
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
           "delete_denied": "Only a manager or the counter may delete attachments.", "signature_kept": "The signature of a delivered order cannot be deleted.",
           "purchase_delete_denied": "A purchase invoice's photo can be deleted only while the invoice is a draft, by someone who may create purchase invoices.",
           "category": "The category must be scan, lab report, photo or report.", "medical_kind": "An attachment on a medical file must be an image or a PDF.",
           "medical_read_only": "Only a doctor, or someone who may record visits, can add attachments to a medical file.",
           "medical_delete_denied": "Only someone who may record visits can delete a medical file's attachments.",
           "project_read_only": "Only the site engineer, or someone who may create invoices, can add project documents.",
           "project_delete_denied": "Only someone who may create invoices can delete project documents.",
           "project_stage": "The document type must be contract, drawing, letter, permit, photo or other.",
           "project_kind": "A document must be an image or a PDF.",
           "report_read_only": "Only the site engineer, or someone who may create invoices, can add photos to a daily report.",
           "report_delete_denied": "Only the site engineer, or someone who may create invoices, can delete a daily report's photos.",
           "report_stage": "A daily report takes site photos only.", "report_closed": "This project is closed; photos can no longer be added to its reports.",
           "report_kept": "This project is closed; the photos of its reports are kept and cannot be deleted.",
           "variation_read_only": "Only the site engineer, or someone who may create invoices, can add files to a variation order.",
           "variation_stage": "A file on a variation order is kept as its signed copy only.", "variation_closed": "This variation order is closed; files can no longer be added to it.",
           "variation_kept": "This variation order is closed; its files are kept and cannot be deleted."},
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


def _purchase_invoices(user):
    """A3 (HG-081): purchase invoices, seen exactly as the purchase screens show them: the view permission and the entity
    scope (HG-034, through the receiving location)."""

    from permissions.services import user_has_permission
    from purchases.models import PurchaseInvoice

    if not user_has_permission(user, "purchases.view_purchase_invoices"):
        raise PermissionDenied("Purchase invoices need purchases.view_purchase_invoices.")
    return entity_scope.scope(PurchaseInvoice.objects.all(), entity_scope.PURCHASE_INVOICE)


def _purchase_invoice_can_change(user, invoice):
    """The purchase screens' own rule for a change: the purchase-create permission."""

    from permissions.services import user_has_permission

    return user_has_permission(user, "purchases.create_purchase_invoice")


def _purchase_invoice_can_delete(user, invoice):
    """Independent review (PR #335): the photo goes with the draft's own changes only; once posted or cancelled, the
    supplier's invoice it was made from stays with the record."""

    from purchases.models import PurchaseInvoice, PurchaseInvoiceStatus

    return _purchase_invoice_can_change(user, invoice) and PurchaseInvoice.objects.filter(
        pk=invoice.pk, status=PurchaseInvoiceStatus.DRAFT).exists()  # fresh from the database, not the caller's copy


def _purchase_invoice_closed(invoice):
    from purchases.models import PurchaseInvoice, PurchaseInvoiceStatus

    return PurchaseInvoice.objects.filter(pk=invoice.pk, status=PurchaseInvoiceStatus.CANCELLED).exists()


def _purchase_invoice_entity(invoice):
    """The entity of a purchase invoice is its receiving location's (``entities.scope.PURCHASE_INVOICE``)."""

    return invoice.receiving_location.entity


# M5 (HG-083): medical owners. A patient's file and a visit are seen as the clinic's own screens show them (the medical
# view right, or a doctor), changed with its write right, and never closed; no signature is taken on them. Medical
# records have no entity field, so an attachment is stamped with the entity being worked in when it is uploaded
# (``entity``), and the existing scope on ``Attachment.entity`` then shows it only there (HG-034).

def _medical_owners(model_name):
    def visible(user):
        from medical import services as med
        from medical.models import ClinicalVisit, PatientFile

        if not med.is_medical_install():
            return (PatientFile if model_name == "patientfile" else ClinicalVisit).objects.none()
        if not med.can_view(user):
            raise PermissionDenied("Patient files need medical.view_records.")
        if model_name == "patientfile":
            return PatientFile.objects.exclude(customer__customer_code="WALK-IN")  # as the clinic's own patient list
        return ClinicalVisit.objects.all()

    return visible


def _medical_can_attach(user, record):
    from medical import services as med

    return med.can_write(user)


def _medical_can_delete(user, record):
    """The role permission, not merely "is linked to an employee": a doctor adds, the records right removes."""

    from medical import services as med
    from permissions.services import user_has_permission

    return user_has_permission(user, med.WRITE)


def _current_entity(record):
    """Medical records carry no entity: the attachment belongs to the entity it was uploaded in (None: main entity)."""

    return entity_scope.entity()


# Contracting C2a / C2b (HG-095): a project's documents and a daily site report's photos. Both are seen as the project
# screens themselves are (``projects.access``): the projects view right, or the sales view right. A project has no entity
# of its own (its money fixes one later), so these attachments are ``group_wide``: the entity scope on ``Attachment.entity``
# is not applied, exactly as the project page itself is not narrowed to an entity. The document kinds ride on ``stage``
# (``projects.documents.KIND_STAGE``): no attachments migration.

def _projects(user):
    from projects import access

    return access.visible_projects(user)


def _project_can_attach(user, project):
    from projects import access

    return access.can_write(user)


def _project_can_delete(user, project):
    from projects import access

    return access.can_delete_documents(user)


def _site_reports(user):
    from projects import access

    return access.visible_reports(user)


def _site_report_closed(report):
    from projects.models import ProjectStatus, SiteReport

    return SiteReport.objects.filter(pk=report.pk).exclude(project__status__in=(ProjectStatus.PLANNED, ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD)).exists()


def _variation_orders(user):
    from projects import access

    return access.visible_variation_orders(user)


def _variation_can_change(user, order):
    """Add or remove the signed copy: whoever may record progress, and only from the entity that holds the project's money
    (the order itself is a 404 from anywhere else)."""

    from projects import access, variations

    return access.can_write(user) and variations.in_reach(order.project)


def _variation_order_closed(order):
    """Contracting C1 (HG-103): files are added and removed while the order is a draft; once it is approved, rejected or
    cancelled the signed copy is the record and stays as it is."""

    from projects.models import VariationOrder, VariationStatus

    return VariationOrder.objects.filter(pk=order.pk).exclude(status=VariationStatus.DRAFT).exists()


def _no_entity(record):
    return None


WORK_STAGES = (Stage.RECEIVE, Stage.DELIVER, Stage.OTHER)
MEDICAL_STAGES = (Stage.SCAN, Stage.LAB, Stage.PHOTO, Stage.REPORT)
#: the six document kinds of a project, stored on the six stages they are mapped to (see ``projects.documents``)
PROJECT_STAGES = (Stage.RECEIVE, Stage.DELIVER, Stage.SCAN, Stage.REPORT, Stage.PHOTO, Stage.OTHER)
#: per-record limits of a project (a drawing set grows) and of a daily report (a few phone photos); both sit well under the
#: global share (ATTACHMENTS_GLOBAL_SHARE of the database), which still applies on top
PROJECT_MAX_COUNT, PROJECT_MAX_BYTES = 40, 40 * 1024 * 1024
REPORT_MAX_COUNT, REPORT_MAX_BYTES = 12, 12 * 1024 * 1024
#: a variation order's signed copy: a scan of a few pages, or a few phone photos of them
VARIATION_MAX_COUNT, VARIATION_MAX_BYTES = 6, 12 * 1024 * 1024

_MEDICAL_RULES = {"can_attach": _medical_can_attach, "closed": lambda record: False, "can_delete": _medical_can_delete,
                  "signature_final": lambda record: False, "entity": _current_entity, "delete_denied": "medical_delete_denied",
                  "read_only_message": "medical_read_only", "stages": MEDICAL_STAGES, "kinds": (Kind.PHOTO, Kind.DOCUMENT),
                  "stage_error": "category", "kind_error": "medical_kind", "default_stage": Stage.SCAN}

OWNERS = {"workorders.workorder": {"visible": _work_orders, "can_attach": _work_order_can_attach, "closed": _work_order_closed,
                                   "can_delete": _work_order_can_delete, "signature_final": _work_order_signature_final, "stages": WORK_STAGES},
          # A3 (HG-081): the supplier's invoice photo kept on the draft it became. No upload screen of its own yet.
          "purchases.purchaseinvoice": {"visible": _purchase_invoices, "can_attach": _purchase_invoice_can_change,
                                        "closed": _purchase_invoice_closed, "can_delete": _purchase_invoice_can_delete,
                                        "signature_final": lambda invoice: False, "entity": _purchase_invoice_entity,
                                        "delete_denied": "purchase_delete_denied", "stages": WORK_STAGES},
          # M5 (HG-083): what a visit produced (scans, lab reports, photos) and what the patient brings that belongs to no visit.
          "medical.clinicalvisit": {"visible": _medical_owners("clinicalvisit"), **_MEDICAL_RULES},
          "medical.patientfile": {"visible": _medical_owners("patientfile"), **_MEDICAL_RULES},
          # Contracting C2a (HG-095): a project's documents: contract, drawing, letter, permit, photo, other. Never closed.
          "projects.project": {"visible": _projects, "can_attach": _project_can_attach, "closed": lambda project: False, "can_delete": _project_can_delete,
                               "signature_final": lambda project: False, "entity": _no_entity, "group_wide": True, "titled": True,
                               "delete_denied": "project_delete_denied", "read_only_message": "project_read_only", "stages": PROJECT_STAGES,
                               "kinds": (Kind.PHOTO, Kind.DOCUMENT), "stage_error": "project_stage", "kind_error": "project_kind",
                               "default_stage": Stage.OTHER, "max_count": PROJECT_MAX_COUNT, "max_owner_bytes": PROJECT_MAX_BYTES},
          # Contracting C2b (HG-095): the photos of a day's report. Closed with the project; deleted by those who may write the report.
          "projects.sitereport": {"visible": _site_reports, "can_attach": _project_can_attach, "closed": _site_report_closed, "can_delete": _project_can_attach,
                                  "signature_final": lambda report: False, "entity": _no_entity, "group_wide": True,
                                  "delete_denied": "report_delete_denied", "read_only_message": "report_read_only", "closed_error": "report_closed",
                                  "kept_when_closed": "report_kept",
                                  "stages": (Stage.PHOTO,), "kinds": (Kind.PHOTO,), "stage_error": "report_stage", "kind_error": "photo",
                                  "default_stage": Stage.PHOTO, "max_count": REPORT_MAX_COUNT, "max_owner_bytes": REPORT_MAX_BYTES},
          # Contracting C1 (HG-103): the copy of a variation order the owner signed. Added and removed while the order is a draft
          # (by those who may draft it); kept for good once the order is approved, rejected or cancelled.
          "projects.variationorder": {"visible": _variation_orders, "can_attach": _variation_can_change, "closed": _variation_order_closed,
                                      "can_delete": _variation_can_change, "signature_final": lambda order: False, "entity": _no_entity, "group_wide": True,
                                      "delete_denied": "variation_read_only", "read_only_message": "variation_read_only", "closed_error": "variation_closed",
                                      "kept_when_closed": "variation_kept", "stages": (Stage.OTHER,), "kinds": (Kind.PHOTO, Kind.DOCUMENT),
                                      "stage_error": "variation_stage", "kind_error": "project_kind", "default_stage": Stage.OTHER,
                                      "max_count": VARIATION_MAX_COUNT, "max_owner_bytes": VARIATION_MAX_BYTES}}


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


def _png_rows_size(width, height, colour, depth, interlace):
    """The exact number of bytes the decompressed scanlines of this PNG must have (filter byte included)."""

    bits = PNG_CHANNELS[colour] * depth
    if not interlace:
        return height * (1 + (width * bits + 7) // 8)
    total = 0
    for x0, y0, dx, dy in ADAM7:
        pass_w, pass_h = (width - x0 + dx - 1) // dx, (height - y0 + dy - 1) // dy
        if pass_w > 0 and pass_h > 0:
            total += pass_h * (1 + (pass_w * bits + 7) // 8)
    return total


def _valid_png(raw):
    """A whole, displayable PNG, checked with the standard library only (no Pillow): signature, IHDR first, IEND
    last, every chunk's CRC, at least one IDAT, bounded dimensions, and the IDAT stream must inflate (never past
    the size it has to have) to exactly the number of bytes the header promises."""

    if len(raw) <= 33 or raw[:8] != PNG or not raw.endswith(PNG_END):
        return False
    pos, first, header, idat, ended = 8, True, None, [], False
    while pos + 12 <= len(raw):
        length, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        end = pos + 12 + length
        if end > len(raw):
            return False
        body = raw[pos + 8:pos + 8 + length]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != struct.unpack(">I", raw[end - 4:end])[0]:
            return False
        if first:
            if kind != b"IHDR" or length != 13:
                return False
            header, first = body, False
        elif kind == b"IHDR" or ended:
            return False
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            ended = True
            if length != 0:
                return False
        pos = end
    if not ended or pos != len(raw) or not idat:
        return False
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
    if not (0 < width <= PNG_MAX_SIDE and 0 < height <= PNG_MAX_SIDE):
        return False
    if depth not in PNG_DEPTHS.get(colour, ()) or compression != 0 or filtering != 0 or interlace not in (0, 1):
        return False
    expected = _png_rows_size(width, height, colour, depth, interlace)
    inflater, buffer, total = zlib.decompressobj(), b"".join(idat), 0
    try:
        while buffer and not inflater.eof:
            out = inflater.decompress(buffer, 65536)
            total += len(out)
            if total > expected:
                return False
            if not out and len(inflater.unconsumed_tail) == len(buffer):
                return False  # no progress
            buffer = inflater.unconsumed_tail
    except zlib.error:
        return False
    return inflater.eof and not inflater.unused_data and total == expected


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
# Long names only: a short one like /JS turns up by chance inside compressed image data and would refuse real scans.
# A plain byte search misses names hidden with #xx escapes or in object streams; PDFs are served as downloads under a
# sandbox CSP, so this is a first filter, not the only defence (HG-072).
PDF_ACTIVE = (b"/JavaScript", b"/OpenAction", b"/Launch", b"/EmbeddedFile")


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


def _check_kind_stage(kind, stage, words, rules=None):
    """The kind and the stage must be ones this owner type takes (M5: a work order never takes a clinic category, a patient
    file never takes receive / deliver or a signature)."""

    rules = rules or {}
    if kind not in rules.get("kinds", Kind.values):
        raise ValidationError(words[rules.get("kind_error", "kind")])
    if stage not in rules.get("stages", WORK_STAGES):
        raise ValidationError(words[rules.get("stage_error", "stage")])


def _setting(name, default):
    return getattr(settings, name, default)


def _serialize_global_check():
    """Make the global storage check and the insert that follows one step across ALL owners.

    The per-owner row lock only serializes uploads to the same record; two uploads to different work orders could
    both pass ``Sum(size)`` and together pass ATTACHMENTS_GLOBAL_SHARE. On PostgreSQL a transaction-scoped advisory
    lock (released at commit or rollback) closes that gap. SQLite takes one writer lock for the whole database, so
    writes are already serialized there and nothing more is needed. Always taken after the owner row lock, so the
    lock order is the same everywhere and cannot deadlock."""

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [GLOBAL_LOCK_KEY])


def owner_limits(owner):
    """(most files, most bytes) one record of this owner type may hold: the owner's own limits (Contracting C2a: a project's
    documents allow more than a work order's photos), else the settings' defaults."""

    rules = _rules(owner_label(owner))
    return (int(rules.get("max_count") or _setting("ATTACHMENTS_MAX_PER_OWNER", 20)),
            int(rules.get("max_owner_bytes") or _setting("ATTACHMENTS_MAX_BYTES_PER_OWNER", 25 * 1024 * 1024)))


def room(owner, lang="ar"):
    """What is left on this record, for the page to show (Contracting C2a, trap 13: files live in the database).

    ``files`` and ``bytes`` are the record's own room; ``global_bytes`` is what is left of the share of the database all
    attachments together may take (None when that guard is off). ``bytes_left`` is the lower of the two."""

    from django.db.models import Count, Sum

    max_count, max_owner = owner_limits(owner)
    mine = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk).aggregate(n=Count("id"), total=Sum("size"))
    used = mine["total"] or 0
    left = max(0, max_owner - used)
    limit_mb, share = _setting("DATABASE_SIZE_LIMIT_MB", None), float(_setting("ATTACHMENTS_GLOBAL_SHARE", 0.2))
    global_left = None
    if limit_mb and share > 0:
        everything = Attachment.objects.aggregate(total=Sum("size"))["total"] or 0
        global_left = max(0, int(int(limit_mb) * 1024 * 1024 * share) - everything)
    bytes_left = left if global_left is None else min(left, global_left)
    return {"files": mine["n"], "max_files": max_count, "files_left": max(0, max_count - mine["n"]), "bytes": used, "max_bytes": max_owner,
            "bytes_left": bytes_left, "global_bytes": global_left, "used_text": _human(used, lang) if used else "0", "max_text": _human(max_owner, lang),
            "left_text": _human(bytes_left, lang) if bytes_left else "0", "global_limited": global_left is not None and global_left < left}


def _check_room(owner, size, words, lang):
    """Per-record count and bytes, then the share of the database plan all attachments together may take."""

    from django.db.models import Count, Sum

    mine = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk).aggregate(n=Count("id"), total=Sum("size"))
    max_count, max_owner = owner_limits(owner)
    if mine["n"] >= max_count:
        raise ValidationError(words["max_count"].format(count=max_count))
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
            raise ValidationError(words[_rules(owner_label(owner)).get("closed_error", "closed")])
        _serialize_global_check()
        _check_room(owner, len(raw), words, lang)
        # A3 (HG-081): an owner without an ``entity`` field names it through its rules (a purchase invoice: its location's).
        entity_of = _rules(owner_label(owner)).get("entity") or (lambda record: getattr(record, "entity", None))
        attachment = Attachment.objects.create(
            content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk, entity=entity_of(owner),
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
    _check_kind_stage(kind, stage, words, _rules(owner_label(owner)))
    raw, name, declared = _read(uploaded_file_or_bytes, max_bytes(kind), words, lang)
    if declared in MARKUP_TYPES or name.lower().endswith(MARKUP_EXTENSIONS):
        raise ValidationError(words["markup"])
    return _store(owner, raw, name, user, kind, stage, words, lang)


def add_signature_png(owner, data_url, user, stage=Stage.OTHER, lang="ar"):
    """Keep the customer's signature, drawn on a canvas and sent as a ``data:image/png;base64,`` URL."""

    words = _words(lang)
    _check_owner(owner, user, words, write=True)
    _check_kind_stage(Kind.SIGNATURE, stage, words, _rules(owner_label(owner)))
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


def _group_wide_types():
    """The content types whose attachments are not narrowed to the entity worked in (a project's: it has no entity of its own)."""

    from django.apps import apps

    return [ContentType.objects.get_for_model(apps.get_model(label)) for label, rules in OWNERS.items() if rules.get("group_wide")]


def _scoped(rows):
    """The entity data scope (HG-034) on ``Attachment.entity``, except for the owner types that are group-wide."""

    types = _group_wide_types()
    if not types or not entity_scope.is_scoped():
        return entity_scope.scope(rows, "entity")
    return rows.filter(entity_scope.q("entity") | Q(content_type__in=types))


def attachments_for(owner, user, lang="ar"):
    """The owner's attachments, oldest first, without their bytes (``data`` is deferred)."""

    _check_owner(owner, user, _words(lang))
    rows = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(owner), object_id=owner.pk)
    return _scoped(rows).select_related("uploaded_by").defer("data")


def attachments_for_many(label, pks, user, limit=60):
    """M5: the attachments on several owners of one type at once (a patient's visits), newest first, without their bytes.

    The same rules as ``attachments_for``, in one query: the owners must be visible to the user and the rows are narrowed to the
    entity being worked in. The visibility check is not repeated per owner, so the cost does not grow with the owners."""

    pks = [pk for pk in pks]
    if not pks:
        return []
    visible = visible_owners(label, user).filter(pk__in=pks)
    rows = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(visible.model), object_id__in=visible.values("pk"))
    return list(_scoped(rows).select_related("uploaded_by").defer("data").order_by("-created_at", "-pk")[:limit])


def get_attachment(pk, user, with_data=True):
    """One attachment (with its bytes unless ``with_data`` is False), only if the user may see its owner and its entity.
    Else OwnerNotFound / PermissionDenied."""

    rows = _scoped(Attachment.objects.filter(pk=pk)).select_related("content_type")
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
        raise PermissionDenied(words[rules.get("delete_denied", "delete_denied")])  # A3: an owner may give its own reason
    with transaction.atomic():
        owner = model.objects.select_for_update().get(pk=owner.pk)  # a delivery committing now is seen before the check
        if attachment.kind == Kind.SIGNATURE and rules["signature_final"](owner):
            raise ValidationError(words["signature_kept"])
        if rules.get("kept_when_closed") and rules["closed"](owner):  # a closed project's diary is a record: its photos stay
            raise ValidationError(words[rules["kept_when_closed"]])
        before = {"owner": owner_label(model), "owner_id": owner.pk, "kind": attachment.kind, "stage": attachment.stage,
                  "filename": attachment.original_filename, "content_type": attachment.content_type_name, "size": attachment.size,
                  "sha256": attachment.sha256}
        entry = AuditLog.objects.create(event_type=AuditEventType.DELETE, actor=user, module="attachments", action="delete_attachment",
                                        object_type="attachments.Attachment", object_id=str(attachment.pk), before_data=before, after_data={})
        attachment.delete()
    return entry
