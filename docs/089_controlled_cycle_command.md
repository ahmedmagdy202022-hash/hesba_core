# 089 Controlled Cycle Command

Checkpoint: `089_FOUNDATION_CONTROLLED_CYCLE_COMMAND`

This step adds a reusable management command for the local controlled business cycle smoke test.

## Added command

`python manage.py controlled_cycle_smoke_test`

## What it does

The command uses the dev seed records:
- supplier `SUP-001`
- customer `CUST-001`
- item `ITEM-001`
- location `MAIN`
- cashbox `CASH-001`

It creates the controlled cycle only if it does not already exist:
- purchase invoice `DEV-PI-TEST-001`
- sales invoice `DEV-SI-TEST-001`

If the records already exist, it prints `TEST_ALREADY_EXISTS` and still prints the current movement counts.

## Expected result after first run

- Stock now: `7`
- Supplier ledger entries: `1`
- Customer ledger entries: `1`
- Cashbox movements: `2`
- Stock movements: `2`

## Why

This avoids manual invoice entry during early testing and keeps the Core business cycle test repeatable.

## Business cycle protected

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`090_FOUNDATION_FIRST_UI_PREP`
