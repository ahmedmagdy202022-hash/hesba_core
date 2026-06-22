# 093_FOUNDATION_FIRST_UI_NAVIGATION_MAP

Status: OK

## Purpose

Start the first safe UI screen after the admin-only foundation and the local controlled business cycle test.

This checkpoint creates a simple Arabic RTL navigation map for the first Hesba Core web UI without changing any data model, posting service, migration, invoice logic, stock logic, balance logic, or report selector.

## What changed

- Replaced the temporary home response with a rendered Django template.
- Added a responsive Arabic RTL home page at `/`.
- Added navigation sections connected to the full Hesba business cycle:
  - Supplier
  - Purchase Invoice
  - Inventory by Location
  - Sales Invoice
  - Customer
  - Cashbox
  - Reports
- Added links to current Django Admin screens for the safe existing foundation records.
- Added protected-rule reminders on the page.
- Added UI smoke tests for the first navigation map.
- Updated README checkpoints with 092 and 093.

## Protected rules

- Sales do not create supplier dues.
- Purchases do not create customer dues.
- Inventory remains calculated by item and location from stock movements.
- Cashboxes are affected only by actual paid amounts.
- Reports are read-only.
- Profit remains Sales minus Cost of Goods Sold.

## What did not change

- No migrations.
- No new tables.
- No changes to purchase posting.
- No changes to sales posting.
- No changes to customer, supplier, cashbox, inventory, or profit calculations.
- No changes to old AppSheet demos.

## How to test locally

Run:

```bash
python manage.py test reports
python manage.py runserver
```

Then open:

```text
http://127.0.0.1:8000/
```

Expected result:

- The first UI navigation map opens.
- The page shows the full business cycle.
- Admin links are visible.
- Protected rules are visible.
- `/admin/` remains available.

## Next checkpoint

094_FOUNDATION_FIRST_UI_REPORT_LINKS_OR_DASHBOARD_SNAPSHOT
