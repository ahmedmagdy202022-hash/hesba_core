# 073 Local CI Check Script

Checkpoint: `073_FOUNDATION_LOCAL_CI_CHECK_SCRIPT`

This step adds a small local script that runs the same basic quality checks as the GitHub workflow.

Added:
- `scripts/ci_local_check.sh`

The script runs:
- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py test`

Why:
- While direct push CI emails are paused, the same checks can still be run locally from a laptop when needed.
- This keeps the migration and test gate available without creating repeated GitHub notification emails during repair work.

Business cycle impact:
- No business logic changed.
- This protects the Core path before more work continues:
  Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

Next: `074_FOUNDATION_MIGRATION_STATE_REVIEW`
