# 056 Sales Posting Foundation

Checkpoint: `056_FOUNDATION_SALES_POSTING`

## Scope

This step adds controlled sales posting.

Added:

- Customer ledger entries
- Sales posting service
- Sales cancellation service
- Sales invoice links on cashbox movements
- Sales invoice links on stock movements
- Cost and margin fields on sales lines
- Location stock helper
- Admin updates

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

This step connects the sales invoice to traceable records.

## Rules

Sales posting affects:

- Customer ledger from remaining due only
- Cashbox movement from paid now only
- Stock movement from selling location
- Sales line cost and margin values
- Audit log

Sales posting does not affect:

- Suppliers
- Supplier ledger
- Purchase invoices

## Posting flow

When posting a draft sales invoice:

1. Validate invoice totals.
2. Validate sales lines.
3. Check available stock in selling location for stock-tracked items.
4. Create customer ledger entry if there is remaining due.
5. Create cashbox in movement if there is paid now.
6. Create stock out movement for each stock-tracked item line.
7. Save controlled cost and margin values on sales lines.
8. Mark invoice as posted.
9. Record audit log.

## Cancellation flow

Posted invoices are not deleted.

Cancelling a posted sales invoice creates reverse rows:

- Customer due decrease
- Cashbox out movement
- Stock return in movement
- Audit log

Then invoice status becomes cancelled.

## Next after merge

Next recommended checkpoint:

`057_FOUNDATION_CUSTOMER_PAYMENTS`
