# 064 Import Apply Services

Checkpoint: `064_FOUNDATION_IMPORT_APPLY_SERVICES`

This step turns approved import batches into controlled Hesba records.

Added:
- Apply service for approved import batches.
- Effective row data selection from corrected review rows when available.
- Import handlers for categories, locations, items, customers, suppliers, cashboxes, users, opening stock, and opening balances.
- Traceability from every imported row to the created or updated target object.

Rules protected:
- Raw import data stays unchanged.
- Only approved batches can be applied.
- Only valid rows can be imported.
- Stock import creates opening stock movements by item and location.
- Opening balances update customers, suppliers, and cashboxes only.
- Imported users get application profiles and no automatic operational data access beyond their assigned role.

Business cycle impact:
- Supplier master data supports purchase invoices.
- Item and location master data support inventory by location.
- Customer master data supports sales invoices.
- Cashboxes support actual paid movements.
- Reports remain read-only and calculate from controlled tables.

Next: `065_FOUNDATION_IMPORT_VALIDATORS`
