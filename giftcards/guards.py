"""HG-071 (S6) D7: the lines the sales engine calls before a return or a payment cancellation touches an invoice that a
gift card paid.

No return-to-card path exists yet: an ordinary return would refund the card's share as cash or as the customer's
credit (on a walk-in, a credit nobody can spend) while the card stays spent. Until that path is decided, a return on an
invoice with a live redemption is refused; the route is to cancel the redemption (a named customer) or the whole
invoice (the walk-in: the card gets its money back, D3). Invoices no card paid pass untouched.
"""

from django.core.exceptions import ValidationError

RETURN_MESSAGE = "This invoice was paid partly by gift card, and a return to the card is not possible yet. Cancel the card payment or the whole invoice instead."
WALK_IN_PAYMENT_MESSAGE = "This is a gift-card payment on a walk-in sale; cancel the whole invoice instead, and the card gets its money back."


def live_redemptions(invoice):
    from .models import EntryKind, GiftCardEntry

    return GiftCardEntry.objects.filter(kind=EntryKind.REDEEMED, invoice=invoice, payment__status="posted")


def before_sales_return(invoice):
    """Called by ``create_sales_return`` under the invoice's row lock."""

    if live_redemptions(invoice).exists():
        raise ValidationError(RETURN_MESSAGE)


def before_customer_payment_cancel(payment):
    """Called by ``cancel_customer_payment``: a walk-in's card payment is undone only with its invoice (whose
    cancellation cancels it, D3, once the invoice is no longer posted)."""

    from sales.pos import WALK_IN_CODE

    from .models import EntryKind, GiftCardEntry

    entry = GiftCardEntry.objects.filter(kind=EntryKind.REDEEMED, payment=payment).select_related("invoice__customer").first()
    if entry is None or entry.invoice is None:
        return
    if entry.invoice.status == "posted" and entry.invoice.customer.customer_code == WALK_IN_CODE:
        raise ValidationError(WALK_IN_PAYMENT_MESSAGE)
