# 058 Reports Base Views

Checkpoint: `058_FOUNDATION_REPORTS_BASE_VIEWS`

## Scope

This step adds read-only report selectors for the first reporting layer.

Added reports:

- Stock report
- Customer report
- Supplier report
- Cashbox report
- Sales report
- Purchase report
- Profit report

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

Reports read from posted movements and ledger rows. Reports do not create, edit, delete, or post transactions.

## Rules protected

- Reports are read-only.
- Stock is calculated from stock movements.
- Customer balance is calculated from customer ledger entries.
- Supplier balance is calculated from supplier ledger entries.
- Cashbox balance is calculated from actual cashbox movements.
- Profit equals sales amount minus cost amount.
- Cost and profit data must still be hidden by permissions in UI/API layers.

## Next after merge

`059_FOUNDATION_PERIOD_AND_CLOSING_MODELS`
