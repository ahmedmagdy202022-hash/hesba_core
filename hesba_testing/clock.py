"""TEST-002: keep the test run on ONE Cairo calendar day.

Dozens of test modules compute ``TODAY = timezone.localdate()`` when they are
imported.  A run that starts shortly before Cairo midnight and is still going
after it would then see the services use the new date while the tests keep the
old constant (warranty dates off by one, "tomorrow" suddenly being today...).

``install()`` pins the *date*, not the time: if the real clock has moved on by
N whole Cairo days since the run started, ``django.utils.timezone.now()`` is
shifted back by N days.  Time of day keeps moving forward, so ordering and
``auto_now`` still behave; only the single jump back at midnight is visible.

How: ``django.utils.timezone.now()`` is ``datetime.now(tz=utc)`` with
``datetime`` read from that module's globals at call time.  Swapping that one
global covers every caller at once - ``timezone.localdate()``/``localtime()``,
``auto_now`` fields, ``default=timezone.now`` (which holds a reference to the
original function) and ``now().date()`` in services - and any test that does
``mock.patch("django.utils.timezone.now"/"localdate")`` still layers on top.
"""

from datetime import datetime as _real_datetime
from datetime import timedelta
from datetime import timezone as _utc
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone as dj_timezone

_state = {"anchor": None, "pin": True, "installed": None, "tz": None}


def _system_clock(tz=None):
    return _real_datetime.now(tz=tz)


# The "real" clock.  Module-level so the proof test can simulate a run that
# crosses midnight without waiting for one.
_clock = _system_clock


def pinned_now(real):
    """``real`` (aware, any zone) moved back by whole days since the anchor."""

    anchor, tz = _state["anchor"], _state["tz"]
    if anchor is None or not _state["pin"]:
        return real
    local = real.astimezone(tz)
    days = (local.date() - anchor).days
    if days <= 0:
        return real
    wall = local.replace(tzinfo=None) - timedelta(days=days)
    return wall.replace(tzinfo=tz).astimezone(real.tzinfo)


class _DatetimeMeta(type):
    """Lets ``isinstance(x, timezone.datetime)`` keep matching plain datetimes."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, _real_datetime)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _real_datetime)


class _PinnedDatetime(_real_datetime, metaclass=_DatetimeMeta):
    """``datetime`` for ``django.utils.timezone`` whose only difference is ``now()``.

    Construction and every classmethod (``combine``, ``fromisoformat``, ...) hand
    back plain ``datetime`` objects, so nothing downstream ever sees this class.
    """

    def __new__(cls, *args, **kwargs):
        return _real_datetime(*args, **kwargs)

    @classmethod
    def now(cls, tz=None):
        shifted = pinned_now(_clock(_utc.utc))
        if tz is None:
            return shifted.astimezone().replace(tzinfo=None)
        return shifted.astimezone(tz)


def anchor_day():
    return _state["anchor"]


def install(anchor=None, pin=True):
    """Pin the run to ``anchor`` (default: the current Cairo date)."""

    uninstall()
    tz = ZoneInfo(settings.TIME_ZONE)
    _state.update(anchor=anchor or _clock(_utc.utc).astimezone(tz).date(), pin=pin, tz=tz)
    _state["installed"] = dj_timezone.datetime
    dj_timezone.datetime = _PinnedDatetime
    return _state["anchor"]


def uninstall():
    if _state["installed"] is not None:
        dj_timezone.datetime = _state["installed"]
    _state.update(anchor=None, pin=True, installed=None, tz=None)
