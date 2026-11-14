"""Restaurants R1a (HG-109): the set-up page «الإضافات والاختيارات», and the words the dish page and the order sheet use.

Seen and changed with ``master_data.manage_items``, the permission that edits a dish's page (its ingredients, its kitchen
switch). Groups are company-wide (``ModifierGroup``), so there is no entity scope here.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Item
from permissions.decorators import require_permission

from . import modifiers, services
from .models import MenuItemModifierGroup, ModifierGroup
from .views import TABLES, _base, _lang

MODIFIER_WORDS = {
    "ar": {
        "title": "الإضافات والاختيارات", "back": "الطاولات",
        "intro": "المجموعة سؤال بيسأله الطبق وقت الطلب، زي «درجة الاستواء» (اختيار واحد لازم) أو «إضافات» (اختياري). الاختيار اللي له صنف إضافة بسعر الصنف، وبيتسجّل سطر تحت الطبق وبيطلع في الفاتورة؛ والاختيار من غير صنف مجاني وبيتطبع مع الطبق في تذكرة المطبخ. اربط المجموعات بالأطباق من صفحة كل طبق.",
        "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "min": "أقل عدد", "min_hint": "0 يعني اختياري", "max": "أكبر عدد", "sort": "الترتيب",
        "active": "شغالة", "options": "الاختيارات", "option": "الاختيار", "option_en": "بالإنجليزي", "item": "صنف الإضافة", "free": "مجاني، من غير صنف",
        "available": "متاح", "save": "حفظ", "saved": "اتحفظت المجموعة.", "new": "مجموعة جديدة", "edit": "تعديل", "none": "لسه مفيش مجموعات.",
        "dishes": "بيسألها", "no_dishes": "لسه مش مربوطة بأي طبق.", "off": "موقوفة", "free_tag": "مجاني", "options_hint": "سيب السطر فاضي لو مش محتاجه. الاختيار المتسجّل قبل كده مبيتمسحش؛ فضّي اسمه أو شيل «متاح» يتوقف.",
        # the dish page's box
        "box_title": "الإضافات والاختيارات", "box_intro": "اختار الأسئلة اللي الطبق ده يسألها وقت الطلب.", "box_save": "حفظ اختيارات الطبق",
        "box_saved": "اتحفظت اختيارات الطبق.", "box_none": "لسه مفيش مجموعات.", "box_setup": "جهّز المجموعات",
        # the order page's sheet
        "required": "لازم", "optional": "اختياري", "add_to_order": "ضيف للطلب", "close": "إغلاق", "has_choices": "فيه اختيارات",
        "pick_more": "اختار من «{group}»: {rule}.",
        "sizes": "الحجم", "choose_size": "اختار الحجم", "from": "من",
    },
    "en": {
        "title": "Add-ons and choices", "back": "Tables",
        "intro": "A group is a question a dish asks when it is ordered, such as “Doneness” (one choice, required) or “Extras” (optional). A choice with an item is a priced add-on: it is recorded as a line under the dish and billed at the item's price. A choice without an item is free and prints with the dish on the kitchen ticket. Attach groups to dishes from each dish's page.",
        "name_ar": "Name in Arabic", "name_en": "Name in English", "min": "Minimum", "min_hint": "0 means optional", "max": "Maximum", "sort": "Order",
        "active": "Active", "options": "Choices", "option": "Choice", "option_en": "In English", "item": "Add-on item", "free": "Free, no item",
        "available": "Available", "save": "Save", "saved": "Group saved.", "new": "New group", "edit": "Edit", "none": "No groups yet.",
        "dishes": "Asked by", "no_dishes": "Not attached to any dish yet.", "off": "Off", "free_tag": "Free", "options_hint": "Leave a row empty if you do not need it. A choice used before is never deleted; empty its name or untick “Available” to turn it off.",
        "box_title": "Add-ons and choices", "box_intro": "Choose the questions this dish asks when it is ordered.", "box_save": "Save the dish's choices",
        "box_saved": "The dish's choices are saved.", "box_none": "No groups yet.", "box_setup": "Set up the groups",
        "required": "Required", "optional": "Optional", "add_to_order": "Add to the order", "close": "Close", "has_choices": "Has choices",
        "pick_more": "Choose from “{group}”: {rule}.",
        "sizes": "Size", "choose_size": "Choose the size", "from": "from",
    },
}
BLANK_ROWS = 3


def _menu_items():
    """The items a priced add-on may be: every active item but the delivery fee (the menu's own list)."""

    from django.db.models import Q

    from .models import ModifierOption

    used = ModifierOption.objects.filter(item__isnull=False).values("item")  # one made inactive since still shows, so the page saves
    return Item.objects.filter(Q(active=True) | Q(pk__in=used)).exclude(item_code=services.FEE_ITEM_CODE).order_by("item_name")


def _posted_options(post):
    rows, index = [], 0
    while f"opt-{index}-name_ar" in post or f"opt-{index}-item" in post or f"opt-{index}-pk" in post:
        rows.append({"pk": post.get(f"opt-{index}-pk", ""), "name_ar": post.get(f"opt-{index}-name_ar", ""), "name_en": post.get(f"opt-{index}-name_en", ""),
                     "item": post.get(f"opt-{index}-item", ""), "active": post.get(f"opt-{index}-active") == "on"})
        index += 1
    return rows


@require_permission(TABLES)
def setup(request):
    lang = _lang(request)
    words = MODIFIER_WORDS[lang]
    raw = str(request.GET.get("edit") or request.POST.get("pk") or "")
    editing = get_object_or_404(ModifierGroup, pk=int(raw)) if raw.isdigit() else None
    error = ""
    if request.method == "POST":
        form = {key: request.POST.get(key, "") for key in ("name_ar", "name_en", "min_choices", "max_choices", "sort")}
        form["active"] = request.POST.get("active") == "on"
        form["options"] = _posted_options(request.POST)
        try:
            modifiers.save_group(form, request.user, editing, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('restaurant:modifiers')}?lang={lang}")
        option_rows = form["options"]
    elif editing:
        form = {"name_ar": editing.name_ar, "name_en": editing.name_en, "min_choices": editing.min_choices, "max_choices": editing.max_choices,
                "sort": editing.sort, "active": editing.active}
        option_rows = [{"pk": option.pk, "name_ar": option.name_ar, "name_en": option.name_en, "item": option.item_id or "", "active": option.active}
                       for option in editing.options.order_by("sort", "pk")]
    else:
        form = {"min_choices": 0, "max_choices": 1, "sort": 0, "active": True}
        option_rows = []
    option_rows = option_rows + [{"pk": "", "name_ar": "", "name_en": "", "item": "", "active": True}] * BLANK_ROWS
    for row in option_rows:
        row["item"] = str(row.get("item") or "")
    dishes = {}
    for link in MenuItemModifierGroup.objects.select_related("item").order_by("item__item_name"):
        dishes.setdefault(link.group_id, []).append(link.item.item_name)
    rows = [{"group": group, "label": group.label(lang), "rule": modifiers.rule(group, lang),
             "options": [{"label": option.label(lang), "price": option.item.default_sale_price if option.item_id else None, "active": option.active}
                         for option in group.options.all()],
             "dishes": dishes.get(group.pk, [])} for group in modifiers.all_groups()]
    return render(request, "restaurant/modifiers.html", _base(request, page_title=words["title"], mw=words, rows=rows, form=form, editing=editing, error=error,
                                                            option_rows=option_rows, items=_menu_items()))


def dish_groups_context(dish, lang):
    """The dish page's box: every group, ticked when the dish asks it, in the dish's order first."""

    chosen = list(MenuItemModifierGroup.objects.filter(item=dish).order_by("sort", "pk").values_list("group_id", flat=True))
    groups = sorted(modifiers.all_groups(), key=lambda group: (group.pk not in chosen, chosen.index(group.pk) if group.pk in chosen else 0, group.sort, group.pk))
    return {"mw": MODIFIER_WORDS[lang], "dish_groups": [{"group": group, "label": group.label(lang), "rule": modifiers.rule(group, lang), "on": group.pk in chosen}
                                                        for group in groups]}


def save_dish_groups(request, dish, lang):
    """POST «حفظ اختيارات الطبق» from the dish page (the caller has checked ``manage_items``)."""

    wanted = [int(raw) for raw in request.POST.getlist("group") if str(raw).isdigit()]
    by_pk = ModifierGroup.objects.in_bulk(wanted)
    modifiers.set_dish_groups(dish, [by_pk[pk] for pk in wanted if pk in by_pk], request.user, lang)
    messages.success(request, MODIFIER_WORDS[lang]["box_saved"])
    return redirect(f"{reverse('restaurant:dish', args=[dish.pk])}?lang={lang}")


def sheets(items, lang):
    """The order page's choice sheets: [{item, groups: [{group, label, rule, required, radio, options}]}] for the dishes that ask."""

    result = []
    asked = modifiers.menu_groups(items)
    for item in items:
        if item.pk not in asked:
            continue
        groups = []
        for group in asked[item.pk]:
            low, high = modifiers.effective_limits(group)
            rule = modifiers.rule(group, lang)
            groups.append({"group": group, "label": group.label(lang), "rule": rule, "required": low > 0, "min": low, "max": high,
                           "message": MODIFIER_WORDS[lang]["pick_more"].format(group=group.label(lang), rule=rule),
                           "radio": low >= 1 and high == 1,
                           "options": [{"option": option, "label": option.label(lang), "price": option.item.default_sale_price if option.item_id else None}
                                       for option in group.live_options]})
        result.append({"item": item, "groups": groups})
    return result


def posted_options(post):
    """The option pks the sheet sent: its checkboxes (``option``) and each single-choice group's radios (``option-g<pk>``)."""

    values = list(post.getlist("option"))
    for key in post:
        if key.startswith("option-g"):
            values.extend(post.getlist(key))
    return values


def menu_entries(items, sheet_items, lang):
    """R1a step 5: the menu with each size family (``variants.VariantGroup``) under one tile that asks the size.

    [{"item": item}] for a plain tile, [{"sizes": {...}}] for a family, at the place of its first size. A family needs two
    active sizes on the menu; one whose size asks questions of its own keeps a tile per size, as before. No schema."""

    from variants.models import Variant

    families = {}
    for variant in Variant.objects.filter(item__in=items, group__active=True).select_related("group", "item").order_by("group_id", "pk"):
        families.setdefault(variant.group_id, []).append(variant)
    first, grouped_ids = {}, set()
    for variants in families.values():
        if len(variants) < 2 or any(variant.item_id in sheet_items for variant in variants):
            continue
        group = variants[0].group
        sizes = [{"item": variant.item, "label": " · ".join(part for part in (variant.size, variant.color) if part) or variant.item.item_name,
                  "price": variant.item.default_sale_price} for variant in variants]
        prices = [size["price"] for size in sizes]
        first[variants[0].item_id] = {"group": group, "name": group.name, "sizes": sizes, "low": min(prices), "same_price": len(set(prices)) == 1,
                                      "category_id": variants[0].item.category_id or "",
                                      "search": " ".join(f"{variant.item.item_name} {variant.item.item_code}" for variant in variants).lower()}
        grouped_ids.update(variant.item_id for variant in variants)
    entries = []
    for item in items:
        if item.pk in first:
            entries.append({"sizes": first[item.pk]})
        elif item.pk not in grouped_ids:
            entries.append({"item": item})
    return entries, [entry["sizes"] for entry in entries if "sizes" in entry]
