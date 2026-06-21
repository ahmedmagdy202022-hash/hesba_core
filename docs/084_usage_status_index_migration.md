# 084 Usage Status Index Migration

Checkpoint: `084_FOUNDATION_USAGE_STATUS_INDEX_MIGRATION`

During the local safe prep, Django detected that the `UsageStatusSnapshot` index names needed a migration.

## Cleanup applied

Added migration:

`settings_core/migrations/0004_usage_index_names.py`

The migration replaces the old auto-generated index names with the current Django-generated names.

## Why

This keeps the migration state clean so local and CI migration checks do not keep producing new migrations.

## Business cycle protected

No business logic changed. This only keeps migration history stable before testing:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`085_FOUNDATION_ADMIN_URL_ENABLED`
