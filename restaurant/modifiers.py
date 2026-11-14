"""Restaurants R1a (HG-109): add-ons and required choices on a dish.

Rules, stated once:

* a dish asks the questions (``ModifierGroup``) linked to it, in their order. A group asks for at least ``min_choices``
  (0 = optional) and at most ``max_choices`` of its active options; a group with no active option asks nothing;
* an option with an item is a **priced add-on**: it becomes its own order line under its dish (``OrderLine.parent``), with
  the dish's quantity and the item's sale price at that moment. Its price, VAT rate, e-receipt code and stock are that
  item's, so the bill, the stock and the e-receipt treat it like any item. An option without an item is a **free
  choice**: it costs nothing, adds no line and is printed with its dish (``OrderLine.options``);
* adding a dish is refused, with nothing written, until every group's minimum is met and no maximum is exceeded, and so
  is any option the dish does not ask for (another group's, an inactive one, one whose item is inactive, one sent twice,
  anything that is not an option's number). A dish with no groups, or with optional groups and nothing chosen, is added
  in one tap and merges with an identical unsent line exactly as before;
* a line with add-ons or choices never merges with another line, and the family moves together: the dish's quantity is
  its add-ons' quantity, removing or voiding the dish removes or voids them, they go to the kitchen on the dish's ticket
  and reach the bill right after it. An add-on is never changed, removed, voided, sent or tapped on its own;
* groups are company-wide, like the menu (see ``ModifierGroup``).

The money path is unchanged: ``services.bill_rows`` turns each add-on into an ordinary invoice row and ``sales.pos.checkout``
posts it like any other.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch

from audit.models import AuditEventType, AuditLog
from master_data.models import Item

from .models import MenuItemModifierGroup, ModifierGroup, ModifierOption

MAX_CHOICES = 20
MAX_OPTIONS = 40
SEPARATOR = " · "

MESSAGES = {
    "ar": {
        "missing": "«{dish}» محتاج تختار من «{group}»: {rule}.", "too_many": "«{group}» يقبل {rule}.",
        "foreign": "الاختيار ده مش من اختيارات «{dish}».", "off": "الاختيار «{option}» مش متاح دلوقتي.", "twice": "الاختيار «{option}» متكرر.",
        "addon_alone": "الإضافة بتتغيّر مع طبقها؛ عدّل الطبق نفسه.",
        "name": "اكتب اسم المجموعة.", "name_taken": "فيه مجموعة بنفس الاسم.",
        "limits": "أقل عدد لازم يكون من 0 لحد أكبر عدد، وأكبر عدد من 1 لحد {max}.", "sort": "الترتيب لازم يكون رقم.",
        "option_name": "اكتب اسم الاختيار أو اختار له صنف.", "option_item": "صنف الإضافة لازم يكون صنف شغال.",
        "option_twice": "«{name}» متكرر في المجموعة.", "no_options": "ضيف اختيار واحد على الأقل.", "too_many_options": "المجموعة تقبل لحد {max} اختيار.",
        "min_options": "أقل عدد ({min}) أكبر من الاختيارات الشغالة ({count}).", "unknown_option": "الاختيار ده مش من المجموعة دي.",
        # the rule, as the sheet and the refusals say it
        "rule_one": "اختيار واحد", "rule_exact": "{n} اختيارات", "rule_range": "من {min} لحد {max}", "rule_upto": "لحد {max}",
        "rule_upto_one": "اختيار واحد بالكتير",
    },
    "en": {
        "missing": "“{dish}” needs a choice from “{group}”: {rule}.", "too_many": "“{group}” takes {rule}.",
        "foreign": "This is not one of the choices of “{dish}”.", "off": "The choice “{option}” is not available now.", "twice": "The choice “{option}” is there twice.",
        "addon_alone": "An add-on changes with its dish; change the dish itself.",
        "name": "Enter the group's name.", "name_taken": "A group with this name exists.",
        "limits": "The minimum must be 0 up to the maximum, and the maximum 1 to {max}.", "sort": "The order must be a number.",
        "option_name": "Enter the choice's name or pick its item.", "option_item": "An add-on's item must be an active item.",
        "option_twice": "“{name}” is in the group twice.", "no_options": "Add at least one choice.", "too_many_options": "A group takes up to {max} choices.",
        "min_options": "The minimum ({min}) is more than the active choices ({count}).", "unknown_option": "This choice is not in this group.",
        "rule_one": "one choice", "rule_exact": "{n} choices", "rule_range": "{min} to {max}", "rule_upto": "up to {max}",
        "rule_upto_one": "one choice at most",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def rule(group, lang="ar", count=None):
    """How many a group asks for, in words: «اختيار واحد», «لحد 3», «من 1 لحد 2»."""

    words = _words(lang)
    low, high = effective_limits(group, count)
    if low == high == 1:
        return words["rule_one"]
    if low == high:
        return words["rule_exact"].format(n=low)
    if low:
        return words["rule_range"].format(min=low, max=high)
    return words["rule_upto_one"] if high == 1 else words["rule_upto"].format(max=high)


def effective_limits(group, count=None):
    """(min, max) as the dish can meet them: never more than the group's active options."""

    if count is None:
        live = getattr(group, "live_options", None)
        count = len(live) if live is not None else sum(1 for option in group.options.all() if option.active)
    return min(group.min_choices, count), min(group.max_choices, count) or group.max_choices


def _audit(obj, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type=f"{obj._meta.label}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


# --- set-up -----------------------------------------------------------------

def _number(raw, default, words, key):
    raw = str(raw if raw not in (None, "") else default).strip()
    if not raw.isdigit():
        raise ValidationError(words[key].format(max=MAX_CHOICES) if key == "limits" else words[key])
    return int(raw)


def _on(value):
    return value not in (False, "", "0", "off", None)


def _item(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, Item):
        return raw
    raw = str(raw).strip()
    return Item.objects.filter(pk=int(raw)).first() if raw.isdigit() else False


@transaction.atomic
def save_group(data, user, group=None, lang="ar"):
    """Create or change a group with its options.

    ``data``: name_ar, name_en, min_choices, max_choices, sort, active, and ``options``: [{pk, name_ar, name_en, item, active}]
    in the order shown. A row with a ``pk`` changes that option (it must be this group's); an emptied row with a ``pk``
    turns it off (an option is never deleted, so what was printed before keeps its meaning); an empty new row is skipped.
    A priced option left without a name takes its item's name."""

    words = _words(lang)
    name_ar = (data.get("name_ar") or "").strip()[:80]
    if not name_ar:
        raise ValidationError(words["name"])
    if ModifierGroup.objects.filter(name_ar__iexact=name_ar).exclude(pk=getattr(group, "pk", None)).exists():
        raise ValidationError(words["name_taken"])
    low = _number(data.get("min_choices"), 0, words, "limits")
    high = _number(data.get("max_choices"), 1, words, "limits")
    if not (1 <= high <= MAX_CHOICES and 0 <= low <= high):
        raise ValidationError(words["limits"].format(max=MAX_CHOICES))
    sort = _number(data.get("sort"), 0, words, "sort")
    active = _on(data.get("active", True))
    existing = {option.pk: option for option in group.options.all()} if group is not None else {}
    rows, seen_names, seen_items = [], set(), set()
    for position, raw in enumerate(data.get("options") or []):
        pk = str(raw.get("pk") or "").strip()
        option = None
        if pk:
            option = existing.get(int(pk)) if pk.isdigit() else None
            if option is None:
                raise ValidationError(words["unknown_option"])
        item = _item(raw.get("item"))
        if item is False or (item is not None and not item.active and (option is None or option.item_id != item.pk)):
            raise ValidationError(words["option_item"])
        name = (raw.get("name_ar") or "").strip()[:80] or (item.item_name[:80] if item else "")
        if not name:
            if option is not None:  # an emptied row turns its option off
                rows.append((option, {"active": False}))
                continue
            continue
        key = name.casefold()
        if key in seen_names or (item is not None and item.pk in seen_items):
            raise ValidationError(words["option_twice"].format(name=name))
        seen_names.add(key)
        if item is not None:
            seen_items.add(item.pk)
        rows.append((option, {"name_ar": name, "name_en": (raw.get("name_en") or "").strip()[:80], "item": item, "sort": position + 1,
                              "active": _on(raw.get("active", True))}))
    live = sum(1 for _option, values in rows if values.get("active"))
    if not live and active:
        raise ValidationError(words["no_options"])
    if len(rows) > MAX_OPTIONS:
        raise ValidationError(words["too_many_options"].format(max=MAX_OPTIONS))
    if active and low > live:
        raise ValidationError(words["min_options"].format(min=low, count=live))
    values = {"name_ar": name_ar, "name_en": (data.get("name_en") or "").strip()[:80], "min_choices": low, "max_choices": high, "sort": sort, "active": active}
    created = group is None
    before = {} if created else {**{key: getattr(group, key) for key in values}, "options": _options_snapshot(group)}
    group = group or ModifierGroup()
    for key, value in values.items():
        setattr(group, key, value)
    group.save()
    for option, changes in rows:
        option = option or ModifierOption(group=group)
        for key, value in changes.items():
            setattr(option, key, value)
        option.save()
    _audit(group, user, "create_modifier_group" if created else "change_modifier_group", {**values, "options": _options_snapshot(group)}, before,
           AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return group


def _options_snapshot(group):
    return [{"pk": option.pk, "name": option.name_ar, "item": option.item.item_code if option.item_id else "", "active": option.active}
            for option in group.options.select_related("item").order_by("sort", "pk")]


@transaction.atomic
def set_dish_groups(dish, groups, user, lang="ar"):
    """The questions ``dish`` asks, in the order given (a group listed twice counts once)."""

    wanted = list(dict.fromkeys(group.pk for group in groups))
    before = list(MenuItemModifierGroup.objects.filter(item=dish).order_by("sort", "pk").values_list("group_id", flat=True))
    MenuItemModifierGroup.objects.filter(item=dish).delete()
    MenuItemModifierGroup.objects.bulk_create([MenuItemModifierGroup(item=dish, group_id=pk, sort=n + 1) for n, pk in enumerate(wanted)])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="restaurant", action="set_dish_modifier_groups", object_type="master_data.Item",
                            object_id=str(dish.pk), before_data={"groups": before}, after_data={"groups": wanted})
    return wanted


def move_dish(old, new):
    """RESTO-004: a dish copied as a stock item takes its old item's place on the menu, so it keeps the questions it asked,
    and an add-on option that pointed at the old item points at the new one (``kitchen._copy_as_stock_item``)."""

    MenuItemModifierGroup.objects.filter(item=old).exclude(group__in=MenuItemModifierGroup.objects.filter(item=new).values("group")).update(item=new)
    MenuItemModifierGroup.objects.filter(item=old).delete()
    ModifierOption.objects.filter(item=old).update(item=new)


# --- reading ----------------------------------------------------------------

def _live_options():
    return Prefetch("options", queryset=ModifierOption.objects.filter(active=True).select_related("item").order_by("sort", "pk"), to_attr="live_options")


def groups_of(dish):
    """The active groups ``dish`` asks, in order, each with ``live_options`` (its active options); a group with none is left out."""

    return menu_groups([dish]).get(dish.pk, [])


def menu_groups(items):
    """{item pk: [groups]} for the given items that ask anything (each group with ``live_options``)."""

    links = (MenuItemModifierGroup.objects.filter(item__in=items, group__active=True).select_related("group")
             .prefetch_related(Prefetch("group__options", queryset=ModifierOption.objects.filter(active=True).select_related("item").order_by("sort", "pk"),
                                        to_attr="live_options"))
             .order_by("item", "sort", "pk"))
    result = {}
    for link in links:
        if link.group.live_options:
            result.setdefault(link.item_id, []).append(link.group)
    return result


def all_groups():
    return ModifierGroup.objects.prefetch_related(Prefetch("options", queryset=ModifierOption.objects.select_related("item").order_by("sort", "pk")))


# --- choosing ---------------------------------------------------------------

def choose(dish, raw_options, lang="ar"):
    """The options chosen for one ``dish``, checked: [(group, [options])] in the dish's order. Refuses anything else.

    ``raw_options``: option pks as a form sends them (or ``ModifierOption`` objects). Nothing is written here."""

    words = _words(lang)
    groups = groups_of(dish)
    offered = {option.pk: (group, option) for group in groups for option in group.live_options}
    chosen, seen = {}, set()
    for raw in raw_options or []:
        raw = raw.pk if isinstance(raw, ModifierOption) else str(raw).strip()
        if raw == "":
            continue
        if not str(raw).isdigit():
            raise ValidationError(words["foreign"].format(dish=dish.item_name))
        pk = int(raw)
        if pk not in offered:
            known = ModifierOption.objects.filter(pk=pk, group__dish_links__item=dish, group__active=True).select_related("group").first()
            if known is not None:  # the dish asks this group, but the option is off
                raise ValidationError(words["off"].format(option=known.label(lang)))
            raise ValidationError(words["foreign"].format(dish=dish.item_name))
        group, option = offered[pk]
        if pk in seen:
            raise ValidationError(words["twice"].format(option=option.label(lang)))
        seen.add(pk)
        if option.item_id and not option.item.active:
            raise ValidationError(words["off"].format(option=option.label(lang)))
        chosen.setdefault(group.pk, []).append(option)
    result = []
    for group in groups:
        picked = chosen.get(group.pk, [])
        low, high = effective_limits(group)
        if len(picked) < low:
            raise ValidationError(words["missing"].format(dish=dish.item_name, group=group.label(lang), rule=rule(group, lang)))
        if len(picked) > high:
            raise ValidationError(words["too_many"].format(group=group.label(lang), rule=rule(group, lang)))
        if picked:
            result.append((group, picked))
    return result


def free_text(chosen, lang="ar"):
    """The free choices as printed with the dish: «ميديم · من غير بصل»."""

    return SEPARATOR.join(option.label(lang) for _group, options in chosen for option in options if not option.item_id)[:255]


def priced(chosen):
    return [option for _group, options in chosen for option in options if option.item_id]


# --- families ---------------------------------------------------------------

def families(lines):
    """Lines in family order: each dish (in its order), then its add-ons (in theirs). An add-on whose dish is not among
    ``lines`` keeps its own place (it never happens through the services)."""

    lines = list(lines)
    present = {line.pk for line in lines}
    children = {}
    for line in lines:
        if line.parent_id in present:
            children.setdefault(line.parent_id, []).append(line)
    ordered = []
    for line in lines:
        if line.parent_id in present:
            continue
        ordered.append(line)
        ordered.extend(children.get(line.pk, []))
    return ordered


def grouped(lines):
    """[(dish, [add-ons])] for display (the ticket, the order page)."""

    lines = list(lines)
    present = {line.pk for line in lines}
    children = {}
    for line in lines:
        if line.parent_id in present:
            children.setdefault(line.parent_id, []).append(line)
    return [(line, children.get(line.pk, [])) for line in lines if line.parent_id not in present]


def addon_label(line):
    """An add-on as the ticket prints it: «+ جبنة زيادة»."""

    return f"+ {line.options or line.item.item_name}"


def bill_description(line, dish):
    """An add-on's invoice row: «+ جبنة زيادة (برجر)»."""

    return f"{addon_label(line)} ({dish.item.item_name})"[:255]
