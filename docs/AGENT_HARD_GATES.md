# Agent Hard Gates

Status: ACTIVE LOG

Record only genuine blockers that require a protected business/product decision. Continue all unaffected work.

## HG-001 — Cashbox master-data permission is undefined

- Track: 1 / 6
- Reason: the permission matrix contains `cashboxes.view_cashboxes`, `cashboxes.move_cash`, and `cashboxes.view_finance`, but no capability that safely authorizes creating or editing Cashbox master data.
- Protected files/behavior: `permissions/migrations/0002_seed_foundation_roles_permissions.py`, permission semantics, `cashboxes.Cashbox` opening balances.
- Risk: reusing `cashboxes.move_cash` would let an operational cash-movement permission alter identity and opening-balance data without an approved audit/accounting policy.
- Proposed decision: approve and seed a dedicated `cashboxes.manage_cashboxes` permission, including role grants and opening-balance edit rules.
- Required tests after approval: role matrix, route/service denial, create audit, opening-balance lock after operational use, and financial-field visibility.
- Current safe behavior: Cashbox master data remains view-only.

## HG-002 — Opening-balance correction semantics are undefined

- Track: 1
- Reason: Customer, Supplier, and Cashbox models allow initial opening balances, but no approved service defines later correction after ledger or cash movements exist.
- Protected files/behavior: party ledgers, cashbox movements, opening-balance fields, audit behavior.
- Risk: direct edits would rewrite historical balances without traceable compensating entries.
- Proposed decision: define whether correction creates dated ledger/cashbox adjustment entries, which period receives them, and who may approve them.
- Required tests after approval: unused-record correction, used-record correction/rejection, closed-period handling, reversal, audit, and report reconciliation.
- Current safe behavior: Customer and Supplier opening balances are locked after creation; Cashboxes are view-only.

## HG-003 — Sales cost can use a stale stored average cost

- Track: 4
- Reason: `sales.services._line_unit_cost()` reads `Item.average_cost`, while the authoritative value is derived from stock movements and the stored field can be stale if movements were introduced outside the purchase services.
- Protected files/behavior: `sales/services.py`, `inventory/services.py`, average-cost and profit calculations.
- Risk: posted sales can preserve an incorrect cost/profit value even though stock quantity validation is correct.
- Proposed decision: approve either transactional recalculation/locking before sales costing or a stronger invariant that all cost-affecting movements refresh the stored average.
- Required tests after approval: stale stored cost characterization, multiple locations, concurrent posting, returns/cancellations, zero/negative stock, and profit-report reconciliation.
- Current safe behavior: no silent cost-logic change; the functional UI will characterize and disclose the existing behavior.

## HG-004 — Stock transfer and adjustment services are missing

- Track: 3
- Reason: permissions and movement types exist, but `inventory/services.py` has no approved transactional transfer or adjustment command. Only read/recalculation helpers exist.
- Protected files/behavior: inventory movement/accounting architecture, average-cost behavior, closed-period handling.
- Risk: creating movement rows directly in views could produce one-sided transfers, bypass validation/audit, or expose/alter cost incorrectly.
- Proposed decision: approve service contracts for paired transfers and controlled adjustments, including date/period, cost, reason, and audit rules.
- Required tests after approval: atomic paired directions, quantity and source-stock validation, permissions, cost visibility, closed periods, reversal, and concurrency.
- Current safe behavior: stock, item detail, and movement history are exposed read-only; transfer/adjustment actions are not offered.

## HG-005 — Direct cash in/out and cashbox transfer services are missing

- Track: 6
- Reason: `cashboxes.move_cash` and movement types exist, but no service owns direct cash movements or paired cashbox transfers.
- Protected files/behavior: cashbox movement/accounting, balances, audit, and closed-period rules.
- Risk: direct model writes from views could create unbalanced transfers, bypass reason/date validation, or mutate a closed period.
- Proposed decision: approve service contracts for direct in/out and atomic paired transfers, with reason, actor, date/period, and reversal semantics.
- Required tests after approval: atomic directions, positive amount, insufficient-balance policy, permissions, closed periods, reversal, audit, and report reconciliation.
- Current safe behavior: cashbox balances (permission-gated) and movement history are read-only.

## HG-006 — Independent purchase/sales return semantics are not modeled

- Track: 2 / 4
- Reason: existing services support complete invoice cancellation/reversal, but there are no return header/line models or services for independent or partial returns.
- Protected files/behavior: purchase/sales posting, ledgers, stock, cashbox, cost/profit, and return accounting.
- Risk: presenting cancellation as a general return, or assembling partial reversal rows in views, would misstate business history.
- Proposed decision: approve return documents, allocation to invoice lines/payments, costing rules, cash settlement, and period behavior.
- Required tests after approval: partial/full quantity limits, repeated return protection, stock/cost, party balance, refund/cashbox, tax/discount allocation, and closed periods.
- Current safe behavior: complete posted-invoice cancellation is exposed explicitly as cancellation; no independent return action is claimed.
