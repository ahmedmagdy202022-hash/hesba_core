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
