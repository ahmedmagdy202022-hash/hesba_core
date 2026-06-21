# 082 First Laptop Run Required

Checkpoint: `082_FOUNDATION_FIRST_LAPTOP_RUN_REQUIRED`

At this point the next useful step needs a laptop run.

## Why manual laptop run is needed now

GitHub files and scripts are prepared, but the project still needs a real local execution to verify:
- Python environment
- installed dependencies
- Django system check
- migration plan
- migration drift check
- local database migration behavior

## Do not continue to business-cycle data entry before this

Before creating invoices or stock movements, we need to confirm that the local project can run the safe checks.

## First manual command group

Use the local instructions in:
- `docs/076_local_test_instructions.md`

Start with the safe prep script only.

## If safe prep passes

Then continue to:
- local CI script
- admin smoke test
- dev master data seed

## If safe prep fails

Stop and send the first error output only.

## Business cycle protected

This pause protects the Core sequence:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports
