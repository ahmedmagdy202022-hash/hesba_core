"""Contracting C2a / C2b (HG-095): what the demo's projects show on «المستندات» and «اليوميات».

Each project gets three documents (the contract and a drawing as PDFs, a site photo) and four days of daily reports, one of
them with a delay and two with photos, so neither tab opens empty. Everything goes through the services the screens use
(``attachments.services.add_attachment`` and ``projects.site_reports.save_report``): nothing here writes a quantity, a
certificate, a cost or any money. The files are made here, from nothing: a one-page PDF and small drawn pictures (no
photograph of anyone, no real plan).

Idempotent: a project that already has a report is left as it is.

Contracting C1 (HG-103): ``seed_variations`` adds one approved variation order to the project that has a bill of
quantities (more of one item, one new item of extra works and two weeks more time, with a signed copy attached), so
«أوامر التغيير» does not open empty. It goes through ``projects.variations`` like the screens and moves no money.
"""

import struct
import zlib
from datetime import timedelta
from functools import lru_cache

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from attachments import services as attachments
from attachments.models import Kind, Stage

from . import documents, site_reports
from .models import SiteReport, VariationOrder

#: per project: four days, oldest first: (weather, work, delay, materials, visitors, crew, equipment, photos)
DAYS = (
    (("clear", "تجهيز حديد تسليح سقف الدور الأول وصب القواعد الباقية", "", "", "", (("نجار", 6), ("حداد", 4), ("عمالة مساعدة", 10)), (("خلاطة", 1),), 0),
     ("hot", "تركيب شدة سقف الدور الأول", "توقف الصب ساعتين بسبب تأخر عربيات الخرسانة عن الميعاد", "", "", (("نجار", 8), ("حداد", 4), ("عمالة مساعدة", 12)), (("خلاطة", 1), ("ونش", 1)), 1),
     ("clear", "صب سقف الدور الأول وبداية المباني في الدور الأرضي", "", "٤٠ طن حديد تسليح", "", (("نجار", 8), ("حداد", 3), ("فني مباني", 6), ("عمالة مساعدة", 14)), (("خلاطة", 2), ("ونش", 1)), 0),
     ("cloudy", "فك الشدة ومعاينة الحديد للدور التاني", "", "", "زيارة المهندس الاستشاري ومعاينة الحديد قبل الصب", (("نجار", 5), ("حداد", 6), ("عمالة مساعدة", 9)), (("ونش", 1),), 2)),
    (("clear", "دهانات الدور الأرضي: معجون وأول وجه", "", "", "", (("فني دهانات", 5), ("عمالة مساعدة", 4)), (("سقالة متحركة", 2),), 0),
     ("clear", "تركيب السيراميك في الفصول الثلاثة الأولى", "", "٣٠٠ م² سيراميك", "", (("فني بلاط", 4), ("عمالة مساعدة", 5)), (), 1),
     ("wind", "أعمال كهرباء ولوحات التوزيع", "الدهانات الخارجية اتأجلت بسبب الرياح العالية", "", "", (("كهربائي", 3), ("عمالة مساعدة", 3)), (("سقالة متحركة", 2),), 0),
     ("hot", "وجه تاني دهانات الدور الأرضي وتركيب الأبواب", "", "", "زيارة صاحب المشروع واستلام الفصول الأولى", (("فني دهانات", 6), ("نجار", 3), ("عمالة مساعدة", 5)), (), 2)),
)
#: (kind, title, text on the page)
DOCUMENTS = (("contract", "عقد المقاولة", "Contract of works"), ("drawing", "رسم الدور الأرضي", "Ground floor plan A-101"))


def _pdf(text):
    """A valid one-page PDF with one line of text (no script, no embedded file: it passes the attachments checks)."""

    stream = f"BT /F1 24 Tf 72 760 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)


@lru_cache(maxsize=None)
def _scene(floors, width=320, height=200):
    """A small drawn picture of a building going up: sky, soil, a frame with ``floors`` slabs and a crane. Valid PNG, a few KB."""

    horizon, floor_height, left, right = int(height * 0.7), 24, 90, 220
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            if y < horizon:
                t = y / horizon
                colour = (int(150 + 80 * t), int(195 + 40 * t), int(235 + 10 * t))
            else:
                colour = (176 - (x * 7 + y * 13) % 9, 152 - (x * 5 + y * 11) % 9, 112)
            above = horizon - y
            if left <= x < right and 0 < above <= floors * floor_height:
                colour = (118, 120, 126) if above % floor_height < 4 else ((108, 110, 116) if (x - left) % 44 < 5 else (196, 192, 182))
            if x == 262 and y < horizon:  # the crane's mast, jib and hook
                colour = (232, 176, 40)
            if 190 <= x < 300 and y in (28, 29):
                colour = (232, 176, 40)
            if x == 205 and 30 <= y < 56:
                colour = (80, 80, 84)
            row += bytes(colour)
        rows.append(b"\x00" + bytes(row))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def _add(project, owner, raw, name, mime, kind, stage):
    return attachments.add_attachment(project, SimpleUploadedFile(name, raw, content_type=mime), owner, kind=kind, stage=stage)


def seed(projects, owner):
    """Three documents and four days of reports on each of ``projects``, as ``owner``. Returns how many projects were filled."""

    today = timezone.localdate()
    filled = 0
    for index, project in enumerate(projects):
        if SiteReport.objects.filter(project=project).exists():
            continue
        for kind, title, text in DOCUMENTS:
            _add(project, owner, _pdf(text), f"{title}.pdf", "application/pdf", Kind.DOCUMENT, documents.KIND_STAGE[kind])
        _add(project, owner, _scene(3 + index), "صورة الموقع.png", "image/png", Kind.PHOTO, documents.KIND_STAGE["photo"])
        for step, (weather, work, delayed, materials, visitors, crew, equipment, photos) in enumerate(DAYS[index % len(DAYS)]):
            report, _ = site_reports.save_report(project, owner, report_date=today - timedelta(days=4 - step), weather=weather, work_done=work, delayed=delayed,
                                                 materials=materials, visitors=visitors, crew=[(label, str(count)) for label, count in crew],
                                                 equipment=[(label, str(count)) for label, count in equipment])
            for shot in range(photos):
                _add(report, owner, _scene(2 + step + shot + 3 * index), f"صورة اليوم {shot + 1}.png", "image/png", Kind.PHOTO, Stage.PHOTO)
        filled += 1
    return filled


def seed_variations(projects, owner):
    """One approved variation order on the first of ``projects`` that has a bill of quantities, as ``owner``. Returns the
    order, or None when there is nothing to put one on (no bill of quantities, or an order is already there)."""

    from decimal import Decimal

    from . import variations

    today = timezone.localdate()
    for project in projects:
        items = list(project.boq.all())
        if not items or VariationOrder.objects.filter(project=project).exists():
            continue
        raised = items[min(1, len(items) - 1)]
        order = variations.save_order(
            project, owner, order_date=today - timedelta(days=6), title="زيادة كميات وأعمال إضافية بطلب المالك",
            reason="طلب المالك في محضر اجتماع الموقع، بعد مراجعة الاستشاري للرسومات المعدّلة.", extension_days="14",
            changes=[(raised.pk, str((Decimal(raised.quantity) * Decimal("0.10")).quantize(Decimal("1"))))],
            new_items=[{"code": "إ-1", "description": "تجهيزات إضافية للموقع العام", "unit": "مقطوعية", "quantity": "1", "rate": "65000"}])
        _add(order, owner, _pdf("Variation order 1 - signed copy"), "النسخة الموقعة من المالك.pdf", "application/pdf", Kind.DOCUMENT, Stage.OTHER)
        return variations.approve(project, order, owner, approved_on=today - timedelta(days=2), owner_reference="محضر اجتماع رقم 7")
    return None
