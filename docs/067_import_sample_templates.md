# 067 Import Sample Templates

Checkpoint: `067_FOUNDATION_IMPORT_SAMPLE_TEMPLATES`

This step adds CSV starter templates for the controlled Go-Live import flow.

Added under `import_templates/`:
- `categories.csv`
- `locations.csv`
- `items.csv`
- `customers.csv`
- `suppliers.csv`
- `cashboxes.csv`
- `opening_stock.csv`
- `opening_balances.csv`
- `users.csv`
- `README.md`

Recommended order:
1. Categories
2. Locations
3. Items
4. Customers
5. Suppliers
6. Cashboxes
7. Opening stock
8. Opening balances
9. Users

Rules protected:
- Column names match the validator and apply services.
- Opening stock references item code and location code.
- Opening balances reference existing customer, supplier, or cashbox codes.
- User rows reference role codes.
- Templates support the preferred migration method: Go-Live date plus opening stock and opening balances.

Business cycle impact:
- Suppliers are ready before purchase invoices.
- Items and locations are ready before inventory by location.
- Customers are ready before sales invoices.
- Cashboxes are ready before actual paid movements.
- Reports stay read-only and calculate from controlled tables after import.

Next: `068_FOUNDATION_IMPORT_TEST_CASES`
