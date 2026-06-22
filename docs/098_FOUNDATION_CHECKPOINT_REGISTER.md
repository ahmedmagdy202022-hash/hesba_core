# 098_FOUNDATION_CHECKPOINT_REGISTER

Status: OK

Purpose:
- Keep the repository checkpoint register aligned after UI and CI work.
- Document that checkpoints 094 through 097 are completed.

Scope:
- Documentation only.
- No migrations.
- No model changes.
- No business posting changes.

Protected cycle:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

Rules protected:
- Reports remain read-only.
- Sales do not create supplier dues.
- Purchases do not create customer dues.
- Cashboxes move only by actual paid amounts.
- Cost and profit remain protected by permissions.
