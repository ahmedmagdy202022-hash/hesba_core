# 090 First UI Prep

Checkpoint: `090_FOUNDATION_FIRST_UI_PREP`

This step adds the first safe local UI route.

## Added

- Root URL `/`
- Simple home view in `reports.views.home`
- Admin remains available at `/admin/`

## Scope

This is only a first UI preparation step.

It does not change business logic.
It does not create invoices.
It does not move inventory.
It does not change customer, supplier, or cashbox balances.

## Business cycle protected

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`091_FOUNDATION_FIRST_UI_DASHBOARD_CARDS`
