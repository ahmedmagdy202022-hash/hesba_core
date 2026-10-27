"""HG-065: the owner's switch for selling without stock, and the list of what was sold before it arrived."""

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render

from config.paging import page_of
from entities import scope as entity_scope
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import short_sales
from .models import SalesInvoiceStatus, ShortSale

ZERO_QTY = Decimal("0.000")

WORDS = {
    "ar": {"title": "البيع من غير رصيد", "intro": "لما المفتاح ده شغال، الكاشير وفاتورة البيع يقدروا يبيعوا صنف الكمية بتاعته مش كفاية، بعد ما البايع يوافق في رسالة بتسمّي كل صنف ناقص. الموافقة بتتسجل في سجل المراجعة باسم اللي وافق.",
           "how": "تكلفة الجزء اللي مالوش رصيد بتتحسب مؤقتًا بآخر سعر شراء (أو سعر الشراء المكتوب على الصنف). أول ما البضاعة توصل، الفرق بين التقدير والتكلفة الحقيقية بيتسوّى في تاريخ وصولها، فالربح والمخزون يطلعوا مظبوطين كأن الشراء جه الأول. الفاتورة نفسها مابتتعدلش.",
           "on": "شغال", "off": "مقفول", "turn_on": "شغّله", "turn_off": "اقفله", "owner_only": "تشغيل المفتاح ده لصاحب الحساب بس.",
           "saved_on": "البيع من غير رصيد اتفتح.", "saved_off": "البيع من غير رصيد اتقفل. أي بيعة ناقصة هتترفض زي الأول.",
           "list": "اتباع قبل ما يوصل", "none": "مفيش مبيعات من غير رصيد لسه.", "invoice": "الفاتورة", "date": "التاريخ", "item": "الصنف",
           "warehouse": "المخزن", "short": "اتباع تحت الصفر", "estimated": "من غير أي رصيد", "estimate": "التكلفة التقديرية", "open": "لسه مستني",
           "settled": "اتسوّى من التكلفة", "state": "الحالة", "waiting": "مستني البضاعة", "done": "اتسوّى", "agreed": "وافق", "back": "المخزون",
           "open_only": "اللي لسه مستني بس", "all": "الكل"},
    "en": {"title": "Selling without stock", "intro": "With this switch on, the till and the sales invoice can sell an item whose stock is not enough, after the seller agrees to a box naming each short item. The agreement goes in the audit log with who agreed.",
           "how": "The part with no stock behind it is costed for now at the last purchase price (or the item's purchase price). When the stock arrives, the difference between the estimate and the real cost is settled on the day it arrives, so profit and stock come out as if the purchase had come first. The invoice itself is never changed.",
           "on": "On", "off": "Off", "turn_on": "Switch on", "turn_off": "Switch off", "owner_only": "Only the account owner can switch this.",
           "saved_on": "Selling without stock is on.", "saved_off": "Selling without stock is off. A short sale is refused as before.",
           "list": "Sold before it arrived", "none": "Nothing has been sold without stock yet.", "invoice": "Invoice", "date": "Date", "item": "Item",
           "warehouse": "Warehouse", "short": "Sold below zero", "estimated": "With no stock at all", "estimate": "Estimated cost", "open": "Still waiting",
           "settled": "Cost settled", "state": "State", "waiting": "Waiting for stock", "done": "Settled", "agreed": "Agreed by", "back": "Stock",
           "open_only": "Still waiting only", "all": "All"},
}


@require_permission("inventory.view_stock")
def short_sales_page(request):
    lang = "en" if (request.POST.get("lang") or request.GET.get("lang")) == "en" else "ar"
    words = WORDS[lang]
    owner = short_sales.is_owner(request.user)
    if request.method == "POST":
        if not owner:
            raise PermissionDenied("Only the account owner can allow selling without stock.")
        on = request.POST.get("enabled") == "1"
        short_sales.set_enabled(on, request.user)
        messages.success(request, words["saved_on" if on else "saved_off"])
        return redirect(f"/sales/short-sales/?lang={lang}")
    sales = (ShortSale.objects.filter(entity_scope.q(entity_scope.SALES_INVOICE, "sales_line__invoice__"),
                                      sales_line__invoice__status=SalesInvoiceStatus.POSTED)
             .annotate(used=Coalesce(Sum("entries__quantity"), Value(ZERO_QTY)))
             .annotate(left=ExpressionWrapper(F("estimated_quantity") - F("used"), output_field=DecimalField(max_digits=14, decimal_places=3)))
             .select_related("sales_line__invoice", "item", "location", "agreed_by").order_by("-pk"))
    waiting_only = request.GET.get("show") != "all"
    if waiting_only:  # filtered in the database, so an old sale still waiting is never cut off by newer ones (Codex #278)
        sales = sales.filter(estimated_quantity__gt=0, left__gt=0)
    page = page_of(request, sales)
    rows = [{"sale": short_sale, "invoice": short_sale.sales_line.invoice, "open": max(short_sale.left, ZERO_QTY),
             "settled": short_sales.settled_amount(short_sale) if short_sale.estimated_quantity else 0}
            for short_sale in page.object_list]
    return render(request, "sales/short_sales.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "section": "sales",
        "enabled": short_sales.enabled(), "owner": owner, "rows": rows, "page": page, "waiting_only": waiting_only,
        "can_view_cost": user_has_permission(request.user, "inventory.view_cost"),
    })
