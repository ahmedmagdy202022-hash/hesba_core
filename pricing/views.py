"""PRICE-001 screens: the lists, one list's header, customers and item prices."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Customer, Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.ui_messages import translate

from .models import CustomerPriceList, PriceList
from .services import assign_customers, list_prices, save_price_list, set_item_prices


VIEW_PERMISSION = "master_data.view_master_data"
MANAGE_PERMISSION = "master_data.manage_items"
PAGE_SIZE = 50

WORDS = {
    "ar": {
        "page_title": "قوائم الأسعار", "title": "قوائم الأسعار",
        "intro": "أسعار مختلفة لنفس الصنف: قطاعي وجملة وسعر خاص لعملاء بعينهم. العميل اللي مش على قائمة بياخد سعر القطاعي، والكاشير يقدر يعدّل السعر في الفاتورة.",
        "new": "قائمة جديدة", "code": "الكود", "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي",
        "adjust": "تعديل تلقائي على سعر القطاعي (%)", "adjust_hint": "مثال: ‎-10 يعني أقل من القطاعي بـ 10٪ لأي صنف مالوش سعر محدد في القائمة.",
        "active": "مفعّلة", "inactive": "موقوفة", "save": "حفظ", "items_count": "أصناف بسعر محدد", "customers_count": "عملاء",
        "empty": "لسه مفيش قوائم أسعار.", "back": "العودة لقوائم الأسعار", "customers": "العملاء على القائمة دي",
        "customers_hint": "علّم على العملاء اللي بيشتروا بالقائمة دي. العميل يبقى على قائمة واحدة بس، فلو كان على قائمة تانية هيتنقل.",
        "prices": "أسعار الأصناف", "prices_hint": "سيب الخانة فاضية عشان الصنف ياخد التعديل التلقائي.",
        "item": "الصنف", "retail": "سعر القطاعي", "list_price": "سعر القائمة", "effective": "السعر المطبّق",
        "search": "بحث بالكود أو الاسم", "filter": "بحث", "saved": "اتحفظت القائمة.", "prices_saved": "اتحفظ {n} سعر.",
        "customers_saved": "اتحفظ عملاء القائمة ({n}).", "bad_price": "أسعار غير صحيحة للأصناف: {codes}", "on_other": "حاليًا على: {name}",
        "view_only": "تقدر تشوف القوائم بس؛ التعديل لمسؤول الأصناف.", "prev": "السابق", "next": "التالي",
    },
    "en": {
        "page_title": "Price lists", "title": "Price lists",
        "intro": "Different prices for the same item: retail, wholesale and special prices for chosen customers. A customer on no list pays retail, and the cashier can still change the price on the invoice.",
        "new": "New list", "code": "Code", "name_ar": "Arabic name", "name_en": "English name",
        "adjust": "Automatic adjustment on retail (%)", "adjust_hint": "Example: -10 means 10% below retail for any item without its own price on this list.",
        "active": "Active", "inactive": "Inactive", "save": "Save", "items_count": "Items with own price", "customers_count": "Customers",
        "empty": "No price lists yet.", "back": "Back to price lists", "customers": "Customers on this list",
        "customers_hint": "Tick the customers who buy on this list. A customer is on one list only, so ticking moves them from any other list.",
        "prices": "Item prices", "prices_hint": "Leave a box empty for the item to follow the automatic adjustment.",
        "item": "Item", "retail": "Retail price", "list_price": "List price", "effective": "Price applied",
        "search": "Search code or name", "filter": "Search", "saved": "Price list saved.", "prices_saved": "{n} prices saved.",
        "customers_saved": "List customers saved ({n}).", "bad_price": "Invalid prices for: {codes}", "on_other": "Now on: {name}",
        "view_only": "You can view price lists; editing is for item managers.", "prev": "Previous", "next": "Next",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "items",
               "can_manage": user_has_permission(request.user, MANAGE_PERMISSION)}
    context.update(extra)
    return context


def _errors(exc, lang):
    return "؛ ".join(translate(message, lang) for message in getattr(exc, "messages", [str(exc)]))


def _header_from(post):
    return {
        "code": post.get("code", ""), "name_ar": post.get("name_ar", ""), "name_en": post.get("name_en", ""),
        "adjust_percent": (post.get("adjust_percent") or "0").replace(",", "."), "active": post.get("active") == "1",
    }


@require_permission(VIEW_PERMISSION)
def price_list_index(request):
    lang = _lang(request)
    header = {"code": "", "name_ar": "", "name_en": "", "adjust_percent": "0", "active": True}
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE_PERMISSION):
            raise PermissionDenied("Managing price lists needs master_data.manage_items.")
        header = _header_from(request.POST)
        try:
            price_list = save_price_list(user=request.user, **header)
        except (ValidationError, InvalidOperation) as exc:
            messages.error(request, _errors(exc, lang))
        else:
            messages.success(request, WORDS[lang]["saved"])
            return redirect(f"{reverse('pricing:detail', args=[price_list.pk])}?lang={lang}")
    lists = PriceList.objects.annotate(item_count=Count("items", distinct=True), customer_count=Count("customer_links", distinct=True))
    return render(request, "pricing/list.html", _context(request, lists=lists, header=header))


@require_permission(VIEW_PERMISSION)
def price_list_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    price_list = get_object_or_404(PriceList, pk=pk)
    query = (request.GET.get("q") or request.POST.get("q") or "").strip()
    items = Item.objects.filter(active=True).order_by("item_code")
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(item_name__icontains=query) | Q(barcode=query))
    page = Paginator(items, PAGE_SIZE).get_page(request.GET.get("page") or request.POST.get("page"))

    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE_PERMISSION):
            raise PermissionDenied("Managing price lists needs master_data.manage_items.")
        action = request.POST.get("action")
        back = f"{reverse('pricing:detail', args=[price_list.pk])}?lang={lang}&q={query}&page={page.number}"
        try:
            if action == "header":
                save_price_list(user=request.user, instance=price_list, **_header_from(request.POST))
                messages.success(request, words["saved"])
            elif action == "customers":
                chosen = Customer.objects.filter(pk__in=[value for value in request.POST.getlist("customer") if value.isdigit()], active=True)
                messages.success(request, words["customers_saved"].format(n=assign_customers(price_list, chosen, request.user)))
            elif action == "prices":
                prices, bad = {}, []
                for item in page.object_list:
                    raw = (request.POST.get(f"price_{item.pk}") or "").strip().replace(",", ".")
                    if f"shown_{item.pk}" not in request.POST:
                        continue
                    try:
                        prices[item] = Decimal(raw) if raw else None
                    except InvalidOperation:
                        bad.append(item.item_code)
                    else:
                        if raw and prices[item] < 0:
                            bad.append(item.item_code)
                if bad:
                    messages.error(request, words["bad_price"].format(codes=", ".join(bad)))
                else:
                    messages.success(request, words["prices_saved"].format(n=set_item_prices(price_list, prices, request.user)))
        except (ValidationError, InvalidOperation) as exc:
            messages.error(request, _errors(exc, lang))
        return redirect(back)

    effective = list_prices(price_list, page.object_list)
    explicit = dict(price_list.items.filter(item__in=page.object_list).values_list("item_id", "price"))
    rows = [{"item": item, "explicit": explicit.get(item.pk), "effective": effective[item.pk]} for item in page.object_list]
    links = dict(CustomerPriceList.objects.select_related("price_list").values_list("customer_id", "price_list__name_ar"))
    link_ids = dict(CustomerPriceList.objects.values_list("customer_id", "price_list_id"))
    customers = [
        {"customer": customer, "on": link_ids.get(customer.pk) == price_list.pk, "other": words["on_other"].format(name=links[customer.pk]) if link_ids.get(customer.pk) not in (None, price_list.pk) else ""}
        for customer in Customer.objects.filter(active=True).order_by("name")
    ]
    return render(request, "pricing/detail.html", _context(request, price_list=price_list, rows=rows, page=page, q=query, customers=customers))
