# Permissions Map

## Roles

- Owner
- Manager
- Cashier
- Stock Keeper
- Accountant

## Default sensitive access

- Profit report: Owner only by default
- Cost fields: hidden from Cashier
- Supplier reports: hidden from Cashier
- Full cashbox reports: hidden from Cashier and Stock Keeper

## Rule

Menu hiding is not enough. Every route and every service must check permissions in the backend.
