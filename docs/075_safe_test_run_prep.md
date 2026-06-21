# 075 Safe Test Run Prep

Checkpoint: `075_FOUNDATION_SAFE_TEST_RUN_PREP`

This step prepares a safer test path before asking for any laptop/manual run.

Added:
- `scripts/dev_safe_test_prep.sh`

The script runs checks that are useful before applying migrations:
- `python manage.py check`
- `python manage.py showmigrations`
- `python manage.py migrate --plan`
- `python manage.py makemigrations --check --dry-run`

Why this script exists:
- It shows the current migration state.
- It shows the migration plan before changing the database.
- It keeps the migration drift check available.
- It avoids running full test data operations before the migration state is understood.

Manual use later:
```bash
bash scripts/dev_safe_test_prep.sh
```

Business cycle impact:
- No operational business logic changed.
- This prepares safe verification before purchases, sales, inventory, customer balances, supplier balances, cashbox movements, and reports are tested.

Next: `076_FOUNDATION_LOCAL_TEST_INSTRUCTIONS`
