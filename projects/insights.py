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

    from .contract import position
    from .services import summary

    today = today or timezone.localdate()
    projects = Project.objects.select_related("customer").order_by("code")
    if status:
        projects = projects.filter(status=status)
    rows = []
    total_count = projects.count()
    for project in projects[:200]:
        figures = summary(project)
        share = time_share(project, today)
        rows.append({"project": project, "figures": figures, "time_pct": share,
                     "behind": share is not None and figures["contract"] > 0 and figures["progress"] + BEHIND_POINTS < share,
                     "retention": position(project)["retention_held"]})  # in the entity being worked in, like the rest of the row
    keys = ("contract", "billed", "cost", "profit", "due")
    totals = {key: money_round(sum((row["figures"][key] for row in rows), ZERO)) for key in keys}
    totals["retention"] = money_round(sum((row["retention"] for row in rows), ZERO))
    totals["progress"] = round(totals["billed"] / totals["contract"] * 100) if totals["contract"] > 0 else 0  # as the dashboard tile
    totals["margin"] = int(totals["profit"] / totals["billed"] * 100) if totals["billed"] > 0 else 0
    totals["losing"] = sum(1 for row in rows if row["figures"]["profit"] < 0)
    totals["behind"] = sum(1 for row in rows if row["behind"])
    totals["shown"], totals["count"] = len(rows), total_count  # more than 200: the page says so
    rows.sort(key=lambda row: (row["figures"]["profit"] >= 0, row["figures"]["margin"], row["project"].code))  # the worst first
    return rows, totals


def certificate_owed(project):
    """{certificate id: what it still owes} for the project's posted certificates, in the entity being worked in.

    Everything the client was billed is lined up by date: each linked posted invoice at what it left owed after its
    returns (a certificate also less the retention it holds and the advance it recovers), and each retention release
    on its own date. The project's collections then pay the oldest first. So money owed on another invoice, or on
    released retention, is never put on an old certificate, and a returned certificate owes only what is left."""

    from django.db.models import Sum

    from sales.models import SalesReturn

    from .contract import effective, in_reach, live_certificates, position

    certificates = {c.invoice_id: c for c in entity_scope.scope(live_certificates(project).filter(invoice__status="posted"),
                                                                "invoice__" + entity_scope.SALES_INVOICE).select_related("invoice")}
    links = entity_scope.scope(project.invoices.select_related("invoice").filter(invoice__status="posted"), "invoice__" + entity_scope.SALES_INVOICE)
    items = []
    for link in links:
        invoice = link.invoice
        returned = SalesReturn.objects.filter(source_invoice=invoice, status="posted").aggregate(total=Sum("due_amount"))["total"] or ZERO
        amount = Decimal(invoice.remaining_due) - returned
        certificate = certificates.get(invoice.pk)
        if certificate is not None:
            _, retention, recovered = effective(certificate)
            amount -= retention + recovered
        items.append((invoice.invoice_date, 0, invoice.pk, max(amount, ZERO), certificate))
    if in_reach(project):
        for release in project.retention_releases.filter(reversed_on__isnull=True):
            items.append((release.release_date, 1, release.pk, Decimal(release.amount), None))
    paid = position(project)["collections"]
    owed = {}
    for *_, amount, certificate in sorted(items, key=lambda item: item[:3]):  # the oldest is paid first
        share = min(amount, paid)
        paid -= share
        if certificate is not None:
            owed[certificate.pk] = money_round(amount - share)
    return owed


def overdue_certificates(today=None, status=None):
    """Posted certificates still owed and older than the credit days: [{certificate, owed, age, late}], oldest first.

    What each still owes comes from ``certificate_owed``. Retention the client keeps until handover is never counted as overdue.
    ``status`` limits it to projects in that status, as the portfolio beside it."""

    from reports.aging import credit_days

    today = today or timezone.localdate()
    terms = credit_days()
    certificates = (entity_scope.scope(Certificate.objects, "invoice__" + entity_scope.SALES_INVOICE)
                    .filter(invoice__status="posted").select_related("project", "project__customer", "invoice").order_by("certificate_date", "pk"))
    if status:
        certificates = certificates.filter(project__status=status)
    by_project = {}
    for certificate in certificates:
        by_project.setdefault(certificate.project_id, []).append(certificate)
    rows = []
    for items in by_project.values():
        owed = certificate_owed(items[0].project)
        for certificate in items:
            age = (today - certificate.certificate_date).days
            left = owed.get(certificate.pk, ZERO)
            if left > 0 and age > terms:
                rows.append({"certificate": certificate, "project": certificate.project, "owed": left, "age": age, "late": age - terms})
    rows.sort(key=lambda row: (row["certificate"].certificate_date, row["certificate"].pk))
    return rows, money_round(sum((row["owed"] for row in rows), ZERO)), terms
