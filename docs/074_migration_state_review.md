# 074 Migration State Review

Checkpoint: `074_FOUNDATION_MIGRATION_STATE_REVIEW`

This step records the current migration dependency map after the CI migration repair work.

Current migration coverage reviewed:
- `master_data`: categories, locations, customers, suppliers, and items.
- `cashboxes`: cashboxes, then cashbox movements after purchases and sales links are available.
- `purchases`: purchase invoices and lines, then supplier payments and supplier ledger entries.
- `sales`: sales invoices, sales lines, customer payments, and customer ledger entries.
- `inventory`: stock movements, then sales invoice and sales line references.
- `closing`: periods, closing runs, summaries, and post-closing adjustments.
- `imports`: import batches, raw rows, review rows, validation/apply alignment.
- `settings_core`: client settings, feature flags, support access, and usage status snapshots.
- `audit`: audit log.

Dependency review:
- `cashboxes.0001` stays before purchase and sales invoice links.
- `purchases.0002` adds supplier payments before cashbox movements reference supplier payments.
- `sales.0001` adds sales invoice and customer payment before cashbox movements reference them.
- `cashboxes.0002` depends on `purchases.0002` and `sales.0001`.
- `inventory.0002` depends on `sales.0001` to add sales stock movement links.

Business rules protected:
- Sales still do not create supplier dues.
- Supplier payments are in purchases and affect suppliers/cashboxes only.
- Customer payments are in sales and affect customers/cashboxes only.
- Cashbox movements are separated and depend on actual paid amounts only.
- Stock movements keep inventory traceable by item and location.
- Reports remain read-only and should read from controlled tables.

Current CI handling:
- Push-to-main CI emails are paused.
- Pull request CI and manual CI remain available.
- Local CI script is available at `scripts/ci_local_check.sh`.

Next: `075_FOUNDATION_SAFE_TEST_RUN_PREP`
