"""ETA-002 (HG-039): the one line the sales engine calls before it cancels a
posted invoice. An invoice the Tax Authority holds (being sent, under review,
accepted, or with a cancellation still pending there) is a live legal
document: reversing its stock, cash and ledger here would leave it standing
against books that say it never happened. It is cancelled on the portal
first; once the authority confirms, the local cancellation goes ahead.

The engine calls this under the invoice's row lock, and sending claims the
invoice under the same lock, so a cancellation and a sending can never pass
each other. Invoices never sent pass untouched."""

from django.core.exceptions import ValidationError

LIVE_MESSAGE = "This invoice is registered with the e-invoice portal. Cancel it on the e-invoice page first, then cancel it here."
RETURN_LIVE_MESSAGE = "This return's credit note is registered with the e-invoice portal, so the return cannot be reversed here."


def _live(submissions):
    from .models import SubmissionStatus

    live = (SubmissionStatus.SENDING, SubmissionStatus.SUBMITTED, SubmissionStatus.VALID, SubmissionStatus.CANCEL_REQUESTED)
    # The latest sending in every environment: a production document counts even while the server talks to preprod.
    for environment in submissions.values_list("environment", flat=True).distinct():
        latest = submissions.filter(environment=environment).first()
        if latest is not None and latest.status in live:
            return True
    return False


def before_sales_invoice_cancel(invoice):
    from .models import Submission

    # T7: the invoice's own sendings only; a credit note for one of its returns is its own document.
    if _live(Submission.objects.filter(invoice_id=invoice.pk, sales_return__isnull=True)):
        raise ValidationError(LIVE_MESSAGE)


def before_sales_return_cancel(sales_return):
    """T7 (Codex #270): a return whose credit note the authority holds (being sent, under review or accepted) is a live
    legal document too; reversing its stock, debt and VAT here would leave that credit standing. Called by the sales
    engine under the return's row lock, the lock sending claims the credit note under."""

    from .models import Submission

    if _live(Submission.objects.filter(sales_return_id=sales_return.pk)):
        raise ValidationError(RETURN_LIVE_MESSAGE)
