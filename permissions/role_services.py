"""FB-ROLES: the owner makes roles of their own, from inside Hesba.

The seeded roles (owner, manager, cashier, ...) stay exactly as the migrations
made them: they are the tested matrix every screen relies on. A business that
needs something in between (a cashier who also receives stock, an accountant
who does not see profit) copies the closest one and ticks what it should do.

Guards:
- a custom role never grants managing roles itself, so a role made here cannot
  make roles or hand out more than the owner meant;
- whoever saves a role must hold every permission they put in it (no one hands
  out what they do not have);
- a role still worn by an active user cannot be switched off (they would be
  locked out without notice).

Every change is in the audit log with what the role could do before and after.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog

from .models import Permission, Role, RolePermission, RoleCode
from .services import user_has_permission

#: Never in a custom role: who manages roles stays the owner's business.
LOCKED = frozenset({"permissions.manage_roles"})
CUSTOM_PREFIX = "custom_"


def grantable_permissions():
    return Permission.objects.filter(active=True).exclude(code__in=LOCKED).order_by("module", "code")


def role_codes(role):
    return sorted(role.rolepermission_set.filter(allow=True, permission__active=True).values_list("permission__code", flat=True))


def _audit(actor, role, action, before, after):
    AuditLog.objects.create(event_type=AuditEventType.PERMISSION_CHANGE, actor=actor, module="permissions", action=action,
                            object_type="Role", object_id=str(role.pk), before_data=before, after_data=after)


def _snapshot(role):
    return {"code": role.code, "name_ar": role.name_ar, "name_en": role.name_en, "active": role.active, "permissions": role_codes(role)}


def _next_code():
    taken = set(Role.objects.filter(code__startswith=CUSTOM_PREFIX).values_list("code", flat=True))
    number = len(taken) + 1
    while f"{CUSTOM_PREFIX}{number}" in taken:
        number += 1
    return f"{CUSTOM_PREFIX}{number}"


def _clean(name_ar, name_en, codes, actor, role=None):
    name_ar, name_en = (name_ar or "").strip()[:255], (name_en or "").strip()[:255]
    if not name_ar:
        raise ValidationError("role_name_required")
    clash = Role.objects.filter(name_ar__iexact=name_ar)
    if role is not None:
        clash = clash.exclude(pk=role.pk)
    if clash.exists():
        raise ValidationError("role_name_taken")
    wanted = set(codes or ())
    if not wanted:
        raise ValidationError("role_needs_permissions")
    if wanted & LOCKED:
        raise ValidationError("role_locked_permission")
    permissions = list(grantable_permissions().filter(code__in=wanted))
    if len(permissions) != len(wanted):
        raise ValidationError("role_unknown_permission")
    if any(not user_has_permission(actor, permission.code) for permission in permissions):
        raise ValidationError("role_beyond_actor")
    return name_ar, name_en, permissions


def _grant(role, permissions):
    RolePermission.objects.filter(role=role).exclude(permission__in=permissions).delete()
    for permission in permissions:
        RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})


def _manage(actor):
    if not user_has_permission(actor, "permissions.manage_roles"):
        raise ValidationError("role_manage_denied")


@transaction.atomic
def create_role(*, name_ar, name_en="", description="", codes, actor):
    _manage(actor)
    name_ar, name_en, permissions = _clean(name_ar, name_en, codes, actor)
    role = Role.objects.create(code=_next_code(), name_ar=name_ar, name_en=name_en, description=(description or "").strip(),
                               is_system_role=False, active=True)
    _grant(role, permissions)
    _audit(actor, role, "create_role", None, _snapshot(role))
    return role


@transaction.atomic
def update_role(role, *, name_ar, name_en="", description="", codes, active=True, actor):
    _manage(actor)
    role = Role.objects.select_for_update().get(pk=role.pk)
    if role.is_system_role:
        raise ValidationError("role_system_locked")
    before = _snapshot(role)
    name_ar, name_en, permissions = _clean(name_ar, name_en, codes, actor, role)
    if not active and in_use(role):
        raise ValidationError("role_in_use")
    role.name_ar, role.name_en, role.description, role.active = name_ar, name_en, (description or "").strip(), bool(active)
    role.save()
    _grant(role, permissions)
    after = _snapshot(role)
    if after != before:
        _audit(actor, role, "update_role", before, after)
    return role


def in_use(role):
    from accounts.models import UserProfile

    return UserProfile.objects.filter(role=role, active=True, user__is_active=True).count()


def copyable(role):
    return role.code != RoleCode.SUPPORT
