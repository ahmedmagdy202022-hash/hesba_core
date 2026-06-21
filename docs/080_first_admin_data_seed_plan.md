# 080 First Admin Data Seed Plan

Checkpoint: `080_FOUNDATION_FIRST_ADMIN_DATA_SEED_PLAN`

This step adds a safe, minimal dev-only seed command for the first admin smoke test.

## Added command

`python manage.py seed_dev_master_data`

## What it creates or updates

The command is idempotent. It uses `update_or_create`, so running it again updates the same dev records instead of creating duplicates.

It creates or updates:
- Category: `DEV-CAT`
- Location: `MAIN`
- Item: `ITEM-001`
- Customer: `CUST-001`
- Supplier: `SUP-001`
- Cashbox: `CASH-001`

## Scope

This is only for local development and admin smoke testing.

It does not create:
- purchase invoices
- sales invoices
- stock movements
- customer payments
- supplier payments
- closing runs
- audit entries

## Why this is safe

The seed only creates master data needed to open admin screens and prepare for a controlled business cycle test.

It does not move inventory, cashbox balance, customer balance, or supplier balance through transactions.

## Business cycle position

This prepares the starting master data before the real controlled test path:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`081_FOUNDATION_CONTROLLED_BUSINESS_CYCLE_TEST_PLAN`
