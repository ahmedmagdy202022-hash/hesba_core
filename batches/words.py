"""AN8 (Ahmed, 18 Oct: "ليه فى تشغيلات فى محل بقالة او سوبر ماركت؟ ممكن تواريخ صلاحية لكن ليه تشغيلات؟").

A shop selling food keeps expiry dates, not batch numbers: a pharmacy or a factory reads the
lot printed on the box, a supermarket only reads "use by". For those shops the same records
are called expiry dates and the batch number is neither asked for nor shown. Display only:
a Batch row with a blank number is what they already were.
"""

#: (activity, sub-activity) that keep expiry dates only; ``None`` as the sub means every one of the activity.
EXPIRY_ONLY = {("commercial", "grocery"), ("commercial", "retail"), ("commercial", "online"), ("commercial", "wholesale"),
               ("restaurants", None)}

WORDS = {
    "ar": {
        "page_title": "تواريخ الصلاحية", "title": "تواريخ الصلاحية",
        "intro": "كل كمية داخلة بتاريخ صلاحيتها. الباقي من كل كمية محسوب من رصيد المخزون الفعلي على أساس إن الأقرب انتهاءً بيتباع الأول.",
        "search": "بحث بالكود أو الاسم", "uncovered": "كميات في المخزون من غير تاريخ صلاحية",
        "uncovered_hint": "غالبًا رصيد قبل تشغيل الخاصية؛ سجّل تاريخ صلاحيته من الفورم تحت.",
        "register": "تسجيل تاريخ صلاحية لرصيد موجود", "saved": "اتسجل تاريخ الصلاحية.", "need_one": "اكتب تاريخ الصلاحية.",
        "retired": "اتشال من المتابعة.", "summary_expired": "أصناف صلاحيتها خلصت ولسه في المخزن", "summary_soon": "أصناف صلاحيتها قربت تخلص",
        "empty": "مفيش أصناف بتاريخ صلاحية.", "include_finished": "اعرض اللي خلص كمان", "on_hand": "الصلاحية للي في المخزون",
        "capability": "تواريخ الصلاحية", "capability_about": "تاريخ صلاحية كل كمية داخلة، وتنبيه قبل ما تخلص.",
        "expired_alert": "{n} صنف صلاحيته خلصت ولسه في المخزون", "expired_detail": "شيله من الرف وراجع شاشة تواريخ الصلاحية.",
        "soon_alert": "{n} صنف صلاحيته قربت تخلص", "followup": "أصناف صلاحيتها قربت تخلص أو خلصت",
        "digest_expired": "أصناف منتهية الصلاحية",
    },
    "en": {
        "page_title": "Expiry dates", "title": "Expiry dates",
        "intro": "Every quantity received, with its expiry date. What is left of each comes from the real stock on hand, assuming what expires first is sold first.",
        "search": "Search code or name", "uncovered": "Stock on hand with no expiry date",
        "uncovered_hint": "Usually stock from before expiry dates were switched on; register its date with the form below.",
        "register": "Register an expiry date for stock on hand", "saved": "Expiry date registered.", "need_one": "Enter the expiry date.",
        "retired": "Removed from tracking.", "summary_expired": "Expired items still in stock", "summary_soon": "Items expiring soon",
        "empty": "No items with an expiry date.", "include_finished": "Include what has sold out", "on_hand": "Expiry dates on hand",
        "capability": "Expiry dates", "capability_about": "The expiry date of everything received, with a warning before it passes.",
        "expired_alert": "{n} items past their expiry date still in stock", "expired_detail": "Take them off the shelf and check the expiry dates screen.",
        "soon_alert": "{n} items expiring soon", "followup": "Items expiring soon or expired",
        "digest_expired": "Expired items",
    },
}


def expiry_only():
    """True for a shop that keeps expiry dates without batch numbers."""

    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    if profile is None:
        return False
    activity, sub = profile.activity_slug or "", profile.sub_activity_slug or ""
    return (activity, sub) in EXPIRY_ONLY or (activity, None) in EXPIRY_ONLY


def words(lang):
    """The expiry-only words for ``lang`` when this shop keeps expiry dates only, else {}."""

    return WORDS["en" if lang == "en" else "ar"] if expiry_only() else {}
