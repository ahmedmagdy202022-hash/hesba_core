"""CAP-001: business capabilities, suggested per activity and switchable later.

A *module* is a whole section of Hesba (sales, purchases, inventory…). A
*capability* is a way of working inside those sections that only some shops
need: a quick till, barcode labels, wholesale price lists, sizes and colours,
batches and expiry dates, serial numbers, instalments, e-invoicing…

Hesba does not ship one program per trade. Every capability is built once, for
every commercial activity, and the chosen sub-activity only decides which ones
start switched on (``SUGGESTED``). The owner confirms or changes them at the
end of setup and can switch any of them later from Settings; nothing is
deleted when one is switched off.

Capabilities marked ``available=False`` are on the roadmap: the catalog lists
them so setup can show what is coming, but they cannot be switched on and have
no screens yet.

Storage: one ``FeatureFlag`` row per capability, ``capability.<slug>``. An
installation made before capabilities existed has no rows; there the ones it
already had (``legacy_on``) count as on, so upgrading never hides a screen
someone uses, and newer ones stay off until switched on.
"""

from django.db import transaction

from audit.models import AuditEventType, AuditLog

from . import setup_catalog as catalog
from .models import ClientProfile, FeatureFlag


FLAG_PREFIX = "capability."
SUGGESTED = catalog.SUGGESTED
OPTIONAL = catalog.OPTIONAL

#: Declaration order is display order. ``paths`` are the URL prefixes the
#: capability owns; they close when it is off (see module_gate).
CAPABILITIES = {
    "pos": {
        "ar": "الكاشير السريع", "en": "Quick till (POS)",
        "about_ar": "شاشة بيع سريعة بالباركود، وحساب الباقي، وإيصال 80mm.",
        "about_en": "A fast sales screen with barcode scanning, change due and an 80mm receipt.",
        "available": True, "legacy_on": True, "paths": ("/sales/pos/",),
    },
    "shift_required": {
        "ar": "وردية قبل البيع", "en": "Shift before selling",
        "about_ar": "مفيش بيع يتسجل (فاتورة أو كاشير) غير لما الكاشير يفتح وردية، عشان درج كل واحد يتحاسب لوحده.",
        "about_en": "No sale is recorded (invoice or till) until the cashier opens a shift, so each drawer is counted on its own.",
        "available": True, "paths": (),
    },
    "barcode": {
        "ar": "الباركود والملصقات", "en": "Barcodes & labels",
        "about_ar": "توليد باركود للأصناف وطباعة ملصقات الأسعار.",
        "about_en": "Generate item barcodes and print price labels.",
        "available": True, "legacy_on": True, "paths": ("/barcode/",),
    },
    "price_lists": {
        "ar": "قوائم الأسعار (قطاعي / جملة)", "en": "Price lists (retail / wholesale)",
        "about_ar": "أكتر من سعر للصنف، وسعر خاص لكل عميل أو فئة عملاء.",
        "about_en": "More than one price per item, and special prices per customer or customer group.",
        "available": True, "paths": ("/pricing/",),
    },
    "quotations": {
        "ar": "عروض الأسعار وأوامر البيع", "en": "Quotations & sales orders",
        "about_ar": "عرض سعر تطبعه أو تبعته واتساب، ولما العميل يوافق يبقى أمر بيع تفوتره مرة واحدة أو على دفعات، وإذن تسليم مع كل فاتورة.",
        "about_en": "A quotation you print or send on WhatsApp; once accepted it becomes a sales order you invoice in one go or in parts, with a delivery note for each invoice.",
        "available": True, "paths": ("/orders/",),
    },
    "online_store": {
        "ar": "المتجر الأونلاين (بالملفات)", "en": "Online store (by file)",
        "about_ar": "نزّل الأصناف بملف Shopify أو WooCommerce وارفعه في المتجر، وارفع ملف طلبات المتجر هنا تبقى فواتير مسودة. الربط المباشر محتاج حساب المتجر.",
        "about_en": "Download the items as a Shopify or WooCommerce file for the store, and upload the store's orders file here as draft invoices. A live link needs the store's account.",
        "available": True, "paths": ("/store/",),
    },
    "units": {
        "ar": "وحدات القياس", "en": "Units of measure",
        "about_ar": "بيع وشراء بالكرتونة أو العلبة أو القطعة مع تحويل تلقائي.",
        "about_en": "Buy and sell by carton, box or piece with automatic conversion.",
        "available": True, "paths": ("/units/",),
    },
    "variants": {
        "ar": "المقاسات والألوان", "en": "Sizes & colours",
        "about_ar": "صنف واحد بمقاسات وألوان، وكل واحد له مخزونه وباركوده.",
        "about_en": "One item in several sizes and colours, each with its own stock and barcode.",
        "available": True, "paths": ("/variants/",),
    },
    "batches_expiry": {
        "ar": "التشغيلات وتاريخ الصلاحية", "en": "Batches & expiry dates",
        "about_ar": "متابعة كل تشغيلة وتاريخ انتهائها، وتنبيه قبل الانتهاء.",
        "about_en": "Track each batch and its expiry date, with alerts before it expires.",
        "available": True, "paths": ("/batches/",),
    },
    "serials": {
        "ar": "السيريال / IMEI والضمان", "en": "Serial / IMEI & warranty",
        "about_ar": "تتبّع كل قطعة برقمها من الشراء للبيع، وفترة الضمان.",
        "about_en": "Track every unit by its number from purchase to sale, with its warranty.",
        "available": True, "paths": ("/serials/",),
    },
    "drugs": {
        "ar": "قاعدة الأدوية والروشتات", "en": "Drug database & prescriptions",
        "about_ar": "المادة الفعالة والتركيز لكل دوا، والبدائل اللي عندك في المخزون، وصرف الروشتة بفاتورة بيع، وتجديد الروشتات المزمنة.",
        "about_en": "The active ingredient and strength of each drug, the alternatives you have in stock, dispensing a prescription on a sales invoice, and chronic refills.",
        "available": True, "paths": ("/pharmacy/",),
    },
    "installments": {
        "ar": "البيع بالتقسيط", "en": "Instalment sales",
        "about_ar": "جدول أقساط للعميل، ومواعيد استحقاق، وتذكير بالتحصيل.",
        "about_en": "An instalment schedule per customer, due dates and collection reminders.",
        "available": True, "paths": ("/instalments/",),
    },
    "vat": {
        "ar": "ضريبة القيمة المضافة على الأصناف", "en": "VAT on items",
        "about_ar": "نسبة ضريبة لكل صنف (14٪ أو معفى)، تتحسب تلقائيًا في الفاتورة والكاشير، وتقرير ضريبة المبيعات.",
        "about_en": "A tax rate per item (14% or exempt), charged automatically on invoices and at the till, with a sales VAT report.",
        "note_ar": "الأفضل تشغّلها أو تقفلها من أول الشهر: الفواتير اللي اتعملت قبل التغيير بتفضل بالضريبة اللي اتحسبت وقتها، فالإقرار بتاع الشهر يبقى مخلوط.",
        "note_en": "Best switched on or off at the start of a month: invoices made before the change keep the tax they were charged, so that month's VAT return would be mixed.",
        "available": True, "paths": ("/taxes/",),
    },
    "e_invoice": {
        "ar": "الفاتورة الإلكترونية (مصلحة الضرائب)", "en": "E-invoicing (Egyptian Tax Authority)",
        "about_ar": "بيانات الشركة والعملاء وأكواد الأصناف، وملف كل فاتورة بصيغة المنظومة مع فحص الناقص. التوقيع والإرسال مرحلة جاية.",
        "about_en": "Company, customer and item-code data, and each invoice's document in the portal format with a check of what is missing. Signing and sending come next.",
        "available": True, "paths": ("/einvoice/",),
    },
    "fixed_assets": {
        "ar": "الأصول الثابتة والإهلاك", "en": "Fixed assets & depreciation",
        "about_ar": "سجل للأصول (عربيات، أجهزة، ديكور) وإهلاك شهري يدخل في صافي الربح.",
        "about_en": "An asset register (vehicles, equipment, fit-out) with monthly depreciation in net profit.",
        "available": True, "paths": ("/assets/",),
    },
}
CAPABILITY_SLUGS = tuple(CAPABILITIES)

#: Which capabilities start on for each commercial sub-activity. Anything not
#: listed is OPTIONAL. Services activities suggest none of the trade ones.
PRESETS = {
    "retail": {"pos": SUGGESTED, "barcode": SUGGESTED},
    "grocery": {"pos": SUGGESTED, "barcode": SUGGESTED, "units": SUGGESTED, "batches_expiry": SUGGESTED},
    "fashion": {"pos": SUGGESTED, "barcode": SUGGESTED, "variants": SUGGESTED},
    "electronics": {"pos": SUGGESTED, "barcode": SUGGESTED, "serials": SUGGESTED, "installments": SUGGESTED},
    "pharmacy": {"pos": SUGGESTED, "barcode": SUGGESTED, "batches_expiry": SUGGESTED, "units": SUGGESTED, "drugs": SUGGESTED},
    "wholesale": {"barcode": SUGGESTED, "price_lists": SUGGESTED, "units": SUGGESTED, "vat": SUGGESTED, "e_invoice": SUGGESTED, "quotations": SUGGESTED},
    "online": {"barcode": SUGGESTED, "variants": SUGGESTED},
    "other": {"pos": SUGGESTED, "barcode": SUGGESTED},
}


def flag_code(slug):
    return f"{FLAG_PREFIX}{slug}"


def label(slug, lang="ar"):
    if slug == "batches_expiry":
        from batches.words import words as expiry_words

        named = expiry_words(lang).get("capability")  # AN8: a supermarket keeps expiry dates, not batches
        if named:
            return named
    entry = CAPABILITIES.get(slug) or {}
    return entry.get(lang) or catalog.fallback_label(slug)


def is_available(slug):
    return bool(CAPABILITIES.get(slug, {}).get("available"))


#: MFG-002: factories by kind. Garments come in sizes and colours; food and
#: chemicals carry batches with expiry dates; furniture and printing sell by
#: piece with barcodes. VAT stays a choice made at setup.
MANUFACTURING_PRESETS = {
    "garments": {"variants": SUGGESTED, "barcode": SUGGESTED, "units": SUGGESTED, "price_lists": SUGGESTED},
    "food": {"batches_expiry": SUGGESTED, "units": SUGGESTED, "barcode": SUGGESTED, "price_lists": SUGGESTED},
    "chemicals": {"batches_expiry": SUGGESTED, "units": SUGGESTED, "price_lists": SUGGESTED},
    "furniture": {"barcode": SUGGESTED, "price_lists": SUGGESTED, "quotations": SUGGESTED},
    "printing": {"units": SUGGESTED, "price_lists": SUGGESTED, "quotations": SUGGESTED},
    "workshop": {"barcode": SUGGESTED, "quotations": SUGGESTED},
    "other": {"units": SUGGESTED, "price_lists": SUGGESTED},
}


#: AUDIT-3: the other activities get their own suggestions too ("*" applies to
#: every sub-activity of the activity, a sub-activity entry adds to it).
ACTIVITY_PRESETS = {
    catalog.RESTAURANTS: {
        "*": {"units": SUGGESTED},
        "cafe": {"pos": SUGGESTED, "variants": SUGGESTED},
        "fast_food": {"pos": SUGGESTED},
        "bakery": {"pos": SUGGESTED, "barcode": SUGGESTED, "batches_expiry": SUGGESTED},
        "cloud_kitchen": {"pos": SUGGESTED},
    },
    catalog.MEDICAL: {
        "dental": {"installments": SUGGESTED},
        "medical_center": {"fixed_assets": SUGGESTED, "batches_expiry": SUGGESTED, "drugs": SUGGESTED},
        "lab": {"batches_expiry": SUGGESTED, "fixed_assets": SUGGESTED},
        "vet": {"batches_expiry": SUGGESTED, "pos": SUGGESTED},
    },
    catalog.EDUCATION: {
        "*": {"installments": SUGGESTED},
    },
    # VAT and e-invoicing change what a bill adds up to, so they stay the
    # owner's own choice (not every contractor or office is VAT-registered).
    catalog.CONTRACTING: {
        "*": {"units": SUGGESTED, "fixed_assets": SUGGESTED},
    },
    catalog.SERVICES: {
        "maintenance": {"serials": SUGGESTED},
        "beauty": {"pos": SUGGESTED},
        "professional": {"quotations": SUGGESTED},
        "digital_marketing": {"quotations": SUGGESTED},
    },
    catalog.OTHER: {
        "*": {"pos": SUGGESTED, "barcode": SUGGESTED},
    },
}

#: AUDIT-3: capabilities that make no sense for an activity are not offered to
#: it at all (a builder never needs a quick till or sizes and colours). One that
#: is already on still shows in Settings, so it can be switched off.
NOT_FOR = {
    catalog.CONTRACTING: {"pos", "shift_required", "barcode", "variants", "serials", "batches_expiry", "installments", "price_lists", "quotations", "online_store"},
    catalog.EDUCATION: {"pos", "shift_required", "barcode", "variants", "serials", "batches_expiry", "units", "quotations", "online_store"},
    catalog.MEDICAL: {"variants", "serials", "quotations", "online_store"},
    catalog.RESTAURANTS: {"serials", "installments", "quotations"},
    catalog.SERVICES: {"variants", "batches_expiry"},
}

#: Wording that fits the activity, where the general wording does not.
ABOUT_FOR = {
    (catalog.CONTRACTING, "units"): ("الكميات بالمتر والمتر المربع والمكعب والطن والشيكارة، مع تحويل تلقائي.",
                                     "Quantities in metres, m², m³, tonnes and bags, with automatic conversion."),
    (catalog.CONTRACTING, "fixed_assets"): ("سجل المعدات والعربيات والسقالات، وإهلاكها الشهري يدخل في صافي الربح.",
                                            "A register of equipment, vehicles and scaffolding, with monthly depreciation in net profit."),
    (catalog.EDUCATION, "installments"): ("مصروفات الطالب على أقساط بمواعيد استحقاق وتذكير بالتحصيل.",
                                          "Student fees in instalments with due dates and collection reminders."),
    (catalog.MEDICAL, "installments"): ("خطة علاج على أقساط (زي تقويم الأسنان) بمواعيد استحقاق.",
                                        "A treatment plan paid in instalments (orthodontics, say) with due dates."),
    (catalog.RESTAURANTS, "units"): ("شراء الخامات بالكيلو والكرتونة وصرفها بالجرام والقطعة.",
                                     "Buy ingredients by the kilo or carton and use them by the gram or piece."),
    (catalog.RESTAURANTS, "variants"): ("أحجام المشروب (صغير، وسط، كبير) كل واحد بسعره.",
                                        "Drink sizes (small, medium, large), each at its own price."),
    (catalog.SERVICES, "serials"): ("رقم كل جهاز داخل للصيانة وضمانه، من الاستلام للتسليم.",
                                    "Each device in for repair by its serial number and warranty, from intake to hand-back."),
}


#: SETTINGS-TRACK: what a sub-activity never needs, on top of its activity's NOT_FOR (Ahmed, 20 Oct: "مش كل الأنشطة
#: ينفع نربط التشغيلات بتواريخ الصلاحية"). A clothes shop has no expiry dates, a pharmacy sells no sizes and colours,
#: an office has nothing to scan. As with NOT_FOR, one that is already on still shows, so it can be switched off.
NOT_FOR_SUB = {
    (catalog.COMMERCIAL, "grocery"): {"serials", "variants", "installments", "quotations"},
    (catalog.COMMERCIAL, "fashion"): {"batches_expiry", "serials"},
    (catalog.COMMERCIAL, "electronics"): {"batches_expiry"},
    (catalog.COMMERCIAL, "pharmacy"): {"serials", "variants", "installments", "quotations"},
    (catalog.SERVICES, "general"): {"serials", "units", "barcode"},
    (catalog.SERVICES, "clinic"): {"serials", "units", "barcode", "quotations"},
    (catalog.SERVICES, "beauty"): {"serials", "units", "quotations"},
    (catalog.SERVICES, "education"): {"serials", "units", "barcode", "pos", "shift_required", "quotations"},
    (catalog.SERVICES, "professional"): {"serials", "units", "barcode", "pos", "shift_required"},
    (catalog.SERVICES, "digital_marketing"): {"serials", "units", "barcode", "pos", "shift_required"},
    (catalog.SERVICES, "other"): {"serials", "units"},
    (catalog.MEDICAL, "clinic"): {"barcode", "units"},
    (catalog.MEDICAL, "dental"): {"barcode", "units"},
    (catalog.MEDICAL, "physio"): {"barcode", "units", "batches_expiry"},
    (catalog.MEDICAL, "lab"): {"barcode"},
    (catalog.RESTAURANTS, "restaurant"): {"barcode"},
    (catalog.RESTAURANTS, "fast_food"): {"barcode"},
    (catalog.RESTAURANTS, "cloud_kitchen"): {"barcode"},
    (catalog.MANUFACTURING, "food"): {"variants", "serials", "installments"},
    (catalog.MANUFACTURING, "garments"): {"batches_expiry", "serials", "installments"},
    (catalog.MANUFACTURING, "chemicals"): {"variants", "serials", "installments", "pos", "shift_required"},
    (catalog.MANUFACTURING, "furniture"): {"batches_expiry"},
    (catalog.MANUFACTURING, "printing"): {"batches_expiry", "variants", "serials", "installments", "pos", "shift_required"},
    (catalog.MANUFACTURING, "workshop"): {"batches_expiry", "variants", "pos", "shift_required"},
}

#: T10: a capability offered only to these (activity, sub-activity) pairs, "*" for every sub-activity of the activity.
#: A drug database is a pharmacy's (or a medical centre's own pharmacy's); a shop or a café never needs one.
ONLY_FOR = {
    "drugs": {(catalog.COMMERCIAL, "pharmacy"), (catalog.MEDICAL, "*")},
}

#: SETTINGS-TRACK: a capability that rides on a module works only while that module is on: expiry dates and serial
#: numbers follow the stock, instalments and e-invoices belong to a customer. Switching one on switches its module on
#: too; switching the module off pauses it (its flag is kept, so it comes back with the module).
NEEDS = {
    "batches_expiry": ("inventory",),
    "serials": ("inventory",),
    "installments": ("customers",),
    "e_invoice": ("customers",),
    "quotations": ("customers",),
    "drugs": ("inventory",),
}

#: ... and what it works best with, said on its card but never enforced.
WORKS_BEST_WITH = {
    "batches_expiry": ("purchases",),
    "serials": ("purchases",),
    "units": ("purchases",),
    "price_lists": ("customers",),
}


#: SETTINGS-DESIGN: how the features screen groups them, the icon of each, and where each one shows once on.
#: "Where" is what the settings track saw appear when the feature was switched on.
GROUPS = (
    ("selling", ("البيع والكاشير", "Selling and the till"), ("pos", "shift_required", "price_lists", "installments", "quotations", "online_store")),
    ("goods", ("الأصناف والمخزون", "Items and stock"), ("barcode", "units", "variants", "batches_expiry", "serials", "drugs")),
    ("money", ("الحسابات والضرايب", "Accounts and tax"), ("vat", "e_invoice", "fixed_assets")),
)
ICONS = {"pos": "pos", "shift_required": "shift", "price_lists": "pricetag", "installments": "instalments", "barcode": "barcode",
         "units": "units", "variants": "variants", "batches_expiry": "expiry", "serials": "serial", "vat": "taxes",
         "e_invoice": "einvoice", "fixed_assets": "assets", "quotations": "quotes", "drugs": "drugs", "online_store": "store"}
WHERE = {
    "pos": ("«الكاشير» في القايمة، وزرار في لوحة القيادة وفي قايمة الفواتير", "“Till” in the menu, with a button on the dashboard and the invoice list"),
    "shift_required": ("فاتورة البيع والكاشير بيطلبوا فتح وردية الأول", "The sales invoice and the till ask for an open shift first"),
    "barcode": ("«طباعة الملصقات» في الأصناف، وبعد كل فاتورة شراء", "“Print labels” on the items list, and after each purchase invoice"),
    "price_lists": ("«قوائم الأسعار» في الأصناف، وسعر العميل في الفاتورة", "“Price lists” on the items list, and the customer's price on the invoice"),
    "units": ("خانة «الوحدة» في فواتير البيع والشراء، و«الوحدات» في الأصناف", "A “Unit” column on sales and purchase invoices, and “Units” on the items list"),
    "variants": ("«مقاسات وألوان» في فورم الصنف، وشاشة المقاسات والألوان", "“Sizes and colours” on the item form, and its own screen"),
    "batches_expiry": ("تاريخ الصلاحية في فاتورة الشراء، وشاشة في المخزون، وتنبيه في لوحة القيادة", "The expiry date on purchase invoices, a screen under stock, and a dashboard alert"),
    "serials": ("خانة السيريال في فواتير البيع والشراء، وشاشة في المخزون", "A serial number field on sales and purchase invoices, and a screen under stock"),
    "installments": ("«الأقساط» في المبيعات، وكارت في لوحة القيادة", "“Instalments” under sales, and a dashboard card"),
    "quotations": ("«عروض الأسعار والأوامر» في القايمة، وزرار «اعمل فاتورة بالباقي» على كل أمر", "“Quotations & orders” in the menu, and “Invoice what is left” on each order"),
    "online_store": ("«المتجر الأونلاين» في القايمة: ملف الأصناف للمتجر، ورفع ملف الطلبات", "“Online store” in the menu: the items file for the store, and the orders upload"),
    "drugs": ("«الأدوية والبدائل» في القايمة، وزرار «اصرف الروشتة» اللي بيفتح فاتورة البيع", "“Drugs & alternatives” in the menu, and “Dispense” opening the sales invoice"),
    "vat": ("«الضرايب» في القايمة، وضريبة كل صنف، وتقرير ضريبة المبيعات", "“Taxes” in the menu, a rate per item, and the sales VAT report"),
    "e_invoice": ("«الفاتورة الإلكترونية» في القايمة، وملف لكل فاتورة مترحّلة", "“E-invoicing” in the menu, and a document for each posted invoice"),
    "fixed_assets": ("«الأصول الثابتة» في القايمة، والإهلاك في المصروفات وصافي الربح", "“Fixed assets” in the menu, and depreciation in expenses and net profit"),
}


def group_of(slug):
    return next((key for key, _, slugs in GROUPS if slug in slugs), "money")


def fits(activity, slug, sub_activity=""):
    """Whether ``slug`` is offered to this activity (and sub-activity) at all."""

    activity, sub_activity = catalog.closest(activity, sub_activity)
    if slug in NOT_FOR.get(activity, set()):
        return False
    if slug == "shift_required" and "pos" in NOT_FOR_SUB.get((activity, sub_activity), set()):
        return False
    only = ONLY_FOR.get(slug)
    if only is not None and (activity, sub_activity) not in only and (activity, "*") not in only:
        return False
    return slug not in NOT_FOR_SUB.get((activity, sub_activity), set())


def needs(slug):
    return NEEDS.get(slug, ())


def _modules_on():
    """The modules switched on, or None while setup is not finished (then nothing is closed, as in module_gate)."""

    profile = ClientProfile.get_active()
    if profile is None or not profile.setup_is_complete:
        return None
    from .setup_services import enabled_modules

    return set(enabled_modules())


def paused_by(slug, modules_on=None):
    """The needed modules that are off, which pause ``slug`` while its own switch stays on."""

    on = _modules_on() if modules_on is None else modules_on
    if on is None:
        return ()
    return tuple(module for module in needs(slug) if module not in on)


def about(activity, slug, lang="ar"):
    if slug == "batches_expiry":
        from batches.words import words as expiry_words

        about_text = expiry_words(lang).get("capability_about")
        if about_text:
            return about_text
    override = ABOUT_FOR.get((activity, slug))
    if override:
        return override[1] if lang == "en" else override[0]
    return CAPABILITIES[slug][f"about_{lang}"]


def preset_state(activity, sub_activity, slug):
    if slug == "shift_required":  # AN4: wherever there is a till, a sale waits for an open shift
        slug = "pos"
    activity, sub_activity = catalog.closest(activity, sub_activity)  # OTHER-001: "closer to services" is preset as services
    if not fits(activity, slug, sub_activity):
        return OPTIONAL
    if activity == catalog.MANUFACTURING:
        return MANUFACTURING_PRESETS.get(sub_activity, {}).get(slug, OPTIONAL)
    if activity in ACTIVITY_PRESETS:
        presets = ACTIVITY_PRESETS[activity]
        return presets.get(sub_activity, {}).get(slug) or presets.get("*", {}).get(slug, OPTIONAL)
    if activity != catalog.COMMERCIAL:
        return OPTIONAL
    return PRESETS.get(sub_activity, {}).get(slug, OPTIONAL)


def suggested(activity, sub_activity):
    """Every capability the activity suggests, shipped or not, in display order."""

    return tuple(slug for slug in CAPABILITY_SLUGS if preset_state(activity, sub_activity, slug) == SUGGESTED)


def default_selection(activity, sub_activity):
    """What setup pre-ticks: the suggested capabilities that exist today."""

    return tuple(slug for slug in suggested(activity, sub_activity) if is_available(slug))


def parse(raw):
    """A submitted comma-separated list, keeping only switchable capabilities."""

    submitted = {part.strip() for part in (raw or "").split(",") if part.strip()}
    return tuple(slug for slug in CAPABILITY_SLUGS if slug in submitted and is_available(slug))


def switched_on():
    """Capabilities whose own switch is on, whatever their modules."""

    rows = dict(FeatureFlag.objects.filter(code__startswith=FLAG_PREFIX).values_list("code", "enabled"))
    return tuple(
        slug for slug in CAPABILITY_SLUGS
        # No row yet: an installation from before CAP-001, where the
        # capabilities it already had (legacy_on) stay on and new ones stay off.
        if is_available(slug) and rows.get(flag_code(slug), bool(CAPABILITIES[slug].get("legacy_on")))
    )


def enabled_capabilities():
    """Capabilities at work: switched on, and (SETTINGS-TRACK) the modules they need on too."""

    on = switched_on()
    if not any(needs(slug) for slug in on):
        return on
    modules = _modules_on()
    if modules is None:
        return on
    return tuple(slug for slug in on if not paused_by(slug, modules))


def capability_enabled(slug):
    return slug in enabled_capabilities()


def closed_capability(path):
    """The capability that owns `path` and is switched off, else None."""

    owner = next((slug for slug in CAPABILITY_SLUGS for prefix in CAPABILITIES[slug]["paths"] if path.startswith(prefix)), None)
    if owner is None:
        return None
    profile = ClientProfile.get_active()
    if profile is None or not profile.setup_is_complete:
        return None
    return None if capability_enabled(owner) else owner


def _write(slug, enabled):
    FeatureFlag.objects.update_or_create(
        code=flag_code(slug),
        defaults={"name": label(slug, "en"), "description": label(slug, "ar"), "enabled": enabled},
    )


def apply_setup_choice(activity, sub_activity, raw=None):
    """Store the capabilities chosen at setup. ``raw=None`` means use the preset."""

    chosen = set(default_selection(activity, sub_activity) if raw is None else parse(raw))
    for slug in CAPABILITY_SLUGS:
        if is_available(slug):
            _write(slug, slug in chosen)
    return tuple(slug for slug in CAPABILITY_SLUGS if slug in chosen)


class CapabilityChangeRefused(ValueError):
    """A capability switch that is not allowed."""


@transaction.atomic
def set_capability_enabled(profile, slug, enabled, user=None, switched_modules=None):
    """Switch ``slug``. Switching it on also switches on the modules it needs (SETTINGS-TRACK), listed into ``switched_modules``."""

    if profile is None or not profile.setup_is_complete:
        raise CapabilityChangeRefused("Finish setup before changing capabilities.")
    if slug not in CAPABILITIES:
        raise CapabilityChangeRefused(f"Unknown capability: {slug!r}")
    if not is_available(slug):
        raise CapabilityChangeRefused(f"{slug} is not available yet.")
    turned_on = []
    if enabled:
        from .setup_services import module_is_enabled, set_module_enabled

        for module in needs(slug):
            if not module_is_enabled(module):
                set_module_enabled(profile, module, True, user)
                turned_on.append(module)
    if switched_modules is not None:
        switched_modules.extend(turned_on)
    was = slug in switched_on()
    if was == enabled:
        return bool(turned_on)
    _write(slug, enabled)
    AuditLog.objects.create(
        event_type=AuditEventType.UPDATE,
        actor=user if user is not None and user.is_authenticated else None,
        module="settings",
        action="enable_capability" if enabled else "disable_capability",
        object_type="settings_core.FeatureFlag",
        object_id=flag_code(slug),
        before_data={"enabled": was},
        after_data={"enabled": enabled},
        reason="Capability switched from the settings screen.",
    )
    return True


def settings_rows(profile, lang="ar"):
    switched = set(switched_on())
    modules = _modules_on()
    activity, sub_activity = catalog.closest(profile.activity_slug if profile else "", profile.sub_activity_slug if profile else "")
    from .activity_fit import module_text

    def name(module):  # in the activity's own words: a clinic's customers are its patients
        return module_text(activity, sub_activity, module, lang)[0]

    rows = []
    for slug in CAPABILITY_SLUGS:
        entry = CAPABILITIES[slug]
        if not fits(activity, slug, sub_activity) and slug not in switched:
            continue
        paused = paused_by(slug, modules) if slug in switched and modules is not None else ()
        if not entry["available"]:
            state = "soon"
        elif slug in switched:
            state = "paused" if paused else "on"
        else:
            state = "off"
        rows.append({
            "slug": slug,
            "label": label(slug, lang),
            "about": about(activity, slug, lang),
            "state": state,
            "suggested": preset_state(activity, sub_activity, slug) == SUGGESTED,
            "note": entry.get(f"note_{lang}", ""),
            "needs": [{"slug": module, "label": name(module), "on": modules is None or module in modules} for module in needs(slug)],
            "works_with": [{"slug": module, "label": name(module), "on": modules is None or module in modules}
                           for module in WORKS_BEST_WITH.get(slug, ())],
            "paused_by": [name(module) for module in paused],
            "fits": fits(activity, slug, sub_activity),
            "icon": ICONS.get(slug, "settings"),
            "where": WHERE.get(slug, ("", ""))[1 if lang == "en" else 0],
            "group": group_of(slug),
        })
    return rows


def grouped_rows(rows, lang="ar"):
    """SETTINGS-DESIGN: the features screen's rows by group, in the groups' order."""

    index = 1 if lang == "en" else 0
    by_slug = {row["slug"]: row for row in rows}
    return [{"key": key, "title": titles[index], "rows": [by_slug[slug] for slug in slugs if slug in by_slug],
             "on": sum(1 for slug in slugs if slug in by_slug and by_slug[slug]["state"] == "on")}
            for key, titles, slugs in GROUPS if any(slug in by_slug for slug in slugs)]
