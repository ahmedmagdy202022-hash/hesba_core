"""AN7 (G07, Ahmed approved): the till's tap-to-add buttons, chosen by the shop.

Bread, vegetables and fruit sold by weight carry no barcode, so the cashier
typed their name on every sale. The shop picks which items get a button; the
choice is one list of item ids in a system setting, no schema. Items the shop
has not picked still show after the picked ones: what sells without a barcode
first, then what sells most (sales.pos.quick_pick_ids).
"""

import json
from urllib.parse import quote

from django.contrib import messages
from django.shortcuts import redirect, render

from master_data.models import Item
from permissions.decorators import require_permission
from settings_core.models import SystemSetting


SETTING_KEY = "pos.quick_items"
MAX_PINNED = 24
SUGGESTIONS = 30


def pinned_ids():
    """The items the shop picked for a button, in the order it put them."""

    raw = SystemSetting.objects.filter(key=SETTING_KEY, active=True).values_list("value", flat=True).first()
    try:
        ids = json.loads(raw or "[]")
    except ValueError:
        return []
    return [int(item_id) for item_id in ids if isinstance(item_id, int) or str(item_id).isdigit()][:MAX_PINNED]


def save_pinned(ids):
    seen = []
    for item_id in ids:
        if item_id not in seen:
            seen.append(item_id)
    SystemSetting.objects.update_or_create(key=SETTING_KEY, defaults={
        "value": json.dumps(seen[:MAX_PINNED]), "data_type": SystemSetting.DataType.JSON, "active": True,
        "description": "AN7: the items with a tap-to-add button on the till, in order."})


CARD_KEY = "pos.card_cashbox"


def card_cashbox(cashboxes=None):
    """AN8 (Ahmed: "ضيف طرق دفع زي كاش او فيزا"): where a card payment at the till lands, a cashbox of its own
    (the card machine's account) so the drawer's cash and the shift count never include it. None until chosen."""

    from cashboxes.models import Cashbox

    raw = SystemSetting.objects.filter(key=CARD_KEY, active=True).values_list("value", flat=True).first()
    if not (raw or "").isdigit():
        return None
    return (cashboxes if cashboxes is not None else Cashbox.objects).filter(pk=int(raw), active=True).first()


def save_card_cashbox(cashbox):
    SystemSetting.objects.update_or_create(key=CARD_KEY, defaults={
        "value": str(cashbox.pk) if cashbox else "", "data_type": SystemSetting.DataType.INTEGER, "active": True,
        "description": "AN8: the cashbox a card payment at the till goes to."})


WORDS = {
    "ar": {"title": "أزرار الكاشير", "intro": "الأصناف اللي هيبقى ليها زرار في شاشة الكاشير، تدوس عليه يتضاف للسلة. مفيد للأصناف اللي من غير باركود (العيش، الخضار والفاكهة بالوزن). لو مختارتش حاجة، الشاشة بتعرض لوحدها الأكتر بيعًا من غير باركود.",
           "picked": "الأزرار اللي اخترتها", "none": "لسه مختارتش أزرار. الكاشير بيعرض الأكتر بيعًا لوحده.", "find": "دوّر على صنف تضيفه",
           "suggest": "أصناف من غير باركود", "results": "نتايج البحث", "add": "+ زرار", "remove": "شيل", "up": "لفوق", "full": "وصلت لأقصى عدد (24 زرار).",
           "no_barcode": "من غير باركود", "back": "رجوع للكاشير", "search": "دوّر", "nothing": "مفيش أصناف بالاسم ده.", "saved": "اتحفظت أزرار الكاشير.",
           "card": "الدفع بالفيزا", "card_intro": "الفلوس اللي بتتدفع بالفيزا بتروح الخزنة دي (حساب ماكينة الفيزا)، مش درج الكاشير، علشان عدّ الوردية يطلع مظبوط.",
           "card_none": "— الفيزا مقفولة —", "card_save": "احفظ", "card_saved": "اتحفظت خزنة الفيزا.", "card_same": "اختار خزنة غير خزنة الكاشير.", "title_full": "إعدادات الكاشير"},
    "en": {"title": "Till buttons", "intro": "Items that get a button on the till: tap it and it goes in the basket. Handy for items without a barcode (bread, vegetables and fruit by weight). With none picked, the till shows what sells most without a barcode by itself.",
           "picked": "Your buttons", "none": "No buttons picked yet. The till shows the best sellers by itself.", "find": "Find an item to add",
           "suggest": "Items without a barcode", "results": "Search results", "add": "+ button", "remove": "Remove", "up": "Up", "full": "That is the most buttons (24).",
           "no_barcode": "no barcode", "back": "Back to the till", "search": "Search", "nothing": "No items by that name.", "saved": "Till buttons saved.",
           "card": "Card payments", "card_intro": "Money paid by card goes to this cashbox (the card machine's account), not the till drawer, so the shift count stays right.",
           "card_none": "— No card payments —", "card_save": "Save", "card_saved": "Card cashbox saved.", "card_same": "Pick a cashbox other than the till's.", "title_full": "Till settings"},
}


@require_permission("master_data.manage_items")
def pos_buttons(request):
    lang = "en" if (request.POST.get("lang") or request.GET.get("lang")) == "en" else "ar"
    words = WORDS[lang]
    pinned = pinned_ids()
    from cashboxes.models import Cashbox
    from entities import scope as entity_scope

    cashboxes = entity_scope.cashboxes(Cashbox.objects).filter(active=True)
    if request.method == "POST" and request.POST.get("action") == "card":
        chosen = request.POST.get("card_cashbox", "")
        cashbox = cashboxes.filter(pk=chosen).first() if chosen.isdigit() else None
        if cashbox is not None and cashbox.is_default:  # the drawer's own cashbox would mix card money into the cash count
            messages.error(request, words["card_same"])
            return redirect(f"/sales/pos/buttons/?lang={lang}#card")
        save_card_cashbox(cashbox)
        messages.success(request, words["card_saved"])
        return redirect(f"/sales/pos/buttons/?lang={lang}")
    if request.method == "POST":
        item_id = request.POST.get("item", "")
        action = request.POST.get("action", "")
        if item_id.isdigit():
            item_id = int(item_id)
            if action == "add" and item_id not in pinned and Item.objects.filter(pk=item_id, active=True).exists():
                if len(pinned) >= MAX_PINNED:
                    messages.error(request, words["full"])
                else:
                    save_pinned(pinned + [item_id])
                    messages.success(request, words["saved"])
            elif action == "remove" and item_id in pinned:
                save_pinned([pk for pk in pinned if pk != item_id])
                messages.success(request, words["saved"])
            elif action == "up" and item_id in pinned and pinned.index(item_id) > 0:
                at = pinned.index(item_id)
                pinned[at - 1], pinned[at] = pinned[at], pinned[at - 1]
                save_pinned(pinned)
        query = request.POST.get("q", "")
        return redirect(f"/sales/pos/buttons/?lang={lang}" + (f"&q={quote(query)}" if query else ""))

    by_id = Item.objects.filter(pk__in=pinned, active=True).in_bulk()
    chosen = [by_id[pk] for pk in pinned if pk in by_id]
    query = request.GET.get("q", "").strip()[:80]
    others = Item.objects.filter(active=True).exclude(pk__in=pinned)
    if query:
        from django.db.models import Q

        others = others.filter(Q(item_name__icontains=query) | Q(item_code__icontains=query) | Q(barcode=query))
    else:
        others = others.filter(barcode="")
    others = list(others.order_by("item_name")[:SUGGESTIONS])
    return render(request, "sales/pos_buttons.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "section": "sales",
        "chosen": chosen, "others": others, "query": query, "full": len(chosen) >= MAX_PINNED,
        "cashboxes": cashboxes, "card_cashbox": card_cashbox(cashboxes),
    })
