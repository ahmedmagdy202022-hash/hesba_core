# 059 Period and Closing Models

Checkpoint: `059_FOUNDATION_PERIOD_AND_CLOSING_MODELS`

## Scope

This step adds the first period and closing foundation.

Added:

- Period
- ClosingRun
- PeriodSummary
- PostClosingAdjustment
- Period guard helper
- Admin registrations
- Migrations

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

Closing uses reports and movement records to save period summaries. It does not replace the transaction trail.

## Rules

- Closed periods are read-only by default.
- Corrections should normally be posted in the current open period.
- A closed period can be reopened only later through owner-only logic with reason and audit log.
- Period summaries are saved results for review, print, and export.
- Transaction rows remain traceable.

## Default recommendation

Quarterly closing is the default recommendation.

## Next after merge

`060_FOUNDATION_CLOSING_SERVICES`
