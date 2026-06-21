# Hesba Import Templates

Checkpoint: `067_FOUNDATION_IMPORT_SAMPLE_TEMPLATES`

Use these CSV templates as starting files for Go-Live imports.

Recommended order:
1. `categories.csv`
2. `locations.csv`
3. `items.csv`
4. `customers.csv`
5. `suppliers.csv`
6. `cashboxes.csv`
7. `opening_stock.csv`
8. `opening_balances.csv`
9. `users.csv`

Important rules:
- Keep column names unchanged.
- Keep source files outside the database and import the rows only.
- Do not import full historical transactions unless reviewed and required.
- Use Go-Live date plus opening stock and opening balances as the preferred migration method.
- `opening_stock.csv` must reference existing items and locations.
- `opening_balances.csv` must reference existing customers, suppliers, or cashboxes.
- Reports and dashboards stay read-only after import.
