# Hesba Core Business Rules

## Business cycle

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Rules

1. Sales do not create supplier dues.
2. Suppliers are affected by purchase invoices, supplier payments, and purchase returns.
3. Customers are affected by sales invoices, customer payments, and sales returns.
4. Inventory is affected by purchases, sales, returns, transfers, adjustments, and opening stock.
5. Cashboxes are affected only by actual paid amounts.
6. Profit equals sales minus cost of goods sold.
7. Reports and dashboards are read-only.
8. Cost and profit must be protected by permissions.
9. Raw input stays clean. Calculations belong in controlled logic and reports.
10. Every transaction must be traceable.
