# 071 CI Migration Fixes

Checkpoint: `071_FOUNDATION_CI_MIGRATION_FIXES`

This step adds missing migration files after the first GitHub Actions run failed at the migration check step.

Added migration coverage for:
- Sales invoices, sales lines, customer payments, and customer ledger entries.
- Supplier payments and supplier ledger entries.
- Stock movement links to sales invoices and sales lines.
- Cashbox movements.
- Closing periods, closing runs, period summaries, and post-closing adjustments.
- Usage status migration alignment placeholder.

Business cycle impact:
- The migration history now better matches the Core path:
  Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

Next: `072_FOUNDATION_CI_RESULT_RECHECK`
