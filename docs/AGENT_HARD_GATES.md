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

## HG-017 — E-invoice data as new tables (EINV-001)

Status: RESOLVED (phase 2 of e-invoicing approved by Ahmed, 2026-09-27)

- New `einvoice` app with **two new tables only**:
  - `ReceiverProfile`: a customer's receiver type (B/P/F), tax or national ID, and structured address;
  - `ItemCode`: an item's EGS or GS1 code and unit type.
- `Customer` and `Item` are not altered.
- The issuer's details (activity code, branch, address, person-ID threshold) are stored as `SystemSetting` rows. The tax number is the existing `company.tax_number`.
- **No posting, stock, ledger or report logic is touched.** `einvoice.services.build_document` only *reads* a posted invoice and its `SalesLineTax` rows. It builds the ETA document (type `i`, version 1.0) and lists the problems that would get it rejected. It also re-checks that the document's `totalAmount` equals `SalesInvoice.total_amount`.
- Mapping, checked against sdk.invoicing.eta.gov.eg (Sept 2026):
  - salesTotal = qty × price; discount = the line discount; netTotal = salesTotal − discount;
  - T1 = netTotal × rate (V009 = general sales, V003 = exempt); line total = netTotal + T1;
  - Hesba's invoice discount → `extraDiscountAmount`;
  - totalAmount = Σ line totals − extraDiscountAmount.
- Not included:
  - signing (CAdES-BES) and submission (phase 3, which needs the owner's portal credentials and e-signature);
  - credit notes for returns;
  - non-EGP invoices.
- The person-buyer threshold defaults to EGP 50,000 and is editable. The owner should confirm the current value with the authority.
- Verification: `einvoice/tests.py` (8 tests), including exact line and total figures (262.30), the problems listed in Arabic and English, the person threshold, draft and currency checks, the screens with audit, and permissions and the capability gate.

## HG-018 — Units of measure stored in base units (UNITS-001)

Status: RESOLVED (approved by Ahmed, 2026-09-27: "موافق ابداً")

- New `units` app with **one new table**: `ItemUnit`. It holds the item, the Arabic and English names, a `factor` (≥ 1.001, 3 decimals), its own barcode and optional sale and purchase prices. With no price set, the base price × factor is used.
- `Item`, the posted line models, stock movements, average cost, ledgers and reports are **not changed**. A line entered in a bigger unit is converted *before* the draft is created (`units.services.convert_line`), so every posted figure stays in the base unit:
  - `base_qty = qty × factor` (must be a whole number of thousandths);
  - `base_price = ceil_to_piastre(qty × price / base_qty)`;
  - `line_discount_amount += base_qty × base_price − qty × price`, so the line total is **exactly** qty × price − discount, the figure the customer or supplier agreed on;
  - the line description records what was entered (for example «Juice — 5 كرتونة × 100.00"), so the detail page and the printout show the carton line. Printing uses the description in place of the item name, which is why the name is kept in it.
- Where it applies: the sales and purchase draft forms (an optional «الوحدة» column, shown only when the `units` capability is on) and the POS. At the POS, scanning a unit barcode adds that unit.
- One small POS fix was needed for this: the checkout subtotal now subtracts each line's `line_discount_amount`. POS lines never had one before, so existing behaviour is unchanged.
- Returns work on the stored base-unit lines, so a returned carton is returned as its pieces at the stored base price. That is the same money, split per piece.
- Risk: the per-piece sale price shown in reports is the rounded-up base price (8.34 for 100/12), and the piastres are in the line discount. Totals, revenue, stock and cost are exact.
- Verification: `units/tests.py` (13 tests). They cover the conversion math (5 × 12 at 100 → 60 @ 8.34, discount 0.40, total 500.00), purchase cost per piece (2 boxes of 24 at 250 → 48 pieces costing 500.00), stock moved in pieces on posting, the POS unit line, rejection of a foreign unit in the form and at the till, the management screen with audit, validation, permissions and the capability gate.

## HG-019 — Batches & expiry as a tracking layer over stock (BATCH-001)

Status: RESOLVED (covered by Ahmed's standing go-ahead of 2026-09-27: «كمّل كل اللي ناقص»)

- New `batches` app with **one new table**: `Batch`. It holds the item, the receiving location, the batch number, the expiry date, the received quantity and the received date, plus either the purchase line it came on or "registered by hand".
- **Stock movements, average cost, posting, returns, ledgers and reports are not changed.**
  - A batch typed on a purchase line (new optional fields «رقم التشغيلة» / «تاريخ الصلاحية», shown only when the capability is on) is stored next to the draft.
  - It counts only once that invoice is posted, and it stops counting if the invoice is cancelled.
- **What is left of each batch is derived, not booked.**
  - The item's real on-hand quantity (all locations, from stock movements) is shared out to its batches with the latest expiry first. Put another way, the batch that expires first is taken to have been sold first (FEFO); a batch without a date counts as expiring last.
  - So batch figures can never disagree with stock: sales, the POS, returns, counts and cancellations all flow through automatically.
  - On-hand stock that no batch covers is shown as "no batch", so old stock can be registered from the screen.
- Screens and alerts:
  - `/batches/` shows expired / soon / fine / no-date batches, with a warning window of 30/60/90/180 days, a search box, the option to show finished batches, registration of batches for stock already on hand, and removal of a hand-registered batch. Everything is audited; managing needs `inventory.adjust_stock`.
  - The dashboard shows two alerts: expired batches still in stock (urgent) and batches expiring within 60 days (soon).
  - The stock page and the purchase detail page link to or show the batch.
- Known limits, documented for the owner:
  - The cashier doesn't pick a batch at sale time. FEFO is assumed, which is the normal pharmacy and grocery practice.
  - The derivation is per item, not per location: stock moved between locations keeps its batches, and a batch's location is where it was received.
  - A supplier return reduces on-hand, and FEFO then treats the earliest-expiring stock as the part returned.
  - Exact per-batch sale traceability (for recalls) would need a batch chosen on each sales line. That is a later option and would itself be a Hard Gate on sales posting.
- Verification: `batches/tests.py` (8 tests). They cover the FEFO split after sales, undated batches and uncovered stock, expired/soon alerts including the dashboard and the capability switch, the purchase form batch counting only once posted and disappearing on cancel, the purchase detail, the screen register/validation/retire with audit, permissions and the gate.

## HG-020 — Sizes & colours as ordinary items grouped by model (VARIANT-001)

Status: RESOLVED (covered by Ahmed's standing go-ahead of 2026-09-27: «كمّل كل اللي ناقص»)

- New `variants` app with **two new tables**:
  - `VariantGroup`: the model's code, name, category, unit, default prices, and ordered sizes and colours;
  - `Variant`: links the group, the size and the colour to one `Item`, unique per combination.
- **Each size × colour is an ordinary `master_data.Item`.** It gets its own code (`MODEL-SIZE-COLOUR`, suffixed on collision), an optional shop-internal EAN-13 barcode, its own stock and average cost, and the existing `size`/`color` fields filled in. So sales, the POS, purchases, returns, stock, costing and every report treat it like any other item. **No protected logic is touched and `Item` is not altered.**
- Services, all audited:
  - create a model with all its combinations (capped at 400);
  - add sizes or colours, which creates only the missing combinations;
  - set one sale and/or purchase price for the whole model;
  - a stock grid by size × colour with row and column totals, read from stock movements.
- Screens:
  - `/variants/` (list and new model) and `/variants/<model>/` (grid, extend, price), linked from the items list;
  - managing needs `master_data.manage_items`;
  - the `variants` capability is suggested for fashion and online.
- Not included: deleting a combination (deactivate it from the item screen as usual), and per-variant price lists beyond what price lists already do per item.
- Verification: `variants/tests.py` (8 tests). They cover parsing, the six items with codes, barcodes, prices and audit, code collisions and refusals, extend adding only the missing combinations, model pricing, the stock grid totals, the screens, a variant sold at the POS by barcode, permissions and the gate.

## HG-021 — Serial / IMEI numbers and warranty as a tracking layer (SERIAL-001)

Status: RESOLVED (covered by Ahmed's standing go-ahead of 2026-09-27: «كمّل كل اللي ناقص»)

- New `serials` app with **four new tables**:
  - `SerialSetting`: whether an item is tracked, and its warranty in months;
  - `SerialNumber`: the item, the serial, and the purchase line it came on or "registered by hand";
  - `SerialSale`: serial ↔ sales line;
  - `SerialReturn`: serial ↔ sales return line.
- **Posting, stock, cost, ledgers and reports are not changed.** A serial's state is read from the documents that carry it:
  - pending: on a purchase draft;
  - in stock: posted purchase or registered, and every posted sale of it has a posted return;
  - sold;
  - void: its purchase was cancelled;
  - retired: by hand, with a reason.
  So cancelling a purchase, a sale or a return needs no clean-up.
- Entry points, active only when the `serials` capability is on:
  - **Purchase and sales forms** get a «السيريال / IMEI» field per line. For a tracked item the count must equal the quantity, and the quantity must be whole. Serials are refused if duplicated, already recorded, not in stock for that item, or entered on an untracked item. The sales line description records "S/N: …", so the printout shows it.
  - **Posting a sales draft** re-checks that its serials are still in stock. This is a view-level guard in `sales.views.invoice_post`; the posting service is untouched.
  - **POS:** scanning a serial / IMEI adds that exact unit on its own line, with the quantity locked at 1. Scanning a tracked item's own barcode asks for the serial, and the server refuses a tracked line without one. The one POS code change is `attach_sale_serials` between draft and post, inside the existing transaction.
- Screens, all audited:
  - `/serials/`: lookup (a single hit opens the detail page), units in stock by item, and registration of serials for units already on hand;
  - `/serials/<id>/`: history covering the purchase (supplier, invoice, date), sales (customer, invoice, date) and returns, plus the warranty ("valid until" / "ended on", counted from the last sale); from here you can record the serial back from a posted sales return of its invoice, or retire it;
  - `/serials/items/`: which items are tracked, and their warranty months.
- Permissions: viewing needs `inventory.view_stock`, register / return / retire need `inventory.adjust_stock`, and item settings need `master_data.manage_items`.
- Known limits:
  - The sales return form does not ask for the serial. The serial is recorded back from its own screen against a posted return, capped at the returned quantity.
  - A purchase return does not free a specific serial; retire it with the reason "returned to supplier".
  - Serials are not location-bound.
- Verification: `serials/tests.py` (11 tests). They cover parsing and month arithmetic, purchase pending/posted/void and re-use after cancel, refusals, a form sale with warranty and the detail page, double-sale refusal and cancel restoring stock, the draft-post guard, the POS serial sale and catalog, return back into stock and resale, the screens with audit, permissions and the gate.

## HG-022 — Instalment plans over posted credit sales (INSTAL-001)

Status: RESOLVED (covered by Ahmed's standing go-ahead of 2026-09-27: «كمّل كل اللي ناقص»)

- New `installments` app with **three new tables**:
  - `InstalmentPlan`: one per posted sales invoice, holding the financed amount (the invoice's `remaining_due`), the count and the first due date;
  - `Instalment`: the number, the due date and the amount; equal to the piastre, the last one taking the rounding, due monthly;
  - `InstalmentPayment`: plan ↔ `CustomerPayment`.
- **The debt is the existing credit sale.** The customer ledger already holds it, and nothing about the invoice, the ledger or aging is changed.
  - **A collection is an ordinary customer payment** made through the protected `record_customer_payment` service as-is. That service moves the cashbox, lowers the customer's due and writes its own audit; the plan only links it.
  - Cancelling that payment from the collections screen stops it counting.
- Settlement is **oldest instalment first**. Linked posted payments, plus the `due_amount` of posted sales returns on the invoice, settle the instalments in date order, and each shows paid / partly paid / overdue / due today / upcoming. A cancelled invoice stops the plan.
- No interest or instalment mark-up is computed. The shop sells at its instalment price, just as it would type any price on the invoice. Charging interest would change invoice totals and would need its own Hard Gate.
- Screens and alerts:
  - the sales detail page links to «تقسيط الباقي» or to the plan;
  - `/instalments/` lists the plans, with an overdue filter;
  - the plan page shows the schedule, a collect form (cashbox and date), a WhatsApp reminder for the next instalment, and a cancel button for a plan with no collections;
  - the dashboard shows an urgent alert with the count and amount of overdue instalments.
- Permissions: viewing needs `sales.view_sales_invoices`, creating or cancelling a plan needs `sales.create_sales_invoice`, and collecting needs `sales.receive_customer_payment`.
- Verification: `installments/tests.py` (9 tests). They cover split and month arithmetic, the plan schedule and the one-plan rule, refusals, oldest-first settlement with cash and ledger effects, the over-collection refusal, a cancelled payment no longer counting, full settlement, return credit, the cancelled-invoice stop, the dashboard alert, the plan-cancel rule, the capability switch, the screens with the WhatsApp link, and permissions and the gate.

## HG-023 — Fixed assets with computed straight-line depreciation (ASSET-001)

Status: RESOLVED (covered by Ahmed's standing go-ahead of 2026-09-27: «كمّل كل اللي ناقص»; depreciation was discussed and kept optional)

- New `fixed_assets` app with **one new table**: `FixedAsset`. It holds the code, name, category, in-service date, cost, salvage value, useful life in months, status, and the disposal date and proceeds.
- **Depreciation is computed, not posted.** Hesba has no general ledger. Straight line: (cost − salvage) / months, to the piastre, with the last month taking the rounding. It runs as a full month from the month in service, and each month is charged on its first day. Nothing is charged in the month of disposal or after it. The disposal result is proceeds − book value on the disposal date.
- **Cash only moves through existing services.**
  - Paying for an asset uses `create_cashbox_operation(DIRECT_OUT)`, which keeps its own period, permission and negative-balance checks.
  - Sale proceeds use `DIRECT_IN`.
  - Cancelling an asset entered by mistake reverses its payment with `cancel_cashbox_operation`.
  - An asset already owned can be registered with no payment.
- **Profit report:** `profit_totals` and the gross/net lines are unchanged. The view adds lines after net profit, in the same way EXP-001 added expenses: "الإهلاك", "نتيجة بيع أصول" (only when non-zero) and "صافي الربح بعد الإهلاك". They appear only when the capability is on, to viewers who may see expenses. Period summaries in closing are not touched.
- The fields that drive the figures cannot be edited after creation; a wrong asset is cancelled (audited) and entered again. Registering an asset with an old in-service date does change the live profit report for those past months. It does not change stored closing summaries.
- Screens:
  - `/assets/`: the register, with totals, this month's depreciation, a new-asset form and life hints;
  - `/assets/<id>/`: the full monthly schedule, disposal (with an optional cashbox) and cancel;
  - linked from the expenses screen.
  - Viewing needs `cashboxes.view_expenses`; recording needs `cashboxes.record_expenses`, and moving cash also needs `cashboxes.move_cash`.
- Every roadmap capability is now available. The capability tests keep the "listed but not available yet" path covered with a patched test-only capability.
- Verification: `fixed_assets/tests.py` (8 tests). They cover the month amounts and rounding, the full-month convention, accumulated and book value, report windows charged on the 1st, the salvage floor, disposal stopping depreciation with the gain/loss and cash in, payment from a cashbox with a negative-balance refusal, cancel reversing the payment, refusals, the profit report line and its capability switch, the screens, and permissions and the gate.

## HG-024 — Cashier shifts: computed expected cash, difference posted on purpose (SHIFT-001)

Status: RESOLVED (Ahmed's standing go-ahead of 2026-09-28 to build and merge what can be done safely)

- New `shifts` app with **one new table**: `Shift`. It holds the cashier, the cashbox, the float counted at opening, open/closed status, the expected and counted cash, the difference, a summary snapshot and notes. At most one open shift per cashier (DB constraint).
- **Expected cash is computed, never typed:** float + the cashier's own cash movements on that cashbox between opening and closing. That covers POS and invoice cash sales, refunds, collections, supplier payments and direct cash in/out. Other users' movements are not counted. A posted shift difference (reference `SHIFT-<id>-DIFF`) is never counted in anyone's shift.
- **Closing only records.** The cashbox is not touched. A manager with `cashboxes.move_cash` may then post the difference through the existing `create_cashbox_operation` (DIRECT_OUT for a shortage, DIRECT_IN for an overage), once per shift. That service keeps its own period, permission and negative-balance checks. No posting, stock, ledger or report logic is changed.
- Selling is never blocked by a missing shift. The till shows «افتح وردية» or «وردية من …» linking to `/shifts/`.
- Permissions: using shifts needs `sales.create_sales_invoice`. Other cashiers' shifts need `cashboxes.view_finance`, and posting a difference needs `cashboxes.move_cash`.
- Verification: `shifts/tests.py` (4 tests). They cover the expected cash from POS sales and a collection with another user's movement excluded, closing with a shortage, the one-open rule and refusals, the manager posting a shortage and an overage exactly once with the cashier refused, the difference excluded from the manager's own open shift, privacy, the POS banner and English.

## HG-025 — Company details frozen on each posted document (PRINT-003)

Status: RESOLVED (Ahmed's standing go-ahead of 2026-09-28)

- New table `printing.DocumentCompanySnapshot`: one row per posted document (kind + id) with the legal company details: name, legal name, currency, phone, address, tax number, commercial register, footer note. Unique per document, never updated.
- It is taken by a `post_save` receiver in the printing app when a sales or purchase invoice, a sales or purchase return, or a customer or supplier payment is saved with status `posted`. `get_or_create` makes it idempotent. **The posting services themselves are not edited**; the receiver only inserts one row inside the same transaction.
- Printing uses the snapshot for posted documents. Drafts and documents posted before this change keep following the current details, which is exactly the old behaviour. The logo is deliberately not copied: it isn't a legal detail, and a copy would repeat hundreds of kilobytes per document.
- Verification: `printing/tests_snapshot.py` (2 tests). An old invoice keeps the old address and tax number after they change, while a new one gets the new details. Drafts follow the current details, and payments are frozen too. The full suite passes with the receiver active on every posting.

## HG-026 — Row locks that work on PostgreSQL (DEPLOY-001)

Status: RESOLVED (Ahmed's go-ahead of 2026-09-28 for the client-owned Supabase deployment)

- **Defect found by running the suite on PostgreSQL for the first time:** 202 of 1255 tests errored with `FOR UPDATE cannot be applied to the nullable side of an outer join`. SQLite ignores `select_for_update()`, so CI never saw it. On PostgreSQL the affected services **could not run at all**: posting, cancelling or returning a sales or purchase invoice (`cashbox` is nullable); reversing a sales or purchase return; cancelling a cash operation (`source_cashbox` and `destination_cashbox` are nullable); reversing an opening-balance adjustment; and cancelling a stock operation (`source_location` and `destination_location` are nullable).
- **Fix:** at exactly those 11 call sites, `select_for_update()` becomes `select_for_update(of=("self",))`, which locks the document row itself. It is the same row every concurrent caller competes for, so the serialisation they rely on is unchanged. The joined rows (customer, location, cashbox) were never lockable on PostgreSQL through these queries, and every cashbox balance check still takes its own cashbox lock. No business rule, amount, movement or ledger entry changes, and SQLite behaves exactly as before.
- **Left untouched on purpose:** call sites whose joins are all non-nullable already worked on PostgreSQL, and keep their original lock: instalment plans, sales and purchase lines on returns, expenses, import rows, post-closing adjustments, and customer and supplier payments.
- **Second defect (not protected logic):** expense numbers (`EXP-…`) and fixed-asset codes (`FA-…`) were built from the row id. PostgreSQL does not reuse ids after a rolled-back attempt, such as a refused expense, so numbering would skip (EXP-000001, EXP-000007…) and read as missing documents. They now count existing rows, with the existing uniqueness loop kept.
- **Verification:** the full suite on PostgreSQL 16 locally, and in CI through the new `django-tests-postgres` job, with the SQLite job unchanged.

## HG-027 — Two cashiers, one last unit; two first entries of a month (DEPLOY-001)

Status: RESOLVED (same go-ahead as HG-026)

- **Race 1, overselling:** sales posting checked stock without locking anything the other poster also locks, since each invoice locks only its own row. Two invoices for the last unit, posted at the same moment, **both passed the check and stock went to -1**. The transfer and adjustment services already lock the `Item` row before reading stock; sales posting, the stock-out side of purchase returns and the reversal of sales returns now do the same, through `inventory.services.lock_items_for_stock_check`, which locks in pk order so concurrent callers cannot deadlock each other. The check itself, the quantities and the movements are unchanged.
- **Race 2, a crash at the start of a month:** the first entry of a month opens its period automatically (HG-010). Two such entries at the same moment both inserted the period, and the second **failed with `IntegrityError`**, a raw error page. The insert now runs in a savepoint; the caller that loses takes the period the winner just opened. Which periods exist and their dates are unchanged.
- **Verification:** `sales/tests_concurrency.py` runs on PostgreSQL only, since SQLite lets one writer in at a time and cannot race. It uses two real threads and a widened read window. Both races were **reproduced with the fixes removed** (`['posted', 'posted']`, and an `IntegrityError`) and pass with them.

## HG-028 — Sales rung up offline, posted once when the till syncs (POS-003)

Status: RESOLVED (Ahmed's go-ahead of 2026-09-28: "work to the end without stopping")

- **The need:** the till must keep selling when the internet drops, without a sale being lost or posted twice.
- **Device side** (`static/hesba/js/pos_offline.js`): while `/healthz/` answers, the till posts exactly as before. When it does not, the sale is kept in the browser with a UUID made at that moment and the time it was rung up, and the cart is cleared. Queued sales are sent one by one when the server answers again. The page warns before being closed or reloaded while sales wait. **Known limit:** the page has to be open already; a till that loads the page while offline has nothing to load, since there is no service worker.
- **Server side** (`offline_pos`, a new table `OfflineSale` with a unique `sale_key`): each sale goes through **the same `checkout` path as a live POS sale** (the same parser, serial checks, prices, tax, stock lock, cashbox and ledger), dated the day it was rung up. The key is written in the same transaction, so a retry (even with a changed body) returns the invoice it already made, and two copies arriving together post once (the unique key plus `IntegrityError` fallback). A sale the server cannot post (stock gone, closed period, walk-in on credit, older than `POS_OFFLINE_MAX_DAYS` = 7, dated in the future) is **refused with the reason in the till's language**, keeps nothing, and stays on the device as "refused" until the cashier removes it.
- **Protected logic touched:** `sales/pos.py` `checkout` takes an optional `sale_date` and `notes`. A live sale passes neither and behaves exactly as before. Posting, stock, cash and ledger services are unchanged.
- **Verification:** `offline_pos/tests.py` (5 tests): posted like a live sale with the offline date, stock, cash and number; once only for a repeated key; refusals leave nothing; date, key and permission checks; the till carries the hooks. The browser was run with the network really cut (Playwright `setOffline`): two sales queued, both posted once when the network came back, and the next online sale posted normally.

## HG-029 — Materials for a project leave stock as an adjustment out (CONTRACT-001)

Status: OPEN (a proposal only; nothing is blocked)

- **What was built without touching protected logic:** issuing materials to a project calls the inventory engine's own `adjust_stock(..., direction="out")`. The engine applies its own `inventory.adjust_stock` permission, open-period check, stock sufficiency check under a row lock, and authoritative average cost. The project records which `StockOperation` belongs to it (`projects.ProjectIssue`, one-to-one). Project cost = quantity × that operation's unit cost, counting posted operations only. Cancelling the operation from the inventory screen returns the stock and drops it from the project's cost.
- **Question for Main Control:** inventory reports list these rows as *adjustment out*, next to shrinkage and breakage. A contractor may want "issued to project" as its own movement type, so the inventory report can tell site consumption from losses.
- **Proposed fix (needs approval, protected):** add `StockMovementType.PROJECT_ISSUE` plus a matching operation type. The inventory, report and closing code paths that sum `ADJUSTMENT_OUT` today would need to learn the new type, and each would need tests pinning stock quantity, stock value and average cost before and after.
- **Risk if left as is:** reporting labels only. Quantities, values, average cost and the project figures are correct either way.

## HG-030 — Production runs move stock through adjustments (MFG-001)

Status: OPEN (a proposal only; nothing is blocked)

- **What was built without touching protected logic:** a production run goes through the inventory engine's own `adjust_stock`. For each component (locked in pk order) it adjusts out at the authoritative average cost. It then adjusts the product in at `cost_round(consumed cost / units made)`. The engine applies its own permission, period and stock checks, and setting that cost needs `inventory.view_cost`. The whole run is one transaction, so a short component leaves nothing behind.
- **Cancelling** goes through `cancel_stock_operation`, product first. The engine refuses when the product has already been sold, and then every component returns.
- **Known effect:** unit cost is rounded to four decimals, so units × unit cost can differ from the consumed total by a fraction of a piastre. The run keeps the exact consumed total.
- **Question for Main Control:** as in HG-029, inventory reports show these rows as adjustments. Two dedicated movement types, `PRODUCTION_OUT` and `PRODUCTION_IN`, would tell production apart from shrinkage. That change is protected (movement types, report sums, closing) and needs approval plus quantity/value/average-cost regression tests.
- **Deferred:** labour and overhead absorbed into product cost; scrap and yield loss; multi-level recipes (a sub-assembly is simply produced first).

## HG-031 — Entities: locations, cashboxes, users and documents get an entity (ENT-001)

Status: APPROVED IN PRINCIPLE by Ahmed (2026-10-05). Implemented as its own PR; see `docs/ERP_FOUNDATION_DESIGN.md` §1.

- **Change:** new `Entity` model. `Location` and `Cashbox` get a required FK, set by a data migration to a seeded "main entity". Documents read their entity from their location or cashbox (no new column on posted documents in this step). Users get allowed entities plus a default.
- **Protected surface:** models, schema and migrations.
- **Risk:** low. A single-entity install behaves exactly as today.
- **Tests:** the full existing suite must stay green without edits. Migration test on a populated database. Group-stock visibility checked against permissions (quantities shown; cost only with `inventory.view_cost`).
- **Implemented (ENT-001):**
  - new app `entities` with `Entity` (one `is_main`, enforced by a partial unique constraint) and `EntityMembership` (recorded, enforced later by HG-034);
  - nullable `entity` FK on `master_data.Location` and `cashboxes.Cashbox`, with data migrations that bind existing rows to the seeded main entity;
  - `save()` fills the main entity when none is given, so no existing caller changed.
  - Posting, stock movement, cost and ledgers are untouched.
  - New permission `inventory.view_group_stock`: every role with `inventory.view_stock`, plus the cashier, so a sale can learn the factory has it. Quantities only; value needs `inventory.view_cost`.
  - The full suite passes unchanged.

## HG-032 — General ledger projected from posted documents (GL-002)

Status: APPROVED IN PRINCIPLE (2026-10-05). Design: `docs/ERP_FOUNDATION_DESIGN.md` §2.

- **Change:** journal entries are generated from posted documents by a read-only projector, keyed by (source type, source id). They are rebuildable, and a cancelled document produces a reversing entry. Posting services are **not** modified; the projector runs after commit and nightly.
- **Protected surface:** reads sales and purchase posting, inventory movement cost, cashbox and party ledgers. Writes none of them.
- **Gate before the ledger becomes authoritative:** reconciliation tests to the piastre. AR = customer report, AP = supplier report, each cash account = cashbox report, inventory = stock valuation, revenue and COGS = profit report. Every entry balances.

**Implementation (GL-002, 2026-10-05):**
- `ledger/projector.py` builds every entry from the sub-ledger rows themselves: `CustomerLedgerEntry`, `SupplierLedgerEntry`, `CashboxMovement`, `StockMovement`, the party and cashbox opening balances, and fixed-asset depreciation. Lines are grouped by (source type, source id, date), so a cancellation is its own reversing entry on the reversal date. No posting service was touched.
- **Freshness:** freshness is checked on read, not after commit or overnight. Each ledger screen compares a fingerprint of its sources (row counts, max ids, opening balances, asset states, the month, the chart size) with the one stored in `SystemSetting ledger.fingerprint`, and rebuilds when they differ. This adds no hook into the posting path.
- **Placing the remainder:** VAT is split out by ratio. Then sales, COGS (= the stock value issued), returns, purchases, expense categories (through `ExpenseAccount`), asset purchase and disposal, stock count gain and loss, production WIP and project issues each go to their own account. A line the projector cannot place goes to the new **1199 Suspense** account, never silently to equity. When one document spans two entities, intercompany lines balance it within each entity. A sale or purchase belongs to the entity of its store (FS-001), whichever entity's cashbox took the money.
- **New core accounts:** 1199 Suspense (`suspense`) and 4203 Gain or loss on asset disposal (`asset_disposal`).
- **Gate evidence:** `ledger/tests_journal.py` runs on the month-acceptance fixture. Every entry balances, suspense is 0, and AR, AP, cash, inventory, net sales and COGS match the existing reports to the piastre, including after a cancellation. The same reconciliation is a screen (`/accounting/reconciliation/`), so any client can check it on their own data. The ledger stays **read-only and secondary**; reports keep using their own selectors until FS-001 is reviewed.

## HG-033 — Average cost per entity (ENT-003)

Status: APPROVED IN PRINCIPLE (2026-10-05). Scheduled after ENT-001 and GL-002 are stable.

- **Change:** the authoritative average cost (HG-003) is computed per item within an entity's locations instead of per item overall. Transfers between entities move stock at the sender's cost (same legal person), or through paired internal invoices at a transfer price (separate legal persons).
- **Protected surface:** inventory movement cost and average-cost logic, sales COGS.
- **Tests:** for a single-entity install, quantity, value and average cost are identical before and after on the month-acceptance fixture. Multi-entity fixtures pin cost per entity, transfer at cost, and the consolidated elimination.

## HG-034 — Entity-scoped permissions (ENT-002)

Status: APPROVED IN PRINCIPLE (2026-10-05).

- **Change:** a user acts only within their allowed entities. Lists, reports and posting refuse other entities. Owner and group manager see "whole group".
- **Protected surface:** the permission core.
- **Tests:** per-role × per-entity matrix covering read, create and post, and a cross-entity URL tampering test for every document type.

**Implementation (HG-034, 2026-10-08):**
- **One rule, one place.** The entity being worked in (ENT-002's `current_entity()`) is the data scope. `entities/scope.py` says where each document finds its entity, then every list, document page, print, action, form choice, report and dashboard figure narrows its queryset with it:
  - a sale or purchase by its store (as the ledger does, FS-001);
  - a return by its source invoice's store;
  - a collection, supplier payment, expense or cash movement by its cashbox;
  - a stock movement by its location;
  - a transfer (cash or stock) by either side.
- **Pre-entity records.** Records with no entity belong to the main entity.
- **Who is restricted.** A user with `EntityMembership` rows who is not the owner (or a superuser) can never choose the whole group, and their screens open on their default entity. The owner sees the whole group, or one entity after choosing it from the bar. A single-entity install has no current entity, so nothing changes there; the full suite runs unchanged.
- **Out of scope = not found.** Another entity's invoice, return, payment, cashbox, store, expense, stock operation, production run or order, and every print of them, answers **404** (the same answer as a document that does not exist). Post, cancel and reverse check the scope *before* calling the service. Form choices only offer the entity's stores and cashboxes, so a tampered POST fails form validation. A new store or cashbox made inside an entity belongs to it.
- **Figures.** These are per entity, and add up to the group:
  - stock, cashbox, sales, purchase and profit reports, VAT in and out, aging, the dashboard cards and the activity panel;
  - customer and supplier balances use the entity of the document that wrote each ledger row (invoice, return or payment), exactly as `ledger/projector.py` does. Party opening balances belong to the main entity.
- **Books.** `/accounting/*` validates `?entity=` against the user's allowed entities. A restricted user cannot ask for the consolidated books or for another entity's.
- **Not changed:**
  - posting, stock movement, cost and the ledgers;
  - permission codes and their checks (`require_permission` still decides *what* a role may do; the scope decides *where*);
  - the group "where is it" stock view (ENT-001), which stays group-wide by design.
- **Known edges:**
  - Shared public links (SHARE-001) carry their own token and are not entity-scoped.
  - Credit-limit alerts compare a customer's balance *in the current entity* with their single credit limit.
  - Parties, items and categories are shared across the group.
- **Tests:** `entities/tests_scope.py` covers:
  - lists per entity;
  - 404 on reading, printing and acting on the other entity's documents, with the documents left unchanged;
  - form choices and tampered POSTs;
  - a new cashbox filed under the branch;
  - customer balance, profit and dashboard per entity summing to the group;
  - the books refusing a restricted user's request for another entity or the consolidation;
  - every seeded role inside the branch never reaching the main shop's sale.

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
