# 085 Admin URL Enabled

Checkpoint: `085_FOUNDATION_ADMIN_URL_ENABLED`

The Django Admin route is now enabled in `config/urls.py`.

## Change

The local test showed that `/admin/` was not connected yet. The URL configuration now includes the Django Admin route.

## Why

This allows the first Admin smoke test to open the core tables and verify that the local database, migrations, and registered models are usable.

## Scope

This does not add business logic. It only exposes the development Admin route for local testing.

## Business cycle protected

The Admin smoke test allows visual confirmation of the controlled cycle:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`086_FOUNDATION_LOCAL_CONTROLLED_CYCLE_OK`
