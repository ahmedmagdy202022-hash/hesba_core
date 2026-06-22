# 103_FOUNDATION_STATUS_TEMPLATE

Status: OK

Scope:
- Adds a dedicated template for the Status counts page.
- Keeps the report read-only.
- Keeps counts only.

No migrations.
No model changes.
No posting logic changes.
No money totals, balances, cost, or profit values.

Protected cycle:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports
