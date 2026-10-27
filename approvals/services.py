"""FB-27: holding an action for a second person's approval, and carrying it out once approved.

The owner picks which actions need approval (none at first). A person who may approve (``approvals.approve``) does
such an action directly, as before. Anyone else gets a waiting request instead, and nothing moves. One of those who
may approve, never the requester, says yes or no. On yes the action runs then, through the same service and in the
requester's name, and the request keeps who approved. If the service refuses it (the month closed since, the
document changed), the request says why and nothing moved.
"""

import json
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from .actions import ACTIONS
from .models import ApprovalRequest, ApprovalStatus

APPROVE_PERMISSION = "approvals.approve"
SETTING_KEY = "approvals.actions"


# ---- what needs approval ---------------------------------------------------------


def held_actions():
    from settings_core.models import SystemSetting

    raw = SystemSetting.objects.filter(key=SETTING_KEY, active=True).values_list("value", flat=True).first()
    try:
        codes = json.loads(raw or "[]")
    except ValueError:
        return set()
    return {code for code in codes if code in ACTIONS}


def can_approve(user):
    return user_has_permission(user, APPROVE_PERMISSION)


def set_held_actions(codes, user):
    """Only the account owner chooses what needs approval."""

    from sales.short_sales import is_owner
    from settings_core.models import SystemSetting

    if not is_owner(user):
        raise PermissionDenied("Only the account owner chooses what needs approval.")
    codes = sorted({code for code in codes if code in ACTIONS})
    before = sorted(held_actions())
    SystemSetting.objects.update_or_create(key=SETTING_KEY, defaults={
        "value": json.dumps(codes), "data_type": SystemSetting.DataType.JSON, "active": True,
        "description": "FB-27: the actions a second person approves."})
    AuditLog.objects.create(event_type=AuditEventType.PERMISSION_CHANGE, actor=user, module="approvals", action="set_held_actions",
                            object_type="SystemSetting", object_id=SETTING_KEY, before_data={"actions": before}, after_data={"actions": codes})
    return codes


# ---- asking ------------------------------------------------------------------------


def _plain(data):
    return {key: value.isoformat() if isinstance(value, date) else value for key, value in (data or {}).items()}


def hold(user, action, object_id, data, label=""):
    """The waiting request for this action, or None when the action may go ahead now."""

    if action not in ACTIONS or action not in held_actions() or can_approve(user):
        return None
    existing = ApprovalRequest.objects.filter(action=action, object_id=object_id, status=ApprovalStatus.PENDING).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            request = ApprovalRequest.objects.create(action=action, object_id=object_id, object_label=str(label)[:200],
                                                     data=_plain(data), requested_by=user)
    except IntegrityError:  # the same request sent twice at once
        return ApprovalRequest.objects.get(action=action, object_id=object_id, status=ApprovalStatus.PENDING)
    AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=user, module="approvals", action="request_approval",
                            object_type="approvals.ApprovalRequest", object_id=str(request.pk),
                            reason=str(request.data.get("reason", "")), after_data={"action": action, "object_id": object_id, "label": request.object_label})
    return request


# ---- deciding ----------------------------------------------------------------------


def _locked(request_id):
    request = ApprovalRequest.objects.select_for_update().select_related("requested_by").get(pk=request_id)
    if request.status != ApprovalStatus.PENDING:
        raise ValidationError("approval_not_waiting")
    return request


def _decided(request, user, status, note=""):
    request.status, request.decided_by, request.decided_at, request.note = status, user, timezone.now(), note[:2000]
    request.save(update_fields=["status", "decided_by", "decided_at", "note"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="approvals", action=f"approval_{status}",
                            object_type="approvals.ApprovalRequest", object_id=str(request.pk), reason=note[:500],
                            after_data={"action": request.action, "object_id": request.object_id, "requested_by": request.requested_by_id})
    return request


def _check_approver(request, user):
    if not can_approve(user):
        raise PermissionDenied("Approving needs approvals.approve.")
    if request.requested_by_id == user.pk:
        raise ValidationError("approval_own_request")


def _still_allowed(request):
    """Why the held action may no longer run now, or "" (Codex #280): its screen's module was switched off, or the
    requester may no longer do it (permission removed, account or profile switched off)."""

    from settings_core.module_gate import closed_module

    action = ACTIONS[request.action]
    if closed_module(action.url(request.object_id)):
        return "approval_module_closed"
    requester = request.requested_by
    if not requester.is_active or not any(user_has_permission(requester, code) for code in action.permissions):
        return "approval_requester_not_allowed"
    return ""


def approve(request_id, user):
    """Carry out the held action now. Returns the request, done or failed with the reason."""

    with transaction.atomic():
        request = _locked(request_id)
        _check_approver(request, user)
        refused = _still_allowed(request)
        if refused:
            return _decided(request, user, ApprovalStatus.FAILED, refused)
        try:
            with transaction.atomic():  # the action's own work is all or nothing
                ACTIONS[request.action].run(request)
        except (ValidationError, PermissionDenied) as exc:
            reason = "; ".join(getattr(exc, "messages", None) or [str(exc)])
            return _decided(request, user, ApprovalStatus.FAILED, reason)
        return _decided(request, user, ApprovalStatus.DONE)


def reject(request_id, user, note=""):
    with transaction.atomic():
        request = _locked(request_id)
        _check_approver(request, user)
        return _decided(request, user, ApprovalStatus.REJECTED, (note or "").strip())


def withdraw(request_id, user):
    with transaction.atomic():
        request = _locked(request_id)
        if request.requested_by_id != user.pk:
            raise PermissionDenied("Only the requester withdraws a request.")
        return _decided(request, user, ApprovalStatus.WITHDRAWN)


def waiting_for(user):
    """The requests this person may decide: everyone's but their own, on documents of the entity being worked in.

    The entity filter is part of the query (Codex #280), so a page or a count never loses older requests behind
    newer ones of another entity."""

    from django.db.models import Q

    if not can_approve(user):
        return ApprovalRequest.objects.none()
    seen = Q(pk__in=[])
    for code, action in ACTIONS.items():
        seen |= Q(action=code, object_id__in=action.visible_ids())
    return ApprovalRequest.objects.filter(seen, status=ApprovalStatus.PENDING).exclude(requested_by=user)
