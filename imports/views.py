import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from permissions.decorators import require_permission

from .models import ImportBatch, ImportRowStatus
from . import columns
from .screen_services import SCREEN_TYPES, SCREEN_TYPE_CODES, column_samples, import_batch, map_columns, pending_columns, preview, upload_batch


PERMISSION = "imports.run_import"

WORDS = {
    "ar": {
        "page_title": "استيراد البيانات",
        "title": "استيراد البيانات من Excel",
        "intro": "نزّل القالب، املاه في Excel، وارفعه هنا. حِسبة هتراجع كل صف وتوريك الأخطاء قبل ما يتسجل أي حاجة.",
        "order": "رتّب الاستيراد كده: التصنيفات، المخازن، الأصناف، العملاء، الموردين، الخزن، وبعدين مخزون أول المدة والأرصدة الافتتاحية.",
        "type": "نوع البيانات",
        "file": "الملف (xlsx أو csv)",
        "go_live": "تاريخ بداية التشغيل (لمخزون أول المدة)",
        "upload": "رفع ومراجعة",
        "template": "القالب",
        "download": "تنزيل",
        "history": "عمليات الاستيراد",
        "batch": "العملية",
        "rows": "الصفوف",
        "valid": "سليمة",
        "invalid": "فيها أخطاء",
        "imported": "اتسجلت",
        "status": "الحالة",
        "row": "الصف",
        "data": "البيانات",
        "errors": "الأخطاء",
        "ok": "سليم",
        "do_import": "سجّل كل الصفوف",
        "fix_first": "صلّح الصفوف اللي فيها أخطاء في الملف وارفعه تاني. مفيش حاجة هتتسجل غير لما كل الصفوف تبقى سليمة.",
        "done": "تم تسجيل {count} صف.",
        "back": "العودة للاستيراد",
        "empty": "لسه مفيش عمليات استيراد.",
        "file_type": "الملف لازم يكون xlsx أو csv.",
        "file_too_big": "الملف أكبر من 5 ميجا.",
        "file_unreadable": "مش قادرين نقرا الملف. احفظه تاني من Excel (xlsx أو CSV UTF-8).",
        "file_empty": "الملف فاضي.",
        "file_too_many_rows": "الملف فيه أكتر من 5000 صف؛ قسّمه على أكتر من ملف.",
        "any_file": "مش لازم تستخدم القالب: ارفع ملف Excel من برنامجك القديم زي ما هو، وحِسبة هتتعرف على الأعمدة، واللي مش واضح هتسألك عليه.",
        "map_title": "ربط الأعمدة", "map_intro": "حِسبة خمّنت كل عمود في ملفك معناه إيه. راجع، صحّح اللي غلط، واختار «تجاهل» للأعمدة اللي مش محتاجها.",
        "column": "العمود في ملفك", "samples": "أمثلة من الملف", "means": "معناه في حِسبة", "ignore": "— تجاهل —", "required": "مطلوب",
        "auto_code": "برنامجي مفيهوش أكواد؛ حِسبة ترقّمهم لوحدها", "show_preview": "اعرض المعاينة", "confirm": "تمام، راجع الصفوف",
        "preview": "معاينة أول الصفوف بعد الربط", "missing": "لسه ناقص: {fields}.",
        "mapping_missing": "فيه حقول مطلوبة مش مربوطة بأي عمود.", "mapping_twice": "نفس الحقل مربوط بعمودين؛ اختار عمود واحد.",
        "mapping_unknown": "اختيار مش معروف.", "mapping_done": "الأعمدة دي اتربطت بالفعل.", "recognised": "اتعرّف عليه تلقائياً", "stock_hint": "ملفك فيه عمود كميات. الكميات مش بتتسجل مع الأصناف؛ بعد ما تستورد الأصناف، ارفع نفس الملف تاني كـ«مخزون أول المدة» واربط عمود الكمية.",
        "status_labels": {"uploaded": "مرفوع", "reviewing": "تحت المراجعة", "approved": "معتمد", "imported": "اتسجل", "failed": "فشل", "cancelled": "ملغي", "draft": "مسودة"},
    },
    "en": {
        "page_title": "Data import",
        "title": "Import data from Excel",
        "intro": "Download the template, fill it in Excel and upload it here. Hesba checks every row and shows the errors before anything is saved.",
        "order": "Import in this order: categories, locations, items, customers, suppliers, cashboxes, then opening stock and opening balances.",
        "type": "Data type",
        "file": "File (xlsx or csv)",
        "go_live": "Go-live date (for opening stock)",
        "upload": "Upload and review",
        "template": "Template",
        "download": "Download",
        "history": "Imports",
        "batch": "Import",
        "rows": "Rows",
        "valid": "Valid",
        "invalid": "With errors",
        "imported": "Saved",
        "status": "Status",
        "row": "Row",
        "data": "Data",
        "errors": "Errors",
        "ok": "OK",
        "do_import": "Save all rows",
        "fix_first": "Fix the rows with errors in the file and upload it again. Nothing is saved until every row is valid.",
        "done": "{count} rows saved.",
        "back": "Back to import",
        "empty": "No imports yet.",
        "file_type": "The file must be xlsx or csv.",
        "file_too_big": "The file is larger than 5 MB.",
        "file_unreadable": "The file could not be read. Save it again from Excel (xlsx or CSV UTF-8).",
        "file_empty": "The file is empty.",
        "file_too_many_rows": "The file has more than 5000 rows; split it into several files.",
        "any_file": "You do not have to use the template: upload the Excel file from your old program as it is. Hesba recognises the columns and asks about the unclear ones.",
        "map_title": "Match the columns", "map_intro": "Hesba guessed what each column in your file means. Check it, correct anything wrong, and choose “Ignore” for columns you do not need.",
        "column": "Column in your file", "samples": "Examples from the file", "means": "Means in Hesba", "ignore": "— ignore —", "required": "required",
        "auto_code": "My program has no codes; let Hesba number them", "show_preview": "Show preview", "confirm": "OK, check the rows",
        "preview": "Preview of the first rows after matching", "missing": "Still missing: {fields}.",
        "mapping_missing": "Some required fields are not matched to any column.", "mapping_twice": "The same field is matched to two columns; pick one.",
        "mapping_unknown": "Unknown choice.", "mapping_done": "These columns are already matched.", "recognised": "recognised automatically", "stock_hint": "Your file has a quantity column. Quantities are not saved with items; after importing the items, upload the same file again as “Opening stock” and match the quantity column.",
        "status_labels": {"uploaded": "Uploaded", "reviewing": "Reviewing", "approved": "Approved", "imported": "Imported", "failed": "Failed", "cancelled": "Cancelled", "draft": "Draft"},
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "settings"}
    context.update(extra)
    return context


def _types(lang):
    return [{"code": code, "file": file, "label": en if lang == "en" else ar} for code, file, ar, en in SCREEN_TYPES]


def _messages(exc, lang):
    words = WORDS[lang]
    return [words.get(m, m) if isinstance(words.get(m, m), str) else m for m in getattr(exc, "messages", [str(exc)])]


@require_permission(PERMISSION)
def import_home(request):
    lang = _lang(request)
    if request.method == "POST":
        target_type = request.POST.get("target_type", "")
        uploaded = request.FILES.get("file")
        go_live = request.POST.get("go_live_date") or None
        if uploaded is None or target_type not in SCREEN_TYPE_CODES:
            messages.error(request, WORDS[lang]["file_type"])
        else:
            try:
                from datetime import date

                batch = upload_batch(target_type=target_type, uploaded=uploaded, user=request.user, go_live_date=date.fromisoformat(go_live) if go_live else None)
            except (ValidationError, ValueError) as exc:
                for text in _messages(exc, lang):
                    messages.error(request, text)
            else:
                if pending_columns(batch) is not None:
                    return redirect(f"/imports/{batch.pk}/columns/?lang={lang}")
                return redirect(f"/imports/{batch.pk}/?lang={lang}")
    batches = ImportBatch.objects.filter(target_type__in=SCREEN_TYPE_CODES)[:30]
    labels = {row["code"]: row["label"] for row in _types(lang)}
    return render(request, "imports/home.html", _context(request, types=_types(lang), batches=batches, type_labels=labels))


@require_permission(PERMISSION)
def batch_detail(request, pk):
    lang = _lang(request)
    batch = get_object_or_404(ImportBatch, pk=pk, target_type__in=SCREEN_TYPE_CODES)
    if pending_columns(batch) is not None:
        return redirect(f"/imports/{batch.pk}/columns/?lang={lang}")
    if request.method == "POST":
        try:
            count = import_batch(batch.pk, request.user)
        except ValidationError as exc:
            for text in _messages(exc, lang):
                messages.error(request, text)
        else:
            messages.success(request, WORDS[lang]["done"].format(count=count))
        return redirect(f"/imports/{batch.pk}/?lang={lang}")
    rows = batch.raw_rows.order_by("row_number")[:500]
    can_import = batch.status == "reviewing" and batch.total_rows and batch.invalid_rows == 0 and batch.valid_rows == batch.total_rows
    label = {row["code"]: row["label"] for row in _types(lang)}[batch.target_type]
    return render(
        request,
        "imports/detail.html",
        _context(request, batch=batch, rows=rows, can_import=can_import, type_label=label, status_label=WORDS[lang]["status_labels"].get(batch.status, batch.status), invalid_status=ImportRowStatus.INVALID),
    )


@require_permission(PERMISSION)
def batch_columns(request, pk):
    """IMPORT-002: match the columns of another program's file to Hesba's fields."""

    lang = _lang(request)
    words = WORDS[lang]
    batch = get_object_or_404(ImportBatch, pk=pk, target_type__in=SCREEN_TYPE_CODES)
    headers = pending_columns(batch)
    if headers is None:
        return redirect(f"/imports/{batch.pk}/?lang={lang}")
    suggested = columns.suggest(batch.target_type, headers)
    auto_code = False
    mapping = suggested
    if request.method == "POST":
        mapping = {header: request.POST.get(f"col_{index}", "") for index, header in enumerate(headers)}
        auto_code = request.POST.get("auto_code") == "on"
        if request.POST.get("action") == "confirm":
            try:
                map_columns(batch.pk, mapping, request.user, auto_code)
            except ValidationError as exc:
                for text in _messages(exc, lang):
                    messages.error(request, text)
            else:
                return redirect(f"/imports/{batch.pk}/?lang={lang}")
    fields = columns.fields(batch.target_type)
    label = {key: (en if lang == "en" else ar) for key, ar, en, _req in fields}
    samples = column_samples(batch, headers)
    rows = [{"index": index, "header": header, "samples": [value for value in samples[header] if value][:3], "chosen": mapping.get(header, ""),
             "recognised": bool(suggested.get(header)) and suggested.get(header) == mapping.get(header)} for index, header in enumerate(headers)]
    missing = columns.missing_required(batch.target_type, mapping, auto_code)
    mapped_keys = [key for key, *_ in fields if key in set(mapping.values()) or (auto_code and key == columns.AUTO_CODE.get(batch.target_type, ("",))[0])]
    preview_rows = [[row.get(key, "") for key in mapped_keys] for row in preview(batch, mapping, auto_code)]
    type_label = {row["code"]: row["label"] for row in _types(lang)}[batch.target_type]
    quantity_names = columns._names("stock", "quantity", ("الكمية", "Quantity"))
    stock_hint = batch.target_type == "items" and any(
        columns.normalise(header) in quantity_names or any(word in columns.normalise(header) for word in ("كميه", "رصيد", "qty", "quantity"))
        for header in headers)
    return render(request, "imports/columns.html", _context(
        request, batch=batch, rows=rows, fields=[{"key": key, "label": label[key], "required": req} for key, _ar, _en, req in fields],
        missing=words["missing"].format(fields="، ".join(en if lang == "en" else ar for _key, ar, en in missing)) if missing else "", auto_code=auto_code, can_auto_code=batch.target_type in columns.AUTO_CODE,
        preview_head=[label[key] for key in mapped_keys], preview_rows=preview_rows, type_label=type_label, stock_hint=stock_hint,
    ))


@require_permission(PERMISSION)
def template_download(request, name):
    allowed = {row[1] for row in SCREEN_TYPES}
    if name not in allowed:
        raise Http404
    path = Path(settings.BASE_DIR) / "import_templates" / name
    if not path.is_file():
        raise Http404
    return FileResponse(path.open("rb"), as_attachment=True, filename=name, content_type=mimetypes.guess_type(name)[0] or "text/csv")
