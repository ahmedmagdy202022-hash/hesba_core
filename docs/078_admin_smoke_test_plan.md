# 078 Admin Smoke Test Plan

Checkpoint: `078_FOUNDATION_ADMIN_SMOKE_TEST_PLAN`

This step defines a simple Django Admin smoke test after the local migration checks pass.

## Purpose

The goal is not full business testing yet. The goal is only to confirm that the admin opens and the core tables are visible without server errors.

## Required before this step

Run safe local checks first:
- `scripts/dev_safe_test_prep.ps1` on Windows
- `scripts/dev_safe_test_prep.sh` on Bash

Only continue if the safe prep passes.

## Admin smoke test order

1. Start the server.
2. Open Django Admin.
3. Log in with a superuser.
4. Confirm the core apps appear.
5. Open each model list page without creating data first.
6. Open add pages only for master data first.

## First admin areas to verify

Master data:
- Categories
- Locations
- Items
- Customers
- Suppliers

Cashboxes:
- Cashboxes
- Cashbox movements should stay review-only during first smoke check

Purchases:
- Purchase invoices
- Purchase lines through invoice flow later
- Supplier payments later after cashboxes are verified

Sales:
- Sales invoices
- Sales lines through invoice flow later
- Customer payments later after cashboxes are verified

Inventory:
- Stock movements
- Transfers and adjustments later

Imports:
- Import batches
- Raw rows
- Review rows

Closing:
- Periods
- Closing runs
- Period summaries
- Post-closing adjustments

Security and audit:
- Roles
- Permissions
- User profiles
- Audit logs should be read-only in normal use

## What not to test yet

Do not create random invoices before master data and migrations are confirmed.
Do not test profit reports before cost and stock movement flow is confirmed.
Do not test closed period reopening before normal period flow is confirmed.
Do not edit audit log rows.

## Business cycle smoke path later

After master data opens correctly, the first controlled business cycle test will be:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`079_FOUNDATION_ADMIN_SMOKE_TEST_INSTRUCTIONS`
