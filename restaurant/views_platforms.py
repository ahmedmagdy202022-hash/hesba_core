"""Restaurants R14 (HG-120): «منصات التوصيل», the delivery platforms' set-up, and the platform order's own steps.

Seen with the invoices' view right and changed with ``master_data.manage_items``, as the kitchen stations, the delivery zones and
the brands are (no new permission). The page links one named customer and one courier per platform; posting, handing over,
collecting and settling go through ``services.pay`` and the T14 ``shipping`` services, unchanged.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse

from master_data.models import Customer
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import platforms
from .views import TABLES, VIEW, _base, _lang

PLATFORM_WORDS = {
    "ar": {
        "title": "منصات التوصيل", "back": "الطاولات",
        "intro": "طلبات وelmenus وأي منصة تانية، من غير ربط بنظام المنصة: الطلب بيتسجّل هنا بإيدك برقمه على المنصة.",
        "how": "إزاي بتشتغل", "step1": "المنصة عميل باسمها، وأسعارها من «قوائم الأسعار»: الطبق بيتحسب على طلبها بسعر قايمتها.",
        "step2": "والمنصة كمان شركة شحن ليها صندوق: لما مندوبها يستلم الطلب وتحصّله، الفلوس بتبقى في صندوقها مستحقة عليها.",
        "step3": "الطلب بيتسجّل آجل على حساب المنصة، ومفيش فلوس بتدخل الدرج. التسوية الأسبوعية من «الشحن والتحصيل»: الصافي للبنك، والعمولة مصروف.",
        "platform": "المنصة", "price_list": "قائمة الأسعار", "menu_prices": "أسعار المنيو", "courier": "شركة الشحن", "commission": "العمولة",
        "commission_hint": "نسبة من إجمالي الطلب بالضريبة؛ بتتسجّل مصاريف شحن على كل طلب يتسلّم للمندوب.",
        "held": "في صندوقها", "branch": "الفرع", "edit": "تعديل", "remove": "فك الربط", "remove_confirm": "تفك ربط المنصة دي؟ العميل وشركة الشحن وطلباتهم بيفضلوا زي ما هم.",
        "save": "حفظ", "new": "منصة جديدة", "none": "لسه مفيش منصات. اربط عميل المنصة بشركة الشحن اللي بتمثّلها من هنا.",
        "pick_customer": "اختار العميل", "pick_courier": "اختار شركة الشحن", "saved": "اتحفظت المنصة.", "removed": "اتفك ربط المنصة.",
        "prices_off": "«قوائم الأسعار» مقفولة من الإعدادات، فطلبات المنصات هتتحسب بأسعار المنيو لحد ما تتفتح.",
        "shipping_off": "«الشحن والتحصيل» مقفول من الإعدادات، فالتسوية الأسبوعية مش هتتعمل لحد ما يتفتح.",
        "to_prices": "قوائم الأسعار", "to_shipping": "الشحن والتحصيل والتسوية", "to_customers": "العملاء", "off": "متوقفة",
    },
    "en": {
        "title": "Delivery platforms", "back": "Tables",
        "intro": "Talabat, elmenus or any other platform, with no link to its system: each order is entered here by hand with its number on the platform.",
        "how": "How it works", "step1": "The platform is a named customer, priced from “Price lists”: a dish on its order takes its list's price.",
        "step2": "The platform is also a courier with its own box: once its rider picks the order up and it collects it, the money sits in that box, owed by the platform.",
        "step3": "The order is posted on the platform's account; no money enters the drawer. The weekly settlement is on “Shipping”: the net to the bank, the commission an expense.",
        "platform": "Platform", "price_list": "Price list", "menu_prices": "Menu prices", "courier": "Courier", "commission": "Commission",
        "commission_hint": "A percentage of the order's total with VAT; recorded as the shipping fee of each order handed to its rider.",
        "held": "In its box", "branch": "Branch", "edit": "Edit", "remove": "Unlink", "remove_confirm": "Unlink this platform? Its customer, courier and their orders stay as they are.",
        "save": "Save", "new": "New platform", "none": "No platforms yet. Link a platform's customer to its courier here.",
        "pick_customer": "Choose the customer", "pick_courier": "Choose the courier", "saved": "Platform saved.", "removed": "Platform unlinked.",
        "prices_off": "“Price lists” is switched off in the settings, so platform orders are priced from the menu until it is on.",
        "shipping_off": "“Shipping” is switched off in the settings, so the weekly settlement cannot be made until it is on.",
        "to_prices": "Price lists", "to_shipping": "Shipping, collection and settlement", "to_customers": "Customers", "off": "Off",
    },
}


#: The board's «طلب منصة» form and a platform order's page (``views.order_detail``).
PLATFORM_ORDER_WORDS = {
    "ar": {
        "new": "طلب منصة", "platform": "المنصة", "number": "رقم الطلب على المنصة", "address": "العنوان", "address_hint": "اختياري؛ من غيره بيتكتب «يستلمه مندوب المنصة».",
        "phone": "الموبايل", "setup": "منصات التوصيل", "intro": "طلب جه من طلبات أو elmenus: بيتحسب بأسعار المنصة ويتسجّل على حسابها.",
        "tag": "طلب {name}", "number_short": "رقم المنصة",
        "pay_title": "سجّله على {name}", "pay_note": "الطلب ده بيتسجّل آجل على حساب {name}، من غير فلوس في الدرج. {name} بتحصّله، والتسوية الأسبوعية من صندوقها.",
        "pay_btn": "سجّل الطلب على {name}", "total": "الإجمالي",
        "panel": "خطوات طلب {name}", "posted": "اتسجّل على الحساب", "handed": "مع مندوب المنصة", "collected": "المنصة حصّلته", "settled": "اتسوّى",
        "hand_over": "سلّمه لمندوب {name}", "hand_over_note": "مع التسليم بتتسجّل عمولة {name} {percent}%: {fee}، وبتتخصم في التسوية.",
        "collect": "{name} حصّلته", "collect_note": "لما {name} تأكد إن الطلب وصل للعميل: المستحق بيتنقل من حساب {name} لصندوقها لحد التسوية.",
        "held_note": "المبلغ في صندوق {name} لحد التسوية الأسبوعية من «الشحن والتحصيل».", "settled_note": "اتسوّى مع {name}: الصافي اتحوّل والعمولة اتسجّلت مصروف.",
        "other_note": "الشحنة حالتها «{state}»؛ تابعها من «الشحن والتحصيل».", "to_shipping": "الشحن والتحصيل والتسوية", "commission": "العمولة",
        "handed_ok": "اتسلّم الطلب لمندوب {name}.", "collected_ok": "اتسجّل إن {name} حصّلت الطلب.",
        "by_platform": "حسب المنصة", "commission_recorded": "العمولة المسجّلة",
        "report_note": "المبيعات قبل الضريبة وبعد المرتجعات. العمولة هي اللي اتسجّلت لما الطلب اتسلّم لمندوب المنصة، وبتتخصم في التسوية. طلبات المنصات داخلة كمان في «الدليفري».",
    },
    "en": {
        "new": "Platform order", "platform": "Platform", "number": "Order number on the platform", "address": "Address", "address_hint": "Optional; without it, “Picked up by the platform's rider” is written.",
        "phone": "Mobile", "setup": "Delivery platforms", "intro": "An order from Talabat or elmenus: priced from the platform's list and posted on its account.",
        "tag": "{name} order", "number_short": "Platform no.",
        "pay_title": "Post it on {name}", "pay_note": "This order is posted on {name}'s account, with nothing in the drawer. {name} collects it, and the weekly settlement is from its box.",
        "pay_btn": "Post the order on {name}", "total": "Total",
        "panel": "{name} order steps", "posted": "Posted on the account", "handed": "With the platform's rider", "collected": "Collected by the platform", "settled": "Settled",
        "hand_over": "Hand it to {name}'s rider", "hand_over_note": "Handing it over records {name}'s {percent}% commission: {fee}, taken off at the settlement.",
        "collect": "{name} collected it", "collect_note": "Once {name} confirms the order reached the customer: the amount moves from {name}'s account to its box until the settlement.",
        "held_note": "The amount sits in {name}'s box until the weekly settlement on “Shipping”.", "settled_note": "Settled with {name}: the net was transferred and the commission booked as an expense.",
        "other_note": "The shipment is “{state}”; follow it on “Shipping”.", "to_shipping": "Shipping, collection and settlement", "commission": "Commission",
        "handed_ok": "The order was handed to {name}'s rider.", "collected_ok": "{name}'s collection was recorded.",
        "by_platform": "By platform", "commission_recorded": "Commission recorded",
        "report_note": "Sales before VAT and after returns. The commission is what was recorded when the order was handed to the platform's rider, taken off at the settlement. Platform orders are also counted under “Delivery”.",
    },
}


def _pick(model, raw, queryset=None):
    raw = str(raw or "").strip()
    if not raw.isdigit():
        return None
    return (queryset if queryset is not None else model.objects).filter(pk=int(raw)).first()


@require_permission(VIEW)
def setup(request):
    from pricing.services import customer_list
    from sales.pos import WALK_IN_CODE
    from settings_core.capabilities import capability_enabled
    from shipping import services as shipping
    from shipping.models import Courier

    lang = _lang(request)
    pw = PLATFORM_WORDS[lang]
    error = ""
    form = {"customer": request.GET.get("edit", ""), "courier": "", "commission": ""}
    found = platforms.platforms()
    editing = next((one for one in found if str(one.customer.pk) == str(form["customer"])), None)
    if editing is not None:
        form = {"customer": str(editing.customer.pk), "courier": str(editing.courier.pk), "commission": f"{editing.commission.normalize():f}"}
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing the delivery platforms needs master_data.manage_items.")
        form = {key: str(request.POST.get(key) or "").strip() for key in ("customer", "courier", "commission")}
        try:
            customer = _pick(Customer, form["customer"])
            if request.POST.get("action") == "remove":
                platforms.remove_platform(customer, request.user)
                messages.success(request, pw["removed"])
            else:
                platforms.save_platform(customer, _pick(Courier, form["courier"], shipping.couriers()), form["commission"], request.user, lang)
                messages.success(request, pw["saved"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect(f"{reverse('restaurant:platforms')}?lang={lang}")
    from entities.services import is_multi_entity, main_entity

    grouped = is_multi_entity()
    rows = []
    for one in found:
        price_list = customer_list(one.customer)
        rows.append({"platform": one, "price_list": (price_list.name_en or price_list.name_ar) if price_list and lang == "en" else (price_list.name_ar if price_list else ""),
                     "held": shipping.held(one.courier), "branch": (one.courier.cashbox.entity or main_entity()).display(lang) if grouped else "",
                     "commission": f"{one.commission.normalize():f}", "off": not (one.customer.active and one.courier.active)})
    return render(request, "restaurant/platforms.html", _base(
        request, page_title=pw["title"], pw=pw, rows=rows, form=form, editing=editing, error=error, grouped=grouped, can_edit=user_has_permission(request.user, TABLES),
        customers=Customer.objects.filter(active=True).exclude(customer_code=WALK_IN_CODE).order_by("name"),
        couriers=shipping.couriers(Courier.objects.filter(active=True)).order_by("name"),
        prices_off=not capability_enabled("price_lists"), shipping_off=not capability_enabled("shipping"),
    ))
