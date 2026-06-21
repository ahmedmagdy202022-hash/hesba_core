# 066 Import Admin Actions

Checkpoint: `066_FOUNDATION_IMPORT_ADMIN_ACTIONS`

This step exposes the controlled import workflow in Django Admin.

Added to `ImportBatchAdmin`:
- Validate selected import batches.
- Approve selected import batches.
- Apply selected import batches.

Rules protected:
- Import batches still cannot be deleted from admin.
- Validate calls the controlled validator service.
- Approve calls the controlled approval service.
- Apply calls the controlled apply service and passes the acting admin user for traceable stock movement creation.
- Each action reports success or validation errors through Django admin messages.

Business cycle impact:
- Suppliers, items, locations, customers, and cashboxes can now move from imported rows into controlled Core tables through one reviewed path.
- Opening stock enters inventory as stock movements by item and location.
- Opening balances prepare customers, suppliers, and cashboxes before live invoices/payments start.
- Reports remain read-only and read from controlled records after import.

Next: `067_FOUNDATION_IMPORT_SAMPLE_TEMPLATES`
