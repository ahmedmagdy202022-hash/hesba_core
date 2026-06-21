# 060 Period Services

Checkpoint: `060_FOUNDATION_CLOSING_SERVICES`

## Scope

This step adds the first services for period end processing.

Added:

- Build saved period summary payload
- Complete period run service
- Save PeriodSummary rows
- Mark period as closed
- Reopen period with reason
- Audit log for close and reopen

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

Period services read from reports and posted records, then save summaries for review.

## Rules

- Period summaries are read-only saved results.
- Closed periods are read-only by default.
- Reopen requires a reason.
- Reopen is prepared for owner-only control in UI/API layer.
- Transaction details remain traceable.
- Summaries do not replace original transaction rows.

## Summary values saved

- Sales total
- Purchase total
- Profit total
- Stock value total
- Customer balance total
- Supplier balance total
- Cashbox balance total

## Next after merge

`061_FOUNDATION_POST_CLOSING_ADJUSTMENT_SERVICES`
