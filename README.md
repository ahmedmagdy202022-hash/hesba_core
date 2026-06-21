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

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

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
