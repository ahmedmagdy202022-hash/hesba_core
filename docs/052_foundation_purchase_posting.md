# 052 Foundation: Purchase Posting

Checkpoint: `052_FOUNDATION_PURCHASE_POSTING`

## Scope

This step adds controlled purchase posting foundations.

Added:

- `StockMovement`
- `CashboxMovement`
- `SupplierLedgerEntry`
- `post_purchase_invoice(invoice_id, user=None)`
- Admin registrations for movement review

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

This step connects a purchase invoice to traceable records:

- Supplier ledger from `remaining_due`
- Inventory movement into `receiving_location`
- Cashbox movement from `paid_now`
- Audit log for posting

## Core rules

Supplier rule:

`remaining_due = total_amount - paid_now`

Only `remaining_due` increases supplier dues.
`paid_now` does not create supplier due.

Cashbox rule:

Only `paid_now` creates a cashbox movement.
Invoice total does not move cashbox.
Remaining due does not move cashbox.

Inventory rule:

Purchase lines do not directly change stock balances.
Posting creates `purchase_in` stock movements.
Stock is later calculated by `Item + Location` from stock movements.

Audit rule:

Purchase posting creates an audit log entry.

## Posting flow

When posting a draft purchase invoice:

1. Validate invoice totals.
2. Validate purchase lines.
3. Create supplier ledger entry if there is remaining due.
4. Create cashbox movement if there is paid now.
5. Create stock movement for each stock-tracked item line.
6. Mark invoice as posted.
7. Record audit log.

## Not included yet

This step does not add:

- Purchase undo flow
- Purchase returns
- Supplier standalone payments
- Average cost recalculation
- Report views
- UI screens

## Next after merge

Next recommended checkpoint:

`053_FOUNDATION_PURCHASE_REVERSAL_AND_AVERAGE_COST_DECISION`

Before sales, confirm the safe flow for undoing posted purchases and updating average cost.
