"""HG-038: the one line the sales and purchases engines call after a
cancellation or a return, so a project's contract figures never become
impossible (more retention released than held, or more advance recovered
than received). Each check runs inside the engine's own transaction: raising
undoes the change. Documents that belong to no project pass untouched."""


def after_sales_invoice_change(invoice):
    from .contract import ensure_consistent
    from .models import Certificate, ProjectInvoice

    # A certificate names its project directly, so the guard never depends on the invoice link.
    certificate = Certificate.objects.select_related("project").filter(invoice_id=invoice.pk).first()
    link = None if certificate else ProjectInvoice.objects.select_related("project").filter(invoice_id=invoice.pk).first()
    project = certificate.project if certificate else (link.project if link else None)
    if project is not None:
        ensure_consistent(project)


def after_customer_payment_change(payment):
    from .contract import ensure_consistent
    from .models import ProjectPayment

    link = ProjectPayment.objects.select_related("project").filter(payment_id=payment.pk).first()
    if link is not None:
        ensure_consistent(link.project)


def after_purchase_invoice_change(invoice):
    from .costs import ensure_consistent
    from .models import SubcontractBill

    bill = SubcontractBill.objects.select_related("subcontract").filter(invoice_id=invoice.pk).first()
    if bill is not None:
        ensure_consistent(bill.subcontract)
