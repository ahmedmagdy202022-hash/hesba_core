"""Restaurants R1b (HG-112): the set-up page «الكومبو», and what the order page needs to take a combo.

Seen and changed with ``master_data.manage_items``, the permission R1a's «الإضافات والاختيارات» uses (a combo makes and
prices a menu item). Combos are company-wide (``Combo``), so there is no entity scope here.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Item
from permissions.decorators import require_permission

from . import combos, modifiers, services
from .models import Combo
from .views import TABLES, _base, _lang

COMBO_WORDS = {
    "ar": {
        "title": "الكومبو", "back": "الطاولات",
        "intro": "الكومبو وجبة بسعر واحد: العميل بيختار صنف من كل خانة، زي ساندويتش وبطاطس ومشروب. في الفاتورة بيطلع كل صنف بسعر المنيو، والتوفير بيتوزّع عليهم خصم بنسبة أسعارهم، فتقرير الأطباق والضريبة والمرتجع بيفضلوا مظبوطين. سعر الكومبو لازم يكون أقل من أرخص اختياراته مع بعض أو قده.",
        "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "price": "سعر الكومبو", "sort": "الترتيب", "active": "شغال",
        "slots": "الخانات", "slot": "الخانة", "slot_en": "بالإنجليزي", "slots_hint": "كل خانة العميل بيختار منها صنف واحد. سيب الخانة أو السطر فاضي لو مش محتاجه؛ تفضية خانة أو اختيار متسجّل بتشيله، والطلبات القديمة مبتتغيّرش.",
        "choice": "الاختيار", "upcharge": "زيادة على السعر", "available": "متاح", "none_item": "— اختار صنف —",
        "save": "حفظ", "saved": "اتحفظ الكومبو.", "new": "كومبو جديد", "edit": "تعديل", "none": "لسه مفيش كومبو.", "off": "موقوف",
        "saving": "بيوفّر", "at_cheapest": "بأرخص اختيارات", "included": "ضمن السعر",
        # the order page
        "sheet_rule": "اختيار واحد · لازم", "pick": "اختار من «{slot}».", "add_to_order": "ضيف الكومبو للطلب", "count": "عدد الكومبو",
        "saves": "بيوفّر {saving}", "saved_tag": "توفير الكومبو", "tile": "كومبو", "close": "إغلاق",
    },
    "en": {
        "title": "Combos", "back": "Tables",
        "intro": "A combo is a meal at one price: the customer picks one item from each slot, such as a sandwich, fries and a drink. On the invoice each item shows at its menu price and the saving is spread over them as discounts in proportion to their prices, so the dishes report, VAT and returns stay right. The combo's price must be at most what its cheapest choices come to together.",
        "name_ar": "Name in Arabic", "name_en": "Name in English", "price": "Combo price", "sort": "Order", "active": "Active",
        "slots": "Slots", "slot": "Slot", "slot_en": "In English", "slots_hint": "The customer picks one item from each slot. Leave a slot or a row empty if you do not need it; emptying a saved slot or choice removes it, and past orders do not change.",
        "choice": "Choice", "upcharge": "Extra charge", "available": "Available", "none_item": "— choose an item —",
        "save": "Save", "saved": "Combo saved.", "new": "New combo", "edit": "Edit", "none": "No combos yet.", "off": "Off",
        "saving": "Saves", "at_cheapest": "at the cheapest choices", "included": "Included",
        "sheet_rule": "one choice · required", "pick": "Choose from “{slot}”.", "add_to_order": "Add the combo to the order", "count": "Combos",
        "saves": "Saves {saving}", "saved_tag": "Combo saving", "tile": "Combo", "close": "Close",
    },
}
SLOT_ROWS = 3
CHOICE_ROWS = 3


def _choice_items():
    """What a slot may offer: every active menu item but a combo's own and the delivery fee; one a choice uses that was
    switched off since still shows, so editing the combo keeps that choice (as R1a's add-on items)."""

    from django.db.models import Q

    from .models import ComboChoice

    used = ComboChoice.objects.values("item")
    return Item.objects.filter(Q(active=True) | Q(pk__in=used), combo__isnull=True).exclude(item_code=services.FEE_ITEM_CODE).order_by("item_name")


def _posted(post):
    slots, index = [], 0
    while any(key.startswith(f"slot-{index}-") for key in post):
        choices, row = [], 0
        while any(key.startswith(f"slot-{index}-choice-{row}-") for key in post):
            prefix = f"slot-{index}-choice-{row}-"
            choices.append({"pk": post.get(prefix + "pk", ""), "item": post.get(prefix + "item", ""), "upcharge": post.get(prefix + "upcharge", ""),
                            "active": post.get(prefix + "active", "on" if not post.get(prefix + "pk") else "") == "on"})
            row += 1
        slots.append({"pk": post.get(f"slot-{index}-pk", ""), "name_ar": post.get(f"slot-{index}-name_ar", ""), "name_en": post.get(f"slot-{index}-name_en", ""),
                      "choices": choices})
        index += 1
    return slots


def _rows(slots):
    """The form's slots with blank rows to fill: every slot shows its choices and ``CHOICE_ROWS`` more, then blank slots."""

    blank_choice = {"pk": "", "item": "", "upcharge": "", "active": True}
    rows = []
    for slot in slots:
        choices = [dict(choice, item=str(choice.get("item") or "")) for choice in slot.get("choices", [])]
        rows.append(dict(slot, choices=choices + [dict(blank_choice) for _ in range(max(1, CHOICE_ROWS - len(choices)))]))
    while len(rows) < max(SLOT_ROWS, len(slots) + 1):
        rows.append({"pk": "", "name_ar": "", "name_en": "", "choices": [dict(blank_choice) for _ in range(CHOICE_ROWS)]})
    return rows


@require_permission(TABLES)
def setup(request):
    lang = _lang(request)
    words = COMBO_WORDS[lang]
    raw = str(request.GET.get("edit") or request.POST.get("pk") or "")
    editing = get_object_or_404(Combo.objects.select_related("item"), pk=int(raw)) if raw.isdigit() else None
    error = ""
    if request.method == "POST":
        form = {key: request.POST.get(key, "") for key in ("name_ar", "name_en", "price", "sort")}
        form["active"] = request.POST.get("active") == "on"
        form["slots"] = _posted(request.POST)
        try:
            combos.save_combo(form, request.user, editing, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('restaurant:combos')}?lang={lang}")
        slots = form["slots"]
    elif editing:
        form = {"name_ar": editing.item.item_name, "name_en": editing.name_en, "price": f"{editing.price:.2f}", "sort": editing.sort, "active": editing.active}
        slots = [{"pk": slot.pk, "name_ar": slot.name_ar, "name_en": slot.name_en,
                  "choices": [{"pk": choice.pk, "item": choice.item_id, "upcharge": f"{choice.upcharge:.2f}", "active": choice.active}
                              for choice in slot.choices.order_by("sort", "pk")]} for slot in editing.slots.order_by("sort", "pk")]
    else:
        form = {"sort": 0, "active": True}
        slots = []
    listing = [{"combo": combo, "label": combo.label(lang), "saving": combos.cheapest_saving(combo),
                "slots": [{"label": slot.label(lang), "choices": [{"name": choice.item.item_name, "price": choice.item.default_sale_price, "upcharge": choice.upcharge,
                                                                   "on": choice.active and choice.item.active} for choice in slot.choices.all()]}
                          for slot in combo.slots.all()]} for combo in combos.all_combos()]
    return render(request, "restaurant/combos.html", _base(request, page_title=words["title"], cw=words, rows=listing, form=form, editing=editing, error=error,
                                                         slot_rows=_rows(slots), items=_choice_items()))


# --- the order page -----------------------------------------------------------------

def combo_sheets(items, lang):
    """The order page's combo sheets: [{combo, label, price, saving, slots: [{slot, label, message, choices: [{choice, name,
    upcharge, groups}]}]}] for the active combos on the menu. ``groups`` are the R1a questions the choice's item asks, as the
    dish sheet shows them."""

    from .views_modifiers import MODIFIER_WORDS

    on_menu = combos.menu_combos(items)
    if not on_menu:
        return []
    words = COMBO_WORDS[lang]
    offered = [choice.item for combo in on_menu.values() for slot in combo.slots.all() for choice in slot.choices.all()]
    asked = modifiers.menu_groups(offered)
    result = []
    for combo in on_menu.values():
        slots = []
        for slot in combo.slots.all():
            choices = []
            for choice in slot.choices.all():
                groups = []
                for group in asked.get(choice.item_id, []):
                    low, high = modifiers.effective_limits(group)
                    rule = modifiers.rule(group, lang)
                    groups.append({"group": group, "label": group.label(lang), "rule": rule, "required": low > 0, "min": low, "max": high, "radio": low >= 1 and high == 1,
                                   "message": MODIFIER_WORDS[lang]["pick_more"].format(group=group.label(lang), rule=rule),
                                   "options": [{"option": option, "label": option.label(lang), "price": option.item.default_sale_price if option.item_id else None}
                                               for option in group.live_options]})
                choices.append({"choice": choice, "name": choice.item.item_name, "upcharge": choice.upcharge, "groups": groups})
            slots.append({"slot": slot, "label": slot.label(lang), "message": words["pick"].format(slot=slot.label(lang)), "choices": choices})
        result.append({"combo": combo, "label": combo.label(lang), "price": combo.price, "saving": combos.cheapest_saving(combo), "slots": slots})
    return result


def posted_choices(post):
    """{slot pk: choice pk} from the sheet's radios (``slot-<pk>``)."""

    return {key[len("slot-"):]: post.get(key) for key in post if key.startswith("slot-") and key[len("slot-"):].isdigit()}


def posted_component_options(post, choices):
    """{slot pk: [option pks]}: the R1a answers sent for each slot's chosen item (``copt-<choice pk>`` and ``copt-<choice pk>-g<group pk>``)."""

    result = {}
    for slot, choice in choices.items():
        choice = str(choice or "").strip()
        if not choice.isdigit():
            continue
        values = list(post.getlist(f"copt-{choice}"))
        for key in post:
            if key.startswith(f"copt-{choice}-g"):
                values.extend(post.getlist(key))
        if values:
            result[slot] = values
    return result
