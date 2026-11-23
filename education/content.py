"""E9 (HG-104): what a centre shares with one study group: recorded videos, files and the address of an online class.

Hesba keeps a title and an https address and nothing more: no upload, no storage, no video hosting, no player, no tracking of who opened
what, no message sent. The address is the only thing here that can hurt a reader (a guardian taps it on a phone), so one function,
``clean_url``, decides what is acceptable, and the views, the guardian portal and the tests all go through it.

Who: whoever may take the group's attendance (``attendance.can_take``: a manager, or the group's own teacher), exactly as the exam
screens decide it. The entity is the group's (``services.scoped``); a content row has no entity column of its own that could drift.
Nothing here moves money, stock, a ledger or a cashbox. Nothing is deleted: «remove» sets ``active`` off.
"""

import re
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from . import attendance as register
from . import services
from .models import ContentKind, Enrollment, EnrollmentStatus, GroupContent, StudyGroup

URL_MAX = 500  # the column's length
TITLE_MAX = 160
NOTE_MAX = 255
ONLINE_GRACE = timedelta(days=1)  # an online class that started more than this long ago is no longer shown to a guardian

HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
#: At least two dot-separated ASCII labels and a last label that is a real top-level name (letters, or punycode «xn--»). That refuses an
#: address typed as numbers («127.0.0.1», «192.168.1.5»), a bare name («localhost»), brackets («[::1]»), and any non-ASCII look-alike host.
HOST = re.compile(rf"^(?:{HOST_LABEL}\.)+(?:[a-z]{{2,63}}|xn--[a-z0-9-]{{1,59}})$")

MESSAGES = {
    "ar": {"forbidden": "محتوى المجموعة لإدارة المركز ولمدرس المجموعة بس.", "title": "اكتب عنوان المحتوى.", "title_long": "العنوان أطول من اللازم (160 حرف على الأكتر).",
           "kind": "اختار نوع المحتوى.", "note_long": "الملاحظة أطول من اللازم (255 حرف على الأكتر).", "closed": "المجموعة دي مقفولة.",
           "starts_at": "ميعاد الحصة مش مظبوط.", "gone": "المحتوى ده اتشال.",
           "url_empty": "اكتب الرابط.", "url_long": "الرابط أطول من اللازم (500 حرف على الأكتر).",
           "url_bad": "الرابط لازم يبدأ بـ https ويكون عنوان موقع حقيقي، من غير مسافات ولا اسم مستخدم."},
    "en": {"forbidden": "Group content is for the centre's managers and the group's own teacher only.", "title": "Enter the content's title.",
           "title_long": "The title is too long (160 characters at most).", "kind": "Choose the kind of content.",
           "note_long": "The note is too long (255 characters at most).", "closed": "This group is closed.",
           "starts_at": "The class time is not valid.", "gone": "This content was removed.",
           "url_empty": "Enter the link.", "url_long": "The link is too long (500 characters at most).",
           "url_bad": "The link must start with https:// and be a real website address, with no spaces and no user name."},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# ---- the link rules (the security core of E9) ----

def clean_url(raw, lang="ar"):
    """The address as it may be stored, or a ``ValidationError``.

    Accepted: ``https://`` and a real host (dotted ASCII labels, a letters-only last label), an optional port, any path, query and
    fragment, at most ``URL_MAX`` characters. Refused: every other scheme (``http:``, ``javascript:``, ``data:``, ``file:``…), an
    address with no scheme (``//host``, ``example.com``), ``https:host`` with no slashes, a user name or password
    (``user:pass@host``, or any ``@`` before the path), a backslash (a browser reads it as «/», this parser does not, so
    ``https://good.com\\@evil.com`` would mean two different hosts), a space, a control or invisible character anywhere (a bidi override
    can make an address read as another site in a right-to-left app), an IP address or a bare name as host, and a bad port.

    Surrounding whitespace is trimmed (it comes from pasting); whitespace inside is refused. Only the scheme is lower-cased: a path such
    as a video's id is case-sensitive."""

    words = _words(lang)
    text = str(raw or "").strip()
    if not text:
        raise ValidationError(words["url_empty"])
    if len(text) > URL_MAX:
        raise ValidationError(words["url_long"])
    for char in text:
        if char.isspace() or unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn") or char == "\\":
            raise ValidationError(words["url_bad"])
    if text[:8].lower() != "https://":
        raise ValidationError(words["url_bad"])
    text = "https://" + text[8:]
    try:
        parts = urlsplit(text)
        port = parts.port  # a non-numeric or out-of-range port raises here
        host = parts.hostname
    except ValueError:
        raise ValidationError(words["url_bad"])
    if not parts.netloc or "@" in parts.netloc or not host or not HOST.match(host):
        raise ValidationError(words["url_bad"])
    if port is not None and not 0 < port < 65536:
        raise ValidationError(words["url_bad"])
    return text


# ---- who, and what is in the entity ----

def can_edit(user, group):
    """The same people who may take the group's attendance and enter its exams: a manager, or the group's own teacher."""

    return register.can_take(user, group)


def scoped_content():
    """Every content row of the groups of the entity being worked in."""

    return GroupContent.objects.filter(group__in=services.scoped(StudyGroup.objects))


def group_items(group):
    """What the staff see for one group: every row not removed, newest first, hidden ones included."""

    return list(group.contents.filter(active=True).order_by("-created_at", "-pk"))


def local_parts(moment):
    """A start time as its local date and ``HH:MM`` (what both screens print), or ``(None, "")``."""

    if moment is None:
        return None, ""
    local = timezone.localtime(moment)
    return local.date(), local.strftime("%H:%M")


# ---- the changes (all by whoever ``can_edit``; each writes one audit row with no address in it) ----

def _snapshot(item):
    host = urlsplit(item.url).hostname or ""  # the host only: an address can carry a share key, and audit rows are read by more people
    return {"group": item.group_id, "kind": item.kind, "title": item.title, "host": host, "published": item.published, "active": item.active,
            "starts_at": item.starts_at.isoformat() if item.starts_at else None}


def _starts_at(raw, words):
    """A ``datetime-local`` box («2026-11-20T18:30») read in the centre's own time zone; blank is allowed (an online class with no time yet)."""

    text = str(raw or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValidationError(words["starts_at"])
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
    return moment


def _log(user, action, item, before):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if not before else AuditEventType.UPDATE, actor=user, module="education", action=action,
                            object_type="education.GroupContent", object_id=str(item.pk), before_data=before, after_data=_snapshot(item))


@transaction.atomic
def save_content(group, data, user, item=None, lang="ar"):
    """Add a content row to ``group``, or change ``item``. ``data``: kind, title, url, note, starts_at (an online class only).

    A new row needs a running group. A changed row must still be active (a removed one stays removed). The row's visibility is not
    touched here: a new row is published, and ``set_published`` is the only thing that changes it afterwards."""

    words = _words(lang)
    if not can_edit(user, group):
        raise PermissionDenied(words["forbidden"])
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError(words["title"])
    if len(title) > TITLE_MAX:
        raise ValidationError(words["title_long"])
    kind = data.get("kind") or ContentKind.VIDEO
    if kind not in ContentKind.values:
        raise ValidationError(words["kind"])
    note = (data.get("note") or "").strip()
    if len(note) > NOTE_MAX:
        raise ValidationError(words["note_long"])
    url = clean_url(data.get("url"), lang)
    starts_at = _starts_at(data.get("starts_at"), words) if kind == ContentKind.ONLINE else None
    if item is None:
        if not group.active:
            raise ValidationError(words["closed"])
        item, before = GroupContent(group=group, created_by=user), {}
    else:
        item = GroupContent.objects.select_for_update().select_related("group").get(pk=item.pk, group=group)
        if not item.active:
            raise ValidationError(words["gone"])
        before = _snapshot(item)
    item.kind, item.title, item.url, item.note, item.starts_at = kind, title, url, note, starts_at
    item.save()
    _log(user, "save_group_content", item, before)
    return item


@transaction.atomic
def set_published(item, published, user, lang="ar"):
    """Hide a row from guardians (or show it again). Staff still see it either way."""

    item = GroupContent.objects.select_for_update().select_related("group").get(pk=item.pk)
    if not can_edit(user, item.group):
        raise PermissionDenied(_words(lang)["forbidden"])
    if not item.active:
        raise ValidationError(_words(lang)["gone"])
    if item.published != bool(published):
        before = _snapshot(item)
        item.published = bool(published)
        item.save(update_fields=["published", "updated_at"])
        _log(user, "show_group_content" if published else "hide_group_content", item, before)
    return item


@transaction.atomic
def remove(item, user, lang="ar"):
    """Take a row out of every list (soft: the row stays, ``active`` goes off). Removing twice is not an error."""

    item = GroupContent.objects.select_for_update().select_related("group").get(pk=item.pk)
    if not can_edit(user, item.group):
        raise PermissionDenied(_words(lang)["forbidden"])
    if item.active:
        before = _snapshot(item)
        item.active = False
        item.save(update_fields=["active", "updated_at"])
        _log(user, "remove_group_content", item, before)
    return item


# ---- what a guardian may read ----

def for_children(kids, scope, now=None):
    """``{student id: [row, …]}`` for the guardian portal: the rows that are published and not removed, of the groups where that child has an
    ACTIVE enrolment (and that are themselves running, as the portal's «groups» card lists only those), inside ``scope`` (the link's entity).

    An online class that started more than a day ago is left out (a link to a class that is over is only noise; staff still see it); one with
    no start time stays. Two reads for all the children, never one per item. Each row's address is checked again by ``clean_url``: a row
    that fails it is not shown, whatever put it in the table. Online classes come first (the soonest first), then the rest, newest first."""

    now = now or timezone.now()
    kid_ids = [kid.pk for kid in kids]
    pairs = Enrollment.objects.filter(student_id__in=kid_ids, status=EnrollmentStatus.ACTIVE, group__in=scope, group__active=True).values_list("student_id", "group_id")
    groups_of = {}
    for student_id, group_id in pairs:
        groups_of.setdefault(student_id, set()).add(group_id)
    every = {group_id for ids in groups_of.values() for group_id in ids}
    stale = Q(kind=ContentKind.ONLINE, starts_at__lt=now - ONLINE_GRACE)
    items = list(GroupContent.objects.filter(group_id__in=every, published=True, active=True).exclude(stale).select_related("group").order_by("-created_at", "-pk")) if every else []
    ready = []
    for item in items:
        try:
            address = clean_url(item.url)
        except ValidationError:
            continue
        day, time = local_parts(item.starts_at)
        ready.append((item.group_id, {"kind": item.kind, "title": item.title, "note": item.note, "url": address, "group": item.group.name, "day": day, "time": time,
                                      "starts_at": item.starts_at}))
    out = {}
    for kid in kids:
        mine = [row for group_id, row in ready if group_id in groups_of.get(kid.pk, ())]
        online = sorted((row for row in mine if row["kind"] == ContentKind.ONLINE), key=lambda row: (row["starts_at"] is None, row["starts_at"] or now))
        out[kid.pk] = online + [row for row in mine if row["kind"] != ContentKind.ONLINE]
    return out
