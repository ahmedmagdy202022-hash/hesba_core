"""Manufacturing F12 (HG-132): the variance report screen, ``/manufacturing/costs/``. Reading only."""

from django.shortcuts import render
from django.utils import timezone

from permissions.decorators import require_permission

from . import variance
from .views import COST, VIEW, _lang

WORDS = {
    "ar": {
        "page_title": "فروق تكلفة الإنتاج", "title": "فروق تكلفة الإنتاج",
        "intro": "كل أمر خلص في الفترة: الخامات المعيارية قصاد اللي اتصرف فعلًا، العمالة والمصاريف المخططة قصاد الفعلية، الكمية السليمة قصاد المطلوبة، وتكلفة القطعة قصاد المعيار.",
        "back": "التصنيع", "from": "من", "to": "لحد", "show": "اعرض", "order": "الأمر", "product": "المنتج", "date": "التاريخ",
        "material": "الخامات", "labour": "العمالة", "overhead": "مصاريف التشغيل", "outside": "تشغيل لدى الغير", "yield": "الكمية السليمة",
        "unit": "تكلفة القطعة", "standard": "المعيار", "actual": "الفعلي", "planned": "المخطط", "diff": "الفرق", "good": "سليم", "ordered": "مطلوب",
        "item": "الخامة", "std_qty": "الكمية المعيارية", "qty": "الكمية الفعلية", "std_value": "القيمة المعيارية", "value": "القيمة الفعلية",
        "details": "تفاصيل الخامات", "none": "مفيش أوامر خلصت في الفترة دي.", "total": "الإجمالي",
        "months": "المحمّل على الإنتاج قصاد المدفوع، شهر بشهر", "month": "الشهر", "absorbed": "المحمّل على المخزون (5102)", "wages": "مرتبات مدفوعة (5201)",
        "paid_overhead": "مصاريف تشغيل مدفوعة (إيجار وكهرباء وصيانة)", "paid": "إجمالي المدفوع", "gap": "الفرق",
        "months_hint": "لو المحمّل أكبر من المدفوع، المخزون متقيّم بأكتر من اللي اتصرف فعلًا، والربح هيبان أعلى لحد ما البضاعة تتباع.",
        "no_months": "دليل الحسابات ده مفيهوش حساب «تكلفة الإنتاج المحمّلة»، فالمقارنة الشهرية مش ظاهرة.",
        "lines_note": "الفعلي للعمالة والمصاريف: السطور الفعلية لو اتسجلت، وإلا الرقم المخطط.",
    },
    "en": {
        "page_title": "Production cost variance", "title": "Production cost variance",
        "intro": "Every order finished in the period: standard materials against what was really issued, planned labour and overhead against actual, good units against the order, and unit cost against the standard.",
        "back": "Manufacturing", "from": "From", "to": "To", "show": "Show", "order": "Order", "product": "Product", "date": "Date",
        "material": "Materials", "labour": "Labour", "overhead": "Overhead", "outside": "Outside processing", "yield": "Good units",
        "unit": "Unit cost", "standard": "Standard", "actual": "Actual", "planned": "Planned", "diff": "Variance", "good": "Good", "ordered": "Ordered",
        "item": "Material", "std_qty": "Standard quantity", "qty": "Actual quantity", "std_value": "Standard value", "value": "Actual value",
        "details": "Material details", "none": "No order finished in this period.", "total": "Total",
        "months": "Absorbed into production against paid, month by month", "month": "Month", "absorbed": "Absorbed into stock (5102)", "wages": "Wages paid (5201)",
        "paid_overhead": "Overhead paid (rent, utilities, maintenance)", "paid": "Total paid", "gap": "Difference",
        "months_hint": "When more is absorbed than was paid, stock is valued above what was really spent, and profit looks higher until the goods are sold.",
        "no_months": "This chart of accounts has no absorbed production cost account, so the monthly comparison is not shown.",
        "lines_note": "Actual labour and overhead: the actual lines where recorded, otherwise the planned figure.",
    },
}


@require_permission(VIEW)
@require_permission(COST)
def costs_screen(request):
    lang = _lang(request)
    words = WORDS[lang]
    date_from, date_to = variance.period(request.GET.get("from", ""), request.GET.get("to", ""), timezone.localdate())
    result = variance.report(date_from, date_to)
    return render(request, "manufacturing/costs.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"],
        "date_from": date_from, "date_to": date_to, **result})
