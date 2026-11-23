"""SH-02 / ED-01: the one rule for a typed phone number, used by every form or service that takes one."""

PHONE_ERROR = {
    "ar": "اكتب رقم تليفون بأرقام بس (ينفع + في الأول ومسافات وشرطات وأقواس).",
    "en": "Type a phone number in digits only (a leading +, spaces, dashes and brackets are fine).",
}


def is_plain_phone(value):
    """True when ``value`` is digits (any script) with an optional leading +, spaces, dashes and brackets, and has a digit.

    An empty value is not judged here: each caller decides whether a phone is required.
    """

    body = (value or "").strip()
    body = body[1:] if body.startswith("+") else body
    return any(ch.isdecimal() for ch in body) and all(ch.isdecimal() or ch in " -()" for ch in body)
