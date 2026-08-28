# Hesba Core / حِسْبَة Core

PostgreSQL-first business management core for stores, services, telecom, inventory, and future activities.

## Current target edition

`HESBA_LITE_STORE_SERVICES`

## First build focus

- Django + PostgreSQL
- Arabic RTL web app
- Separate database per client
- Multi-line purchase invoices
- Multi-line sales invoices
- Inventory by item and location
- Customer, supplier, and cashbox balances from real movements
- Read-only reports
- Role-based access

## Business cycle

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Current checkpoints

- `047_GITHUB_INITIAL_STRUCTURE_MERGED`
- `048_FOUNDATION_APPS_SKELETON_READY`
- `049_FOUNDATION_MODELS_SETTINGS_ROLES_PERMISSIONS = OK`
- `050_FOUNDATION_MASTER_DATA_MODELS = OK`
- `051_FOUNDATION_PURCHASE_INVOICE_MODELS = OK`
- `052_FOUNDATION_PURCHASE_POSTING = OK`
- `053_FOUNDATION_PURCHASE_COST_AND_REVERSAL = OK`
- `054_FOUNDATION_SUPPLIER_PAYMENTS = OK`
- `055_FOUNDATION_SALES_INVOICE_MODELS = OK`
- `056_FOUNDATION_SALES_POSTING = OK`
- `057_FOUNDATION_CUSTOMER_PAYMENTS = OK`
- `058_FOUNDATION_REPORTS_BASE_VIEWS = OK`
- `059_FOUNDATION_PERIOD_MODELS = OK`
- `060_FOUNDATION_PERIOD_RUN_SERVICES = OK`
- `061_FOUNDATION_POST_CLOSING_ADJUSTMENT_SERVICES = OK`
- `062_FOUNDATION_USAGE_STATUS = OK`
- `063_FOUNDATION_IMPORT_BATCHES = OK`
- `064_FOUNDATION_IMPORT_APPLY_SERVICES = OK`
- `065_FOUNDATION_IMPORT_VALIDATORS = OK`
- `066_FOUNDATION_IMPORT_ADMIN_ACTIONS = OK`
- `067_FOUNDATION_IMPORT_SAMPLE_TEMPLATES = OK`
- `068_FOUNDATION_IMPORT_TEST_CASES = OK`
- `069_FOUNDATION_BASIC_CI = OK`
- `070_FOUNDATION_IMPORT_MIGRATION_ALIGNMENT = OK`
- `071_FOUNDATION_CI_MIGRATION_FIXES = OK`
- `072_FOUNDATION_CI_NOISE_CONTROL = OK`
- `073_FOUNDATION_LOCAL_CI_CHECK_SCRIPT = OK`
- `074_FOUNDATION_MIGRATION_STATE_REVIEW = OK`
- `075_FOUNDATION_SAFE_TEST_RUN_PREP = OK`
- `076_FOUNDATION_LOCAL_TEST_INSTRUCTIONS = OK`
- `077_FOUNDATION_LOCAL_TEST_RESULT_REVIEW = OK`
- `078_FOUNDATION_ADMIN_SMOKE_TEST_PLAN = OK`
- `079_FOUNDATION_ADMIN_SMOKE_TEST_INSTRUCTIONS = OK`
- `080_FOUNDATION_FIRST_ADMIN_DATA_SEED_PLAN = OK`
- `081_FOUNDATION_CONTROLLED_BUSINESS_CYCLE_TEST_PLAN = OK`
- `082_FOUNDATION_FIRST_LAPTOP_RUN_REQUIRED = OK`
- `083_FOUNDATION_MIGRATION_CONFLICT_CLEANUP = OK`
- `084_FOUNDATION_USAGE_STATUS_INDEX_MIGRATION = OK`
- `085_FOUNDATION_ADMIN_URL_ENABLED = OK`
- `086_FOUNDATION_LOCAL_CONTROLLED_CYCLE_OK = OK`
- `087_FOUNDATION_REPORT_SMOKE_SNAPSHOT = OK`
- `088_FOUNDATION_LOCAL_REPORT_RESULT_REVIEW = OK`
- `089_FOUNDATION_CONTROLLED_CYCLE_COMMAND = OK`
- `090_FOUNDATION_FIRST_UI_PREP = OK`
- `091_FOUNDATION_FIRST_UI_CHECK = OK`
- `092_FOUNDATION_MOBILE_TABLET_CONTINUATION = OK`
- `093_FOUNDATION_FIRST_UI_NAVIGATION_MAP = OK`
- `094_FOUNDATION_DASHBOARD_SNAPSHOT = OK`
- `095_RESTORE_STRICT_MIGRATION_CHECK = OK`
- `096_FOUNDATION_READ_ONLY_REPORT_HUB = OK`
- `097_STATUS_ROUTE_ENTRY = OK`
- `098_FOUNDATION_CHECKPOINT_REGISTER = OK`
- `099_FOUNDATION_SAFE_STATUS_COUNTS = OK`
- `100_FOUNDATION_EXPANDED_SAFE_STATUS_COUNTS = OK`
- `101_FOUNDATION_CHECKPOINT_REGISTER_AFTER_STATUS_COUNTS = OK`

## First build order

1. Foundation
2. Master data
3. Purchases
4. Sales
5. Payments
6. Reports
7. Dashboard
8. Security tests
9. Delivery package

## Runtime configuration

Local development uses SQLite by default, so no environment file is required:

```powershell
python manage.py migrate
python manage.py runserver
```

Production settings are environment-driven. Copy `.env.example` outside source
control (or configure the same variables in the deployment platform), replace
every placeholder, and use `DATABASE_BACKEND=postgresql`. The PostgreSQL driver
is included in `requirements.txt`; database credentials and the target host are
intentionally not stored in this repository.

Before deployment, run:

```powershell
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py test
```

TLS is assumed in production. Secure cookies, HTTPS redirection, and HSTS default
on whenever `DEBUG=False`. Set `TRUST_PROXY_SSL_HEADER=True` only when the named
deployment proxy is trusted to replace the forwarded-protocol header.
