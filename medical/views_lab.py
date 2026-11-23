"""M9 (HG-092) screens: finding a sample by its code, printing its label, and importing results from an analyzer's file.

Every screen is a lab screen: 404 outside a lab (or in a non-lab entity of a group: lab data follows the entity being worked in),
403 without a clinical permission (reading) or without the permission to record results (the import). Nothing here moves
money; the rules live in ``medical.samples`` and ``medical.lab_import``.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from barcode.symbology import barcode_svg

from . import lab_import, samples, services
from .views import _base, _lab_guard, _lang, _tabs

WORDS = {
    "ar": {
        "tab_sample": "العيّنات", "tab_import": "استيراد النتايج",
        "sample_title": "العيّنات", "sample_intro": "امسح الباركود اللي على أنبوبة العيّنة أو اكتب كودها، وتفتحلك نتايج المريض في اليوم ده.",
        "code": "كود العيّنة", "placeholder": "امسح أو اكتب الكود، زي L12-20261112", "open": "افتح النتايج", "camera_hint": "على الموبايل زرار الكاميرا جنب الخانة.",
        "not_found": "مفيش عيّنة بالكود ده. اتأكد من الكود وجرّب تاني.",
        "code_help": "كود العيّنة: حرف L ورقم المريض وتاريخ اليوم (سنة وشهر ويوم). هو مش رابط ولا سر؛ بيفتح صفحة المريض لمين مسموحله يشوف نتايج المعمل بس.",
        "import_h": "نتايج من جهاز التحاليل", "import_p": "ارفع ملف CSV طالع من الجهاز، يتعرض الأول من غير ما يتكتب حاجة.", "import_go": "استيراد من ملف",
        "label_h": "ملصق العيّنة", "label_p": "ملصق صغير فيه الباركود واسم المريض والتاريخ وأسماء التحاليل، يتلزق على الأنبوبة.",
        "label_day": "تاريخ العيّنة", "label_print": "اطبع ملصق العيّنة", "label_link": "ملصق العيّنة",
        "sheet_back": "نتايج المريض", "size_roll": "ملصق 50×25 مم", "size_a4": "ورقة A4", "copies": "عدد الملصقات", "show": "اعرض", "print": "اطبع",
        "label_hint": "اختار الحجم من فوق قبل الطباعة: ملصق 50×25 مم للطابعة الصغيرة، أو ورقة A4 لو مفيش.", "more": "+{n}", "none_tests": "مفيش تحاليل مسجّلة",
        "opened": "عيّنة {code} · {day}", "no_results_day": "مفيش نتايج متسجّلة في اليوم ده لسه.",
        # the import
        "title": "استيراد نتايج من ملف", "intro": "ارفع ملف CSV طالع من جهاز التحاليل. هنعرضلك المعاينة الأول، ومش هيتكتب حاجة غير لما تأكد.",
        "format_h": "شكل الملف", "format_p": "التلات أعمدة بالترتيب: كود العيّنة، كود التحليل، القيمة. سطر العناوين اختياري، والفاصل فاصلة أو فاصلة منقوطة.",
        "format_note": "كود التحليل بتحطه من «التحاليل والمعدلات». السطر بيملا نتيجة تحليل مطلوب للمريض في اليوم ده بس، ومبيعملش مريض ولا تحليل ولا طلب جديد.",
        "file": "ملف CSV", "preview": "اعرض المعاينة", "back": "التحاليل والمعدلات", "again": "ارفع ملف تاني",
        "preview_h": "معاينة الملف", "preview_p": "لسه مفيش حاجة اتكتبت. راجع الأسطر وبعدين أكّد.", "file_name": "الملف", "rows_n": "{n} سطر",
        "apply": "اكتب {n} نتيجة", "apply_none": "مفيش سطر صالح يتكتب", "left_note": "الأسطر التانية هتتساب زي ما هي.",
        "result_h": "اتكتبت {n} نتيجة", "result_zero": "ماتكتبتش أي نتيجة", "result_left": "واتساب {n} سطر من غير ما يتكتب.", "result_p": "النتايج دخلت زي ما لو اتكتبت بإيد: بنفس المعدل والحكم.",
        "m6_note": "لو اتبعت رابط نتيجة لمريض ونتيجة يومه اتضافت، الرابط ده بيبطل وتبعتله رابط جديد.",
        "shown_note": "بنعرض أول {n} سطر، والمشاكل الأول. الأعداد فوق على الملف كله.",
        "c_line": "سطر", "c_sample": "كود العيّنة", "c_patient": "المريض", "c_test": "التحليل", "c_value": "القيمة", "c_status": "الحالة",
        "s_write": "هيتكتب", "s_written": "اتكتب", "s_same": "متسجّل بنفس القيمة", "s_differs": "متسجّل بقيمة تانية", "s_not_ordered": "مش مطلوب للمريض",
        "s_unknown_sample": "عيّنة مش معروفة", "s_unknown_test": "كود تحليل مش معروف", "s_not_number": "القيمة مش رقم", "s_duplicate": "سطر مكرر", "s_bad_row": "سطر ناقص",
        "d_differs": "المسجّل {old}، والملف فيه {new}. مش هيتغيّر.", "d_same": "المسجّل {old}.",
        "d_not_ordered": "التحليل ده مش مطلوب للمريض في اليوم ده (مفيش فاتورة بيه)، فمش هيتعمل.",
        "d_nothing": "مفيش تحاليل مطلوبة للمريض في اليوم ده، فمش هيتعمل.", "d_future": "تاريخ العيّنة لسه ماجاش.",
        "d_dup_same": "نفس سطر {n}.", "d_dup_conflict": "قيمته غير سطر {n}، فالاتنين اتسابوا.",
        "d_columns": "أقل من 3 أعمدة.", "d_empty": "في خانة فاضية.", "d_long": "خانة طويلة أوي.", "d_not_number": "التحليل ده بمعدل طبيعي فلازم القيمة رقم.",
    },
    "en": {
        "tab_sample": "Samples", "tab_import": "Import results",
        "sample_title": "Samples", "sample_intro": "Scan the barcode on the sample tube or type its code to open the patient's results for that day.",
        "code": "Sample code", "placeholder": "Scan or type the code, like L12-20261112", "open": "Open the results", "camera_hint": "On a phone, the camera button is next to the box.",
        "not_found": "No sample has this code. Check it and try again.",
        "code_help": "The sample code is the letter L, the patient's number and the day's date (year, month, day). It is not a link and not a secret; it only opens the patient's page for those allowed to see the lab's results.",
        "import_h": "Results from the analyzer", "import_p": "Upload the CSV file the analyzer exports. It is shown first and nothing is written.", "import_go": "Import from a file",
        "label_h": "Sample label", "label_p": "A small label with the barcode, the patient's name, the date and the tests' names, to stick on the tube.",
        "label_day": "Sample date", "label_print": "Print the sample label", "label_link": "Sample label",
        "sheet_back": "Patient's results", "size_roll": "50×25 mm label", "size_a4": "A4 sheet", "copies": "Number of labels", "show": "Show", "print": "Print",
        "label_hint": "Choose the size above before printing: a 50×25 mm label for the small printer, or an A4 sheet if there is none.", "more": "+{n}", "none_tests": "No tests recorded",
        "opened": "Sample {code} · {day}", "no_results_day": "No results are recorded for this day yet.",
        "title": "Import results from a file", "intro": "Upload the CSV file the analyzer exports. You see a preview first, and nothing is written until you confirm.",
        "format_h": "The file", "format_p": "Three columns in this order: sample code, test code, value. A header row is optional; the separator is a comma or a semicolon.",
        "format_note": "A test's code is set under “Tests and ranges”. A line fills the result of a test ordered for that patient on that day only; it never makes a patient, a test or an order.",
        "file": "CSV file", "preview": "Show the preview", "back": "Tests and ranges", "again": "Upload another file",
        "preview_h": "Preview of the file", "preview_p": "Nothing has been written yet. Check the lines, then confirm.", "file_name": "File", "rows_n": "{n} lines",
        "apply": "Write {n} results", "apply_none": "No good line to write", "left_note": "The other lines are left as they are.",
        "result_h": "{n} results written", "result_zero": "No result was written", "result_left": "{n} lines were left without being written.", "result_p": "The results went in as if typed by hand: with the same range and flag.",
        "m6_note": "If a results link was sent to a patient and that day's results were added, the link stops working and a new one must be sent.",
        "shown_note": "Showing the first {n} lines, problems first. The counts above cover the whole file.",
        "c_line": "Line", "c_sample": "Sample code", "c_patient": "Patient", "c_test": "Test", "c_value": "Value", "c_status": "Status",
        "s_write": "Will be written", "s_written": "Written", "s_same": "Already entered, same value", "s_differs": "Already entered, different value", "s_not_ordered": "Not ordered",
        "s_unknown_sample": "Unknown sample", "s_unknown_test": "Unknown test code", "s_not_number": "Value is not a number", "s_duplicate": "Duplicate line", "s_bad_row": "Incomplete line",
        "d_differs": "Entered: {old}; the file has {new}. It will not be changed.", "d_same": "Entered: {old}.",
        "d_not_ordered": "This test was not ordered for the patient that day (no invoice with it), so it is not done.",
        "d_nothing": "No tests were ordered for the patient that day, so it is not done.", "d_future": "The sample date has not come yet.",
        "d_dup_same": "Same as line {n}.", "d_dup_conflict": "Its value differs from line {n}, so both were left.",
        "d_columns": "Fewer than 3 columns.", "d_empty": "An empty cell.", "d_long": "A cell is too long.", "d_not_number": "This test has a normal range, so its value must be a number.",
    },
}

#: How a status is drawn (the shared op-status chips).
CHIP = {lab_import.WRITE: "posted", lab_import.SAME: "draft", lab_import.DIFFERS: "reopened", lab_import.NOT_ORDERED: "reopened",
        lab_import.UNKNOWN_SAMPLE: "cancelled", lab_import.UNKNOWN_TEST: "cancelled", lab_import.NOT_NUMBER: "cancelled",
        lab_import.DUPLICATE: "draft", lab_import.BAD_ROW: "cancelled"}
MAX_COPIES = 40


def _day(raw):
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise Http404("Not a date.")


# ---- finding a sample by its code ----

def lab_sample(request):
    _lab_guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    code = (request.GET.get("code") or "").strip()
    error = ""
    if code:
        found = samples.find(code)
        if found is not None:
            patient, day = found
            return redirect(f"{reverse('medical:lab', args=[patient.pk])}?lang={lang}&day={day.isoformat()}#lab-day-{day.isoformat()}")
        error = words["not_found"]
    response = render(request, "medical/lab_sample.html", _base(lang, page_title=words["sample_title"], sw=words, code=code, error=error,
                                                                 can_import=services.can_write(request.user), **_tabs(request, "lab_sample")))
    if error:
        response.status_code = 404
    return response


# ---- the sample label ----

@require_safe
def lab_label(request, pk, day=None):
    """The label of one patient's samples of one day: a Code 128 barcode of the sample code, the patient's name, the day and the tests'
    short names. Nothing else: no phone, no file number, no diagnosis, never M6's link. Printable before any result exists."""

    _lab_guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    patient = get_object_or_404(samples.patients(), pk=pk)
    today = timezone.localdate()
    chosen = _day(day or request.GET.get("day") or today.isoformat())
    if chosen > today:
        raise Http404("The sample day has not come.")
    size = "a4" if request.GET.get("size") == "a4" else "roll"
    default = 8 if size == "a4" else 2
    raw = str(request.GET.get("copies") or "")
    copies = max(1, min(int(raw), MAX_COPIES)) if raw.isdigit() else default
    code = samples.sample_code(patient, chosen)
    names = [samples.short_name(test) for test in samples.tests_of(patient, chosen)]
    shown = names[:samples.MAX_TESTS_ON_LABEL]
    more = len(names) - len(shown)
    return render(request, "medical/lab_label.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "sw": words, "patient": patient, "day": chosen, "code": code,
                                                      "svg": mark_safe(barcode_svg(code, module_width=2, height=60, show_text=False)), "size": size, "copies": copies,
                                                      "names": shown, "more": more, "more_text": words["more"].format(n=more), "labels": range(copies), "page_title": f"{words['label_link']} — {patient.name}",
                                                      "back_url": f"{reverse('medical:lab', args=[patient.pk])}?lang={lang}&day={chosen.isoformat()}"})


# ---- importing results ----

def _lines_context(lines, words, written=False):
    rows = []
    for line in lab_import.shown(lines):
        status = "written" if written and line.written else line.status
        rows.append({"line": line, "status": status, "chip": CHIP[line.status], "text": words[f"s_{status}"], "detail": _detail(line, words)})
    return rows


def _detail(line, words):
    if line.status == lab_import.DIFFERS:
        return words["d_differs"].format(old=line.existing, new=line.new)
    if line.status == lab_import.SAME:
        return words["d_same"].format(old=line.existing)
    if line.status == lab_import.NOT_ORDERED:
        return words["d_nothing"] if line.reason == "nothing" else words["d_not_ordered"]
    if line.status == lab_import.UNKNOWN_SAMPLE and line.reason == "future":
        return words["d_future"]
    if line.status == lab_import.DUPLICATE:
        return words[f"d_dup_{line.reason}"].format(n=line.other)
    if line.status == lab_import.BAD_ROW:
        return words[f"d_{line.reason}"]
    if line.status == lab_import.NOT_NUMBER:
        return words["d_not_number"]
    return ""


@require_http_methods(["GET", "HEAD", "POST"])
def lab_import_results(request):
    _lab_guard(request, write=True)
    lang = _lang(request)
    words = WORDS[lang]
    context = _base(lang, page_title=words["title"], sw=words, state="upload", error="", **_tabs(request, "lab_import"))
    if request.method != "POST":
        return render(request, "medical/lab_import.html", context)
    try:
        if request.POST.get("action") == "apply":
            name, rows = lab_import.unpack(request.user, request.POST.get("payload"), lang)
            lines, totals = lab_import.apply(request.user, name, rows, lang)
            context.update(state="result", name=name, totals=totals, rows=_lines_context(lines, words, written=True), total=len(lines),
                           written=totals[lab_import.WRITE], left=len(lines) - totals[lab_import.WRITE], capped=len(lines) > lab_import.SHOWN_ROWS)
        else:
            upload = request.FILES.get("file")
            rows = lab_import.read_file(upload, lang)
            lines = lab_import.classify(rows)
            totals = lab_import.counts(lines)
            context.update(state="preview", name=upload.name[:100], totals=totals, rows=_lines_context(lines, words), total=len(lines),
                           payload=lab_import.pack(request.user, upload.name, rows), good=totals[lab_import.WRITE], capped=len(lines) > lab_import.SHOWN_ROWS)
    except ValidationError as exc:
        context.update(state="upload", error=" ".join(exc.messages))
        return _status(render(request, "medical/lab_import.html", context), 400)
    context["t"] = {"result_h": words["result_h"].format(n=context.get("written", 0)), "result_left": words["result_left"].format(n=context.get("left", 0)),
                    "rows": words["rows_n"].format(n=context["total"]), "apply": words["apply"].format(n=context.get("good", 0)),
                    "shown": words["shown_note"].format(n=lab_import.SHOWN_ROWS)}
    context["statuses"] = [(status, words[f"s_{status}"], context["totals"][status], CHIP[status]) for status in lab_import.STATUSES if context["totals"][status]]
    return render(request, "medical/lab_import.html", context)


def _status(response, code):
    response.status_code = code
    return response
