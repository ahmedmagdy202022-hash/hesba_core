"""USERS-001: the owner manages staff accounts from inside Hesba.

Before this, adding a cashier meant Django Admin. Every account made here is a
plain user (never staff, never superuser), so Django Admin stays closed to it
(ADMIN-001). Guards keep the business from locking itself out: nobody can
switch off or demote themselves, and the last active owner cannot be removed.
"""

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog
from permissions.models import Role, RoleCode

from .models import UserProfile


MANAGE_PERMISSION = "permissions.manage_roles"


def assignable_roles():
    return Role.objects.filter(active=True).exclude(code=RoleCode.SUPPORT).order_by("code")


def _snapshot(user):
    profile = getattr(user, "hesba_profile", None)
    return {
        "username": user.username,
        "display_name": profile.display_name if profile else "",
        "phone": profile.phone if profile else "",
        "role": profile.role.code if profile and profile.role else None,
        "active": bool(user.is_active and (profile.active if profile else False)),
    }


def _audit(actor, user, action, before, after, reason=""):
    AuditLog.objects.create(
        event_type=AuditEventType.PERMISSION_CHANGE,
        actor=actor,
        module="accounts",
        action=action,
        object_type="User",
        object_id=str(user.pk),
        reason=reason,
        before_data=before,
        after_data=after,
    )


def _active_owner_count(excluding=None):
    owners = UserProfile.objects.filter(active=True, user__is_active=True, role__code=RoleCode.OWNER)
    if excluding is not None:
        owners = owners.exclude(user=excluding)
    return owners.count()


def _validate_password(password, user=None):
    try:
        password_validation.validate_password(password, user)
    except ValidationError as exc:
        raise ValidationError(list(exc.messages))


@transaction.atomic
def create_user_account(*, username, password, role, actor, display_name="", phone=""):
    User = get_user_model()
    username = (username or "").strip()
    if not username:
        raise ValidationError("username_required")
    if User.objects.filter(username__iexact=username).exists():
        raise ValidationError("username_taken")
    if role is None or role.code == RoleCode.SUPPORT or not role.active:
        raise ValidationError("role_invalid")
    candidate = User(username=username)
    _validate_password(password, candidate)
    user = User.objects.create_user(username=username, password=password, is_staff=False, is_superuser=False)
    UserProfile.objects.create(
        user=user,
        role=role,
        display_name=(display_name or "").strip(),
        phone=(phone or "").strip(),
        active=True,
        must_change_password=True,
    )
    user.refresh_from_db()
    _audit(actor, user, "create_user", None, _snapshot(user))
    return user


@transaction.atomic
def update_user_account(user, *, actor, role, active, display_name="", phone=""):
    profile = UserProfile.objects.select_for_update().filter(user=user).first()
    if profile is None:
        profile = UserProfile.objects.create(user=user, role=role, active=True)
    before = _snapshot(user)
    if role is None or not role.active or (role.code == RoleCode.SUPPORT and (profile.role is None or profile.role.code != RoleCode.SUPPORT)):
        raise ValidationError("role_invalid")
    if user.pk == actor.pk and (not active or role.pk != profile.role_id):
        raise ValidationError("no_self_lockout")
    was_owner = profile.role is not None and profile.role.code == RoleCode.OWNER and profile.active and user.is_active
    stays_owner = role.code == RoleCode.OWNER and active
    if was_owner and not stays_owner and _active_owner_count(excluding=user) == 0:
        raise ValidationError("last_owner")
    profile.role = role
    profile.active = active
    profile.display_name = (display_name or "").strip()
    profile.phone = (phone or "").strip()
    profile.save()
    if user.is_active != active:
        user.is_active = active
        user.save(update_fields=["is_active"])
    after = _snapshot(user)
    if after != before:
        _audit(actor, user, "update_user", before, after)
    return user


@transaction.atomic
def reset_user_password(user, *, password, actor):
    """Give a user a temporary password; they must change it at next sign-in."""

    _validate_password(password, user)
    user.set_password(password)
    user.save(update_fields=["password"])
    UserProfile.objects.filter(user=user).update(must_change_password=True)
    _audit(actor, user, "reset_password", None, {"username": user.username, "must_change_password": True})
    return user
