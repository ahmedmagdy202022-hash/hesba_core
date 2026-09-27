"""TAX-001: how VAT is charged on sales lines and taken out of revenue (HG-015).

Rules (prices are entered *before* tax):
- A line's taxable amount is quantity x unit price - line discount.
- Its tax is taxable x rate / 100, rounded to the piastre, at the item's rate
  (or the default rate). Tax is fixed when the draft is created.
- The invoice's own discount is an extra discount on the total *after* tax, so
  it never changes the tax due.
- total = sum(line totals) + tax - invoice discount, the formula the sales
  service has always used; the header ``tax_amount`` is the sum of line taxes.
- Revenue and profit are always net of tax. Refunds on a return include the
  tax on the returned part, and that tax share is recorded with the return.

With the ``vat`` capability off nothing here charges tax, and an invoice
without tax behaves exactly as it did before VAT existed.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from config.money import allocate_proportionally, money_round
from settings_core.capabilities import capability_enabled

from .models import ItemTaxRate, PurchaseLineTax, PurchaseReturnLineTax, SalesLineTax, SalesReturnLineTax, TaxRate


ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


def vat_enabled():
    return capability_enabled("vat")


def default_rate():
    return TaxRate.objects.filter(active=True, is_default=True).first()


def rate_for(item):
    link = ItemTaxRate.objects.select_related("tax_rate").filter(item=item).first()
    if link is not None and link.tax_rate.active:
        return link.tax_rate
    return default_rate()


def rates_by_item():
    """{item_id: "14.00"} for the sales screens' live totals, or None when off."""

    if not vat_enabled():
        return None
    from master_data.models import Item

    default = default_rate()
    default_value = str(default.rate) if default is not None else "0.00"
    links = dict(ItemTaxRate.objects.filter(tax_rate__active=True).values_list("item_id", "tax_rate__rate"))
    return {str(pk): str(links[pk]) if pk in links else default_value for pk in Item.objects.filter(active=True).values_list("pk", flat=True)}


def compute_lines(lines, price_key="unit_sale_price"):
    """Per requested line: (tax_rate, rate, taxable, tax), in order."""

    rates = {}
    result = []
    for data in lines:
        item = data["item"]
        if item.pk not in rates:
            rates[item.pk] = rate_for(item)
        tax_rate = rates[item.pk]
        rate = tax_rate.rate if tax_rate is not None else ZERO
        taxable = money_round(data["quantity"] * data[price_key] - (data.get("line_discount_amount") or ZERO))
        result.append((tax_rate, rate, taxable, money_round(taxable * rate / HUNDRED)))
    return result


@transaction.atomic
def create_sales_draft_with_tax(header, lines, user=None):
    """``create_sales_draft`` with VAT filled in when the shop charges it."""

    from sales.services import create_sales_draft

    if not vat_enabled():
        return create_sales_draft(header, lines, user)
    taxes = compute_lines(lines)
    header = dict(header, tax_amount=money_round(sum((tax for *_, tax in taxes), ZERO)))
    invoice = create_sales_draft(header, lines, user)
    for line, (tax_rate, rate, taxable, tax) in zip(invoice.lines.order_by("line_number"), taxes):
        SalesLineTax.objects.create(line=line, tax_rate=tax_rate, rate=rate, taxable_amount=taxable, tax_amount=tax)
    return invoice


def line_taxes(invoice, lines):
    """{line.pk: tax} for an invoice's lines.

    Lines drafted with VAT carry their own rows. An older invoice with a tax
    typed on the header and no rows spreads that header tax over the lines in
    proportion to their totals, so its revenue is net of tax too.
    """

    rows = dict(SalesLineTax.objects.filter(line__in=lines).values_list("line_id", "tax_amount"))
    if rows:
        return {line.pk: rows.get(line.pk, ZERO) for line in lines}
    header_tax = invoice.tax_amount or ZERO
    if not header_tax:
        return {line.pk: ZERO for line in lines}
    weights = [line.line_total_amount for line in lines]
    if sum(weights, ZERO) <= 0:
        weights = [Decimal("1") for _ in lines]
    return {line.pk: amount for line, amount in zip(lines, allocate_proportionally(header_tax, weights))}


def return_tax_share(line, line_tax, quantity, cumulative_quantity):
    """The tax given back by returning ``quantity`` of ``line``."""

    if not line_tax:
        return ZERO
    if cumulative_quantity == line.quantity:
        prior = SalesReturnLineTax.objects.filter(
            return_line__source_line=line, return_line__sales_return__status="posted"
        ).aggregate(total=Sum("tax_amount"))["total"] or ZERO
        return money_round(line_tax - prior)
    return money_round(line_tax * quantity / line.quantity)


def record_return_taxes(return_lines_with_tax):
    for return_line, tax in return_lines_with_tax:
        if tax:
            SalesReturnLineTax.objects.create(return_line=return_line, tax_amount=tax)


def returns_tax(returns):
    """Total tax given back by a queryset of sales returns."""

    return SalesReturnLineTax.objects.filter(return_line__sales_return__in=returns).aggregate(total=Sum("tax_amount"))["total"] or ZERO


def vat_report(date_from, date_to):
    """Output VAT for posted sales in the window, net of returns, by rate."""

    from sales.models import SalesInvoice, SalesReturn

    invoices = SalesInvoice.objects.filter(status="posted", invoice_date__gte=date_from, invoice_date__lte=date_to)
    returns = SalesReturn.objects.filter(status="posted", return_date__gte=date_from, return_date__lte=date_to)
    rows = defaultdict(lambda: {"taxable": ZERO, "tax": ZERO, "returned_taxable": ZERO, "returned_tax": ZERO})
    for rate, taxable, tax in SalesLineTax.objects.filter(line__invoice__in=invoices).values_list("rate", "taxable_amount", "tax_amount"):
        rows[rate]["taxable"] += taxable
        rows[rate]["tax"] += tax
    for rate, tax, line_taxable, line_quantity, quantity in SalesReturnLineTax.objects.filter(return_line__sales_return__in=returns).values_list(
        "return_line__source_line__tax__rate", "tax_amount", "return_line__source_line__tax__taxable_amount",
        "return_line__source_line__quantity", "return_line__quantity",
    ):
        rows[rate]["returned_tax"] += tax
        if line_taxable is not None and line_quantity:
            rows[rate]["returned_taxable"] += money_round(line_taxable * quantity / line_quantity)
    untracked = invoices.filter(lines__tax__isnull=True, tax_amount__gt=0).distinct().aggregate(total=Sum("tax_amount"))["total"] or ZERO
    result = []
    for rate in sorted((key for key in rows if key is not None), reverse=True):
        row = rows[rate]
        result.append({
            "rate": rate,
            "taxable": money_round(row["taxable"] - row["returned_taxable"]),
            "tax": money_round(row["tax"]),
            "returned_tax": money_round(row["returned_tax"]),
            "net_tax": money_round(row["tax"] - row["returned_tax"]),
        })
    # Returns of invoices whose tax was typed on the header (no line rows).
    untracked_returned = rows[None]["returned_tax"] if None in rows else ZERO
    untracked_net = money_round(untracked - untracked_returned)
    return {
        "rows": result,
        "untracked_header_tax": untracked_net,
        "net_tax": money_round(sum((row["net_tax"] for row in result), ZERO) + untracked_net),
    }


# --- TAX-002: purchases (input VAT) -------------------------------------------
#
# A shop charging VAT recovers the VAT its suppliers charge, so that tax is not
# part of what the goods cost it. Purchase lines drafted with VAT on carry a
# PurchaseLineTax row; posting takes that tax out of the stock cost. A tax typed
# by hand on a purchase header (no rows) stays in cost exactly as before: that
# is the right treatment for a shop that is not VAT-registered.


@transaction.atomic
def create_purchase_draft_with_tax(header, lines, user=None):
    """``create_purchase_draft`` with input VAT filled in when the shop charges VAT."""

    from purchases.services import create_purchase_draft

    if not vat_enabled():
        return create_purchase_draft(header, lines, user)
    taxes = compute_lines(lines, price_key="unit_purchase_price")
    header = dict(header, tax_amount=money_round(sum((tax for *_, tax in taxes), ZERO)))
    invoice = create_purchase_draft(header, lines, user)
    for line, (tax_rate, rate, taxable, tax) in zip(invoice.lines.order_by("line_number"), taxes):
        PurchaseLineTax.objects.create(line=line, tax_rate=tax_rate, rate=rate, taxable_amount=taxable, tax_amount=tax)
    return invoice


def purchase_line_taxes(lines):
    """{line.pk: recoverable tax}; zero for lines without a tax row."""

    rows = dict(PurchaseLineTax.objects.filter(line__in=lines).values_list("line_id", "tax_amount"))
    return {line.pk: rows.get(line.pk, ZERO) for line in lines}


def purchase_return_tax_share(line, line_tax, quantity, cumulative_quantity):
    if not line_tax:
        return ZERO
    if cumulative_quantity == line.quantity:
        prior = PurchaseReturnLineTax.objects.filter(
            return_line__source_line=line, return_line__purchase_return__status="posted"
        ).aggregate(total=Sum("tax_amount"))["total"] or ZERO
        return money_round(line_tax - prior)
    return money_round(line_tax * quantity / line.quantity)


def record_purchase_return_taxes(return_lines_with_tax):
    for return_line, tax in return_lines_with_tax:
        if tax:
            PurchaseReturnLineTax.objects.create(return_line=return_line, tax_amount=tax)


def input_vat(date_from, date_to):
    """Recoverable VAT on posted purchases in the window, net of purchase returns, by rate."""

    from purchases.models import PurchaseInvoice, PurchaseReturn

    invoices = PurchaseInvoice.objects.filter(status="posted", invoice_date__gte=date_from, invoice_date__lte=date_to)
    returns = PurchaseReturn.objects.filter(status="posted", return_date__gte=date_from, return_date__lte=date_to)
    rows = defaultdict(lambda: {"taxable": ZERO, "tax": ZERO, "returned_taxable": ZERO, "returned_tax": ZERO})
    for rate, taxable, tax in PurchaseLineTax.objects.filter(line__invoice__in=invoices).values_list("rate", "taxable_amount", "tax_amount"):
        rows[rate]["taxable"] += taxable
        rows[rate]["tax"] += tax
    for rate, tax, line_taxable, line_quantity, quantity in PurchaseReturnLineTax.objects.filter(return_line__purchase_return__in=returns).values_list(
        "return_line__source_line__tax__rate", "tax_amount", "return_line__source_line__tax__taxable_amount",
        "return_line__source_line__quantity", "return_line__quantity",
    ):
        rows[rate]["returned_tax"] += tax
        if line_taxable is not None and line_quantity:
            rows[rate]["returned_taxable"] += money_round(line_taxable * quantity / line_quantity)
    result = []
    for rate in sorted((key for key in rows if key is not None), reverse=True):
        row = rows[rate]
        result.append({
            "rate": rate,
            "taxable": money_round(row["taxable"] - row["returned_taxable"]),
            "tax": money_round(row["tax"]),
            "returned_tax": money_round(row["returned_tax"]),
            "net_tax": money_round(row["tax"] - row["returned_tax"]),
        })
    return {"rows": result, "net_tax": money_round(sum((row["net_tax"] for row in result), ZERO))}


def vat_return(date_from, date_to):
    """Output VAT − input VAT for the window: what the shop owes (or can carry forward)."""

    output = vat_report(date_from, date_to)
    incoming = input_vat(date_from, date_to)
    return {"output": output, "input": incoming, "payable": money_round(output["net_tax"] - incoming["net_tax"])}
