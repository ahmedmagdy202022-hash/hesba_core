"""R1 step 2: the reference drug list where a pharmacy enters items (HG-082 tables, read only here).

* :func:`search_reference` answers the «اختار من القايمة» picker on the new-item form (one query, at most 20 rows).
* :class:`DrugFields` is the optional "drug data" part of the new-item form when the ``drugs`` capability is on. It only
  fills ``Item`` fields for the person to review and, on save, hands its values to :func:`services.save_drug` unchanged.
  Nothing is created from the list by itself: a row becomes an item only when the person presses «احفظ الصنف».
* :func:`unstocked_alternatives` lists reference rows with the same active ingredient that match no item the pharmacy
  already has (one query), for «بدائل مش عندك» on the drug page.

Nothing here writes ``DrugReference``, ``Item`` prices, stock or any posting.
"""

import calendar
import re

from django import forms
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.db.models.functions import Lower, Replace, Trim
from django.utils import timezone

from master_data.models import Item

from . import services
from .models import DosageForm, DrugReference
from .reference import _MARKS, clean_text, fold

#: The picker shows at most this many rows; the person types more to narrow it.
SEARCH_LIMIT = 20
#: «بدائل مش عندك» on the drug page.
ALTERNATIVES_LIMIT = 8
#: A list price older than this many months is shown as «سعر قديم».
STALE_MONTHS = 6
MIN_QUERY = 2
_QUERY_LIMIT = 100

WORDS = {
    "ar": {"source": "القايمة المرجعية", "ingredient": "المادة الفعالة", "strength": "التركيز", "form": "الشكل", "maker": "الشركة",
           "rx": "الدوا ده بروشتة", "rx_help": "اختيارك انت؛ القايمة مش بتعلّمها لوحدها.", "ingredient_hint": "مثال: Paracetamol، والتركيبة بعلامة +",
           "strength_hint": "500 mg"},
    "en": {"source": "Reference list", "ingredient": "Active ingredient", "strength": "Strength", "form": "Dosage form", "maker": "Maker",
           "rx": "Sold on prescription only", "rx_help": "Your choice; the list never ticks it for you.", "ingredient_hint": "For example: Paracetamol; a combination with +",
           "strength_hint": "500 mg"},
}

#: Every string of the picker, the filled form and «بدائل مش عندك»; the template reads ``drug.words`` and the script
#: gets the same dict, so Arabic and English stay side by side.
UI = {
    "ar": {
        "open": "اختار من القايمة", "open_label": "اختار الدوا من القايمة المرجعية", "hint": "صيدلية؟ ابحث في القايمة المرجعية بدل ما تكتب كل حاجة بإيدك.",
        "title": "اختار من القايمة المرجعية", "close": "اقفل", "search": "ابحث في القايمة المرجعية",
        "placeholder": "اكتب أول حروف الدوا أو المادة الفعالة أو الباركود", "short_title": "اكتب حرفين على الأقل",
        "short_body": "اكتب اسم الدوا أو المادة الفعالة أو الباركود.", "loading": "بدوّر…", "count": "{n} نتايج", "count_one": "نتيجة واحدة", "count_two": "نتيجتين", "count_many": "{n} نتيجة",
        "cap": "بيظهر أول {n} بس", "empty_title": "مفيش نتيجة", "empty_body": "مفيش نتيجة. اكتب الاسم بشكل تاني أو كمّل يدوي",
        "error_title": "مقدرناش نوصل للقايمة", "error_body": "اتأكد من النت وجرّب تاني، أو كمّل يدوي.", "retry": "جرّب تاني",
        "source": "من القايمة المرجعية", "disclaimer": "السعر مرجعي، راجع تسعيرة الهيئة", "manual": "مش لاقيه؟ كمّل يدوي",
        "rx_chip": "بروشتة", "currency": "ج.م", "no_price": "من غير سعر",
        "banner_title": "اتملّا من القايمة المرجعية", "banner_body": "الخانات الملوّنة جاية من القايمة. راجعها وعدّل أي حاجة، ولسه ماتحفظش حاجة.",
        "again": "اختار دوا تاني", "suggested": "سعر مقترح", "from_list": "من القايمة المرجعية", "price_of": "سعر {month}",
        "can_change": "تقدر تغيّره.", "own": "سعرك انت", "list_price": "سعر القايمة {price} ({month})", "restore": "رجّع سعر القايمة",
        "stale": "سعر قديم", "stale_body": "راجع السعر قبل ما تحفظ.", "no_date": "من غير تاريخ",
        "drug_part": "بيانات الدوا", "drug_part_hint": "بتساعد في البدائل والبحث. كلها اختيارية.",
        "missing_title": "بدائل مش عندك", "missing_intro": "دي مش أصناف عندك. مفيش مخزون ولا مبيعات عليها لحد ما تضيفها.",
        "same": "نفس التركيز", "other": "تركيز تاني", "add": "ضيفه كصنف", "list_price_short": "سعر القايمة", "decide": "ده اقتراح بنفس المادة الفعالة. القرار للصيدلي.",
    },
    "en": {
        "open": "Choose from list", "open_label": "Choose the drug from the reference list", "hint": "A pharmacy? Search the reference list instead of typing everything.",
        "title": "Choose from the reference list", "close": "Close", "search": "Search the reference list",
        "placeholder": "Type the first letters of the drug, the ingredient or the barcode", "short_title": "Type at least two letters",
        "short_body": "Type a drug name, an active ingredient or a barcode.", "loading": "Searching…", "count": "{n} results", "count_one": "1 result", "count_two": "2 results", "count_many": "{n} results",
        "cap": "Only the first {n} are shown", "empty_title": "No results", "empty_body": "No results. Try another spelling or continue manually",
        "error_title": "Could not reach the list", "error_body": "Check the connection and try again, or continue manually.", "retry": "Try again",
        "source": "From the reference list", "disclaimer": "Reference price, check the official price list", "manual": "Not there? Continue manually",
        "rx_chip": "Rx", "currency": "EGP", "no_price": "No price",
        "banner_title": "Filled from the reference list", "banner_body": "The tinted fields come from the list. Check them and change anything; nothing is saved yet.",
        "again": "Choose another drug", "suggested": "Suggested price", "from_list": "from the reference list", "price_of": "price {month}",
        "can_change": "You can change it.", "own": "Your price", "list_price": "list price {price} ({month})", "restore": "Use the list price",
        "stale": "Old price", "stale_body": "Check the price before you save.", "no_date": "no date",
        "drug_part": "Drug data", "drug_part_hint": "Helps with alternatives and search. All optional.",
        "missing_title": "Alternatives not in your stock", "missing_intro": "These are not your items. No stock or sales until you add them.",
        "same": "Same strength", "other": "Other strength", "add": "Add as item", "list_price_short": "List price", "decide": "A suggestion with the same active ingredient. The pharmacist decides.",
    },
}

# The characters ``reference.fold`` drops or changes, so the database folds a column the way Python folds the query.
_FOLD_PAIRS = (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي"), *zip("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
_DROPPED = "".join(chr(code) for code in range(0x0600, 0x0700) if _MARKS.match(chr(code)))


def db_fold(expression):
    """``reference.fold`` as a database expression (case, hamza forms, ة, ى, Arabic digits, harakat and tatweel).

    Used to match Arabic names and item names in SQL without a stored key (no migration in this step)."""

    folded = Lower(expression)
    for old, new in _FOLD_PAIRS:
        folded = Replace(folded, Value(old), Value(new))
    for mark in _DROPPED:
        folded = Replace(folded, Value(mark), Value(""))
    return Trim(folded)


def six_months_before(day, months=STALE_MONTHS):
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def is_stale(price_date, today=None):
    """True when the list's price has no date or is older than :data:`STALE_MONTHS` months."""

    return price_date is None or price_date < six_months_before(today or timezone.localdate())


def clean_query(raw):
    """(text, folded) for a typed or scanned query; control characters dropped, so odd input never reaches the database."""

    text = clean_text(raw, _QUERY_LIMIT)
    return text, fold(text)


def _barcodes(text):
    """The barcode strings a typed or scanned code may be stored as (a 12-digit UPC-A is stored with a leading 0)."""

    digits = re.sub(r"[\s-]+", "", text.translate(services._DIGITS))
    if not (digits.isascii() and digits.isdigit()) or len(digits) not in (8, 12, 13, 14):
        return []
    return [digits, "0" + digits] if len(digits) == 12 else [digits]


def db_digits(expression):
    """Only the digit part of :func:`db_fold` (Arabic-Indic digits and «٫»): what a query without Arabic letters needs."""

    for old, new in _FOLD_PAIRS[5:]:
        expression = Replace(expression, Value(old), Value(new))
    return expression


_ARABIC = re.compile("[؀-ۿ]")
_DIGIT = re.compile(r"[\d.]")


def search_reference(raw, limit=SEARCH_LIMIT):
    """(folded query, rows): active reference rows matching a name prefix or part, the Arabic name, the active ingredient,
    or an exact barcode. Barcode hits first, then name prefixes, then the rest, each by name. One query; [] below 2 letters.

    Cost (no stored Arabic key yet, HG-082): the cheap stored keys are tested first; the Arabic name is folded in SQL
    only for a query with Arabic letters, once per row with an Arabic name in WHERE and once more per matching row for
    the rank. A query without Arabic letters (Latin, digits, a barcode) can only match the Arabic name where it holds
    the same Latin text or digits, so there it is compared case-insensitively with only its Arabic digits folded.
    ``name_key`` is searched in both cases: it folds ``name_en``, or ``name_ar`` when a row has no English name."""

    text, folded = clean_query(raw)
    if len(folded) < MIN_QUERY:
        return folded, []
    ingredient = services.ingredient_key(text)
    codes = _barcodes(text)
    rows = DrugReference.objects.filter(is_active=True)
    if _ARABIC.search(folded):
        rows = rows.alias(ar_key=db_fold(F("name_ar")))
        # A letter the fold never produces (not ا, ه or ي) is in the stored name as typed: a plain LIKE on it first
        # skips most rows before the fold runs, without changing which rows match.
        kept = [char for char in dict.fromkeys(folded) if _ARABIC.match(char) and char not in "اهي"][:2]
        in_arabic = Q(*(Q(name_ar__contains=char) for char in kept), ar_key__contains=folded)
        arabic_prefix = Q(ar_key__startswith=folded)
    else:
        if _DIGIT.search(folded):
            rows = rows.alias(ar_key=db_digits(F("name_ar")))
            in_arabic, arabic_prefix = Q(ar_key__icontains=folded), Q(ar_key__istartswith=folded)
        else:
            in_arabic, arabic_prefix = Q(name_ar__icontains=folded), Q(name_ar__istartswith=folded)
    condition = Q(name_key__contains=folded)
    if len(ingredient) >= MIN_QUERY:
        condition |= Q(ingredient_key__contains=ingredient)
    if codes:
        condition |= Q(barcode__in=codes)
    condition |= ~Q(name_ar="") & in_arabic  # last: the only part that is not a plain stored column
    rank = [When(Q(name_key__startswith=folded) | arabic_prefix, then=Value(1))]
    if codes:
        rank.insert(0, When(barcode__in=codes, then=Value(0)))
    rows = (rows.filter(condition).alias(rank=Case(*rank, default=Value(2), output_field=IntegerField()))
            .order_by("rank", "name_key", "pk")[:limit])
    return folded, list(rows)


def month_label(day):
    return f"{day.month:02d}/{day.year}" if day else ""


def source_line(row, lang="ar"):
    version = f" · {row.source_version}" if row.source_version else ""
    return f"{WORDS[lang]['source']}: {row.source}{version}"


def as_json(row, lang="ar", today=None):
    """One reference row for the picker (and the server-side ``?ref=`` prefill)."""

    lang = "en" if lang == "en" else "ar"
    return {
        "id": row.pk, "name_ar": row.name_ar, "name_en": row.name_en, "ingredient": row.active_ingredient, "strength": row.strength,
        "form": {"code": row.form, "label": services.FORM_LABELS[lang].get(row.form, "") if row.form else ""},
        "pack": row.pack, "manufacturer": row.manufacturer,
        "public_price": f"{row.public_price:.2f}" if row.public_price is not None else None,
        "price_date": row.price_date.isoformat() if row.price_date else None, "price_month": month_label(row.price_date),
        "stale": is_stale(row.price_date, today), "barcode": row.barcode, "prescription_only": row.prescription_only,
        "is_drug": row.is_drug, "source": source_line(row, lang),
    }


def reference_id(raw_pk):
    """A reference row id as an int, or None for anything that is not one (never an error)."""

    try:
        pk = int(str(raw_pk or "").strip())
    except (TypeError, ValueError):
        return None
    return pk if 0 < pk <= 2**31 - 1 else None


def reference_row(raw_pk):
    """The active reference row ``?ref=`` names, or None for anything else (never an error)."""

    pk = reference_id(raw_pk)
    return DrugReference.objects.filter(pk=pk, is_active=True).first() if pk is not None else None


# ---- the drug part of the new-item form ----

class DrugFields(forms.Form):
    """The optional drug data typed with a new item. Saved with :func:`services.save_drug` once the item is saved."""

    # Only says which list row filled the form; never an error (a forged or stale value is simply ignored).
    reference = forms.CharField(required=False, widget=forms.HiddenInput(attrs={"data-drug-reference": ""}))
    active_ingredient = forms.CharField(required=False, max_length=255)
    strength = forms.CharField(required=False, max_length=60)
    form = forms.ChoiceField(required=False, choices=())
    manufacturer = forms.CharField(required=False, max_length=120)
    prescription_only = forms.BooleanField(required=False)

    def __init__(self, *args, lang="ar", **kwargs):
        kwargs.setdefault("prefix", "drug")
        super().__init__(*args, **kwargs)
        self.lang = "en" if lang == "en" else "ar"
        words = WORDS[self.lang]
        self.fields["form"].choices = [("", "—"), *((value, services.FORM_LABELS[self.lang][value]) for value in DosageForm.values)]
        for name, label in (("active_ingredient", "ingredient"), ("strength", "strength"), ("form", "form"), ("manufacturer", "maker"), ("prescription_only", "rx")):
            self.fields[name].label = words[label]
        self.fields["active_ingredient"].help_text = words["ingredient_hint"]
        self.fields["active_ingredient"].widget.attrs.update({"list": "drug-ingredients", "autocomplete": "off"})
        self.fields["strength"].widget.attrs.update({"placeholder": words["strength_hint"], "autocomplete": "off", "dir": "auto"})
        self.fields["manufacturer"].widget.attrs.update({"autocomplete": "off"})
        self.fields["prescription_only"].help_text = words["rx_help"]
        self.reference_row = None
        self.item_initial = {}

    def clean_reference(self):
        """The reference row's id as an int, or None for anything else (as ``?ref=``)."""

        return reference_id(self.cleaned_data.get("reference"))

    def clean(self):
        cleaned = super().clean()
        given = any((cleaned.get("strength") or "").strip() or cleaned.get(name) for name in ("form", "manufacturer", "prescription_only"))
        if given and not (cleaned.get("active_ingredient") or "").strip():
            self.add_error("active_ingredient", services.MESSAGES[self.lang]["ingredient"])
        return cleaned

    @property
    def text_fields(self):
        return [self[name] for name in ("active_ingredient", "strength", "form", "manufacturer")]

    @property
    def words(self):
        return UI[self.lang]

    @property
    def ingredients(self):
        """The active ingredients already written, so the form offers one spelling (as on the drug page)."""

        return services.ingredients()

    @property
    def prefill(self):
        return as_json(self.reference_row, self.lang) if self.reference_row is not None else None

    def save_for(self, item, user):
        """Hand the typed drug data to :func:`services.save_drug` (same audit, same rules); nothing when it was left empty."""

        if not (self.cleaned_data.get("active_ingredient") or "").strip():
            return None
        data = {name: self.cleaned_data.get(name) for name in ("active_ingredient", "strength", "form", "manufacturer", "prescription_only")}
        return services.save_drug(item, data, user, self.lang)

    def attach(self, item_form):
        """Draw this part right after the item's main part (after sizes and colours when that trade has them).

        A new dict on the form itself: ``section_inserts`` is a class attribute shared by every form."""

        inserts = dict(getattr(item_form, "section_inserts", {}) or {})
        inserts["variant" if "main" in inserts else "main"] = "pharmacy/_item_drug.html"
        item_form.section_inserts = inserts


def for_new_item(request, lang="ar"):
    """The drug part of a new item, or None when the ``drugs`` capability is off or the person may not edit items.

    ``?ref=<id>`` (from «ضيفه كصنف» on the drug page) opens the form filled from that reference row; ``item_initial``
    then holds the item's name, barcode and suggested sale price. The prescription box is never ticked from the list."""

    if not services.enabled() or not services.can_edit(request.user):
        return None
    if request.method == "POST":
        fields = DrugFields(request.POST, lang=lang)
        fields.reference_row = reference_row(request.POST.get("drug-reference"))
        return fields
    row = reference_row(request.GET.get("ref"))
    if row is None:
        return DrugFields(lang=lang)
    fields = DrugFields(lang=lang, initial={"reference": row.pk, "active_ingredient": row.active_ingredient, "strength": row.strength,
                                            "form": row.form, "manufacturer": row.manufacturer})
    fields.reference_row = row
    name = (row.name_en or row.name_ar) if lang == "en" else (row.name_ar or row.name_en)
    fields.item_initial = {key: value for key, value in (("item_name", name), ("barcode", row.barcode), ("default_sale_price", row.public_price)) if value}
    return fields


# ---- «بدائل مش عندك» on the drug page ----

def unstocked_alternatives(item, limit=ALTERNATIVES_LIMIT):
    """Active reference rows with ``item``'s active ingredient that match no item the pharmacy has (by barcode, or by the
    folded English or Arabic name): the same strength first, then the others. One query; [] without drug data.

    Each row carries ``same`` (True for the same strength).

    Known limitation (kept on purpose in this step): "the pharmacy has it" means the same barcode or exactly the same
    folded name, so a stocked product spelt differently (another word order, a pack in the name) still shows here."""

    drug = getattr(item, "drug", None) if item is not None else None
    if drug is None or not drug.ingredient_key:
        return []
    barcodes = Item.objects.exclude(barcode="").values("barcode")
    names = Item.objects.annotate(folded=db_fold(F("item_name"))).values("folded")
    same = When(strength_key=drug.strength_key, then=Value(0)) if drug.strength_key else When(pk__isnull=True, then=Value(0))
    rows = (DrugReference.objects.filter(is_active=True, ingredient_key=drug.ingredient_key)
            .annotate(ar_key=db_fold(F("name_ar")), strength_rank=Case(same, default=Value(1), output_field=IntegerField()))
            .exclude(barcode__in=barcodes).exclude(name_key__in=names).exclude(Q(ar_key__in=names) & ~Q(name_ar=""))
            .order_by("strength_rank", "name_key", "pk")[:limit])
    rows = list(rows)
    for row in rows:
        row.same = row.strength_rank == 0
        row.stale = is_stale(row.price_date)
        row.price_month = month_label(row.price_date)
    return rows
