# 079 Admin Smoke Test Instructions

Checkpoint: `079_FOUNDATION_ADMIN_SMOKE_TEST_INSTRUCTIONS`

This step defines the exact first admin smoke test instructions to use after the safe local checks pass.

## Start point

Only start this after:
- local dependencies are installed
- safe prep script passes
- migrations are applied successfully

## Commands to prepare admin access

Create a superuser only in the local dev database:

`python manage.py createsuperuser`

Start the local server:

`python manage.py runserver`

Open:

`http://127.0.0.1:8000/admin/`

## First login checks

After login:
1. Confirm the admin homepage opens.
2. Confirm the apps list appears.
3. Do not create invoices yet.
4. Do not edit audit logs.
5. Do not test closing or reopening yet.

## First pages to open only

Open list pages first:
- Users
- Roles
- Permissions
- User profiles
- Categories
- Locations
- Items
- Customers
- Suppliers
- Cashboxes
- Purchase invoices
- Sales invoices
- Stock movements
- Import batches
- Periods
- Audit logs

## First add pages allowed

Only open add pages for clean master data first:
- Category
- Location
- Item
- Customer
- Supplier
- Cashbox

## Do not create transaction data yet

Do not create these yet during first admin smoke test:
- Purchase invoice
- Sales invoice
- Supplier payment
- Customer payment
- Stock movement
- Closing run
- Post-closing adjustment

## Expected result

The expected result is simple:
- Admin opens.
- List pages open.
- Add pages for master data open.
- No server error appears.

## Next

`080_FOUNDATION_FIRST_ADMIN_DATA_SEED_PLAN`
