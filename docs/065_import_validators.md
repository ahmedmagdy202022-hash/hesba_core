# 065 Import Validators

Checkpoint: `065_FOUNDATION_IMPORT_VALIDATORS`

This step adds validation before approved import batches are applied.

Added:
- `validate_import_batch(batch_id)` service.
- Row validators for categories, locations, items, customers, suppliers, cashboxes, stock, opening balances, and users.
- Per-row valid/invalid status update.
- Per-row error storage in `validation_errors`.

Rules protected:
- Approved, imported, or cancelled batches cannot be revalidated.
- Unsupported target types are blocked.
- Missing required codes/names are rejected.
- Decimal, boolean, and date formats are checked before apply.
- Stock opening rows must reference an existing stock-tracked item and existing location.
- Opening balances must reference an existing customer, supplier, or cashbox.
- User imports must reference an existing role when a role code is provided.

Business cycle impact:
- Suppliers can be safely prepared before purchase invoices.
- Items and locations can be safely prepared before inventory movement.
- Customers can be safely prepared before sales invoices.
- Cashboxes can be safely prepared before real paid movements.
- Reports stay cleaner because bad import rows stop before entering controlled tables.

Next: `066_FOUNDATION_IMPORT_ADMIN_ACTIONS`
