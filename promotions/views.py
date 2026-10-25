"""T5 screen: the promotions, their form, and what each one gave."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Category, Item
from permissions.decorators import require_permission

from . import services
from .models import Promotion, PromotionKind

WORDS = {
    "ar": {
        "title": "العروض والكوبونات", "intro": "العرض بيتطبق لوحده في فاتورة البيع والكاشير وهو شغال: أحسن عرض واحد بس، وعلى السطور اللي مفيهاش خصم مكتوب بإيدك. الكوبون بيشتغل لما الكود يتكتب.",
        "new": "عرض جديد", "edit": "تعديل العرض", "name": "اسم العرض", "kind": "النوع", "percent": "النسبة %", "amount": "مبلغ الخصم", "min_total": "لما الفاتورة توصل لـ",
        "buy": "اشتري", "get": "وخد ببلاش", "item": "على صنف", "category": "أو على قسم", "everything": "كل الأصناف", "coupon": "كود الكوبون (اختياري)",
        "starts": "من يوم", "ends": "لحد يوم", "from_time": "من الساعة", "to_time": "لحد الساعة", "active": "شغال", "save": "حفظ", "saved": "اتحفظ.",
        "state": "الحالة", "running": "شغال دلوقتي", "stopped": "واقف", "invoices": "فواتير", "given": "الخصم اللي اتعمل", "none": "مفيش عروض لسه.",
        "what": "على", "when": "إمتى", "always": "على طول", "open": "تعديل",
    },
    "en": {
        "title": "Promotions & coupons", "intro": "A running promotion applies by itself on the sales invoice and at the till: only the single best one, and only on lines without a discount typed by hand. A coupon works when its code is typed.",
        "new": "New promotion", "edit": "Edit promotion", "name": "Name", "kind": "Kind", "percent": "Percent %", "amount": "Amount off", "min_total": "When the invoice reaches",
        "buy": "Buy", "get": "get free", "item": "On an item", "category": "or a section", "everything": "Every item", "coupon": "Coupon code (optional)",
        "starts": "From", "ends": "Until", "from_time": "From time", "to_time": "To time", "active": "On", "save": "Save", "saved": "Saved.",
        "state": "State", "running": "Running now", "stopped": "Off", "invoices": "Invoices", "given": "Discount given", "none": "No promotions yet.",
        "what": "On", "when": "When", "always": "Always", "open": "Edit",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@require_permission("master_data.view_master_data")
def promotion_list(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = services.can_manage(request.user)
    editing = None
    if request.GET.get("edit", "").isdigit() or request.POST.get("promotion", "").isdigit():
        editing = get_object_or_404(Promotion, pk=request.GET.get("edit") or request.POST.get("promotion"))
    error = ""
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        try:
            services.save(request.POST, request.user, editing, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            flash.success(request, words["saved"])
            return redirect(f"{reverse('promotions:list')}?lang={lang}")
    running = {promo.pk for promo in services.running()}
    used = services.report()
    rows = [{"promo": promo, "running": promo.pk in running, "use": used.get(promo.pk, {"invoices": 0, "discount": 0})}
            for promo in Promotion.objects.select_related("item", "category")]
    form = request.POST if request.method == "POST" else None
    return render(request, "promotions/list.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "rows": rows, "error": error,
        "can_manage": can_manage, "editing": editing, "form": form, "kinds": PromotionKind.choices, "today": timezone.localdate(),
        "items": Item.objects.filter(active=True).order_by("item_name")[:800] if can_manage else [],
        "categories": Category.objects.filter(active=True).order_by("name_ar") if can_manage else []})
