"""MFG-002: the stages a production order passes through, per kind of factory.

The sub-activity chosen at setup picks the list; the words are the ones the
shop floor uses. An order keeps the list it was opened with.
"""

STAGES = {
    "garments": (("cutting", "قص", "Cutting"), ("sewing", "خياطة", "Sewing"), ("finishing", "تشطيب وكي", "Finishing & ironing"),
                 ("quality", "فحص جودة", "Quality check"), ("packing", "تغليف", "Packing")),
    "food": (("prep", "تحضير وخلط", "Preparation & mixing"), ("cooking", "طهي / خَبز", "Cooking / baking"),
             ("cooling", "تبريد", "Cooling"), ("packing", "تعبئة وتغليف", "Filling & packing")),
    "furniture": (("cutting", "تقطيع الخشب", "Cutting"), ("assembly", "تجميع", "Assembly"), ("painting", "دهان", "Painting"),
                  ("finishing", "تشطيب وتنجيد", "Finishing & upholstery")),
    "printing": (("prepress", "تجهيز وتصميم", "Pre-press"), ("printing", "طباعة", "Printing"),
                 ("binding", "تقطيع وتجليد", "Cutting & binding"), ("packing", "تغليف", "Packing")),
    "chemicals": (("mixing", "خلط", "Mixing"), ("processing", "معالجة", "Processing"), ("quality", "فحص جودة", "Quality check"),
                  ("filling", "تعبئة", "Filling")),
}
DEFAULT = (("prep", "تجهيز", "Preparation"), ("making", "تصنيع", "Making"), ("quality", "فحص جودة", "Quality check"),
           ("packing", "تغليف", "Packing"))


def stages_for(sub_activity):
    return STAGES.get(sub_activity or "", DEFAULT)


def current_stages():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return stages_for(profile.sub_activity_slug if profile else "")
