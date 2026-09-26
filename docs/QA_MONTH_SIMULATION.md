# QA-001 — one trading month, reconciled

`reports/tests_month_acceptance.py` runs a full calendar month of a small grocery
through the real services (last calendar month, so it never touches the future):
capital, rent, two purchases, 26 days of walk-in cash sales, a credit customer
who pays and returns goods, a supplier payment, a purchase return, electricity,
and the month-end close.

Figures are worked out by hand from the story and asserted exactly:

| Check | Expected | Agreeing sources |
|---|---|---|
| Cash in the till | 16,087.50 | cashbox service, cashbox report, closing snapshot |
| Stock (rice / oil / tea) | 98 / 42 / 102 | movement ledger, stock report |
| Karim owes | 0.00 | customer report, aging, dashboard receivables |
| Shop owes Delta | 1,987.50 | supplier report, aging, closing snapshot |
| Net sales | 4,150.00 | profit report, dashboard |
| Cost of goods | 2,717.93 (rice after P2 at 20.7353) | movement costs, profit report |
| Gross profit | 1,432.07 | profit report, dashboard, closing snapshot |
| Expenses / net profit | 1,250.00 / 182.07 | expenses, dashboard |
| After close | posting a sale or an expense into the month is refused; nothing moves | |

## Finding (no change made)
The stock report values stock as `quantity × Item.average_cost` (the display cache),
while the movement ledger gives the exact value. For a moving-average item the two can
differ by a fraction of a cent (here 2,032.0594 vs 2,032.0586); both show as 2,032.06.
Report calculations are protected logic, so this is documented and pinned by the test
rather than changed. If the closing snapshot ever needs to match the ledger to the
fourth decimal, switch `stock_report` to `get_item_stock_value` (a Hard Gate decision).
