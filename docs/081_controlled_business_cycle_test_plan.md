# 081 Controlled Business Cycle Test Plan

Checkpoint: `081_FOUNDATION_CONTROLLED_BUSINESS_CYCLE_TEST_PLAN`

This step defines the first controlled test for the real Hesba Core cycle.

## Purpose

This is not a random admin test. It is the first controlled path that should prove the core rules work together.

## Required before this test

Do not start this test before:
- local safe prep passes
- migrations apply successfully
- admin smoke test passes
- dev master data seed is created

## Seed required first

Run:

`python manage.py seed_dev_master_data`

This creates the minimum master data only:
- supplier
- item
- location
- customer
- cashbox

## Controlled test path

1. Supplier exists.
2. Purchase invoice is created with at least one line.
3. Purchase is posted.
4. Inventory increases in the receiving location.
5. Supplier due increases only by remaining due.
6. Cashbox decreases only by paid now.
7. Sales invoice is created with at least one line.
8. Sale is posted.
9. Inventory decreases from the selling location.
10. Customer due increases only by remaining due.
11. Cashbox increases only by paid now.
12. Profit is based on sale amount minus cost of goods sold.
13. Reports read from the resulting controlled records.

## Rules to verify

- Sales do not create supplier dues.
- Suppliers are affected only by purchase invoices, supplier payments, and purchase returns.
- Customers are affected only by sales invoices, customer payments, and sales returns.
- Inventory is affected only by purchases, sales, returns, transfers, adjustments, and opening stock.
- Cashboxes are affected only by actual paid amounts.
- Reports remain read-only.

## What not to do during this test

Do not create more than one test business cycle at first.
Do not mix direct cash movements with invoice payments yet.
Do not test returns before the normal purchase and sale path is stable.
Do not test closing before reports are verified.

## Expected result

After one controlled cycle, we should be able to see:
- supplier balance from purchase due
- item stock by location
- customer balance from sales due
- cashbox movement from paid amounts
- profit from sale minus cost
- read-only reports reflecting the data

## Next

`082_FOUNDATION_FIRST_LAPTOP_RUN_REQUIRED`
