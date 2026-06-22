# 102_FOUNDATION_UI_NAV_SEPARATION

Status: OK

Scope:
- UI navigation only.
- Adds clear top navigation for Dashboard, Reports, and Status.
- Keeps Admin as a temporary internal entry point.

No migrations.
No model changes.
No posting logic changes.

Protected cycle:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports
