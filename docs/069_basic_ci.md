# 069 Basic CI

Checkpoint: `069_FOUNDATION_BASIC_CI`

This step adds a basic GitHub Actions workflow for the Django project.

Added:
- `.github/workflows/django-tests.yml`

Workflow triggers:
- Push to `main`
- Pull requests

Checks included:
- Install dependencies from `requirements.txt`
- Run Django system checks
- Check that migrations are committed
- Run Django tests

Why this matters:
- Import workflow tests can run automatically.
- New changes should not silently break purchases, sales, inventory, payments, reports, or import services.
- Migration drift can be caught before deployment.

Business cycle impact:
- CI adds a safety gate around the full Core path:
  Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

Next: `070_FOUNDATION_IMPORT_FIXES_AFTER_CI`
