"""SEC-002: optional two-step sign-in with an authenticator app (TOTP, RFC 6238).

* The user adds Hesba to Google Authenticator / Microsoft Authenticator (setup
  key or the otpauth link) and confirms with a first code before it is on.
* Sign-in: the right password no longer signs in by itself; the session only
  remembers who passed the password step, for a few minutes, and the six-digit
  code (or a one-time recovery code) finishes the sign-in.
* Wrong codes count as failed sign-ins (SEC-001), so the same lock applies,
  and the password failures are only cleared once the code is right, so the
  lock cannot be reset by re-entering a known password.
* A code is accepted once (last used step is kept) and a code from the
  neighbouring 30 seconds is accepted for clock drift.
* Someone who manages users can switch it off for a user who lost their phone.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from django.conf import settings
from django.db import transaction

from audit.models import AuditEventType, AuditLog

from .models import TwoFactor


STEP_SECONDS = 30
DIGITS = 6
RECOVERY_COUNT = 8
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def grouped(secret):
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def _key(secret):
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP_SECONDS)


def clean_code(value):
    return "".join(ch for ch in (value or "").translate(_DIGIT_MAP) if not ch.isspace() and ch != "-").lower()


def matching_step(secret, code, now=None, after=0):
    """The step the code belongs to (now ±1), if it is newer than `after`; else None."""

    code = clean_code(code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    base = current_step(now)
    for step in (base, base - 1, base + 1):
        if step > after and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret, username):
    issuer = getattr(settings, "TWO_FACTOR_ISSUER", "Hesba")
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}"


def _hash(code):
    return hashlib.sha256(clean_code(code).encode()).hexdigest()


def _recovery_codes():
    codes = ["".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(8)) for _ in range(RECOVERY_COUNT)]
    return [f"{code[:4]}-{code[4:]}" for code in codes]


def device_for(user):
    if user is None or not getattr(user, "pk", None):
        return None
    return TwoFactor.objects.filter(user=user).first()


def _audit(user, actor, action, after=None):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=actor, module="accounts", action=action, object_type="auth.User",
                            object_id=str(user.pk), after_data=after or {"username": user.get_username()})


@transaction.atomic
def enable(user, secret, code):
    """Switch it on once the first code proves the app is set up; returns the recovery codes."""

    if TwoFactor.objects.filter(user=user).exists():
        return None
    step = matching_step(secret, code)
    if step is None:
        return None
    codes = _recovery_codes()
    TwoFactor.objects.create(user=user, secret=secret, last_step=step, recovery_hashes=[_hash(c) for c in codes])
    _audit(user, user, "enable_two_factor")
    return codes


@transaction.atomic
def verify(user, code):
    """True and consumes the code if it is a fresh app code or an unused recovery code."""

    device = TwoFactor.objects.select_for_update().filter(user=user).first()
    if device is None:
        return False
    step = matching_step(device.secret, code, after=device.last_step)
    if step is not None:
        device.last_step = step
        device.save(update_fields=["last_step"])
        return True
    hashed = _hash(code)
    if clean_code(code) and hashed in device.recovery_hashes:
        device.recovery_hashes = [h for h in device.recovery_hashes if h != hashed]
        device.save(update_fields=["recovery_hashes"])
        _audit(user, user, "two_factor_recovery_used", {"left": len(device.recovery_hashes)})
        return True
    return False


@transaction.atomic
def new_recovery_codes(user, code):
    if not verify(user, code):
        return None
    codes = _recovery_codes()
    TwoFactor.objects.filter(user=user).update(recovery_hashes=[_hash(c) for c in codes])
    _audit(user, user, "two_factor_new_recovery_codes")
    return codes


@transaction.atomic
def disable(user, actor):
    deleted, _ = TwoFactor.objects.filter(user=user).delete()
    if deleted:
        _audit(user, actor, "disable_two_factor" if actor == user else "reset_two_factor")
    return bool(deleted)
