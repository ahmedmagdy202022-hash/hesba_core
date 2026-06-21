# 055 Sales Invoice Models Foundation

Checkpoint: `055_FOUNDATION_SALES_INVOICE_MODELS`

## Scope

This step adds the first sales invoice foundation models.

Added:

- `SalesInvoice`
- `SalesLine`
- Sales admin registrations

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

This step starts the sales invoice part of the cycle.

## Rules

Sales invoices affect customers only, not suppliers.

Sales invoices do not directly affect:

- Suppliers
- Inventory
- Cashboxes
- Profit reports

Future sales posting will create controlled records for:

- Customer due from remaining due only
- Cashbox movement from paid now only
- Stock movement out from selling location
- Cost of goods sold
- Profit calculation
- Audit log

## Payment status rule

- `paid_now = total_amount` → Paid
- `paid_now = 0` → Credit
- `paid_now > 0 and paid_now < total_amount` → Partial

## Next after merge

Next recommended checkpoint:

`056_FOUNDATION_SALES_POSTING`
