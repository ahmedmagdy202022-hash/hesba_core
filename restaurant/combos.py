"""Restaurants R1b (HG-112): combos (meal deals).

Rules, stated once:

* a combo («وجبة برجر») is sold through its own menu item: a non-stock service item whose sale price is the combo's price.
  That item never goes on an order or a bill. Taking a combo asks one choice from each slot (``ComboSlot``); a choice
  (``ComboChoice``) is an item, with what choosing it adds to the price («بطاطس كبيرة» +10.00);
* the chosen items become the order's lines, each at its own menu price, and the saving is spread over them as line
  discounts: ``saving = sum of their menu prices − (combo price + upcharges)``, split in proportion to the menu prices,
  rounded to the piastre with the largest remainders taking what is left, so the discounts add up exactly to the saving
  (``spread``). VAT is then the sales engine's, per line, on what each line charges;
* each combo taken is its own **bundle** of quantity-1 lines (``OrderLine.bundle``, numbered from 1 per order). Two combos
  are two bundles, so every figure of two is exactly twice one (one line of quantity 2 would round VAT on the doubled line
  and could differ by a piastre: trap 1). The bundle's first line (its **head**) stands for the whole combo: removing,
  voiding or splitting it does the same to every component, and nothing else changes a component on its own (but its
  kitchen note). A bundle never merges with another line;
* a component may carry its own R1a add-ons and free choices (``restaurant.modifiers``): an add-on is billed at its own
  price and never discounted;
* set-up refuses a combo whose price is above what its cheapest choices come to (a saving is never negative), and a
  choice that is itself a combo. A price changed later on the items screen is checked again when the combo is taken.

Combos are company-wide, like the menu (see ``ModifierGroup``). The money path is unchanged: ``services.bill_rows`` sends
each component's discount as its row's ``line_discount_amount``, which the sales engine already supports.
"""

from decimal import ROUND_DOWN, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from master_data.models import Item

from .models import Combo, ComboChoice, ComboSlot

PIASTRE = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_SLOTS = 5
MAX_CHOICES = 8
CODE_PREFIX = "COMBO-"

MESSAGES = {
    "ar": {
        "name": "اكتب اسم الكومبو.", "name_taken": "فيه كومبو أو صنف بنفس الاسم.", "price": "السعر لازم يكون رقم أكبر من صفر.",
        "no_slots": "ضيف خانة واحدة على الأقل، زي «الساندويتش» أو «المشروب».", "too_many_slots": "الكومبو يقبل لحد {max} خانات.",
        "slot_name": "اكتب اسم الخانة.", "slot_twice": "الخانة «{name}» متكررة.", "no_choices": "الخانة «{slot}» محتاجة اختيار واحد على الأقل.",
        "too_many_choices": "الخانة تقبل لحد {max} اختيارات.", "choice_item": "اختيار الخانة لازم يكون صنف شغال من المنيو.",
        "choice_combo": "«{item}» كومبو؛ الكومبو مينفعش يبقى جوه كومبو تاني.", "choice_twice": "«{item}» متكرر في الخانة «{slot}».",
        "upcharge": "الزيادة لازم تكون رقم صفر أو أكبر.", "unknown": "الخانة أو الاختيار ده مش من الكومبو ده.",
        "above": "سعر الكومبو ({price}) أكبر من أرخص اختياراته مع بعض ({cheapest}). خليه {cheapest} أو أقل، عشان الكومبو يوفّر دايمًا.",
        # taking a combo
        "off": "الكومبو أو الاختيار ده مش متاح دلوقتي.", "foreign": "الاختيار ده مش من اختيارات «{slot}».",
        "missing": "اختار من «{slot}».", "dearer": "الكومبو بقى أغلى من أصنافه بأسعار المنيو؛ راجع سعره من شاشة الكومبو.",
        "combo_item": "«{item}» كومبو: افتحه من المنيو واختار من كل خانة.",
        "component": "الكومبو بيتغيّر كله مع بعض؛ عدّل الكومبو نفسه من أول سطر فيه.",
        "one_each": "كل كومبو بيتسجّل لوحده؛ لو عايز كومبو تاني، ضيفه من المنيو.",
        "whole": "عدد الكومبو لازم يكون رقم صحيح من 1 لحد {max}.",
        "addon_combo": "«{item}» كومبو؛ مينفعش يبقى إضافة.",
    },
    "en": {
        "name": "Enter the combo's name.", "name_taken": "A combo or an item with this name exists.", "price": "The price must be a number above zero.",
        "no_slots": "Add at least one slot, such as “Sandwich” or “Drink”.", "too_many_slots": "A combo takes up to {max} slots.",
        "slot_name": "Enter the slot's name.", "slot_twice": "The slot “{name}” is there twice.", "no_choices": "The slot “{slot}” needs at least one choice.",
        "too_many_choices": "A slot takes up to {max} choices.", "choice_item": "A slot's choice must be an active menu item.",
        "choice_combo": "“{item}” is a combo; a combo cannot sit inside another combo.", "choice_twice": "“{item}” is in the slot “{slot}” twice.",
        "upcharge": "The extra charge must be zero or more.", "unknown": "This slot or choice is not part of this combo.",
        "above": "The combo's price ({price}) is above its cheapest choices together ({cheapest}). Make it {cheapest} or less, so the combo always saves.",
        "off": "This combo or choice is not available now.", "foreign": "This is not one of the choices of “{slot}”.",
        "missing": "Choose from “{slot}”.", "dearer": "The combo now costs more than its items at menu prices; check its price on the combos screen.",
        "combo_item": "“{item}” is a combo: open it from the menu and choose from each slot.",
        "component": "A combo changes as a whole; change the combo itself from its first line.",
        "one_each": "Each combo is recorded on its own; for another combo, add it from the menu.",
        "whole": "The number of combos must be a whole number from 1 to {max}.",
        "addon_combo": "“{item}” is a combo; it cannot be an add-on.",
    },
}


def words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# --- the money ----------------------------------------------------------------

def spread(prices, saving):
    """``saving`` split over ``prices`` in proportion, to the piastre, adding up to ``saving`` exactly.

    Each share is first rounded down; the piastres left go one each to the largest remainders (the earlier line first on a
    tie). No share passes its price while the saving does not pass their sum (set-up and taking a combo make sure of it)."""

    prices = [money_round(Decimal(price)) for price in prices]
    saving = money_round(Decimal(saving))
    total = sum(prices, ZERO)
    if saving <= 0 or total <= 0:
        return [ZERO for _ in prices]
    exact = [saving * price / total for price in prices]
    shares = [value.quantize(PIASTRE, rounding=ROUND_DOWN) for value in exact]
    left = int(((saving - sum(shares, ZERO)) / PIASTRE).to_integral_value())
    order = sorted(range(len(prices)), key=lambda index: (-(exact[index] - shares[index]), index))
    for index in order[:left]:
        shares[index] += PIASTRE
    return shares


def _cheapest(slot_choices):
    """What the cheapest pick of each slot comes to, the way the saving counts it: its menu price less its upcharge."""

    return sum((min(money_round(choice.item.default_sale_price) - money_round(choice.upcharge) for choice in choices) for choices in slot_choices), ZERO)


def cheapest_saving(combo):
    """What the combo saves at its cheapest choices (the set-up page shows it)."""

    slots = [[choice for choice in slot.choices.all() if choice.active and choice.item.active] for slot in combo.slots.all()]
    if not slots or not all(slots):
        return ZERO
    return money_round(_cheapest(slots) - combo.price)


# --- set-up ---------------------------------------------------------------------

def _money(raw, words_, key, allow_zero):
    raw = str(raw if raw is not None else "").strip().replace(",", "")
    if raw == "" and allow_zero:
        return ZERO
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValidationError(words_[key])
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero) or value >= Decimal("1000000"):
        raise ValidationError(words_[key])
    return money_round(value)


def _on(value):
    return value not in (False, "", "0", "off", None)


def _item(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, Item):
        return raw
    raw = str(raw).strip()
    return Item.objects.filter(pk=int(raw)).first() if raw.isdigit() else False


def combo_item_ids():
    return set(Combo.objects.values_list("item_id", flat=True))


def _next_code():
    number = Combo.objects.count() + 1
    while Item.objects.filter(item_code=f"{CODE_PREFIX}{number:03d}").exists():
        number += 1
    return f"{CODE_PREFIX}{number:03d}"


def _snapshot(combo):
    return {"name": combo.item.item_name, "name_en": combo.name_en, "price": str(combo.price), "active": combo.active, "sort": combo.sort,
            "slots": [{"name": slot.name_ar, "choices": [[choice.item.item_code, str(choice.upcharge)] for choice in slot.choices.select_related("item").order_by("sort", "pk")
                                                         if choice.active]}
                      for slot in combo.slots.order_by("sort", "pk")]}


@transaction.atomic
def save_combo(data, user, combo=None, lang="ar"):
    """Create or change a combo with its slots and choices.

    ``data``: name_ar, name_en, price, sort, active, and ``slots``: [{pk, name_ar, name_en, choices: [{pk, item, upcharge,
    active}]}] in the order shown. A row with a ``pk`` changes that slot or choice (it must be this combo's); an emptied row
    with a ``pk`` removes it (an order line keeps its own item, price and discount, so nothing printed before changes); an
    empty new row is skipped. The combo's menu item is made on the first save and takes its name and price on every save."""

    words_ = words(lang)
    name_ar = (data.get("name_ar") or "").strip()[:80]
    if not name_ar:
        raise ValidationError(words_["name"])
    own_item = combo.item_id if combo is not None else None
    if Item.objects.filter(item_name__iexact=name_ar, combo__isnull=False).exclude(pk=own_item).exists():
        raise ValidationError(words_["name_taken"])
    price = _money(data.get("price"), words_, "price", allow_zero=False)
    sort = int(str(data.get("sort") or "0").strip()) if str(data.get("sort") or "0").strip().isdigit() else 0
    active = _on(data.get("active", True))
    combos = combo_item_ids() | ({own_item} if own_item else set())
    slots_now = {slot.pk: slot for slot in combo.slots.all()} if combo is not None else {}
    choices_now = {choice.pk: choice for slot in slots_now.values() for choice in slot.choices.all()}
    plan, seen_slots = [], set()
    for raw_slot in data.get("slots") or []:
        pk = str(raw_slot.get("pk") or "").strip()
        slot = None
        if pk:
            slot = slots_now.get(int(pk)) if pk.isdigit() else None
            if slot is None:
                raise ValidationError(words_["unknown"])
        name = (raw_slot.get("name_ar") or "").strip()[:80]
        rows, seen_items = [], set()
        for raw in raw_slot.get("choices") or []:
            choice_pk = str(raw.get("pk") or "").strip()
            choice = None
            if choice_pk:
                choice = choices_now.get(int(choice_pk)) if choice_pk.isdigit() else None
                if choice is None or (slot is not None and choice.slot_id != slot.pk) or slot is None:
                    raise ValidationError(words_["unknown"])
            item = _item(raw.get("item"))
            if item is None:
                if choice is not None:
                    rows.append((choice, None))  # an emptied row removes its choice
                continue
            if item is False or (not item.active and (choice is None or choice.item_id != item.pk)):  # a saved choice keeps an item switched off since
                raise ValidationError(words_["choice_item"])
            if item.pk in combos:
                raise ValidationError(words_["choice_combo"].format(item=item.item_name))
            if item.pk in seen_items:
                raise ValidationError(words_["choice_twice"].format(item=item.item_name, slot=name))
            seen_items.add(item.pk)
            rows.append((choice, {"item": item, "upcharge": _money(raw.get("upcharge"), words_, "upcharge", allow_zero=True), "active": _on(raw.get("active", True))}))
        if not name:
            if any(values for _choice, values in rows):
                raise ValidationError(words_["slot_name"])
            if slot is not None:
                plan.append((slot, None, rows))  # an emptied slot is removed with its choices
            continue
        if name.casefold() in seen_slots:
            raise ValidationError(words_["slot_twice"].format(name=name))
        seen_slots.add(name.casefold())
        live = [values for _choice, values in rows if values and values["active"] and values["item"].active]
        if not live:
            raise ValidationError(words_["no_choices"].format(slot=name))
        if len([values for _choice, values in rows if values]) > MAX_CHOICES:
            raise ValidationError(words_["too_many_choices"].format(max=MAX_CHOICES))
        plan.append((slot, {"name_ar": name, "name_en": (raw_slot.get("name_en") or "").strip()[:80]}, rows))
    kept = [entry for entry in plan if entry[1] is not None]
    if not kept:
        raise ValidationError(words_["no_slots"])
    if len(kept) > MAX_SLOTS:
        raise ValidationError(words_["too_many_slots"].format(max=MAX_SLOTS))

    class _Pick:  # the set-up's rows, priced as ``_cheapest`` reads a choice
        def __init__(self, values):
            self.item, self.upcharge = values["item"], values["upcharge"]

    cheapest = _cheapest([[_Pick(values) for _choice, values in rows if values and values["active"] and values["item"].active] for _slot, _values, rows in kept])
    if price > cheapest:
        raise ValidationError(words_["above"].format(price=f"{price:,.2f}", cheapest=f"{cheapest:,.2f}"))
    created = combo is None
    before = {} if created else _snapshot(combo)
    if created:
        item = Item.objects.create(item_code=_next_code(), item_name=name_ar, default_sale_price=price, is_stock_tracked=False, active=active)
        combo = Combo.objects.create(item=item, name_en=(data.get("name_en") or "").strip()[:80], sort=sort, active=active)
    else:
        item = combo.item
        item.item_name, item.default_sale_price, item.active = name_ar, price, active
        item.save(update_fields=["item_name", "default_sale_price", "active", "updated_at"])
        combo.name_en, combo.sort, combo.active = (data.get("name_en") or "").strip()[:80], sort, active
        combo.save(update_fields=["name_en", "sort", "active"])
    position = 0
    for slot, values, rows in plan:
        if values is None:
            slot.delete()
            continue
        position += 1
        slot = slot or ComboSlot(combo=combo)
        slot.name_ar, slot.name_en, slot.sort = values["name_ar"], values["name_en"], position
        slot.save()
        removed = [choice.pk for choice, values_ in rows if choice is not None and values_ is None]
        # A choice whose item changes is replaced by a new row: two choices swapping their items would otherwise meet the
        # one-item-per-slot rule halfway through the saves (nothing points at a choice, so a new row loses nothing).
        replaced = {choice.pk for choice, values_ in rows if choice is not None and values_ is not None and choice.item_id != values_["item"].pk}
        ComboChoice.objects.filter(pk__in=[*removed, *replaced]).delete()
        for index, (choice, values_) in enumerate((row for row in rows if row[1] is not None), start=1):
            choice = choice if choice is not None and choice.pk not in replaced else ComboChoice(slot=slot)
            choice.item, choice.upcharge, choice.active, choice.sort = values_["item"], values_["upcharge"], values_["active"], index
            choice.save()
    combo = Combo.objects.select_related("item").get(pk=combo.pk)
    AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="restaurant",
                            action="create_combo" if created else "change_combo", object_type="restaurant.Combo", object_id=str(combo.pk),
                            before_data=before, after_data=_snapshot(combo))
    return combo


# --- reading --------------------------------------------------------------------

def _choices_prefetch(only_active):
    queryset = ComboChoice.objects.select_related("item").order_by("sort", "pk")
    if only_active:
        queryset = queryset.filter(active=True, item__active=True)
    return Prefetch("slots", queryset=ComboSlot.objects.order_by("sort", "pk").prefetch_related(Prefetch("choices", queryset=queryset)))


def all_combos():
    return Combo.objects.select_related("item").prefetch_related(_choices_prefetch(False)).order_by("sort", "pk")


def menu_combos(items):
    """{item pk: combo} for the active combos among the menu's ``items`` whose every slot offers something now."""

    result = {}
    for combo in Combo.objects.filter(item__in=items, active=True).select_related("item").prefetch_related(_choices_prefetch(True)):
        slots = list(combo.slots.all())
        if slots and all(list(slot.choices.all()) for slot in slots):
            result[combo.item_id] = combo
    return result


# --- taking a combo -------------------------------------------------------------

def choose(combo, raw, lang="ar"):
    """The choice made for each slot, checked: [(slot, choice)] in the combo's order. Refuses anything else.

    ``raw``: {slot pk (or slot): choice pk (or choice)} as the sheet sends it. Nothing is written here."""

    words_ = words(lang)
    combo = Combo.objects.select_related("item").prefetch_related(_choices_prefetch(False)).get(pk=combo.pk)
    if not combo.active or not combo.item.active:
        raise ValidationError(words_["off"])
    slots = list(combo.slots.all())
    by_slot = {slot.pk: slot for slot in slots}
    picked = {}
    for key, value in (raw or {}).items():
        key = key.pk if isinstance(key, ComboSlot) else key
        value = value.pk if isinstance(value, ComboChoice) else value
        slot = by_slot.get(int(key)) if str(key).strip().isdigit() else None
        if slot is None:
            raise ValidationError(words_["foreign"].format(slot=combo.label(lang)))
        choice = next((one for one in slot.choices.all() if str(one.pk) == str(value).strip()), None)
        if choice is None:
            raise ValidationError(words_["foreign"].format(slot=slot.label(lang)))
        if not choice.active or not choice.item.active:
            raise ValidationError(words_["off"])
        picked[slot.pk] = choice
    for slot in slots:
        if slot.pk not in picked:
            raise ValidationError(words_["missing"].format(slot=slot.label(lang)))
    return [(slot, picked[slot.pk]) for slot in slots]


def priced(combo, chosen, lang="ar"):
    """[(slot, choice, menu price, discount)] for one combo taken: each component at its menu price now, with its share of
    the saving. Refuses a combo that would cost more than its parts (its price raised, or its items made cheaper, since set-up)."""

    prices = [money_round(choice.item.default_sale_price) for _slot, choice in chosen]
    charged = combo.price + sum((money_round(choice.upcharge) for _slot, choice in chosen), ZERO)
    saving = sum(prices, ZERO) - charged
    if saving < 0:
        raise ValidationError(words(lang)["dearer"])
    return [(slot, choice, price, share) for (slot, choice), price, share in zip(chosen, prices, spread(prices, saving))]


def whole_count(raw, lang="ar", most=99):
    words_ = words(lang)
    text = str(raw if raw not in (None, "") else "1").strip()
    try:
        number = Decimal(text.replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError(words_["whole"].format(max=most))
    if not number.is_finite() or number % 1 or not 1 <= number <= most:
        raise ValidationError(words_["whole"].format(max=most))
    return int(number)


# --- families on an order ------------------------------------------------------

def bundle_lines(line):
    """The components of ``line``'s combo taken, in order (the line alone when it is not a component)."""

    if not line.bundle:
        return [line]
    return list(type(line).objects.filter(order_id=line.order_id, bundle=line.bundle).order_by("pk"))


def head_of(line):
    lines = bundle_lines(line)
    return lines[0]


def is_head(line):
    return not line.bundle or head_of(line).pk == line.pk


def next_bundle(order, extra=()):
    used = set(order.lines.filter(bundle__isnull=False).values_list("bundle", flat=True)) | set(extra)
    return max(used, default=0) + 1


def blocks(families):
    """The order page's rows: ``families`` ([(line, add-ons)], ``modifiers.grouped``) with each combo taken gathered into one
    block at the place of its first component: {"combo", "bundle", "head", "members": [{"line", "addons"}], "gross",
    "saving", "amount", "voided", "sent"}. A plain family is {"line", "addons"}."""

    result, open_blocks = [], {}
    for line, addons in families:
        if not line.bundle:
            result.append({"line": line, "addons": addons})
            continue
        block = open_blocks.get(line.bundle)
        if block is None:
            block = {"combo": line.combo, "bundle": line.bundle, "head": line, "members": [], "gross": ZERO, "saving": ZERO, "amount": ZERO}
            open_blocks[line.bundle] = block
            result.append(block)
        block["members"].append({"line": line, "addons": addons})
        live = [line] + list(addons)
        block["gross"] += sum((one.gross for one in live), ZERO)
        block["saving"] += line.line_discount
        block["amount"] += sum((one.amount for one in live), ZERO)
    for block in open_blocks.values():
        block["voided"] = all(member["line"].voided for member in block["members"])
        block["sent"] = any(member["line"].ticket_id for member in block["members"])
        block["ready"] = all(member["line"].ready_at for member in block["members"]) and not block["voided"]
    return result
