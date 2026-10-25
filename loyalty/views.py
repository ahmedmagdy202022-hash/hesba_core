"""T6 screen: the loyalty rules and the customers holding the most points."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse

from permissions.decorators import require_permission

from . import services

WORDS = {
    "ar": {
        "title": "نقاط الولاء", "intro": "كل فاتورة بيع مترحّلة لعميل باسمه بتكسب نقاط. العميل يصرفها خصم على أي فاتورة جاية أو في الكاشير. الإلغاء والمرتجع بيرجّعوا النقاط لوحدهم.",
        "rules": "القواعد", "amount_per_point": "كل كام جنيه = نقطة", "point_value": "قيمة النقطة (جنيه)", "min_points": "أقل نقاط تتصرف مرة واحدة",
        "expiry_months": "النقاط بتخلص بعد كام شهر (0 = مبتخلصش)", "save": "حفظ", "saved": "اتحفظ.", "example": "مثال: فاتورة {total} بتكسب {points} نقطة، و{min} نقطة = {value} جنيه خصم.",
        "leaders": "العملاء اللي معاهم أكتر نقاط", "customer": "العميل", "points": "النقاط", "worth": "قيمتها", "none": "لسه محدش كسب نقاط.", "card": "الكارت",
    },
    "en": {
        "title": "Loyalty points", "intro": "Every posted sales invoice to a named customer earns points. The customer spends them as a discount on a later invoice or at the till. A cancellation or a return takes them back by itself.",
        "rules": "Rules", "amount_per_point": "Pounds per point", "point_value": "A point is worth (EGP)", "min_points": "Fewest points spent at a time",
        "expiry_months": "Points run out after (months, 0 = never)", "save": "Save", "saved": "Saved.", "example": "Example: an invoice of {total} earns {points} points, and {min} points take {value} off.",
        "leaders": "Customers with the most points", "customer": "Customer", "points": "Points", "worth": "Worth", "none": "No customer has earned points yet.", "card": "Card",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@require_permission("sales.view_sales_invoices")
def loyalty_home(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = services.can_manage(request.user)
    error = ""
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        try:
            services.save_rules(request.POST, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            flash.success(request, words["saved"])
            return redirect(f"{reverse('loyalty:home')}?lang={lang}")
    rules = services.rules()
    example_total = rules.amount_per_point * 50
    example = words["example"].format(total=f"{example_total:,.2f}", points=services.earned_for(example_total, rules), min=rules.min_points,
                                      value=f"{services.value_of(rules.min_points, rules):,.2f}")
    return render(request, "loyalty/home.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "rules": rules, "error": error,
        "form": request.POST if request.method == "POST" else None, "can_manage": can_manage, "example": example, "leaders": services.leaders()})
