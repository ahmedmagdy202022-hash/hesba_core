from django.db import transaction

from audit.models import AuditEventType, AuditLog
from cashboxes.models import CashboxDirection, CashboxMovement, CashboxMovementType

from .models import ProjectCostEntry


@transaction.atomic
def record_project_cost(project, entry_date, cost_type, amount, paid_now=0, supplier=None, cashbox=None, user=None, notes=""):
    entry = ProjectCostEntry.objects.create(
        project=project,
        entry_date=entry_date,
        cost_type=cost_type,
        supplier=supplier,
        cashbox=cashbox,
        amount=amount,
        paid_now=paid_now,
        notes=notes,
        created_by=user,
    )
    entry.full_clean()

    if entry.paid_now and entry.paid_now > 0:
        CashboxMovement.objects.create(
            cashbox=entry.cashbox,
            movement_date=entry.entry_date,
            movement_type=CashboxMovementType.DIRECT_OUT,
            direction=CashboxDirection.OUT,
            amount=entry.paid_now,
            description=f"Project cost {entry.project.project_code}",
            created_by=user,
        )

    AuditLog.objects.create(
        event_type=AuditEventType.CREATE,
        actor=user,
        module="industries",
        action="record_project_cost",
        object_type="ProjectCostEntry",
        object_id=str(entry.id),
        after_data={"project_id": entry.project_id, "amount": str(entry.amount), "paid_now": str(entry.paid_now)},
    )
    return entry
