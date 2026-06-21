# 068 Import Test Cases

Checkpoint: `068_FOUNDATION_IMPORT_TEST_CASES`

This step adds automated tests for the controlled import workflow.

Added:
- `imports/tests.py`

Covered scenarios:
- Category batch can be validated, approved, and applied.
- Invalid item batch is blocked before approval.
- Opening stock batch creates traceable stock movement rows.
- Opening balances batch updates existing customer, supplier, and cashbox records.

Rules protected:
- Invalid rows remain invalid and block approval.
- Valid rows can move through the full path: raw row, validation, approval, apply.
- Applied rows store target model and target object id for traceability.
- Opening stock uses the stock movement table instead of direct balance updates.
- Opening balances update only the related master records.

Business cycle impact:
- Import tests protect the Go-Live path before live purchase invoices, sales invoices, stock movement, customer balances, supplier balances, cashboxes, and reports are used.

Next: `069_FOUNDATION_BASIC_CI`
