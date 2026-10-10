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


def before_sales_invoice_cancel(invoice):
    from .models import Submission, SubmissionStatus

    live = (SubmissionStatus.SENDING, SubmissionStatus.SUBMITTED, SubmissionStatus.VALID, SubmissionStatus.CANCEL_REQUESTED)
    # The latest sending in every environment: a production document counts even while the server talks to preprod.
    for environment in Submission.objects.filter(invoice_id=invoice.pk).values_list("environment", flat=True).distinct():
        latest = Submission.objects.filter(invoice_id=invoice.pk, environment=environment).first()
        if latest is not None and latest.status in live:
            raise ValidationError(LIVE_MESSAGE)
