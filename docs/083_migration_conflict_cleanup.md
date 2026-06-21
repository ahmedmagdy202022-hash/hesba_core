# 083 Migration Conflict Cleanup

Checkpoint: `083_FOUNDATION_MIGRATION_CONFLICT_CLEANUP`

During the first local safe prep run, Django detected multiple migration leaf nodes.

## Cause

Some manually added repair migrations duplicated existing app migration chains.

## Cleanup applied

Removed duplicate repair migrations and kept the existing earlier migration chains as the source of truth.

Removed duplicate branches from:
- sales
- purchases
- cashboxes
- inventory
- closing

## Why

The migration graph must have one clear path per app before local migration planning can continue.

## Business cycle protected

No business logic was changed. The cleanup only protects migration order before testing:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

Re-run safe local test prep after the local copy is refreshed or the duplicate local files are removed.
