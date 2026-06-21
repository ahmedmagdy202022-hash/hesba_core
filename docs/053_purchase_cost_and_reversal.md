# 053 Purchase Cost and Reversal Foundation

Checkpoint: `053_FOUNDATION_PURCHASE_COST_AND_REVERSAL`

## Scope

This step improves purchase posting before sales.

Added:

- Stock quantity helper
- Stock value helper
- Average cost recalculation
- Posted purchase cancellation service

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

This step keeps purchase effects traceable.

## Average cost

Average cost is recalculated from stock movements.

Stock quantity comes from movement rows.
Stock value comes from quantity multiplied by unit cost.

Average cost is:

`stock value / stock quantity`

Cost remains sensitive and must be protected by permissions.

## Purchase posting update

When posting a purchase invoice:

1. Supplier ledger is updated by remaining due only.
2. Cashbox movement is created by paid now only.
3. Stock movements are created into the receiving location.
4. Average cost is recalculated for affected items.
5. Invoice status becomes posted.
6. Audit log is recorded.

## Purchase cancellation

Posted invoices are not deleted.

Cancelling a posted purchase creates reverse rows:

- Supplier due decrease
- Cashbox in movement
- Stock movement out
- Average cost recalculation
- Audit log

Then the invoice status becomes cancelled.

## Not included yet

This step does not add:

- Separate purchase return documents
- Standalone supplier payment screen
- Sales invoices
- Report views
- UI screens

## Next after merge

Next recommended checkpoint:

`054_FOUNDATION_SUPPLIER_PAYMENTS`
