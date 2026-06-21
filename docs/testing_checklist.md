# First Build Testing Checklist

## Core flow test

Starting point:

- Main cashbox balance: 10000
- Main location stock: 0

Purchase:

- Buy 10 units at 100
- Invoice total: 1000
- Paid now: 600
- Supplier due: 400

Sale:

- Sell 4 units at 130
- Invoice total: 520
- Paid now: 300
- Customer due: 220

Expected result:

- Stock: 6
- Cashbox: 9700
- Supplier due: 400
- Customer due: 220
- Profit: 120

## Security tests

- Cashier cannot view cost.
- Cashier cannot view profit.
- Cashier cannot open purchase screens.
- Stock Keeper cannot view full cashbox balances.
- Accountant cannot view profit by default.
- Reports are read-only.
