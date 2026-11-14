"""Restaurants R2b (HG-110): one bill paid several ways (cash and a card, two cards), on top of the unchanged checkout.

Rules, stated once:

* ``checkout(..., tenders=[(cashbox, amount), ...])``. With no ``tenders``, or one, the call is the plain one-cashbox call
  it always was. With two or more, the **first** tender is the invoice's ``paid_now`` into its cashbox (the invoice's own
  cashbox, exactly as a one-cashbox sale), and **each further** tender is an ordinary customer payment into its own
  cashbox (``record_customer_payment``, numbered ``<invoice number>-T2``, ``-T3``…, dated the invoice's day), settled on
  the invoice (``settlements.settle``, source ``split``), all in the sale's one transaction: a refusal keeps nothing;
* every amount is above zero, no cashbox appears twice, each is active and never a held kind (cheques, a courier's
  collections, gift cards); the tenders (with a gift-card part) never add up to more than the total, and for the walk-in
  they must reach it (``checkout``'s walk-in rule counts them); the card button cannot be combined with tenders;
* the further tenders are recorded and settled before the invoice is posted, so what reacts to the posting (the
  e-receipt's payment method) already sees them;
* cancelling the invoice cancels each of its live split payments (``_on_audit``, the gift-card precedent HG-071 D3), so
  the walk-in keeps no credit and the card cashbox goes back; the listener runs before the deposits hook removes the
  invoice's settlement rows, because ``sales`` comes before ``deposits`` in ``INSTALLED_APPS``;
* a walk-in's split payment is undone only with its invoice, and a walk-in invoice paid several ways is not returned (the
  part in the other cashboxes would become a credit nobody can use): cancel the whole invoice instead (HG-071 D7's rule).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models.signals import post_save

from audit.models import AuditLog
from config.money import money_round

ZERO = Decimal("0.00")
WALK_IN_RETURN_MESSAGE = "This walk-in invoice was paid several ways; a return would leave the part paid into another cashbox as a credit nobody can use. Cancel the whole invoice instead."
WALK_IN_PAYMENT_MESSAGE = "This payment is part of a walk-in invoice paid several ways; cancel the whole invoice instead, and every part goes back."
NOTES = {"ar": "جزء من فاتورة {invoice} اتدفع بطريقة تانية", "en": "Part of invoice {invoice} paid another way"}
CANCEL_REASON = "اتلغت فاتورة {invoice} اللي الدفعة دي جزء منها"


def split(tenders, cash_due, card=False):
    """(first cashbox, first amount, [(cashbox, amount), ...] for the others) from ``tenders``, checked before anything is
    written. One tender is the plain call: its cashbox and its amount as ``tendered``, nothing else checked."""

    from cashboxes.models import Cashbox, HELD_KINDS

    tenders = list(tenders or [])
    if not tenders:
        raise ValidationError("err_tender_amount")
    if len(tenders) == 1:
        box, amount = tenders[0]
        return box, amount, []
    if card:
        raise ValidationError("err_tender_card")
    fresh = {box.pk: box for box in Cashbox.objects.filter(pk__in=[getattr(box, "pk", None) for box, _amount in tenders])}
    checked, seen = [], set()
    for box, amount in tenders:
        if not isinstance(amount, Decimal) or not amount.is_finite() or money_round(amount) <= 0:
            raise ValidationError("err_tender_amount")
        box = fresh.get(getattr(box, "pk", None))
        if box is None or not box.active or box.kind in HELD_KINDS:
            raise ValidationError("err_tender_cashbox")
        if box.pk in seen:
            raise ValidationError("err_tender_repeated")
        seen.add(box.pk)
        checked.append((box, money_round(amount)))
    if sum((amount for _box, amount in checked), ZERO) > cash_due:
        raise ValidationError("err_tenders_over")
    return checked[0][0], checked[0][1], checked[1:]


def pay_others(invoice, others, day, user, lang="ar"):
    """Record and settle the further tenders on the (draft) invoice, inside the sale's transaction."""

    from settlements import services as settlements
    from settlements.models import SettlementSource

    from .services import record_customer_payment

    notes = NOTES.get(lang, NOTES["ar"]).format(invoice=invoice.invoice_number)
    for number, (box, amount) in enumerate(others, start=2):
        payment = record_customer_payment(f"{invoice.invoice_number}-T{number}", day, invoice.customer, box, amount, user, notes=notes)
        settlements.settle(invoice, payment, amount, SettlementSource.SPLIT)


def live_payments(invoice):
    """The invoice's split settlements whose payment is still posted."""

    from settlements.models import InvoiceSettlement, SettlementSource

    return InvoiceSettlement.objects.filter(invoice=invoice, source=SettlementSource.SPLIT, customer_payment__status="posted")


def largest_cashbox_id(invoice):
    """The cashbox of the largest tender (the first one on a tie): the invoice's own cashbox when it was paid one way."""

    best = (invoice.paid_now or ZERO, invoice.cashbox_id)
    for amount, cashbox_id in live_payments(invoice).values_list("amount", "customer_payment__cashbox_id").order_by("pk"):
        if amount > best[0]:
            best = (amount, cashbox_id)
    return best[1]


def _walk_in(invoice):
    from .pos import WALK_IN_CODE

    return invoice.customer.customer_code == WALK_IN_CODE


# ---- the lines the sales engine calls (HG-071 D7's rule, for split payments) ----------------------------------------


def before_sales_return(invoice):
    """Called by ``create_sales_return`` under the invoice's row lock."""

    if _walk_in(invoice) and live_payments(invoice).exists():
        raise ValidationError(WALK_IN_RETURN_MESSAGE)


def before_customer_payment_cancel(payment):
    """Called by ``cancel_customer_payment``: a walk-in's split payment goes only with its invoice (whose cancellation
    cancels it, once the invoice is no longer posted)."""

    from settlements.models import SettlementSource

    settlement = getattr(payment, "settlement", None)
    if settlement is None or settlement.source != SettlementSource.SPLIT:
        return
    invoice = settlement.invoice
    if invoice.status == "posted" and _walk_in(invoice):
        raise ValidationError(WALK_IN_PAYMENT_MESSAGE)


# ---- following the sales engine ------------------------------------------------------------------------------------


def _on_audit(sender, instance, created, **kwargs):
    """A cancelled invoice cancels its live split payments, each on its own date, in the same transaction.

    Without it, ``settlements.release`` (run by the deposits hook) drops the invoice's settlement rows, the split payment
    stays as the customer's credit (the walk-in's too) and the card cashbox stays over (HG-071 D3's case).
    """

    if not created or instance.module != "sales" or instance.action != "cancel_posted_sales_invoice":
        return
    from sales.models import SalesInvoice

    from .services import cancel_customer_payment

    invoice = SalesInvoice.objects.get(pk=int(instance.object_id))
    for payment_id in list(live_payments(invoice).order_by("pk").values_list("customer_payment_id", flat=True)):
        cancel_customer_payment(payment_id, instance.actor, CANCEL_REASON.format(invoice=invoice.invoice_number))


def connect():
    post_save.connect(_on_audit, sender=AuditLog, dispatch_uid="sales-split-tenders-follow-the-invoice", weak=False)
