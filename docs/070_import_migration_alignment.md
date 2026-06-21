# 070 Import Migration Alignment

Checkpoint: `070_FOUNDATION_IMPORT_MIGRATION_ALIGNMENT`

This step aligns the import migrations with the current import models before relying on CI migration checks.

Added:
- `imports/migrations/0003_import_status_choices_and_options.py`

Why:
- The current import models include choices for batch status, raw row status, and review status.
- The current import models include verbose names for the admin screens.
- The migration history needs to match the model state so `makemigrations --check --dry-run` has a better chance to stay clean.

Protected areas:
- Import batch status values.
- Import raw row status values.
- Import review status values.
- Admin model naming for import screens.

Business cycle impact:
- This does not change operational sales, purchases, inventory, customers, suppliers, or cashboxes.
- It protects the Go-Live import path that prepares master data, opening stock, and opening balances before live transactions start.

Next: `071_FOUNDATION_CI_RESULT_REVIEW`
