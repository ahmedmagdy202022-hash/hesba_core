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

Contracting C13: the rest of the merged contracting features each get a small demo of their own, every one through the
service the screen uses and never by writing a quantity, a cost, a stock movement or a ledger row here. ``seed_site_life``
(a schedule with one late task, materials issued on a voucher and the surplus returned, a subcontract billed by quantities
with its advance, purchase requests, a paid labour week, an owned and a hired machine) and ``seed_tenders`` (one won, one
lost) run inside the demo's projects step; ``seed_bank_side`` (cheques on the project, letters of guarantee) runs after the
demo's bank exists. Every date is counted back from ``timezone.localdate()``, read once per function and passed down.
``tour_path`` gives the «جرّب» card the address of the screens that belong to one project.
"""

import struct
import zlib
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal
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


# ---- Contracting C13: one small demo for each of the merged features --------------------------------------------------

ONE = Decimal("1")


def _nearest(amount, step):
    """``amount`` rounded to the nearest multiple of ``step`` (a cheque of 41,000, not 40,873.20)."""

    return (Decimal(amount) / step).quantize(ONE) * step


def _main_project():
    """The demo's project with a bill of quantities (the first one made): where every feature below is shown."""

    from .models import Project

    return Project.objects.filter(boq__isnull=False).order_by("pk").distinct().first()


def _spare(item):
    """Stock of ``item`` above its minimum: what a demo issue may take without raising the shop's low-stock alert."""

    from inventory.services import get_item_stock_quantity

    return Decimal(get_item_stock_quantity(item)) - Decimal(item.min_stock or 0)


def seed_site_life(master, owner, projects):
    """C3, C12, C11, C7, C5 and C6 on the demo's projects, as ``owner``: the schedule and the subcontract on the project with the
    bill of quantities, the rest on that project too (or the first one). Each part is skipped when it is already there or the
    demo has what it needs missing (no supplier, no site engineer). Returns the names of the parts that were made."""

    today = timezone.localdate()
    main = next((project for project in projects if project.boq.exists()), None)
    site = main or (projects[0] if projects else None)
    if site is None:
        return []
    stocked = [item for item in master["items"][:3] if item.is_stock_tracked] + list(master["raw"])
    suppliers, cashbox = master["suppliers"], master["cashboxes"][0]
    made = []
    for name, ok in (
        ("schedule", main is not None and seed_schedule(main, owner, today) is not None),
        ("materials", seed_materials(site, owner, stocked, master["location"], today) is not None),
        ("subcontract", main is not None and bool(suppliers) and seed_subcontract(main, owner, suppliers[0], cashbox, today) is not None),
        ("purchase_requests", bool(suppliers) and seed_purchase_requests(site, owner, stocked, suppliers[0], today) is not None),
        ("labour", seed_labour(site, owner, cashbox, today) is not None),
        ("equipment", bool(suppliers) and seed_equipment(site, owner, suppliers[-1], today) is not None),
    ):
        if ok:
            made.append(name)
    return made


def seed_schedule(project, owner, today):
    """C3: four tasks, named after the first four items of the bill of quantities and typed with their progress: the first done,
    the second 80% done and four days past its end (the one late task), the others still to start. Returns the late task, or
    None when the project already has a schedule or has fewer than four items."""

    from . import schedule
    from .models import ProjectTask

    items = list(project.boq.order_by("line_number")[:4])
    if len(items) < 4 or ProjectTask.objects.filter(project=project).exists():
        return None
    plan = ((60, 35, 100), (34, 4, 80), (-2, -75, 0), (-76, -150, 0))  # (days back to its start, days back to its end, percent done)
    previous, late = None, None
    for item, (start, end, percent) in zip(items, plan):
        task = schedule.save_task(project, owner, {"name": f"تنفيذ {item.description}", "start_date": (today - timedelta(days=start)).isoformat(),
                                                   "end_date": (today - timedelta(days=end)).isoformat(),
                                                   "predecessor": str(previous.pk) if previous else ""})
        if percent:
            schedule.set_progress(project, task, owner, str(percent))
        if end > 0 and percent < 100 and late is None:
            late = task
        previous = task
    return late


def seed_materials(project, owner, stocked, location, today):
    """C12: one issue voucher of three lines (taken 9 days ago) and, 2 days ago, a return of the surplus of the first two
    items, through ``projects.returns``. Quantities are kept inside what the shop holds above its minimum. Returns the return's
    operations, or None when it is already there or the shop has fewer than two items to spare."""

    from . import returns

    if location is None or project.issues.filter(operation__reference_number__startswith=f"{returns.RETURN}-{project.code}-").exists():
        return None
    lines = []
    for item, wanted in zip(stocked, (40, 12, 8)):
        quantity = min(Decimal(wanted), (_spare(item) / 2).to_integral_value(rounding="ROUND_FLOOR"))
        if quantity >= 1:
            lines.append({"item": item, "quantity": quantity})
    if len(lines) < 2:
        return None
    returns.issue_voucher(project, owner, location=location, lines=lines, operation_date=today - timedelta(days=9))
    surplus = [{"item": line["item"], "quantity": (line["quantity"] / 5).to_integral_value(rounding="ROUND_CEILING")} for line in lines[:2]]
    return returns.return_materials(project, owner, location=location, lines=surplus, operation_date=today - timedelta(days=2))


def seed_subcontract(project, owner, supplier, cashbox, today):
    """C11: a subcontract by quantities on the project's third item (two lines, 5% retention, 10% advance recovery), an advance
    of 10% of its value paid 30 days ago, and one bill 6 days ago for half of the first line, posted. Returns the subcontract,
    or None when the project already has one with lines or has no third item."""

    from purchases.services import post_purchase_invoice

    from . import costs, subcontracting
    from .models import SubcontractPaymentKind

    items = list(project.boq.order_by("line_number"))
    if len(items) < 3 or project.subcontracts.filter(lines__isnull=False).exists():
        return None
    base = items[2]
    first_quantity = (Decimal(base.quantity) * Decimal("0.5")).quantize(ONE)
    first_rate = (Decimal(base.rate) * Decimal("0.8")).quantize(ONE)
    second_quantity = (Decimal(base.quantity) * Decimal("0.2")).quantize(ONE)
    second_rate = (Decimal(base.rate) * Decimal("0.85")).quantize(ONE)
    total = first_quantity * first_rate + second_quantity * second_rate
    value = (total / 1000).to_integral_value(rounding=ROUND_CEILING) * 1000
    subcontract = costs.save_subcontract(project, owner, {"supplier": supplier, "scope": f"مقاولة باطن بالكميات: {base.description}", "value": value,
                                                          "retention_rate": "5", "advance_recovery_rate": "10"})
    first = subcontracting.save_line(subcontract, owner, {"description": f"{base.description} - الأدوار المتكررة", "unit": base.unit,
                                                          "quantity": first_quantity, "rate": first_rate, "boq_line": base})
    subcontracting.save_line(subcontract, owner, {"description": f"{base.description} - الدور الأرضي والسور", "unit": base.unit,
                                                  "quantity": second_quantity, "rate": second_rate})
    subcontracting.pay(subcontract, owner, kind=SubcontractPaymentKind.ADVANCE, cashbox=cashbox, amount=(value / 10).quantize(ONE),
                       payment_date=today - timedelta(days=30), notes="دفعة مقدمة")
    bill = subcontracting.bill_by_quantities(subcontract, owner, quantities={first.pk: str((first_quantity / 2).quantize(ONE))}, description="مستخلص 1",
                                             bill_date=today - timedelta(days=6))
    post_purchase_invoice(bill.invoice_id, owner)
    return subcontract


def seed_purchase_requests(project, owner, stocked, supplier, today):
    """C7: the site engineer drafts and submits two requests; the owner approves the first (one quantity cut, one line typed as
    free text and so left out of the order), which makes its draft purchase order; the second waits for a decision. Returns the
    approved request, or None when the project has requests already, the purchases module is off, or there is no site engineer."""

    from django.contrib.auth import get_user_model

    from orders import purchasing

    from . import purchase_requests as requests
    from .models import PurchaseRequest

    engineer = get_user_model().objects.filter(username="engineer", is_active=True).first()
    if engineer is None or len(stocked) < 3 or not purchasing.enabled() or PurchaseRequest.objects.filter(project=project).exists():
        return None
    first = requests.save_request(project, engineer, {"needed_by": (today + timedelta(days=7)).isoformat(), "note": "احتياج الأسبوع القادم لصب سقف الدور الثاني", "lines": [
        {"item": stocked[0].pk, "quantity": "120", "note": "للمبنى الرئيسي"}, {"item": stocked[1].pk, "quantity": "30"},
        {"description": "مواسير تغذية مياه قطر بوصة", "unit": "متر", "quantity": "150", "note": "للدور الأرضي"}]})
    requests.submit(project, first, engineer)
    cut = first.lines.order_by("position")[1]
    approved = requests.approve(project, first, owner, supplier=supplier, quantities={cut.pk: "25"}, note="المورد يسلّم خلال أسبوع")
    second = requests.save_request(project, engineer, {"needed_by": (today + timedelta(days=5)).isoformat(), "lines": [{"item": stocked[2].pk, "quantity": "40"}]})
    requests.submit(project, second, engineer)
    return approved


#: (name, trade, day rate): day-workers are named people; the trade is its own column, never a title on the name.
WORKERS = (("محمود عبد الحميد", "نجار مسلح", "450"), ("سيد فتحي", "حداد مسلح", "450"), ("ياسر عطية", "فني مباني", "400"),
           ("عادل منصور", "عامل مساعد", "250"), ("رمضان سعيد", "عامل مساعد", "250"))


def seed_labour(project, owner, cashbox, today):
    """C5: five day-workers, a week of attendance (7 days back from 9 days ago, no Friday: 6 days each) paid on pay sheet 1
    two days ago, then the last two days recorded and left unpaid for the next sheet. The carpenter has two overtime days
    (2 and 3 hours). Returns the sheet, or None when the project has labour already."""

    from . import labour
    from .models import LabourDay

    if LabourDay.objects.filter(project=project).exists():
        return None
    workers = [labour.save_worker(owner, {"name": name, "trade": trade, "day_rate": rate}) for name, trade, rate in WORKERS]
    overtime = {1: "2", 3: "3"}  # the carpenter's: the 2nd and the 4th working day
    worked = 0
    for back in range(9, 2, -1):
        day = today - timedelta(days=back)
        if day.weekday() == 4:  # Friday: the site rests
            continue
        labour.record_day(project, owner, work_date=day, entries=[(worker, "1", overtime.get(worked, "0") if n == 0 else "0") for n, worker in enumerate(workers)])
        worked += 1
    sheet = labour.pay_sheet(project, owner, period_from=today - timedelta(days=9), period_to=today - timedelta(days=3), cashbox=cashbox,
                             pay_date=today - timedelta(days=2))
    for back in (2, 1):
        labour.record_day(project, owner, work_date=today - timedelta(days=back), entries=[(worker, "1", "0") for worker in workers])
    return sheet


def seed_equipment(project, owner, supplier, today):
    """C6: an owned excavator (350 an hour) with 19 hours on three days, and a hired tower crane (650 an hour, from ``supplier``)
    with 22 hours on three days, left unbilled so «اعمل فاتورة الإيجار» has work to do. Returns the hired machine, or None
    when the register is not empty."""

    from . import equipment
    from .models import Equipment

    if Equipment.objects.exists():
        return None
    owned = equipment.save_equipment(owner, {"name": "حفار شوفل", "kind": "owned", "hour_rate": "350"})
    hired = equipment.save_equipment(owner, {"name": "ونش برجي 40 متر", "kind": "hired", "hour_rate": "650", "supplier": supplier})
    for machine, back, hours, operator in ((owned, 8, "6", "عصام حسين"), (owned, 7, "8", "عصام حسين"), (owned, 5, "5", "عصام حسين"),
                                           (hired, 6, "8", "طارق سليم"), (hired, 5, "8", "طارق سليم"), (hired, 4, "6", "طارق سليم")):
        equipment.log_hours(project, owner, equipment=machine, work_date=today - timedelta(days=back), hours=hours, operator=operator)
    return hired


#: (code, description, unit, quantity, {heading: cost per unit}): what a tender's price analysis holds.
WON_TENDER = (("1", "حفر وردم للأساسات", "م³", "800", {"labour": "18", "equipment": "22"}),
              ("2", "خرسانة مسلحة للهيكل", "م³", "260", {"materials": "2100", "labour": "450", "equipment": "120"}),
              ("3", "مباني طوب أحمر", "م²", "1800", {"materials": "150", "labour": "70"}),
              ("4", "لياسة داخلية وخارجية", "م²", "4200", {"materials": "45", "labour": "40"}),
              ("5", "دهانات داخلية", "م²", "3600", {"materials": "38", "subcontract": "22"}))
LOST_TENDER = (("1", "خرسانة مسلحة للأعمال الإنشائية", "م³", "1400", {"materials": "2150", "labour": "470", "equipment": "130"}),
               ("2", "مباني طوب أحمر", "م²", "6500", {"materials": "155", "labour": "72"}),
               ("3", "أعمال كهرباء وإنارة", "نقطة", "900", {"materials": "260", "subcontract": "120"}))


def _priced_tender(owner, name, rows, **data):
    from tenders import services as tenders

    tender = tenders.save_tender({"name": name, "overhead_percent": "8", "profit_percent": "10", **data}, owner)
    for code, description, unit, quantity, costs in rows:
        item = tenders.save_item(tender, owner, {"code": code, "description": description, "unit": unit, "quantity": quantity})
        for heading, unit_cost in costs.items():
            tenders.save_cost(item, owner, {"heading": heading, "unit_cost": unit_cost})
    return tender


def seed_tenders(owner, customers):
    """C8: one tender won and one lost, through ``tenders.services``. The won one is priced, submitted and won (its project, bill
    of quantities and budget come from the unchanged ``win``); the lost one is for a prospect with no customer record yet, priced,
    submitted and closed with its reason. Returns the project the won tender made, or None when there are tenders already or
    the demo has no customer to win for."""

    from tenders import services as tenders
    from tenders.models import Tender, TenderStatus

    if Tender.objects.exists() or len(customers) < 2:
        return None
    today = timezone.localdate()
    won = _priced_tender(owner, "فيلا سكنية - الشيخ زايد", WON_TENDER, customer=customers[1], due_on=today - timedelta(days=20),
                         notes="عطاء محدود من المالك لفيلا من دورين وسطح")
    tenders.submit(won, owner, on=today - timedelta(days=25))
    project = tenders.win(won, owner, on=today - timedelta(days=12))
    lost = _priced_tender(owner, "مبنى إداري - العاصمة الإدارية", LOST_TENDER, prospect_name="شركة الأفق للتطوير العمراني", due_on=today - timedelta(days=30),
                          notes="تنافس على العطاء ثلاثة مقاولين")
    tenders.submit(lost, owner, on=today - timedelta(days=33))
    tenders.close(lost, owner, TenderStatus.LOST, "عرضنا أعلى من أقل عطاء بنسبة 6%", on=today - timedelta(days=8))
    return project


def seed_bank_side(master, owner):
    """C9 and C4, which need the demo's bank account: on the project with the bill of quantities, a cheque cleared (25% of what the
    second certificate is still owed, rounded to 1,000) and one in hand (35%), both through ``contract.receive_cheque``, so the
    rest stays due and overdue; and two letters of guarantee (a performance bond expiring in 20 days and an advance guarantee).
    Skipped, returning [], when there is no project or no bank."""

    from cheques import services as cheques

    project = _main_project()
    bank = cheques.banks().order_by("pk").first()
    if project is None or bank is None:
        return []
    today = timezone.localdate()
    made = []
    if seed_cheques(project, owner, bank, today) is not None:
        made.append("cheques")
    if seed_guarantees(project, owner, bank, today) is not None:
        made.append("guarantees")
    return made


def seed_cheques(project, owner, bank, today):
    """C9: see ``seed_bank_side``. Returns (cleared, in hand), or None when the project already has a cheque or nothing is due."""

    from cheques import services as cheques

    from . import contract
    from .models import ProjectPayment

    if ProjectPayment.objects.filter(project=project, payment__cheque__isnull=False).exists():
        return None
    due = contract.position(project)["due_now"]
    if due <= 0:
        return None
    cleared = contract.receive_cheque(project, owner, data={"number": "300871", "bank_name": "البنك التجاري الدولي", "due_date": (today - timedelta(days=5)).isoformat(),
                                                           "amount": str(_nearest(due * Decimal("0.25"), Decimal("1000"))), "received_on": (today - timedelta(days=12)).isoformat()})
    cheques.deposit(cleared, bank, owner, on=today - timedelta(days=6))
    cheques.clear(cleared, owner, on=today - timedelta(days=3))
    waiting = contract.receive_cheque(project, owner, data={"number": "300872", "bank_name": "بنك مصر", "due_date": (today + timedelta(days=14)).isoformat(),
                                                            "amount": str(_nearest(due * Decimal("0.35"), Decimal("1000"))), "received_on": (today - timedelta(days=2)).isoformat()})
    return cleared, waiting


def seed_guarantees(project, owner, bank, today):
    """C4: a performance bond of 5% of the contract value (10% cash margin, 1,500 commission) issued 11 months ago and expiring in
    20 days, and an advance guarantee for the advance received at the start (expiring in 170 days). Returns the first, or None when the
    project already has a guarantee."""

    from guarantees import services as guarantees
    from guarantees.models import Guarantee

    from . import contract

    if Guarantee.objects.filter(project=project).exists():
        return None
    value = contract.contract_value(project)
    advance = contract.position(project)["advance"]
    performance = guarantees.save_guarantee({"kind": "performance", "number": f"PB-{today.year}-0187", "bank": bank, "beneficiary": project.customer, "project": project,
                                             "amount": str(_nearest(value * Decimal("0.05"), Decimal("1000"))), "issued_on": (today - timedelta(days=345)).isoformat(),
                                             "expires_on": (today + timedelta(days=20)).isoformat(), "margin_percent": "10", "commission": "1500",
                                             "notes": "خطاب ضمان حسن تنفيذ، يُجدَّد قبل انتهائه"}, owner)
    guarantees.save_guarantee({"kind": "advance", "number": f"AG-{today.year}-0094", "bank": bank, "beneficiary": project.customer, "project": project,
                               "amount": str(advance), "issued_on": (today - timedelta(days=60)).isoformat(),
                               "expires_on": (today + timedelta(days=170)).isoformat(), "margin_percent": "10"}, owner)
    return performance


def tour_path(url_name):
    """The address of the demo screen a «جرّب» step opens, for the steps that belong to one project's record: the project with the
    bill of quantities, its newest certificate, its variation order, the newest daily report that has photos, its subcontract by
    quantities. None when the demo has no such record (an emptied demo), and the card then drops the step."""

    from django.urls import reverse

    from .models import Subcontract, VariationOrder

    project = _main_project()
    if project is None:
        return None
    args = (project.pk,)
    if url_name == "projects:certificate":
        certificate = project.certificates.order_by("-number").first()
        args += (certificate.number,) if certificate else (None,)
    elif url_name == "projects:variation":
        order = VariationOrder.objects.filter(project=project).order_by("number").first()
        args += (order.pk,) if order else (None,)
    elif url_name == "projects:diary_day":
        reports = list(site_reports.reports_for(project))
        photos = site_reports.photo_counts(reports)
        report = next((row for row in reports if photos.get(row.pk)), reports[0] if reports else None)
        args += (report.pk,) if report else (None,)
    elif url_name == "projects:subcontract":
        sub = Subcontract.objects.filter(project=project, lines__isnull=False).order_by("pk").distinct().first()
        args += (sub.pk,) if sub else (None,)
    if None in args:
        return None
    return reverse(url_name, args=args)
