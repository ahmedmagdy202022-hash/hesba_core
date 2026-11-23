"""AN4 (Ahmed: "مينفعش حد يسجل بيع من غير ما يفتح وردية"): no sale is posted without an open shift.

The rule is the ``shift_required`` capability: on by default wherever the
activity has a till (it follows ``pos``), and the owner can switch it off in
Settings. It guards the screens that post a sale (the sales invoice and the
quick till); the services stay as they are, so imports, education fees and
the offline till's sync (rung up while a shift was open) are unaffected.
"""

from django.core.exceptions import ValidationError

from settings_core.capabilities import capability_enabled

from .services import open_shift_for


MESSAGE = {
    "ar": "افتح وردية الأول عشان تسجّل بيع.",
    "en": "Open a shift first to record a sale.",
}


def shift_missing(user):
    """True when this user may not post a sale yet: the rule is on and they have no open shift."""

    return capability_enabled("shift_required") and open_shift_for(user) is None


def require_open_shift(user, lang="ar"):
    if shift_missing(user):
        raise ValidationError(MESSAGE["en" if lang == "en" else "ar"])
