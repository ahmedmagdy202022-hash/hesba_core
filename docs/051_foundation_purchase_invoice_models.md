# 051 Foundation Models: Purchase Invoices

Checkpoint: `051_FOUNDATION_PURCHASE_INVOICE_MODELS`

## Scope

This step adds the foundation models for multi-line purchase invoices.

Added models:

- `PurchaseInvoice`
- `PurchaseLine`

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

This step starts the purchase part of the cycle:

- A purchase invoice belongs to a supplier.
- A purchase invoice has a receiving location.
- A purchase invoice can have many item lines.
- Paid now is recorded separately from invoice total.
- Remaining due is calculated from total amount minus paid now.

## Rules protected in this step

1. Purchase invoices affect suppliers only, not customers.
2. Supplier due is based on remaining due only.
3. Cashboxes are not moved by invoice total.
4. Cashboxes will later be affected only by `paid_now` through real cashbox movement records.
5. Inventory is not changed directly by purchase lines in this step.
6. Inventory will later increase through traceable stock movement records into the receiving location.
7. Multi-line invoices are supported from the beginning.
8. Reports are not implemented here.

## Payment status rule

- `paid_now = total_amount` → Paid
- `paid_now = 0` → Credit
- `paid_now > 0 and paid_now < total_amount` → Partial

The model validates:

`remaining_due = total_amount - paid_now`

If `paid_now` is greater than zero, a cashbox must be selected.

## Purchase lines

Each purchase line has:

- Line number
- Item
- Description
- Quantity
- Unit purchase price
- Line discount
- Line total

Line total must equal:

`quantity × unit_purchase_price - line_discount_amount`

## What is intentionally not done yet

This step does not post the invoice.
This step does not create stock movements.
This step does not create supplier ledger movements.
This step does not create cashbox movements.
This step does not update item average cost.

These actions must be added in a later controlled posting step so every transaction is traceable.

## Next after merge

Next step should build controlled purchase posting foundation:

- Supplier ledger movement from `remaining_due`
- Cashbox movement from `paid_now`
- Stock movement into `receiving_location`
- Average cost update logic
- Audit log record for posting/cancelling

Do not start sales until purchase posting rules are stable.
