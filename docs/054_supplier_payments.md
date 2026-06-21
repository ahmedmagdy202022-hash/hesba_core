# 054 Supplier Payments Foundation

Checkpoint: `054_FOUNDATION_SUPPLIER_PAYMENTS`

## Scope

This step adds standalone supplier payments.

Added:

- `SupplierPayment`
- Link from supplier ledger to supplier payment
- Link from cashbox movement to supplier payment
- Service to record supplier payment
- Service to cancel supplier payment
- Admin updates

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

Supplier payment belongs to the supplier and cashbox part of the cycle only.

## Rules

Supplier payment affects:

- Supplier ledger
- Cashbox movement
- Audit log

Supplier payment does not affect:

- Customers
- Sales invoices
- Inventory
- Item cost

## Record payment flow

When a supplier payment is recorded:

1. Create supplier payment document.
2. Create supplier ledger due decrease.
3. Create cashbox out movement.
4. Create audit log entry.

## Cancel payment flow

When a supplier payment is cancelled:

1. Create supplier ledger due increase.
2. Create cashbox in movement.
3. Mark supplier payment as cancelled.
4. Create audit log entry.

The payment document is not deleted.

## Next after merge

Next recommended checkpoint:

`055_FOUNDATION_SALES_INVOICE_MODELS`
