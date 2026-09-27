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
- Observation at the time: a **reopened** period still refused postings (`status != open`). This was resolved in HG-011: a reopened period now takes corrections.

## HG-011 — Invoices and payments respect closed periods (PERIOD-002)

Status: RESOLVED (part of the commercial-readiness mandate, 2026-09-26)

- Finding: `post_sales_invoice`, `cancel_posted_sales_invoice`, `post_purchase_invoice`, `cancel_posted_purchase_invoice`, `record_customer_payment`, `cancel_customer_payment`, `record_supplier_payment` and `cancel_supplier_payment` never called `ensure_period_is_open`; only returns, cash operations, stock operations and adjustments did. A backdated invoice or payment could be posted into a closed month and silently change the figures its closing run had saved.
- Fix: each of those eight services now checks its document date, the same way returns already did. Invoice cancellation writes its reversing rows on the invoice date, so it checks that date. The check runs before any row is written, and the refusal leaves nothing behind.
- Reopened periods: `ensure_period_is_open` now accepts **open or reopened**. Reopening is owner-only, needs a reason and is audited, and it exists to correct a period, so it takes entries until the period is closed again. Before, a reopened period refused everything, which left no legitimate way to enter a late invoice.
- Tests updated on purpose:
  - `closing/tests_services.py` pinned "a reopened period is rejected"; it now pins that a reopened period takes corrections.
  - The re-closing test posted its late invoice into a *closed* period, which was the bug itself; it now reopens first, then posts.
- Risk: an installation that deliberately posted into closed months must now reopen the month, or record a post-closing adjustment in the open period.
- Verification: `closing/tests_period_lock.py` covers sale post and cancel, purchase post and cancel, customer payment record and cancel, and supplier payment record and cancel, each refused in a closed month with no cash, stock or ledger row written, plus a reopened month accepting the late invoice.

## HG-012 — Staff accounts in Hesba; Django Admin for superusers only (USERS-001 / ADMIN-001)

Status: RESOLVED (part of the commercial-readiness mandate, 2026-09-26)

- Problem:
  - Adding a cashier needed Django Admin, a developer screen with English table names.
  - Any staff user could reach it and edit ledgers and settings directly.
  - There were no password rules.
- Decision:
  - **Users screen** at `/settings/users/`, needing `permissions.manage_roles` (Owner). It lists, creates, edits (role, active, name, phone) and resets passwords. Every account it creates is a plain user, never staff or superuser.
  - **Guards**: nobody can disable their own account or change their own role, and the last active Owner cannot be demoted or disabled, even by a superuser. The Support role is not assignable from the screen.
  - **Temporary passwords**: new accounts and resets set `must_change_password`. `accounts.middleware.ForcePasswordChangeMiddleware` then sends the user to `/profile/password/` before any other page; logout stays reachable, and `next` is checked against the host.
  - **Password rules**: Django's standard validators (similarity, minimum length 8, common passwords, numeric-only) are now configured in `AUTH_PASSWORD_VALIDATORS`.
  - **Django Admin**: `admin.site.has_permission` now allows superusers only; before, any staff user got in. The "Manage in Admin" links show to superusers only, and the admin path can be moved with the `ADMIN_URL` setting.
- Unchanged: `permissions.services.user_has_permission`, roles and their permissions, seeded data.
- Tests changed on purpose: `settings_core/tests_ui.py` pinned "staff owner sees the Admin links". It now pins that a staff owner does not, and a superuser does.
- Every change is audited as `permission_change` (`create_user`, `update_user`, `reset_password`); passwords are never written to the log.
- Verification: `accounts/tests_users.py`:
  - plain account created, audited, must change password, role permission works;
  - weak, duplicate (case-insensitive) and Support accounts refused;
  - self-lockout and last-owner guards;
  - a disabled user cannot sign in;
  - reset;
  - the screens, with the Arabic error;
  - only the Owner manages users; superusers hidden;
  - forced change flow, off-site `next` ignored, logout reachable;
  - a staff owner is sent away from Admin while a superuser gets in;
  - the Admin path comes from settings.

## HG-013 — Import screen guards (IMPORT-001)

Status: RESOLVED (part of the commercial-readiness mandate, 2026-09-27)

- Problem: the existing import pipeline (`imports/services.py`, `validators.py`, `apply_services.py`) had no screen. It also had three gaps a screen would expose:
  - importing customers, suppliers, cashboxes or opening balances **overwrote `opening_balance` even after the record had been used**. That bypasses HG-002, which requires a dated, auditable adjustment after use.
  - importing opening stock twice **duplicated the stock**.
  - opening stock could be dated inside **closed books**.
- Decision: `imports/screen_services.py` wraps the pipeline without changing it, and marks such rows invalid with an Arabic reason:
  - a changed opening balance on a record with operational use (`cashboxes.services.target_has_operational_use`); an unchanged balance still passes;
  - opening stock for an item and location that already has an `opening_stock` movement, or that repeats inside the same file;
  - opening stock dated in a closed period (`ensure_period_is_open`).
- **All or nothing**: a batch imports only when every row is valid, in one transaction.
- After an opening-stock import, each item's `average_cost` display cache is refreshed from the movements (HG-003).
- Users are not importable from the screen; they go through Settings → Users (HG-012).
- New dependency: `openpyxl` (pure Python) for `.xlsx`. CSV is accepted as UTF-8 or Windows Arabic (cp1256), with comma, semicolon or tab separators.
- Unchanged: `imports/services.py`, `validators.py`, `apply_services.py`, and the import models.
- Verification: `imports/tests_screen.py`:
  - CSV and xlsx imports; cp1256 with semicolons;
  - one bad row blocks the file;
  - the used-balance guard, including the unchanged-balance pass;
  - opening stock once, no in-file duplicates, no closed books, average cost refreshed;
  - wrong type and empty file; users not offered;
  - template download, with an unknown name or path traversal returning 404;
  - owner only.

## HG-014 — Price lists as new tables (PRICE-001)

Status: RESOLVED (commercial-capabilities plan approved by Ahmed, 2026-09-27)

- Why a schema change: wholesale and special-customer prices need somewhere to live. `Item` has one `default_sale_price` and `Customer` has no price field.
- Decision: a new `pricing` app with **three new tables only**:
  - `PriceList`: code, Arabic/English name, `adjust_percent` applied to the retail price, active;
  - `PriceListItem`: an explicit price per item per list, unique;
  - `CustomerPriceList`: one list per customer.
  `Item` and `Customer` are not altered. Removing the app leaves every other table as it was.
- What a price list changes: **only the price the sales invoice form and the POS suggest**. The cashier can still type any price, and posting reads the invoice line exactly as before. Protected sales posting, cost, stock, ledgers and report calculations are untouched.
- Resolution: the customer's active list names the item → that price; otherwise retail × (100 + adjust%) ÷ 100, rounded to 2 decimal places; otherwise retail. When the `price_lists` capability is off, it is always retail.
- Permissions: viewing uses `master_data.view_master_data` and editing uses `master_data.manage_items`. No new permission rows.
- Every change to a list, its prices or its customers is written to the audit log.
- Verification: `pricing/tests.py`.

## HG-015 — VAT per sales line; revenue net of tax (TAX-001)

Status: RESOLVED (approved by Ahmed as the first e-invoicing step, 2026-09-27)

- Problem found:
  - Sales posting spread `invoice.total_amount`, **including** `tax_amount`, across the lines as revenue. Any tax typed on an invoice was therefore counted as sales and profit.
  - Returns used the same split.
  - Item-level VAT could not be correct without changing this.
- Protected changes, `sales/services.py` only:
  1. `post_sales_invoice`: each line's share of the total is weighted by `line_total + line_tax`, and `line_profit = share - line_tax - cost`.
  2. `_sales_source_allocations` uses the same weights. `_prepare_sales_return_lines` adds the tax share of each returned quantity; a full return gives back the remainder, so the tax returned sums exactly. `create_sales_return` stores that share in `SalesReturnLineTax`. The refund amount, the cash/due split, stock and cost are computed as before.
  3. **When tax is 0 both are arithmetically identical to before.** The whole pre-existing suite passes unchanged.
- Report calculations, all switched to net of tax:
  - `profit_report` return rows, `profit_totals` and `get_profit_summary` subtract the tax given back on returns;
  - analytics `net_sales` and the daily chart use `total_amount - tax_amount` and returns net of their tax;
  - unchanged on purpose: the dashboard "sales today" card and the closing snapshot `sales_total` stay gross invoice totals, which is what was invoiced and collected.
- Behaviour change on older data: an invoice whose tax was typed on the header (no line tax rows) now also keeps that tax out of revenue and profit. It is spread over the lines by their totals. This fixes the old defect; test `test_a_tax_typed_on_the_header_is_kept_out_of_revenue`.
- New tables (the `taxes` app); `SalesInvoice`, `SalesLine` and `SalesReturn` are unaltered:
  - `TaxRate`, seeded with VAT14 = 14% (T1/V009, default) and EXEMPT = 0% (T1/V003). The ETA codes are to be verified against the authority's current list before e-invoicing.
  - `ItemTaxRate`, one per item; no row means the default rate.
  - `SalesLineTax` and `SalesReturnLineTax`.
- Charging rules:
  - prices are entered before tax;
  - tax per line = (qty × price − line discount) × rate, rounded to the piastre;
  - the invoice discount applies after tax;
  - total = Σ lines + tax − discount, the existing formula.
  - All of this is behind the new `vat` capability, which wholesale suggests. With `vat` off, nothing is charged.
- Where the tax is applied:
  - the sales invoice form hides the manual tax field and uses `create_sales_draft_with_tax`;
  - POS totals include the tax, so a walk-in pays the full amount;
  - the live totals in both screens show tax per line;
  - VAT report at `/taxes/report/`, and rate settings at `/taxes/` (view needs `master_data.view_master_data`, edit needs `manage_items`).
- **Not included — next Hard Gates:**
  - Purchase (input) VAT. `purchases/services._purchase_line_allocations` puts `invoice.total_amount`, **including tax**, into inventory cost. That is right for a shop not registered for VAT and wrong for one that is, because the tax is recoverable. The proposed fix is to allocate `total − tax` into cost when `vat` is on, and to record input tax per line the same way. That touches purchase posting and average cost.
  - Tax-inclusive shelf prices.
- Verification: `taxes/tests.py`, 12 tests, covering:
  - exact tax per line; rounding; capability off;
  - revenue 240 and profit 90 on an invoice of 268 including 28 tax and a 10 discount;
  - refund 109.90 with 14.00 tax returned, and VAT net 14.00;
  - a full return in two steps gives back exactly the tax;
  - an untaxed invoice posts exactly as before;
  - header-typed tax is kept out of revenue;
  - POS charges tax and change is correct;
  - screens and permissions.

## HG-016 — Purchase (input) VAT is recoverable, not stock cost (TAX-002)

Status: RESOLVED (approved by Ahmed, 2026-09-27: "the right thing, done the best way")

- Problem:
  - Purchase posting spread `invoice.total_amount`, including `tax_amount`, into inventory cost (`_purchase_line_allocations`).
  - For a VAT-registered shop that tax is recovered from the authority, so stock cost, average cost, cost of goods sold and profit were all overstated. The VAT return also had no input side.
- Protected changes, `purchases/services.py` only:
  1. `_purchase_line_allocations`: each line's share of the total is weighted by `line_total + input_tax`, and its **cost = share − input tax**. Posting and the unit-cost fallback both use this.
  2. `_purchase_source_allocations` uses the same weights, so a purchase return's refund includes its tax.
  3. `_prepare_purchase_return_lines` adds each return line's tax share; a full return takes back the remainder. `create_purchase_return` records it in `PurchaseReturnLineTax`.
  4. The stock out-movement stays at the line's (now net) unit cost.
- **Which invoices count the tax as recoverable:** only lines drafted with VAT on (`PurchaseLineTax` rows).
  - An invoice without tax, or one with a hand-typed header tax (a shop that is not registered), allocates **exactly as before**, with the tax in cost.
  - The whole pre-existing suite passes unchanged, and `test_without_vat_a_typed_header_tax_stays_in_cost_as_before` pins it (570 ÷ 10 = 57.0000).
- New tables in the `taxes` app: `PurchaseLineTax` and `PurchaseReturnLineTax`. The purchases models are unaltered.
- Rates: the item's own rate (`ItemTaxRate`) or the default, the same as on sales.
- Screens:
  - the purchase form hides the manual tax field when VAT is on and uses `create_purchase_draft_with_tax`; its live total includes VAT;
  - `/taxes/report/` is now the **VAT return**: output (sales net of returns) − input (purchases net of returns) = payable, or a credit to carry forward.
- Verification: 7 new tests in `taxes/tests.py`, covering:
  - an invoice of 670 including 70 tax gives stock of 600 (A at 50.0000, B at 25.0000);
  - a 10 discount after tax gives cost 590 with the tax still 70;
  - not registered: the header tax stays in cost;
  - returning 2 gives a refund of 114.00 with 14.00 tax taken back, and stock value 400;
  - a full return in two steps takes back exactly 70;
  - a sale of 5 costs 250 with profit 250, output 70 − input 70 = 0 payable;
  - the purchase form.

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
