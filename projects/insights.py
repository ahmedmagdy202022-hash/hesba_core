"""CONTRACT-003: the contractor's portfolio, read from what the projects already hold.

Read-only. Every figure comes from ``services.summary`` (posted documents
only, in the entity being worked in) and the contract module:

- *billed %* is billed against the contract value;
- *time %* is the share of the project's planned span already gone, so a
  project billing behind its calendar shows as behind;
- *retention held* is what the client keeps until handover.

A progress certificate is *overdue* when its posted invoice still has money
owed and it is older than the credit days the aging report uses
(``reports.aging.credit_days``).
"""

from decimal import Decimal

from django.utils import timezone

from config.money import money_round
from entities import scope as entity_scope

from .models import Certificate, Project, ProjectStatus

ZERO = Decimal("0")
#: Billing this many points behind the calendar counts as behind schedule.
BEHIND_POINTS = 15


def time_share(project, today=None):
    """Percent of the planned span gone (0–100), or None without both dates."""

    today = today or timezone.localdate()
    if not project.start_date or not project.end_date or project.end_date <= project.start_date:
        return None
    span = (project.end_date - project.start_date).days
    gone = (min(max(today, project.start_date), project.end_date) - project.start_date).days
    return round(gone / span * 100)


def portfolio(status=ProjectStatus.ACTIVE, today=None):
    """[{project, figures, time_pct, behind, retention}] and the totals."""

    from .contract import retention_held
    from .services import summary

    today = today or timezone.localdate()
    projects = Project.objects.select_related("customer").order_by("code")
    if status:
        projects = projects.filter(status=status)
    rows = []
    for project in projects[:200]:
        figures = summary(project)
        share = time_share(project, today)
        rows.append({"project": project, "figures": figures, "time_pct": share,
                     "behind": share is not None and figures["contract"] > 0 and figures["progress"] + BEHIND_POINTS < share,
                     "retention": retention_held(project)})
    keys = ("contract", "billed", "cost", "profit", "due")
    totals = {key: money_round(sum((row["figures"][key] for row in rows), ZERO)) for key in keys}
    totals["retention"] = money_round(sum((row["retention"] for row in rows), ZERO))
    totals["progress"] = int(totals["billed"] / totals["contract"] * 100) if totals["contract"] > 0 else 0
    totals["margin"] = int(totals["profit"] / totals["billed"] * 100) if totals["billed"] > 0 else 0
    totals["losing"] = sum(1 for row in rows if row["figures"]["profit"] < 0)
    totals["behind"] = sum(1 for row in rows if row["behind"])
    rows.sort(key=lambda row: (row["figures"]["profit"] >= 0, row["figures"]["margin"], row["project"].code))  # the worst first
    return rows, totals


def overdue_certificates(today=None):
    """Posted certificates still owed and older than the credit days: [{certificate, owed, age, late}], oldest first.

    What a certificate still owes is its share of the project's *due now* (``contract.position``: invoices less
    retention held, advance recovered and collections), with collections paying the oldest certificate first.
    Retention the client keeps until handover is never counted as overdue."""

    from reports.aging import credit_days

    from .contract import net_payable, position

    today = today or timezone.localdate()
    terms = credit_days()
    certificates = (entity_scope.scope(Certificate.objects, "invoice__" + entity_scope.SALES_INVOICE)
                    .filter(invoice__status="posted").select_related("project", "project__customer", "invoice").order_by("certificate_date", "pk"))
    by_project = {}
    for certificate in certificates:
        by_project.setdefault(certificate.project_id, []).append(certificate)
    rows = []
    for items in by_project.values():
        left = max(position(items[0].project)["due_now"], ZERO)
        owed = {}
        for certificate in reversed(items):  # the newest is the last to be paid
            share = min(max(net_payable(certificate), ZERO), left)
            owed[certificate.pk] = share
            left -= share
        for certificate in items:
            age = (today - certificate.certificate_date).days
            if owed[certificate.pk] > 0 and age > terms:
                rows.append({"certificate": certificate, "project": certificate.project, "owed": money_round(owed[certificate.pk]),
                             "age": age, "late": age - terms})
    rows.sort(key=lambda row: (row["certificate"].certificate_date, row["certificate"].pk))
    return rows, money_round(sum((row["owed"] for row in rows), ZERO)), terms
