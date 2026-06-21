# Database Tables Build Order

The first database build must follow the business cycle and dependency order.

## Build order

1. settings and edition settings
2. users, roles, and permissions
3. clients and locations
4. categories and items
5. customers and suppliers
6. cashboxes
7. opening balances and opening stock
8. purchase invoices and purchase lines
9. sales invoices and sales lines
10. customer and supplier payments
11. stock movements
12. cashbox movements
13. returns, adjustments, and transfers
14. period tables
15. report views
16. audit log
17. import and barcode future tables

## Balance rule

Balances are not manually edited.

- Customer balance comes from sales, customer payments, sales returns, and opening balance.
- Supplier balance comes from purchases, supplier payments, purchase returns, and opening balance.
- Cashbox balance comes from opening balance and cashbox movements.
- Stock balance comes from opening stock and stock movements.
