# Agent Hard Gates

Status: ALL GATES RESOLVED (HG-001–HG-007 from the end-to-end run; HG-008 approved 2026-09-26)
Decision source: Main Control decision comment on PR #54
Implemented on: `agent/end-to-end-functional-cycle`, merged to `develop` via PR #54 (`c77c7e5`)
Resolved: 2026-08-30

This log records the seven protected business/accounting decisions raised during the end-to-end functional run. Main Control approved all seven decisions. They are implemented, migrated, and covered by the final verification run. No new Hard Gate remains open.

## HG-001 — Cashbox master-data permission

Status: RESOLVED

- Approved decision: add a dedicated `cashboxes.manage_cashboxes` permission. Grant it to Owner and Manager. Accountant retains finance visibility only and does not receive cashbox master-data mutation rights.
- Implementation: the permission is seeded through `permissions/migrations/0004_seed_approved_financial_permissions.py`; cashbox create/edit routes use the new permission and no longer overload `cashboxes.move_cash`.
- Safety result: identity/master-data mutation remains separate from cash movement and financial visibility.
- Verification: role matrix, authorized CRUD, route denial, finance-field visibility, and permission decorator tests passed.
- Primary checkpoint: `15f3aa9`.

## HG-002 — Opening-balance correction semantics

Status: RESOLVED

- Approved decision: an opening balance may be edited directly before operational use. After operational use, correction must be an auditable dated adjustment available to Owner and Accountant, with an append-only reversal rather than deletion or historical rewriting.
- Implementation: `master_data.adjust_opening_balances` is seeded for Owner and Accountant. Customer, supplier, and cashbox adjustments create linked ledger/cash movements, enforce closed-period rules, record actor/reason, and support dated reversal. Direct editing is limited to unused records.
- Schema: adjustment and linkage migrations are included in `cashboxes/0008`, `purchases/0005`, and `sales/0008`.
- Verification: unused and used records, permissions, audit linkage, closed periods, reversal, and report/balance reconciliation passed.
- Primary checkpoint: `15f3aa9`.

## HG-003 — Authoritative sales cost

Status: RESOLVED

- Approved decision: posted sales cost comes from the authoritative inventory movement cost. `Item.average_cost` is a maintained cache, not the accounting source of truth.
- Implementation: inventory services calculate the authoritative moving average from cost-bearing movements; sales posting snapshots that cost transactionally. Cost-affecting inventory operations refresh the item cache without allowing a stale cache value to determine posted cost.
- Verification: stale-cache regression, multiple locations, sales posting/cancellation, returns, stock validation, and profit-report reconciliation passed.
- Primary checkpoints: `efdf0cf`, `6f7e10a`.

## HG-004 — Stock transfer and adjustment services

Status: RESOLVED

- Approved decision: provide transactional stock transfer and adjustment services with linked movements, a required reason, explicit permissions, closed-period enforcement, and append-only reversal.
- Implementation: `inventory.services` owns atomic paired transfers, positive/negative adjustments, authoritative cost handling, source-stock validation, audit metadata, and reversal. Views only call these services.
- Schema: `inventory/migrations/0004_stockmovement_reversal_of_stockoperation_and_more.py` adds operation and reversal linkage.
- Verification: atomic paired directions, insufficient stock, quantity and reason validation, permission denial, cost visibility, closed periods, cache refresh, reversal, UI actions, and responsive behavior passed.
- Primary checkpoint: `efdf0cf`.

## HG-005 — Direct cash operations and cashbox transfers

Status: RESOLVED

- Approved decision: support direct cash in, direct cash out, and cashbox transfer through atomic services. Transfers create linked movements, require a reason, require matching currency, prohibit an overdrawn source, and reverse by appending linked movements.
- Implementation: `cashboxes.services` owns operation creation/cancellation, balance locking, validation, audit metadata, and closed-period enforcement. The UI is gated by `cashboxes.move_cash`.
- Schema: `cashboxes/migrations/0009_cashboxmovement_reversal_of_cashboxoperation_and_more.py` adds operation and reversal linkage.
- Verification: all operation types, atomic directions, same-currency and positive-amount rules, nonnegative source balance, permissions, closed periods, audit, reversal, cashbox report reconciliation, and bilingual responsive UI passed.
- Primary checkpoint: `2b86940`.

## HG-006 — Independent purchase and sales returns

Status: RESOLVED

- Approved decision: model independent return documents linked to the source invoice. Support partial and full quantities within remaining-return caps, reverse stock/party/cash effects, and cancel a return through an append-only reversal.
- Implementation: purchase and sales return headers/lines, services, routes, forms, detail screens, movement/ledger/cash links, settlement handling, costing, and cancellation are implemented. Existing full invoice cancellation remains a separate operation.
- Schema: return models and links are added by `purchases/0006`, `sales/0009`, `inventory/0005`–`0006`, and `cashboxes/0010`–`0011`.
- Verification: partial/full/repeated-return limits, source linkage, stock and authoritative cost, supplier/customer ledger effects, cash refunds, permission denial, closed periods, cancellation reversal, reports, Arabic/English, and responsive layouts passed.
- Primary checkpoint: `6f7e10a`.

## HG-007 — Money rounding and residual allocation

Status: RESOLVED

- Approved decision: use `ROUND_HALF_UP` to two decimal places per line, calculate invoice totals from rounded lines, allocate invoice-level amounts proportionally, and assign the residual to the last allocation so the parts reconcile exactly.
- Implementation: `config/money.py` centralizes money/cost rounding and deterministic proportional allocation. Purchase and sales posting and return services use the shared policy.
- Verification: fractional quantities, half-cent boundaries, discounts, multi-line residuals, exact invoice reconciliation, posting, reversal, returns, average cost, and reports passed.
- Primary checkpoints: `efdf0cf`, `6f7e10a`.

## HG-008 — Separate supplier visibility from shared master data

Status: RESOLVED (approved by Ahmed on 2026-09-26: «موافق على ترشيحاتك»)

- Question (roadmap Q1): the cashier could see suppliers, including their balances. Customers, suppliers, items and locations all opened with the single `master_data.view_master_data` permission.
- Approved decision: give suppliers their own view permission. The cashier, who sells and collects, does not get it. Every other role that saw suppliers keeps seeing them.
- Implementation: `permissions/migrations/0005_seed_view_suppliers_permission.py` seeds `master_data.view_suppliers` for Owner, Manager, Stock Keeper, Accountant and Support. The suppliers entry in `master_data/views.py` `ENTITY_CONFIG` uses it (list, hub card and opening-balance screens follow the config), and the Suppliers navigation item in `reports/navigation.py` requires it. `master_data.view_master_data` is unchanged.
- Risk: an installation whose roles were edited by hand keeps its edits; only the seeded roles receive the new permission. A custom role that needs suppliers must be granted `master_data.view_suppliers` in Admin.
- Verification: `permissions/tests_view_suppliers.py` covers the role matrix, cashier refusal on both supplier routes, cashier access to customers and items, the navigation and hub, and continued access for Stock Keeper and Accountant.

## HG-009 — Operating expenses (EXP-001)

Status: RESOLVED (Ahmed asked for the commercial activity to be finished end to end, expenses included, on 2026-09-26)

- Question: every shop pays rent, salaries and utilities, and Hesba had nowhere to record them. The profit report showed sales minus cost of goods only, so it overstated what the owner actually earned, and cash left the drawer with no category.
- Decision: a new `expenses` app. An expense is an additive record linked one-to-one to a **direct cash-out `CashboxOperation`** created through the existing `cashboxes.services.create_cashbox_operation`. No cashbox, ledger, posting or report calculation code is changed:
  - the negative-balance guard, the closed-period guard, row locking and the append-only reversal all stay the cashbox module's own;
  - cancelling an expense calls `cancel_cashbox_operation`, which appends the inverse movement;
  - the expense has no status of its own. It is in effect exactly while its cash operation is posted, so the two can never disagree. The cashbox operations screen refuses to reverse an operation that belongs to an expense and links to the expense instead.
- Permissions (`permissions/migrations/0006_seed_expense_permissions.py`), both under the existing `cashboxes` permission module so the permission schema is untouched:
  - `cashboxes.view_expenses` for Owner, Manager and Accountant;
  - `cashboxes.record_expenses` for Owner and Accountant, the roles that already hold `cashboxes.move_cash`, which the underlying cash operation still requires.
- Profit report: `profit_totals` is unchanged and its figure is now labelled gross profit. Viewers who may read expenses also get two new lines under it: expenses posted in the window, and net profit = gross profit − expenses. Cancelled expenses count as never spent.
- Setup and Settings: `expenses` leaves `MODULES_WITHOUT_BACKEND`, becomes a normal switchable module, and is gated by `ModuleGateMiddleware` at `/expenses/`.
- Risk: expenses record in the cashbox currency. The single-currency rule (SETTINGS-002) keeps that equal to the company currency.
- Verification: `expenses/tests.py` covers the role matrix; the cash-out and inverse rows with explicit balances; the negative-cash refusal with nothing written; rounding; sequential numbers; audit rows; window totals that ignore cancelled expenses; the screens in Arabic and English; manager read-only; cashier refusal; the module gate; category management; the cashbox screen refusing to reverse an expense; and gross/expense/net figures on the profit report.
## HG-010 — Accounting periods open themselves (PERIOD-001)

Status: RESOLVED (part of the commercial-readiness mandate, 2026-09-26)

- Problem: `closing.services.ensure_period_is_open` refused any date with no period ("No period found for this date."), and no screen could create a period; only Django Admin could. On a fresh install, cash operations, returns, stock adjustments, opening-balance corrections and expenses were therefore all refused until someone created a period by hand.
- Decision: `provision_period_for(date)` opens the **calendar-month** period for a date when none covers it, and `ensure_period_is_open` calls it before deciding. Rules:
  - it never overlaps an existing period; the month is trimmed to the gap around the date;
  - it refuses a date on or before the end of the latest **closed** period, so closed books cannot be reopened by the back door;
  - it refuses a month after the current one; a future period is opened on purpose;
  - each automatic period is audited (`closing` / `auto_open_period`);
  - completing setup opens the current month;
  - the periods screen gets "Open a month" (`closing.run_closing` only), for months up to the current one.
- Unchanged: the closed-period refusal, closing runs and summaries, reopening, post-closing adjustments, and every caller of `ensure_period_is_open`.
- Tests updated on purpose: `closing/tests_services.py` pinned the old "missing period is rejected" behaviour. It now pins the new one: a past or current month is opened, and a future month is still rejected.
- Verification: `closing/tests_auto_periods.py`:
  - a cash operation on an empty install;
  - opened once per month;
  - December bounds;
  - trimming between two existing periods;
  - refusal inside and before closed books;
  - future months refused;
  - setup opening the month;
  - the screen for the owner, a malformed month, and cashier 403.
- Observation, not changed: a **reopened** period still refuses postings (`status != open`). Whether reopening should allow corrections is a product decision for Ahmed.

## HG-011 — Invoices and payments ignore closed periods (open)

Status: OPEN, fix proposed as PERIOD-002

- Finding: `post_sales_invoice`, `cancel_posted_sales_invoice`, `post_purchase_invoice`, `cancel_posted_purchase_invoice`, and customer and supplier payments and their cancellations never call `ensure_period_is_open`. Only returns, cash operations, stock operations and adjustments do. A backdated invoice or payment can therefore be posted into a month that has already been closed, which silently changes the figures the closing run saved.
- Proposed fix: check the invoice date, payment date or cancellation date against `ensure_period_is_open` in those services, the same way returns already do, with tests for each path. With HG-010 in place this no longer blocks a fresh install.
- Risk of the fix: an installation that deliberately posts into closed months loses that ability; it has to reopen (see the HG-010 observation) or use a post-closing adjustment.

## Final gate verification

- Full Django suite: 794 tests passed in 576.477 seconds.
- Django system check: no issues.
- Production-shaped deployment check: no issues.
- Migration drift check: no changes detected.
- Existing database migration check: current.
- Fresh empty-database migration: all migrations applied successfully; follow-up migration check was current.
- Static collection dry run: all 174 assets resolved.
- Arabic/English and desktop, tablet-landscape, and mobile verification: passed with no page-level overflow or browser console errors on the sampled affected routes.

## PR #54 final-review hardening

The final blocking implementation review did not introduce a new business/accounting decision. The following defects were corrected under the already-approved service and audit rules:

- Django Admin now freezes used Customer, Supplier, and Cashbox opening balances; used Cashbox currency is also immutable. Stock/cash movements, party ledger entries, purchase/sales invoices, and purchase/sales lines are view-only. Draft creation/editing remains exclusively in the service-backed functional UI, and no transaction can be changed or bulk-deleted through Admin.
- Proportional allocations reject negative inputs and cap intermediate rounded shares so every allocation is nonnegative while the exact rounded total is preserved.
- Purchase-return stock validation and sales-return cancellation validation aggregate quantities by item/location before checking availability.
- Sales-return cash refunds lock the Cashbox row before balance validation and movement creation.
- The Cashbox master-data form freezes currency after operational use.
- Stock transfers require a nonblank reason in the form, service, and model validation path.

Regression coverage for every item above is included in the final 794-test run. No migration was required and the model-drift check remains clean.

No unresolved business or accounting decision was discovered while implementing or verifying these approvals.
