# 107 Working ERP scope

This checkpoint moves Hesba from a static investor screen toward working ERP controls.

## Implemented in this branch

- Owner dashboard context reads live counts and totals from existing models.
- Quick actions link to real Admin add/list screens.
- Purchase invoices now have Admin actions to post and cancel through controlled services.
- Sales invoices now have Admin actions to post and cancel through controlled services.
- Sector module scaffolding added for store, services, construction, and factory use cases.
- Construction project cost model added with paid-now validation.
- Product recipe and component line models added for factory/BOM foundation.
- Recipe requirement calculation helper added.
- Project cost service creates a cashbox out movement only by paid_now.

## Accounting rules preserved

- Sales do not affect suppliers.
- Purchases do not affect customers.
- Cashboxes move only by actual paid amounts.
- Inventory is calculated from stock movements.
- Cost and profit remain protected.
- Reports read data only and do not post transactions.

## Still required before production

- Run and commit migrations for the new sector module.
- Build dedicated non-admin forms for sales, purchases, customers, suppliers, cashboxes, projects, and production.
- Add PDF templates and print actions.
- Add permission tests for owner, manager, cashier, stock keeper, and accountant.
- Add full construction progress billing and production order posting in controlled services.
