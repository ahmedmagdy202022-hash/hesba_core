"""M2 (HG-086): the medicines a doctor writes on a visit, chosen from the reference drug list or typed by hand.

* :func:`lines_from_post` reads the «الأدوية» rows of the visit form; :func:`resolve` checks them and turns them into
  unsaved :class:`~medical.models.VisitMedicine` rows for ``services.save_visit``, which writes them with the visit in one
  transaction. Nothing else writes a line.
* The snapshot rule: a line chosen from the list copies the row's names, ingredient, strength and form when it is first
  saved. Saving the visit again keeps that copy as long as the line still points at the same row (or at nothing, once the
  row was deleted); only choosing another drug copies again. A re-import never rewrites an old prescription.
* :func:`allergy_terms` is the one allergy rule (advice only, never a refusal): a chosen drug's active ingredient, or the
  first word of its trade name, appearing as a word or a phrase of the recorded allergies, both reduced the way the
  reference list reduces names (case, hamza forms, ة/ى, Arabic digits, harakat). No drug-class or synonym guessing: an
  allergy written «بنسلين» does not flag Amoxicillin, and «Aspirin» does not flag a row whose only name is
  «Acetylsalicylic acid».

A prescription is clinical text: no price, no stock, no invoice is read or written here.
"""

import re

from pharmacy import picker
from pharmacy.reference import _MARKS, clean_text
from pharmacy.services import FORM_LABELS, _plain, ingredient_key

#: A visit takes at most this many medicine lines.
MAX_LINES = 20
#: The posted fields of one line, in the order the form sends them (``rx_<field>``, one value per line).
FIELDS = ("id", "reference", "name", "dose", "frequency", "duration", "route", "notes")
LIMITS = {"name": 255, "dose": 60, "frequency": 80, "duration": 60, "route": 60, "notes": 255}
#: What the visit form's search never returns: a prescription is not priced.
PRICE_KEYS = ("public_price", "price_date", "price_month", "stale")

WORDS = {
    "ar": {
        "section": "الأدوية", "intro": "دوّر على الدوا في القايمة أو اكتبه بإيدك، وحدّد الجرعة وعدد المرات والمدة.",
        "add": "ضيف دوا", "add_label": "ضيف دوا من القايمة المرجعية", "hand": "اكتب دوا بإيدك", "empty": "مفيش أدوية في الروشتة لسه.",
        "no_list": "لسه مفيش قايمة أدوية متحمّلة. اكتب الدوا بإيدك، واطلب من المالك أو دعم حسبة يحمّلوا القايمة.",
        "name": "اسم الدوا", "name_hint": "الاسم والتركيز، مثال: Augmentin 1 g", "dose": "الجرعة", "frequency": "كام مرة",
        "duration": "لمدة", "route": "طريقة الاستعمال", "notes": "ملاحظات", "more": "طريقة الاستعمال وملاحظات",
        "edit": "عدّل", "edit_label": "عدّل {name}", "remove": "شيل", "remove_label": "شيل {name} من الروشتة", "up": "طلّعه فوق",
        "down": "نزّله تحت", "done": "تمام", "medicine_n": "الدوا رقم {n}", "from_list": "من القايمة",
        "allergy": "تنبيه حساسية: «{terms}» مكتوب في الحساسية المسجّلة. راجع قبل ما تكتب الدوا ده.",
        "allergy_chip": "حساسية", "saved_allergy": "⚠ تنبيه حساسية في الروشتة: {lines}. الكشف اتحفظ؛ راجعه لو محتاج.",
        "instructions": "تعليمات للمريض", "instructions_hint": "راحة، أكل، تمارين، أي حاجة غير الأدوية. الأدوية بتتكتب في «الأدوية».",
        "print_instructions": "تعليمات",
        # the sheet
        "title": "اختار دوا من القايمة", "close": "اقفل", "search": "دوّر في القايمة المرجعية",
        "placeholder": "اكتب أول حروف الدوا أو المادة الفعالة", "short_title": "اكتب حرفين على الأقل",
        "short_body": "اكتب اسم الدوا التجاري أو المادة الفعالة.", "loading": "بدوّر…", "count": "{n} نتايج", "count_one": "نتيجة واحدة",
        "count_two": "نتيجتين", "count_many": "{n} نتيجة", "cap": "بيظهر أول {n} بس", "empty_title": "مفيش نتيجة",
        "empty_body": "اكتب الاسم بشكل تاني، أو اكتب الدوا بإيدك.", "nolist_title": "مفيش قايمة أدوية متحمّلة",
        "nolist_body": "اكتب الدوا بإيدك، واطلب من المالك أو دعم حسبة يحمّلوا القايمة.", "error_title": "مقدرناش نوصل للقايمة",
        "error_body": "اتأكد من النت وجرّب تاني، أو اكتب الدوا بإيدك.", "retry": "جرّب تاني", "manual": "مش لاقيه؟ اكتبه بإيدك",
        "source": "من القايمة المرجعية", "disclaimer": "القايمة للمساعدة بس؛ القرار للدكتور.", "rx_chip": "بروشتة",
    },
    "en": {
        "section": "Medicines", "intro": "Search the drug list or type the medicine, then set the dose, how often and for how long.",
        "add": "Add medicine", "add_label": "Add a medicine from the reference list", "hand": "Type a medicine", "empty": "No medicines on this prescription yet.",
        "no_list": "No drug list is loaded yet. Type the medicine by hand, and ask the owner or Hesba support to load the list.",
        "name": "Medicine", "name_hint": "Name and strength, e.g. Augmentin 1 g", "dose": "Dose", "frequency": "How often",
        "duration": "For", "route": "How to use", "notes": "Notes", "more": "How to use and notes",
        "edit": "Edit", "edit_label": "Edit {name}", "remove": "Remove", "remove_label": "Remove {name} from the prescription", "up": "Move up",
        "down": "Move down", "done": "Done", "medicine_n": "Medicine {n}", "from_list": "From the list",
        "allergy": "Allergy alert: «{terms}» is in the recorded allergies. Check before prescribing this.",
        "allergy_chip": "Allergy", "saved_allergy": "⚠ Allergy alert on the prescription: {lines}. The visit was saved; review it if needed.",
        "instructions": "Instructions for the patient", "instructions_hint": "Rest, diet, exercises: anything other than medicines. Medicines go in «Medicines».",
        "print_instructions": "Instructions",
        "title": "Choose a medicine from the list", "close": "Close", "search": "Search the reference list",
        "placeholder": "Type the first letters of the drug or the active ingredient", "short_title": "Type at least two letters",
        "short_body": "Type a trade name or an active ingredient.", "loading": "Searching…", "count": "{n} results", "count_one": "1 result",
        "count_two": "2 results", "count_many": "{n} results", "cap": "Only the first {n} are shown", "empty_title": "No results",
        "empty_body": "Try another spelling, or type the medicine by hand.", "nolist_title": "No drug list is loaded",
        "nolist_body": "Type the medicine by hand, and ask the owner or Hesba support to load the list.", "error_title": "Could not reach the list",
        "error_body": "Check the connection and try again, or type the medicine by hand.", "retry": "Try again", "manual": "Not there? Type it by hand",
        "source": "From the reference list", "disclaimer": "The list only helps; the doctor decides.", "rx_chip": "Rx",
    },
}

#: Quick picks that fill the line's text (the doctor can still type anything): the dose by dosage form, how often, before
#: or after food (added to «كام مرة»), and for how long.
CHIPS = {
    "ar": {
        "dose": {"tablet": ["قرص", "نص قرص", "قرصين"], "capsule": ["كبسولة", "كبسولتين"], "syrup": ["5 مل", "2.5 مل", "10 مل"],
                 "suspension": ["5 مل", "2.5 مل", "10 مل"], "drops": ["نقطتين", "3 نقط", "نقطة"], "cream": ["دهان خفيف"],
                 "spray": ["بخة", "بختين"], "sachet": ["كيس"], "suppository": ["لبوسة"], "injection": ["حقنة"],
                 "": ["قرص", "كبسولة", "5 مل"]},
        "frequency": ["مرة يوميًا", "مرتين يوميًا", "3 مرات يوميًا", "كل 8 ساعات", "عند اللزوم"],
        "food": ["قبل الأكل", "بعد الأكل"],
        "duration": ["3 أيام", "5 أيام", "7 أيام", "10 أيام", "14 يوم", "شهر", "مستمر"],
    },
    "en": {
        "dose": {"tablet": ["1 tablet", "½ tablet", "2 tablets"], "capsule": ["1 capsule", "2 capsules"], "syrup": ["5 ml", "2.5 ml", "10 ml"],
                 "suspension": ["5 ml", "2.5 ml", "10 ml"], "drops": ["2 drops", "3 drops", "1 drop"], "cream": ["Thin layer"],
                 "spray": ["1 puff", "2 puffs"], "sachet": ["1 sachet"], "suppository": ["1 suppository"], "injection": ["1 injection"],
                 "": ["1 tablet", "1 capsule", "5 ml"]},
        "frequency": ["Once daily", "Twice daily", "3 times daily", "Every 8 hours", "As needed"],
        "food": ["before food", "after food"],
        "duration": ["3 days", "5 days", "7 days", "10 days", "14 days", "1 month", "Ongoing"],
    },
}

MESSAGES = {
    "ar": {"line_name": "الدوا رقم {n}: اكتب اسمه أو اختاره من القايمة.", "too_many": "الروشتة بتاخد لحد {limit} دوا.",
           "bad_lines": "الأدوية مش مظبوطة؛ جرّب تاني."},
    "en": {"line_name": "Medicine {n}: type its name or choose it from the list.", "too_many": "A prescription takes up to {limit} medicines.",
           "bad_lines": "The medicines could not be read; try again."},
}

_ARABIC = re.compile("[؀-ۿ]")


def _lang(lang):
    return "en" if lang == "en" else "ar"


# ---- reading and checking the lines ----

def lines_from_post(post):
    """The posted lines as a list of dicts of text, in the order the form sent them (``rx_<field>``, one value per line)."""

    lists = {field: post.getlist(f"rx_{field}") for field in FIELDS}
    count = max((len(values) for values in lists.values()), default=0)
    return [{field: (lists[field][index] if index < len(lists[field]) else "") for field in FIELDS} for index in range(count)]


def _set_name(line, typed):
    """A typed name is kept as the Arabic name when it has Arabic letters, else as the English one."""

    line.name_ar, line.name_en = (typed, "") if _ARABIC.search(typed) else ("", typed)


def _copy(line, row):
    """The snapshot: what the reference row says now, copied onto the line."""

    line.reference = row
    line.name_ar, line.name_en = clean_text(row.name_ar, 255), clean_text(row.name_en, 255)
    line.active_ingredient, line.strength, line.form = row.active_ingredient[:255], row.strength[:60], row.form[:12]


def resolve(raw_lines, visit, lang, errors):
    """Unsaved ``VisitMedicine`` rows for the posted lines (positions not set yet), appending any refusal to ``errors``.

    Blank rows are skipped. A line whose id is one of this visit's own lines and whose reference is the one it already
    has (or none, after the row was deleted) keeps its stored snapshot; only its dose, frequency, duration, route and
    notes change, and a hand-written line's name. A posted reference that is not an active row turns the line into a
    hand-written one with the typed name. An id from another visit is ignored (the line is new)."""

    from pharmacy.models import DrugReference

    from .models import VisitMedicine

    words = MESSAGES[_lang(lang)]
    if not isinstance(raw_lines, (list, tuple)) or not all(isinstance(raw, dict) for raw in raw_lines):
        errors.append(words["bad_lines"])
        return []
    existing = {line.pk: line for line in visit.medicines.all()} if visit is not None and visit.pk else {}
    wanted = {picker.reference_id(raw.get("reference")) for raw in raw_lines} - {None}
    rows = {row.pk: row for row in DrugReference.objects.filter(pk__in=wanted, is_active=True)} if wanted else {}
    lines = []
    for raw in raw_lines:
        text = {field: clean_text(raw.get(field), limit) for field, limit in LIMITS.items()}
        ref_id = picker.reference_id(raw.get("reference"))
        old = existing.pop(picker.reference_id(raw.get("id")), None)  # an id is used once; a repeat makes a new line
        if old is None and ref_id is None and not any(text.values()):
            continue  # a row added and left empty
        number = len(lines) + 1
        if old is not None and old.reference_id == ref_id:
            line = old  # the stored snapshot stays as it is
            if ref_id is None and text["name"] and text["name"] != old.name(lang):
                _set_name(line, text["name"])  # the doctor changed a hand-written line's name
        elif ref_id in rows:
            line = old or VisitMedicine()
            _copy(line, rows[ref_id])
        elif old is not None and old.reference_id is None and ref_id is not None and (old.active_ingredient or old.strength or old.form):
            # The list row was deleted while the form was open: the form still posts its id, the stored line already
            # lost its reference (SET_NULL). It is the same medicine, not a line turned into a hand-written one, so
            # the copy made when it was prescribed stays.
            line = old
        else:
            if not text["name"]:
                errors.append(words["line_name"].format(n=number))
            line = old or VisitMedicine()
            line.reference, line.active_ingredient, line.strength, line.form = None, "", "", ""
            _set_name(line, text["name"])
        for field in ("dose", "frequency", "duration", "route", "notes"):
            setattr(line, field, text[field])
        lines.append(line)
    if len(lines) > MAX_LINES:
        errors.append(words["too_many"].format(limit=MAX_LINES))
    return lines


def write(visit, lines):
    """Save ``lines`` on ``visit`` in their order and delete its other lines. Called inside ``save_visit``'s transaction."""

    kept = []
    for position, line in enumerate(lines, 1):
        line.visit, line.position = visit, position
        line.save()
        kept.append(line.pk)
    visit.medicines.exclude(pk__in=kept).delete()


# ---- the allergy advice ----

_WORD_SPLIT = re.compile(r"[^\w]+")
_PHRASE_SPLIT = re.compile(r"[,،؛;/+&|()\[\]\n\r]+|\s+(?:و|and)\s+")
_PREFIXES = ("وال", "بال", "ال", "و")
MIN_TERM = 3


def _reduce(text):
    """As the reference list reduces a name (``pharmacy.reference.fold``), without its length limit."""

    return _MARKS.sub("", _plain(text))


def _compact(text):
    """As ``pharmacy.services.ingredient_key`` reduces one ingredient: no spaces, hyphens, dots or brackets."""

    return re.sub(r"[\s\-_.()]+", "", text)


def allergy_keys(allergies):
    """Every word (with and without a leading «ال», «و», «وال», «بال»), every run of up to four words written together
    (so «clavulanic acid» meets the ingredient key «clavulanicacid») and every phrase of the allergy text, reduced."""

    reduced = _reduce(allergies)
    if not reduced.strip():
        return frozenset()
    keys = {_compact(phrase) for phrase in _PHRASE_SPLIT.split(reduced)}
    words = [word for word in _WORD_SPLIT.split(reduced) if word]
    for at, word in enumerate(words):
        keys.add(word)
        keys.update(word[len(prefix):] for prefix in _PREFIXES if word.startswith(prefix))
        keys.update("".join(words[at:at + size]) for size in (2, 3, 4) if at + size <= len(words))
    return frozenset(key for key in keys if len(key) >= MIN_TERM)


def _brand(name):
    """(reduced, shown) for the first word of a trade name that has a letter, or None."""

    for shown in str(name or "").split():
        reduced = "".join(_WORD_SPLIT.split(_reduce(shown)))
        if len(reduced) >= MIN_TERM and any(char.isalpha() for char in reduced):
            return reduced, shown
    return None


def drug_terms(active_ingredient="", names=()):
    """[(reduced, shown)]: each part of the active ingredient (a combination split as ``ingredient_key`` splits it) and the
    first word of each trade name."""

    terms = []
    for part in re.split(r"\s*(?:\+|/|،|,|&)\s*", str(active_ingredient or "")):
        key = ingredient_key(part)
        if len(key) >= MIN_TERM:
            terms.append((key, part.strip()))
    for name in names:
        brand = _brand(name)
        if brand:
            terms.append(brand)
    return terms


def allergy_terms(keys, active_ingredient="", names=()):
    """The shown words of a drug that appear in the allergy keys (``allergy_keys``), each once. [] means no warning."""

    if not keys:
        return []
    found = []
    for reduced, shown in drug_terms(active_ingredient, names):
        if reduced in keys and shown not in found:
            found.append(shown)
    return found


def line_terms(keys, line):
    return allergy_terms(keys, line.active_ingredient, (line.name_ar, line.name_en))


def row_terms(keys, row):
    return allergy_terms(keys, row.active_ingredient, (row.name_ar, row.name_en))


def allergies_of(patient, animal=None):
    """The allergy text a prescription for ``patient`` is checked against: the animal's own on a vet visit, never the
    owner's file on a vet install (as the printed prescription does), else the patient's file. Never opens a file."""

    from .models import PatientFile
    from .vet import is_vet

    if animal is not None:
        return animal.allergies
    if is_vet():
        return ""
    return PatientFile.objects.filter(customer=patient).values_list("allergies", flat=True).first() or ""


def allergies_for_visit(visit):
    return allergies_of(visit.patient, visit.animal if visit.animal_id else None)


def saved_warning(visit, lang):
    """The «تنبيه حساسية» message after a save, or "" when no line matches."""

    keys = allergy_keys(allergies_for_visit(visit))
    hits = []
    for line in visit.medicines.all():
        terms = line_terms(keys, line)
        if terms:
            hits.append(f"{line.name(lang)} («{'، '.join(terms) if lang != 'en' else ', '.join(terms)}»)")
    return WORDS[_lang(lang)]["saved_allergy"].format(lines="؛ ".join(hits) if lang != "en" else "; ".join(hits)) if hits else ""


# ---- showing the lines ----

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _strength_in_name(strength, names):
    """True when the strength's first number is already a number in a trade name («بنادول 500 مجم», 500 mg): it is then
    not printed twice."""

    found = _NUMBER.search(_plain(strength))
    if not found:
        return False
    return any(found.group() in _NUMBER.findall(_plain(name)) for name in names if name)


def display(line, lang, keys=frozenset(), number=None):
    """What a template shows for one line (the form, the saved visit, the print)."""

    lang = _lang(lang)
    terms = line_terms(keys, line)
    words = WORDS[lang]
    other = line.name_ar if lang == "en" else line.name_en
    return {
        "id": line.pk or "", "reference": line.reference_id or "", "number": number or line.position,
        "name": line.name(lang), "other_name": other if other and other != line.name(lang) else "",
        "from_list": bool(line.reference_id or line.active_ingredient or line.strength or line.form),
        "ingredient": line.active_ingredient, "strength": line.strength, "form": line.form,
        "form_label": FORM_LABELS[lang].get(line.form, "") if line.form else "",
        "strength_shown": "" if _strength_in_name(line.strength, (line.name(lang),)) else line.strength,
        "dose": line.dose, "frequency": line.frequency, "duration": line.duration, "route": line.route, "notes": line.notes,
        "how": " · ".join(part for part in (line.dose, line.frequency, line.duration) if part),
        "how_full": " · ".join(part for part in (line.dose, line.frequency, line.duration, line.route) if part),
        "allergy": terms, "allergy_text": words["allergy"].format(terms=", ".join(terms) if lang == "en" else "، ".join(terms)) if terms else "",
    }


def for_visit(visit, lang):
    """The saved visit's lines as ``display`` dicts, numbered from 1, with the allergy advice."""

    lines = list(visit.medicines.all())
    if not lines:
        return []
    keys = allergy_keys(allergies_for_visit(visit))
    return [display(line, lang, keys, number) for number, line in enumerate(lines, 1)]


def search_json(row, lang, keys=frozenset(), today=None):
    """One reference row for the visit form's sheet: the pharmacy picker's fields without the price, plus the allergy words."""

    data = picker.as_json(row, lang, today)
    for key in PRICE_KEYS:
        data.pop(key, None)
    data["allergy"] = row_terms(keys, row)
    return data


def form_context(request, patient, visit, lang):
    """The «الأدوية» part of the visit form: the lines (from a refused post, else the saved visit), the words, the quick picks."""

    from pharmacy.models import DrugReference

    lang = _lang(lang)
    animal = getattr(visit, "animal", None) if visit is not None else None
    if request.method == "POST":
        from .models import Animal

        lines = resolve(lines_from_post(request.POST), visit, lang, [])
        from .vet import is_vet

        if is_vet() and str(request.POST.get("animal") or "").isdigit():  # a vet visit: the animal chosen on the refused form
            animal = Animal.objects.filter(pk=int(request.POST["animal"]), owner=patient).first()
    else:
        lines = list(visit.medicines.all()) if visit is not None else []
    keys = allergy_keys(allergies_of(patient, animal))
    return {"rx": {"lines": [display(line, lang, keys, number) for number, line in enumerate(lines, 1)], "words": WORDS[lang],
                   "chips": CHIPS[lang], "list_loaded": DrugReference.objects.filter(is_active=True).exists(),
                   "max_lines": MAX_LINES, "blank": display(_blank(), lang)}}


def _blank():
    from .models import VisitMedicine

    return VisitMedicine()


def print_lines(visit, lang):
    """The printed prescription's lines, numbered, without the allergy advice (the print shows the allergies once)."""

    return [display(line, lang, number=number) for number, line in enumerate(visit.medicines.all(), 1)]
