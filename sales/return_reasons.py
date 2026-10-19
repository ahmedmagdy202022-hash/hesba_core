"""TRACK-ACTS W01: the one-click return reasons fit the shop.

"Wrong size" and "colour not right" are a clothes shop's reasons; a pharmacy returns
a wrong item or a near expiry, an electronics shop a faulty device. The reason stays
free text: these only fill the box.
"""

import json

from settings_core.models import ClientProfile

REASONS = {
    "fashion": (("المقاس مش مظبوط", "Wrong size"), ("فيه عيب", "Faulty"), ("اللون مش مناسب", "Colour not right"),
                ("العميل غيّر رأيه", "Customer changed their mind")),
    "pharmacy": (("صنف غلط", "Wrong item"), ("الصلاحية قريبة", "Close to expiry"), ("العلبة مفتوحة أو تالفة", "Pack opened or damaged"),
                 ("العميل غيّر رأيه", "Customer changed their mind")),
    "electronics": (("الجهاز فيه عطل", "Faulty device"), ("مش زي المواصفات", "Not as described"), ("ناقص منه حاجة", "Missing parts"),
                    ("العميل غيّر رأيه", "Customer changed their mind")),
    "grocery": (("صنف غلط", "Wrong item"), ("الصلاحية قريبة أو خلصت", "Near or past expiry"), ("العبوة تالفة", "Damaged pack"),
                ("العميل غيّر رأيه", "Customer changed their mind")),
    "wholesale": (("صنف غلط", "Wrong item"), ("كمية زيادة عن الطلب", "More than ordered"), ("بضاعة تالفة", "Damaged goods"),
                  ("العميل غيّر رأيه", "Customer changed their mind")),
}
GENERAL = (("صنف غلط", "Wrong item"), ("فيه عيب", "Faulty"), ("العميل غيّر رأيه", "Customer changed their mind"))


def return_reasons(lang="ar"):
    profile = ClientProfile.get_active()
    reasons = REASONS.get(getattr(profile, "sub_activity_slug", ""), GENERAL)
    return json.dumps([en if lang == "en" else ar for ar, en in reasons], ensure_ascii=False)
