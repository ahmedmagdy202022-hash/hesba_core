# 086 Local Controlled Cycle OK

Checkpoint: `086_FOUNDATION_LOCAL_CONTROLLED_CYCLE_OK`

The first local controlled Hesba Core business cycle was executed successfully on the laptop.

## Result

The local run printed:

- `CONTROLLED_CYCLE_OK`
- Stock now: `7`
- Supplier ledger entries: `1`
- Customer ledger entries: `1`
- Cashbox movements: `2`
- Stock movements: `2`

## Meaning

The controlled test proved the first full chain works locally:

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox

## Verified rules

- Purchase invoice increased inventory.
- Purchase paid amount reduced the cashbox only by paid now.
- Purchase remaining due created supplier due.
- Sales invoice decreased inventory.
- Sales paid amount increased the cashbox only by paid now.
- Sales remaining due created customer due.
- Sales did not create supplier due.

## Next

`087_FOUNDATION_REPORT_SMOKE_SNAPSHOT`
