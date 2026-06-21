# 062 Usage Status

Checkpoint: `062_FOUNDATION_USAGE_STATUS`

## Scope

This step adds a simple usage warning foundation.

Added:

- Usage status levels: Green, Yellow, Orange, Red
- Usage status snapshot model
- Usage status service
- Admin registration
- Migration

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

Usage status reads activity size from the core records and helps protect the client from unexpected running cost growth.

## Status meaning

- Green: normal usage
- Yellow: usage is increasing
- Orange: close to safe limits
- Red: action needed

## Recommendations

Before increasing cost, suggest:

- Close periods regularly
- Archive old periods
- Clean trial data
- Limit report date ranges
- Save file links instead of file blobs
- Use summaries for heavy reports

## Next after merge

`063_FOUNDATION_IMPORT_BATCHES`
