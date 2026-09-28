"""SHARE-001: a link the customer can open without an account.

The link carries a signed token (Django signing with the SECRET_KEY and its
own salt), so it cannot be guessed or edited to reach another document, and
it expires after SHARE_LINK_DAYS (default 60). Nothing is stored. The page is
read-only, shows what the printed document shows (never cost or profit) and a
cancelled document shows its "cancelled" watermark.
"""

from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.urls import reverse

from reports.aging import whatsapp_number


SALT = "hesba.share.v1"
KINDS = ("sales_invoice", "sales_return", "customer_payment", "customer_statement")


def max_age():
    return getattr(settings, "SHARE_LINK_DAYS", 60) * 86400


def make_token(kind, pk):
    if kind not in KINDS:
        raise ValueError(kind)
    return signing.dumps({"k": kind, "id": int(pk)}, salt=SALT, compress=True)


def read_token(token):
    """(kind, pk) or None for a forged, altered or expired token."""

    try:
        data = signing.loads(token, salt=SALT, max_age=max_age())
    except signing.BadSignature:
        return None
    kind, pk = data.get("k"), data.get("id")
    if kind not in KINDS or not isinstance(pk, int):
        return None
    return kind, pk


def share_url(request, kind, pk, lang="ar"):
    return request.build_absolute_uri(f"{reverse('share_document', args=[make_token(kind, pk)])}?lang={lang}")


def whatsapp_link(phone, text):
    number = whatsapp_number(phone)
    return f"https://wa.me/{number}?text={quote(text)}" if number else f"https://wa.me/?text={quote(text)}"
