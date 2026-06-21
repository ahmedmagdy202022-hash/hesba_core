# 087 Report Smoke Snapshot

Checkpoint: `087_FOUNDATION_REPORT_SMOKE_SNAPSHOT`

This step adds a small read-only report smoke layer after the first local controlled cycle passed.

## Added files

- `reports/services.py`
- `reports/management/commands/report_smoke_snapshot.py`

## What the snapshot reads

The command reads existing posted movements and returns:
- supplier balance
- customer balance
- cashbox balance
- item stock by location
- supplier ledger count
- customer ledger count
- cashbox movement count
- total sales
- total cost
- total profit

## Command

`python manage.py report_smoke_snapshot`

## Business rules protected

The snapshot is read-only.

It reads from:
- supplier ledger entries
- customer ledger entries
- cashbox movements
- stock movements
- sales lines

It does not create invoices, payments, stock movements, or adjustments.

## Expected after the first controlled cycle

- Supplier balance: `600.00`
- Customer balance: `260.00`
- Cashbox balance: `700.00`
- Item location stock: `7.000`
- Total sales: `360.00`
- Total cost: `300.00`
- Total profit: `60.00`

## Business cycle protected

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`088_FOUNDATION_REPORT_SMOKE_RESULT_REVIEW`
