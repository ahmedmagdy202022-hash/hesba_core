# Agent Hard Gates

Status: HG-001–HG-037 RESOLVED. Later entries carry their own status: an OPEN PROPOSAL (HG-041–HG-044) is a decision still waiting, and an IMPLEMENTED note is built and waiting for review. Read each entry's status before protected work.
Decision source: Main Control decision comment on PR #54
Implemented on: `agent/end-to-end-functional-cycle`, merged to `develop` via PR #54 (`c77c7e5`)
Resolved: 2026-08-30

This log records the seven protected business/accounting decisions raised during the end-to-end functional run. Main Control approved all seven decisions. They are implemented, migrated, and covered by the final verification run. Gates raised after that run are listed below with their own status.

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

## HG-035 — Salesperson on the sales invoice, rep on the customer (PERF-001)

Status: IMPLEMENTED (2026-10-08), requested by Ahmed: groups with distributors and sales reps need per-rep performance.

- **Change:**
  - Two nullable foreign keys to `staff.Employee`, both with `SET_NULL`:
    - `SalesInvoice.salesperson` (migration `sales/0010`);
    - `Customer.sales_rep` (migration `master_data/0003`).
  - `create_sales_draft` fills `salesperson` from the header. Failing that, it uses the customer's active rep, and then the active employee linked to the selling user. Otherwise it stays empty. One rule covers every path: the invoice form, POS, offline sync, restaurant, appointments and projects.
- **Protected surface:** models and schema, plus a single added field in draft creation. Posting, stock movement, cost, cashbox, party ledgers and the general ledger never read the field. Existing invoices keep `NULL` and appear as "No rep".
- **Report:** `/reports/reps/` (`reports.view_all_sales_report`; the profit column also needs `reports.view_profit_report`), scoped to the current entity (HG-034). For each rep it shows:
  - net sales before VAT and after returns, returns, gross profit, invoice count and average invoice;
  - collected: cash taken on their invoices plus payments from the customers they look after;
  - credit sold, and commission at `commission_percent` of net sales;
  - explained alerts: high returns (≥ 10 %), low collection (< 50 %), customers but no sales.

  An "unassigned" row keeps the totals equal to the profit report's sales and profit for the window. A test pins this to the piastre.
- **Tests:** `reports/tests_reps.py` covers:
  - who gets the sale (rep, then seller, then nobody, an explicit choice overriding, and an inactive rep being skipped);
  - figures, commission and alerts, with totals equal to `profit_totals`;
  - screen permissions and the profit column;
  - the form, the invoice detail and the print.

## HG-036 — Manufacturing restructure: work in progress, labour and overhead in product cost, scrap (R2-9)

Status: RESOLVED — approved by Ahmed on 9 Oct 2026 ("موافق تعمل كل حاجة صح") and implemented in branch `claude/hg036-mfg-costing`. The original proposal is kept below; what was built follows it under "Implemented".

- **Asked for:** Ahmed's round-2 feedback said manufacturing is "very bad" and needs restructuring (R2 plan, part 3).
- **Done without touching protected logic:**
  - the manufacturing home is now a control board: orders on the floor with their stage and progress, materials short for all open orders together (per material and warehouse), each product's material cost per unit against its sale price, and the latest runs;
  - recipes have their own page, with three component lines and "+ component".

  Everything still goes through `services.plan`, `orders.*` and `services.produce` as before.
- **Needs this gate** (each item touches stock movement, cost or the ledger):
  1. **Work in progress.** Issue materials to a WIP location when an order starts, and move finished units to the finished-goods warehouse on completion. This needs either transfers in the order flow, or new `PRODUCTION_OUT`/`PRODUCTION_IN` movement types (see HG-030). Either way it changes stock reports and closing.
  2. **Labour and overhead in product cost.** Today the finished product enters stock at material cost only; `labor_cost`/`overhead_cost` on the order appear on its cost card but not in inventory value. Absorbing them changes average cost (HG-003) and needs a ledger counter-entry (e.g. "applied labour / overhead"), which touches GL-002 projection.
  3. **Scrap and yield.** The stage log already records defects. Writing them off as a stock adjustment out (or a lower output) changes stock quantity, cost per good unit and the ledger.
- **Proposed tests if approved:** quantity, value and average-cost regressions per scenario; a trial balance that still balances; stock reports per warehouse before and after WIP; cancellation reversing every leg.
- **Risk if left as is:** low. Product cost is materials only (understated by labour and overhead), and defects stay as a quality figure without moving stock.
- **Implemented (approved):**
  - **Files:** `manufacturing/models.py` (+ migration `0003_wip_conversion_scrap`), `manufacturing/services.py`, `manufacturing/orders.py`, `ledger/projector.py`, the manufacturing board/order/run templates, and `reports/insights.py`, `reports/activity_panel.py` (run cost now includes conversion).
  - **Schema:** `ProductionRun.conversion_cost` (labour + overhead absorbed) and `ProductionRun.scrap_quantity`, both defaulting to 0 so every existing run keeps its meaning. `total_cost` still means materials consumed. New `OrderMaterialIssue(order, operation)` links an order to the stock operations that issued its materials.
  - **Work in progress:** the first stage of an order calls `services.issue`, which adjusts each component out through `adjust_stock` (same permission, period and stock-lock rules). Short materials now stop the start, not the finish. Finishing calls `services.produce(..., issued=...)`, which uses those same operations as the run's consumption instead of consuming again. Cancelling an order on the floor reverses every issue through `cancel_stock_operation`. Orders started before this change have no issues and consume at the finish as before.
    - No new movement types: the issued value leaves the stock report when it is issued and sits in the work-in-progress account (`wip`, 110502 on the manufacturing chart; COGS where a chart has none).
  - **Labour and overhead:** the product is adjusted in at `cost_round((materials + labour + overhead) / good units)`, so its authoritative average cost, and with it each later sale's cost (HG-003), includes conversion cost.
    - In GL-002 the output operation credits work in progress with the material part and **absorbed production cost** (`production_cost`, 5102) with the conversion part. The labour and overhead themselves were already booked as expenses when paid.
  - **Scrap:** good units = order quantity − all stage defects, capped at the fewest good units any stage recorded. Only good units enter stock; they carry the whole cost (normal loss), and the run records `scrap_quantity`. A lot with no good units cannot be finished.
  - **Not changed:** inventory movement types, stock report sums, average-cost formula, closing logic, plain production runs (no conversion, no scrap).
  - **Tests (`manufacturing/tests_hg036.py` + updated order/board tests):**
    - stock quantities after start, finish and both cancellations;
    - WIP balance 190 while on the floor and 0 after;
    - finished goods 290; absorbed cost −100;
    - average cost 14.5 (and 15.2632 with one scrapped unit);
    - trial balance balanced and reconciliation clean in every scenario;
    - an order started before HG-036 still consumes at the finish.

## HG-037 — Warehouse minimum/maximum and transfers by request and receipt (R2-7 follow-up)

Status: RESOLVED — approved by Ahmed on 9 Oct 2026 ("موافق تعمل كل حاجة صح"). Implemented in branch `claude/wh-minmax-transfers`.

- **Asked for:** the R2 warehouse plan left two items behind a gate: a minimum and maximum per warehouse, and transfers that are requested by one warehouse and received by it, not pushed in one step.
- **Files:**
  - `inventory/models.py` + migration `0007_location_levels_transfer_requests`;
  - new `inventory/transfer_requests.py` (service) and `inventory/transfer_request_views.py`;
  - `inventory/warehouse_views.py`, `inventory/urls.py`;
  - templates under `templates/inventory/`;
  - a status label in `settings_core/display_labels.py`.
- **Schema:**
  - `LocationStockLevel(location, item, min_quantity, max_quantity)`, one per pair. `Item.min_stock` is untouched and still drives purchase suggestions.
  - `TransferRequest` and `TransferRequestLine`. Each line keeps its send, receive and shortage `StockOperation`.
- **Stock behaviour:**
  - nothing moves while a request is only requested;
  - sending is `transfer_stock` from the source to the entity's **goods in transit** location (`TRANSIT-<entity>`, made on first use; not a selling or receiving place);
  - receiving is `transfer_stock` from transit to the destination;
  - what was sent but did not arrive is written off from transit with `adjust_stock` out, which needs `inventory.adjust_stock`;
  - cancelling a sent request reverses each send with `cancel_stock_operation`, so the goods go back to the source;
  - a received request cannot be cancelled.

  The engine's permission, period and lock rules apply to every leg. Total stock and value never change except by a recorded shortage.
- **Ledger:**
  - transfers move value between inventory lines only;
  - a shortage is an ordinary adjustment out, so it lands in stock-count losses;
  - between entities, the existing intercompany balancing applies.
- **Permissions (existing codes only):**
  - viewing and requesting: `inventory.view_stock`;
  - sending and receiving: `inventory.transfer_stock`;
  - a shortage at receipt: `inventory.adjust_stock`;
  - setting levels: `master_data.manage_locations`;
  - cancelling: the requester or a transfer user.
- **Refill suggestion:**
  - an item at or below its minimum in a warehouse is refilled up to its maximum (or its minimum when there is none);
  - the source is the warehouse in the same scope with the most stock above its own minimum.
- **Tests (`inventory/tests_transfer_requests.py`):**
  - levels, suggestions and the rules;
  - request → send → receive quantities per warehouse and in transit, with total quantity and value unchanged;
  - a partial send plus a shortage on arrival;
  - the shortage permission;
  - cancelling before and after sending, and refusing after receipt;
  - trial balance balanced and reconciliation clean;
  - the screens and who may act.
- **Known effect:** the goods-in-transit location shows in location pickers (stock list, manual transfer, count). The warehouses hub shows it only while something is on the way.

## HG-041 — Services work orders and spare parts (proposed, not built)

- **Status:** OPEN PROPOSAL. Nothing is built yet. This records the decision before the services work order package (`docs/ACTIVITY_SCORECARD.md`, step 2) touches stock.
- **Why it is a gate:**
  - A work order keeps its own states (received → diagnosed → quoted → approved → in repair → ready → delivered), so it needs new tables.
  - Spare parts leave stock for a repair.
    - The obvious shortcut, `inventory.services.adjust_stock` (an outbound adjustment), is wrong: `ledger/projector.py` projects an outbound adjustment as **stock-count loss**. Repair parts would show as shrinkage instead of the cost of the job.
- **Proposed design (A, recommended): parts move only through the sales engine.**
  - The work order holds its parts as planned lines. Nothing leaves stock while the job is open.
  - Delivering the job makes its sales invoice through `taxes.services.create_sales_draft_with_tax` / `sales.services.post_sales_invoice`, unchanged.
    - Labour goes on as a service item.
    - Parts go on as stock items.
  - Posting moves the parts out at authoritative movement cost (HG-003), as cost of goods sold. The ledger and the customer's account are exactly those of a sale.
  - **Warranty jobs:** the part goes on the same invoice at price 0.
    - The cost still lands in cost of goods sold, against a customer, so margin per job and per technician stays true.
    - A test must first prove the sales engine accepts a zero-price stock line and costs it.
  - **Not touched:** inventory movement types, the projector, sales posting, average cost.
- **Alternative (B, needs approval):** a new movement type, "issued to a job".
  - It would be projected to a "cost of services" account, and would let parts leave stock before the invoice (long repairs).
  - It changes inventory movement and accounting (protected), so it is not done without Ahmed's approval.
- **Files (A):** a new `workorders` app (models, services, views, templates, migration); the nav and dashboard panel for `services`; demo data. Sales, inventory and ledger: none.
- **Risks (A):**
  - Stock looks available until the job is delivered. The work order screen shows parts reserved by open jobs to make up for it.
  - Parts reserved and then not used are simply dropped from the planned lines.
- **Tests to write:**
  - delivering a job posts one invoice whose stock lines move the exact quantities at movement cost;
  - a warranty part at price 0 costs the part and leaves the receivable unchanged;
  - cancelling the invoice brings the parts back;
  - an open job moves nothing;
  - permissions per role;
  - the trial balance stays balanced and reconciliation clean.

## HG-042 — Medical insurance: patient share and insurer share (design A, built in MED-008)

- **Status:** BUILT with design A (MED-008), the default recorded in the decisions table below. Design B was not built.
- **What was built (MED-008):**
  - **New tables, migration `medical/0007_insurance`:** `InsuranceContract` (insurer customer, covered %, claim day), `ServiceCoverage` (a service's own %, 0 = not covered), `PatientCover` (patient, insurer, member number, expiry) and `InsuredBill` (the appointment, the agreed figures, both invoices).
  - **Billing:** `medical.insurance.bill_insured` makes the two draft invoices through the unchanged `create_sales_draft_with_tax`.
    - The patient's share is rounded half up, and the insurer takes the rest, so the two add up to the visit price to the piastre.
    - If the insurer pays it all, there is no patient invoice; the appointment's own invoice is then the insurer's.
    - Before tax, the two shares always add up to the visit's price. With VAT on, each invoice rounds its own tax, so their combined tax can differ by a piastre from what one invoice would have charged.
  - **Claim:** `claim()` is read-only, scoped to the entity, and shows posted returns as rejected.
  - **Cancelling:** sales cancelling is protected, so cancelling one invoice does not cancel the other automatically. Instead, a visit with only one of its two invoices cancelled is listed on the insurance page.
  - **Reports:** the clinic report (`medical.insights._billed`) and `appointments.performance` now add both invoices of an insured visit. They use the same net-sales formula; only which invoices count changed.
  - **Not touched:** the customer ledger, sales posting, aging, returns, cashbox movements, `parties.balance`.
  - **Scope:** insurance covers appointments only. Dental steps and physiotherapy packages are still billed to the patient alone.
  - **Tests:** `medical/tests_insurance.py` (22).
- **Why it is a gate:** one visit is owed by two parties: the patient pays a share (co-pay) and the insurance company pays the rest, monthly, against a claim. Splitting one invoice's receivable between two customers changes the customer ledger (protected).
- **Proposed design (A, recommended): two ordinary invoices per insured visit.**
  - Each insurance company is a customer, carrying its contract: coverage % per service and a monthly claim day.
  - An insured visit makes two sales invoices through the unchanged sales engine:
    - the patient's, for their share;
    - the insurer's, for the rest.
  - Both carry the visit, so the patient file shows one visit with both invoices.
  - **Monthly claim:** a read-only statement of the insurer's posted invoices for the month, printed and sent.
  - **Payment:** the insurer pays through the ordinary customer collection.
  - **Rejections:** a rejected line becomes the existing sales return against the insurer's invoice. If the patient must then pay it, a new invoice goes to the patient.
  - **Not touched:** the customer ledger, sales posting, aging, report calculations.
- **Alternative (B, needs approval):** one invoice with a receivable split between two customers. It keeps one document per visit, but it changes ledger posting and aging, so it is not done without Ahmed's approval.
- **Files (A):** `medical` (an insurer contract model and migration, the visit-to-invoices service, the claim statement view); the reports for revenue per insurer and claims outstanding; demo data.
- **Risks (A):**
  - Revenue for one visit is split over two invoices. Reports per doctor and per service must add both, and the tests pin that down.
  - Cancelling one invoice must offer to cancel the other.
- **Tests to write:**
  - the shares add up to the visit price to the piastre, with rounding on the insurer's side;
  - each customer's balance moves by exactly its share;
  - the claim lists only that insurer's posted invoices in the month;
  - a rejection through a sales return lowers the insurer's balance;
  - permissions;
  - the trial balance and reconciliation.

## HG-043 — Medical specialty records (proposed, not built)

- **Status:** OPEN PROPOSAL. Nothing is built yet. This records the schema decision before the medical package (`docs/ACTIVITY_SCORECARD.md`, step 4) adds specialty records.
- **Why it is a gate:** the current `medical.PatientFile` and `medical.ClinicalVisit` cannot hold:
  - a dental chart and treatment in stages;
  - lab results against reference ranges;
  - physiotherapy session packages;
  - an animal and its owner;
  - a reception queue.

  Each of these needs new models and migrations.
- **Proposed design (A, recommended): new tables in the `medical` app, one per specialty. Each links to the patient file and, where it is done in a visit, to the visit.**
  - **Dental:** `ToothRecord` (patient, tooth number in FDI notation, condition, planned treatment, stage, done in visit).
  - **Lab:** `LabTest` (the test, unit, reference low/high, linked to its service item) and `LabResult` (visit, test, value, flag computed from the range). The result sheet prints like any document.
  - **Physiotherapy:** `SessionPackage`, plus a session count used per visit.
    - The package is sold as an ordinary service invoice (unchanged sales engine), and its revenue is recognised at sale, like any service today.
    - Spreading package revenue over sessions (deferred revenue) would change ledger posting, so it is not part of this design.
  - **Vet:** `PatientFile` gains a kind (person/animal), species, breed and an owner (the existing customer). One migration, no behaviour change for people.
  - **Reception queue:** `QueueTicket` (appointment or walk-in, number, arrived/called/done times). Read by the waiting screen.
  - **Shown only for their sub-activity:** dental, lab, physio, vet, clinic/medical centre for the queue.
- **Not touched:** sales posting, customer ledgers, appointments' existing statuses, report calculations.
- **Files:** `medical` (models, migrations, services, views, templates); demo data per sub-activity; the medical dashboard panel.
- **Risks:**
  - The `PatientFile` change (kind/owner) is the only change to an existing table. It adds nullable fields with defaults, and existing rows stay people.
  - Lab flags depend on the units entered: a range is only checked against the same unit.
- **Tests to write:**
  - each specialty's record is created and shown only for its sub-activity;
  - lab flags (low/normal/high) at the boundaries;
  - session counts never go below zero, and a package's invoice is an ordinary posted sale;
  - a vet file's owner is the customer who pays;
  - the queue order;
  - permissions per role;
  - migrations forward and back on a database with existing patient files.

## HG-044 — Restaurants: dish cost, waste and ingredient deduction (proposed, not built)

- **Status:** OPEN PROPOSAL. Nothing is built yet. This records the decision before the restaurant package (`docs/ACTIVITY_SCORECARD.md`, step 5) touches stock.
  - `docs/PROJECT_STATE.md` already notes that deducting ingredients by recipe on sale needs a Hard Gate.
- **Why it is a gate:** waste and recipe deduction both take ingredients out of stock, and each decides which account the cost lands in. The existing outbound adjustment is projected as **stock-count loss** (`ledger/projector.py`).
- **Proposed design:**
  1. **Dish cost and margin: read-only, no gate needed.**
     - Cost per dish comes from the recipe and ingredient average cost, and is compared with the menu price.
     - It reuses `manufacturing.Recipe`, read-only.
  2. **Waste (A, recommended now): the existing stock adjustment, unchanged, with a "waste" reason.**
     - The value lands in the stock-loss account, which is where shrinkage and spoilage belong in a small restaurant's books.
     - A waste report reads those adjustments by reason, kept apart from count differences.
  3. **Waste (B, needs approval):** a new movement type, "waste out", projected to its own "food waste" expense account. It changes inventory movement and projection (protected).
  4. **Ingredient deduction on sale (needs approval, either way):**
     - **C1:** the dish becomes a stock item made by a daily production run from its recipe, using the existing manufacturing engine unchanged. Ingredients become dish cost through HG-030/HG-036, and selling the dish costs it as any stock item. The kitchen records the day's production.
     - **C2:** posting a sale of a dish with a recipe moves its ingredients out at cost as cost of goods sold. Real-time, but it changes sales posting (protected).
     - **Recommended:** C1, because it keeps every engine unchanged. Ahmed decides between C1 and C2 before anything is built.
- **Files (1 and 2A):** `restaurant` (dish cost and waste screens, reports), `reports` (the waste report). No inventory or ledger change.
- **Risks:**
  - With 2A, waste and count differences share one account, so only the report separates them by reason.
  - With C1, stock of dishes must be produced before it is sold. Selling more than was produced does not go negative: posting the sale is refused for lack of stock (`sales.services._validate_stock_available`), so the order cannot be paid. C1 therefore needs the dish produced before payment (a daily kitchen run, or a run made for the missing quantity just before the sale posts), and its tests must cover a sale beyond what was produced.
- **Tests to write:**
  - dish cost equals the recipe's ingredient cost at average cost;
  - waste moves the exact quantity and value into stock loss, and the waste report shows it by reason;
  - (C1) a production run plus a sale costs the dish at the run's cost, with the ledger balanced and reconciliation clean;
  - permissions.
- **W16 note (demo and two RESTO-004 follow-ups, no engine change):**
  - The restaurant demos switch their dishes to "made in the kitchen" before the sample month, so every plate sold is cooked from its ingredients through the manufacturing engine. The dashboard margin then matches the dish-costing screen (77.6% in the restaurant demo, down from 98%).
  - A kitchen dish with no plates ready is cooked to order, so it is no longer counted as "out of stock" (`reports.selectors.stock_alert_counts`, `reports.followup`).
  - The waste report no longer counts ingredients that a production run consumed (`restaurant.insights.waste`). Before this, cooking showed up as waste.
  - Sales posting, the manufacturing engine, inventory movements and the ledger are unchanged.
## HG-039 — Signing and sending e-invoices to the Tax Authority (ETA-002)

Status: APPROVED — Ahmed on 9 Oct 2026 ("خلص كله"), the third open decision in the audit report. Implemented in branch `claude/eta-002-submission`.

- **Why a gate:** a new table (`einvoice.Submission`, migration `0002_eta_submissions`), and Hesba starts talking to an outside system on the client's behalf.
- **What does not change:** sales invoices, posting, returns and every balance. Sending only reads a posted invoice through the EINV-001 document builder.
- **How it works:**
  - **Configuration:** comes only from the deployment's environment (`ETA_ENVIRONMENT`, `ETA_CLIENT_ID`, `ETA_CLIENT_SECRET`, `ETA_SIGNER_URL`, `ETA_SIGNER_TOKEN`). No secret is stored in the database or shown on a screen.
  - **Signing:** the document is serialized by the authority's rule (`einvoice.portal.serialize`). The client's own signer, next to their e-signature token, returns the CAdES-BES signature.
  - **Sending and reading back:** Hesba then submits, reads the status and cancels through the authority's API (`einvoice.portal`).
- **Rules:**
  - only a posted invoice with complete data is sent;
  - an invoice that is valid, still being checked, or being sent right now is not sent twice: the invoice is claimed under its row lock with a `sending` row before the signer or portal is called. A claim left by a server that died mid-call is reconciled after 10 minutes: Hesba searches the authority for the invoice's internal ID over the claim's time (`GET /api/v1.0/documents/search`). If the document is found, the claim takes its uuid and status and nothing is sent again. If it is not found, the claim is closed as rejected and the invoice may be sent. If the authority cannot be asked, nothing is sent. The outcome is audited (`eta_send_recovered`). The screen offers this check ("ask the portal about this invoice") on any invoice stuck at sending. The search covers a one-hour window from the claim, well inside the authority's 30-day limit. A send whose outcome is unknown keeps its claim (audited `eta_send_unknown`) and is reconciled the same way before any retry: the connection cut after the request went out, or the authority answered 5xx. Only a definite 4xx refusal is recorded as rejected, and only a failure before the request (signer, login) drops the claim;
  - a cancellation is claimed before the authority is called: the row becomes `cancel_requested`, with `cancel_requested_at`, so it is never asked twice. Only a definite 4xx refusal puts it back to valid. A lost answer keeps it requested (audited `eta_cancel_unknown`), and a refresh with no request on the authority's side puts it back to valid ("never reached the portal"), but only when it was read more than 5 minutes after the request, so a request still on its way is never taken for a lost one. It stays a request until the authority's details say `cancelled`. A refresh reloads the row under its lock, and also reads a pending request from the authority's own `cancelRequestDate`, so a refresh racing a cancellation never undoes it. The receiver may decline it, and the invoice is then valid again. Nothing can be sent again while a cancellation is pending;
  - the signed text keeps each string's JSON escaping, exactly as it is sent;
  - each recorded state of a sending or a cancellation is committed together with its audit row. A cancellation is audited when it is claimed, before it goes out. A refusal returns a cancellation to valid only while the row is still that request's claim, so a newer status saved by a refresh is never undone;
  - the signer token is a required setting: Hesba never calls the signer without it. `ETA_ENVIRONMENT` is set in the deployment and not pinned by the blueprint, so a sync never moves a live client back to the test portal. Unset means `preprod`. Any other value than `preprod` or `prod` is reported as missing setup and nothing is sent, so a typo never falls back to testing;
  - a rejected or invalid sending is kept as history and the invoice can be sent again;
  - only a valid invoice can be cancelled, with a reason;
  - every send, status check and cancellation is in the audit log;
  - sending and cancelling need `sales.create_sales_invoice`; testing the connection needs `settings.manage_settings`.
- **Tests (`einvoice/tests_portal.py`):** run against a fake portal and signer, with no network:
  - the serialization rule, escaping included;
  - sign → submit → status → cancellation request → cancelled or declined, with the exact headers;
  - a second send while the first is on its way, and a stale claim;
  - rejection with the authority's reason, then sending again;
  - invalid after checking;
  - signer down or settings missing: nothing is sent;
  - incomplete data is never sent;
  - the screens, and who may send;
  - the connection check.
- **Needs from Ahmed / the client to go live:** the portal account, the ERP client ID and secret, the signing token and a signer (docs/ETA_INTEGRATION.md).
- **Protected touch: local cancellation of a sent invoice (Codex review on #181).**
  - **Reason.** Sending claims the invoice under its row lock and then calls the portal outside any transaction. `cancel_posted_sales_invoice` never looked at the e-invoice side. An invoice could therefore be reversed here (stock, cash and customer ledger) while the authority accepted it. A legal document would then stand against books that say it never happened.
  - **Change.**
    - `sales/services.py::cancel_posted_sales_invoice` gains one line, under the lock it already holds: `einvoice.guards.before_sales_invoice_cancel(invoice)`. It refuses when the latest sending, in any environment, is sending, submitted, valid or cancel-requested.
    - Rejected, invalid, cancelled and never-sent invoices pass untouched, so the reversal itself is unchanged.
    - `send_invoice` now re-reads the invoice under the same lock and refuses one that is no longer posted.
    - Together the two paths serialise: whichever takes the lock first wins, and the other sees its result.
  - **Risk.** A user must cancel on the e-invoice page first and wait for the authority to confirm before cancelling here. That is the legally required order. The message says so in Arabic and English.
  - **Tests.** `LocalCancellationTests` covers:
    - each live state blocking;
    - a cancellation during signing;
    - a rejected invoice cancelling freely;
    - a production document while the server talks to preprod;
    - an invoice cancelled between building and locking never being sent;
    - the Arabic message on the sales screen.
  - **Not covered.** A sales *return* on an accepted invoice needs an ETA credit note, which is a separate feature (ETA-003).
## HG-038 — Contracting phase 2: bill of quantities, progress certificates, retention, advances, subcontractors, budget (CONTRACT-002)

Status: APPROVED — Ahmed approved on 9 Oct 2026 ("خلص كله"), after the audit report listed this as an open decision; the first demo feedback item also asked for it ("قدرات نشاط المقاولات محتاج تعديل كبير يمشي مع طبيعة النشاط"). Implemented in branch `claude/contract-002`.

- **Why a gate:**
  - it adds schema (models and a migration in `projects`);
  - retention and advances change where customer and supplier balances appear in the projected general ledger (GL-002 / HG-032);
  - a project's cost now also reads purchase invoices.
- **What does not change:**
  - sales and purchase posting, cancellation and returns;
  - customer and supplier ledger rows and the party balances and statements built from them;
  - cashbox movements;
  - stock movement and average cost;
  - report figures outside the projects screens and the general ledger.

  Every certificate is an ordinary draft sales invoice made through `create_sales_draft_with_tax`, and every subcontractor bill an ordinary draft purchase invoice through `create_purchase_draft_with_tax`. Both are posted, collected or paid, returned and cancelled from their own screens exactly as today. An advance is an ordinary customer payment (`record_customer_payment`).
- **Schema (`projects` only):**
  - `Project.retention_rate` and `Project.advance_recovery_rate`, both defaulting to 0;
  - `ProjectExpense.heading`, defaulting to "other", so every existing link keeps its meaning;
  - new `BoqLine`, `Certificate` + `CertificateLine`, `ProjectAdvance`, `RetentionRelease`, `Subcontract`, `SubcontractBill`, `SubcontractRelease`, `ProjectPurchase`, `BudgetLine`.
- **Contract arithmetic (on top of the invoice, never inside it):**
  - **gross:** the certificate's gross is the work done since the last certificate, from the bill of quantities (cumulative quantity to date − quantity already certified, × rate) or a lump sum. The invoice carries the gross, so revenue and VAT are the full work value;
  - **retention** = gross × the project's retention rate;
  - **advance recovered** = gross × the recovery rate, capped by the advance not yet recovered;
  - **net payable now** = the invoice total − retention − recovery.

  A certificate whose invoice is cancelled stops counting. Only one certificate may be a draft at a time, so the quantities to date stay in order. A draft certificate can be withdrawn, which deletes its draft invoice: drafts have no ledger, stock or cash rows.
- **General ledger (`ledger/projector.py`, `ledger/chart.py`, `ledger/reports.py`):**
  - an advance payment credits **customer advances** (2105, already in the chart) instead of receivable;
  - a posted certificate moves its recovery from customer advances to receivable, and its retention from receivable to **retention receivable** (new 1111 on the contracting chart). The same lines reverse in the cancellation entry;
  - a retention release moves it back to receivable;
  - subcontractor bills mirror this with **retention payable** (new 2106);
  - a service purchase linked to a project books to **project costs** (5103) instead of other general expenses.

  The customer reconciliation check now compares receivable + retention receivable + customer advances with the customers' balances (and payable + retention payable with the suppliers'). Those sums equal the party ledgers by construction. On a chart without these accounts (any other activity), the lines stay in receivable and payable as before.
- **Cost and budget:**
  - a project's cost is materials issued + linked expenses + linked service purchases (posted, before tax, after posted returns);
  - a purchase invoice with stock lines cannot be linked: those goods go to the site through "issue from stock", so they are never counted twice;
  - each cost reads against a budget per heading (materials, subcontractors, labour, equipment, other).
- **Permissions (existing codes only):**
  - certificates, retention and the bill of quantities: `sales.create_sales_invoice`;
  - advances: `sales.receive_customer_payment`;
  - subcontracts and their bills: `purchases.create_purchase_invoice`;
  - figures that show cost or profit: `reports.view_profit_report`.
- **Returns, cancellations and releases (review of #179):**
  - **Sales return on a certificate:** gives back that invoice line's quantity to the bill of quantities. It also takes back the same share of the certificate's gross, retention and recovery as the return is of the invoice total (`projects.contract.return_share`). The projector books exactly that share in the return's entry and reverses it in the return's cancellation entry.
  - **Purchase return on a subcontractor bill:** does the same for its retention (`projects.costs.return_share`).
  - **Releases:** append-only. A release is never edited or deleted; it can be reversed on a date (`reversed_on`), which posts the opposite entry. A release or reversal dated in a closed month is refused (`ensure_period_is_open`).
  - **One guard line in the engines:** `projects.guards` is called at the end of these services, inside their own transaction:
    - sales: `cancel_posted_sales_invoice`, `create_sales_return`, `cancel_sales_return`, `cancel_customer_payment`;
    - purchases: `cancel_posted_purchase_invoice`, `create_purchase_return`, `cancel_purchase_return`.

    It refuses (and so undoes) any change that would leave a project with more retention released than held, or more advance recovered than received. Documents of no project pass untouched; nothing else in those services changes.
  - **Entity scope:** project screens resolve submitted cashboxes, collections and purchase invoices through `entities.scope`.
  - **Second review:**
    - return shares are allocated on the cumulative returned total, so partial returns add up to exactly the whole deduction;
    - a return cannot be cancelled once a later certificate billed the quantity it gave back;
    - the guard takes the project's (or subcontract's) row lock, like certificates and releases;
    - a project's certificates and a subcontract's bills stay in one entity, so retention and advances (and their releases) are held in one place.
  - **Third review:**
    - a project's payments join it in the same entity: the entity of its first certificate, or else of its first payment;
    - an instalment-plan collection cannot be linked as an advance;
    - the project's owner cannot change once it has invoices, payments, certificates or releases;
    - certificate, payment and subcontractor-bill lists, and the certificate page and print, are scoped to the working entity.
  - **Fourth review:**
    - **Entity reach.** A project's money (terms, bill of quantities, certificates, payment links, releases and reversals) can change only from the entity it lives in, or from the whole group. The same holds for a subcontract's bills and releases and for linked service purchases. Every submitted id is resolved through `entities.scope`, and the services check it again.
    - **Scoped figures.** The contract figures (`contract.position`), the project summary, the subcontract figures and the cost by heading are computed from the working entity's own documents. Another entity sees none of this project's money.
    - **Closed months.** Linking or unlinking an advance moves that payment's journal entry between receivable and customer advances on the payment's own date, so it is refused when that month is closed. Linking or unlinking a posted service purchase moves its expense into or out of project cost on the invoice's date (and its returns' dates), so it is refused the same way. A collection link changes nothing in the books, so it is not restricted.
    - **Stable return shares.** Returns are replayed in the order they were made and cancelled. A new return takes the returned share on the total still standing, less what those returns already took. Each share is fixed when the return is made, and cancelling a return reverses exactly its own share: no other return's entry moves.
    - **Dated releases.** A release may take only what is held on its date and stays held on every later date: posted documents on their dates, returns on theirs, earlier releases and reversals (`contract.lowest_held`). The consistency guard checks the same balance on every date, not only today.
  - **Fifth review:**
    - **Dated advance recovery.** A certificate recovers only advance received by its own date that stays unclaimed on every later date (`contract.advance_events`). Unlinking or cancelling an advance is refused when an earlier certificate needs it on any date.
    - **Header permission.** The project header's owner and contract figures need `sales.view_sales_invoices`. A purchases-only user (the stock keeper) on the subcontractors tab sees neither.
    - **Reserved service items.** Bills and certificates use `services.service_item`. When a catalog item holding the reserved code (`PRJ-SUB`, `PRJ-BILL`) tracks stock or is inactive, it is left alone and the next free code (`PRJ-SUB-2`, …) is used as a non-stock service. A bill therefore never moves stock or books to inventory.
  - **Sixth review:**
    - **Permanent entity.** A project belongs for good to the entity of its first certificate, cancelled ones included, else of its first payment. A subcontract belongs to the entity of its first bill. A cancellation can no longer move a project (and the projection of its past releases) to another entity.
    - **Exact return tax.** A linked service purchase's cost subtracts each posted return's total less the tax that return actually recorded, as the ledger books it. Lines at different VAT rates therefore stay exact.
  - **Seventh review:**
    - **Deductions capped.** Retention and advance recovery together cannot exceed 100% of a certificate: the terms are refused, and recovery is capped at what the certificate leaves after retention.
    - **Cancellation after a release.** A sales or purchase invoice's cancellation is dated back to the invoice. A certificate (or subcontractor bill) whose retention was released on some date, even if that release was reversed later, therefore cannot be cancelled. The guard says so plainly and points to a return, which is dated today and keeps the history intact.
  - **Eighth review:**
    - **No instalments on a certificate.** `installments.services.create_plan` refuses a certificate's invoice (one guard line), and the sales screen no longer offers it: its collections must be recorded on the project so retention and the advance come off.
    - **A certificate's invoice stays on its project.** `services.unlink` refuses it. The sales-side guard also resolves a project through the certificate itself, not only through the invoice link.
  - **Ninth review:** the project list computes due-now for a whole page in grouped queries (`contract.due_now_many`). A project with returns on its certificates, or with releases seen from an entity, falls back to `position`. The reserved service item is created with an atomic get-or-create, so two first bills at once reuse one row.
  - **Tenth review:** a project billed as a lump sum cannot get a bill of quantities afterwards, so billed work is never billed again. Certificates cannot be dated before the latest live one, so quantities to date run forward in time.
  - **Eleventh review:** the project accounts (retention receivable and payable, project costs, project work in progress) follow the Projects module, not only the contracting activity. Any activity that switches Projects on gets them, so the ledger books the retention its project screens show.
  - **Twelfth review:** an ordinary invoice linked to a project anchors it to that invoice's entity, as a first certificate or a first payment does. Linking an invoice from another entity is refused, so its collections always stay linkable. An instalment that pays an invoice on the project links as that project's collection, never as an advance. That invoice cannot be unlinked while such instalments count there. The payments screen offers only these instalments.
  - **Thirteenth review:** when an upgrade adds a control account whose default code the user already took for an account of their own, `ensure_chart` creates the control under the nearest free code beside it under the same parent. The user's account is untouched, and nothing that posts to the control is silently skipped.
  - **Fourteenth review:** a backdated certificate counts only the returns made by its date, so quantities already billed then are never billed again. A project collection that loses its CP- number to a collection saved at the same moment takes the next free number instead of failing.
- **Known limits:**
  - the customer's aging still shows the whole balance, retention included; the project screen shows what is due now;
  - advances paid to subcontractors are not tracked separately: a supplier payment before the bill nets on the supplier's account as today.
- **Tests:** `projects/tests_contract.py`:
  - certificate quantities, gross, retention, recovery and net per scenario;
  - the advance cap;
  - a cancelled certificate releasing its quantities;
  - one draft at a time and withdrawing it;
  - retention release;
  - subcontractor bill and release;
  - the purchase-link rule;
  - budget against actual;
  - trial balance balanced and reconciliation clean, with the exact amounts in receivable, retention receivable, customer advances, payable, retention payable and project costs;
  - the screens and who may act.
## HG-040 — Education module: students, payers, courses, study groups, enrolments (EDU-001)

- **Status:** approved by Ahmed on 10 October 2026, as part of the education restructure: "اعادة هيكلة التعليمي". See `docs/ACTIVITY_SCORECARD.md`.
- **Why:** education scored 54%. A student was only a customer and a class was only an appointment. There was no group, schedule, enrolment, attendance or monthly fee.
- **Change.** A new `education` app (new models and one migration), which touches no existing model:
  - `Student`, whose `payer` is an ordinary `Customer` (a parent, or the student themselves). Brothers and sisters share one account.
  - `Course`, which owns a non-stock service `Item`, so its invoices are ordinary sales invoices. Renaming the course or changing its price keeps the item in step.
  - `StudyGroup`, with its teacher (an `Employee`), room, weekdays, time, length, capacity, fee and dates. A teacher or a room is never booked into two groups at once.
  - `Enrollment`, with its start and stop dates, a discount percentage and a status. There is one active enrolment per student and group. Capacity is checked under the group's row lock.
- **Entity scope (Codex on #185, HG-034):** students, courses and groups belong to the entity they were made in, and unowned ones count as the main entity's. Every list, file, choice and change is scoped to the entity being worked in; the whole group sees everything. A student, a group and its course must share one entity. A room clash counts within one entity; a teacher clash counts everywhere.
- **Not touched:**
  - sales and payment posting, the customer ledger, cash, stock and reports;
  - the permission core: viewing uses `master_data.view_master_data`, and changing uses `master_data.manage_parties`.

  The screens appear only on an education install, or in an education entity of a group.
- **EDU-002 (attendance):**
  - `ClassSession` is one per group and day, held or cancelled, with the topic covered.
  - `Attendance` is one mark per student and class: present, late, absent or excused.
  - The roster is the enrolments running on that day. Unmarked students count as present, and taking a day again corrects it.
  - A teacher (a user linked to the group's employee) takes attendance for their own groups only; managers can take it for any group.
  - Attendance rates count held classes only, with late counted as attended.
  - Absent and late students get a one-tap WhatsApp link to the parent.
  - No money moves.
  - **Codex on #186:**
    - A student stopped and enrolled again on the same day is one row, with one mark.
    - Attendance is taken only on the group's own weekdays and within its dates, and the group page offers its latest class day.
    - A message about an older class gives its date instead of "today".
    - Attendance screens are scoped to the entity being worked in.
  - **Schema change and its risk plan (Codex on #186):**
    - **Files:**
      - `education/models.py` (`ClassSession`, `Attendance`, `Presence`, `SessionStatus`);
      - migration `education/0002`;
      - `education/attendance.py` (all writes);
      - `education/views.py`;
      - `templates/education/today.html` and `attendance.html`;
      - `reports/navigation.py` ("My classes").
    - **Schema:** two new tables in the education app only.
      - Unique constraints: one session per group and day, one mark per student and session.
      - No existing table is altered, and the migration only adds tables, so rolling back is dropping them; no other app reads them.
    - **Risk 1, a correction rewrites history.** Taking a class again replaces its marks.
      - The audit row keeps the full before and after: status, topic, and each student's mark and note.
      - Nothing else is deleted.
    - **Risk 2, rates drift.** Rates count only held classes, over a window of exactly the last N calendar days, today included.
    - **Risk 3, a teacher reaches others' groups.** A teacher-only user sees and changes only the groups linked to their own employee record, and only through the attendance screens.
    - **No money or stock is touched.**
    - **Verification** (`education/tests.py`):
      - roster on a day, including stop-and-rejoin;
      - weekday and term checks;
      - future days refused;
      - unknown marks refused;
      - corrections recorded mark by mark;
      - excused counted;
      - the 30-day window;
      - teacher-only access and menu;
      - entity scope;
      - malformed dates.
- **EDU-003 (monthly fees):** `education/billing.py` makes one draft sales invoice per parent for a month. It goes through `create_sales_draft_with_tax`, and through `post_sales_invoice` when posting is asked for. Neither function is changed.
  - **Lines:** one per enrolment, using the course's own service item.
    - **Monthly groups:** one month at the group's fee, with the enrolment's discount as the line discount.
    - **Per-class groups:** the classes attended that month; late counts as attended.
    - **Whole courses:** billed once.
  - **Duplicates:** a new `FeeCharge` row records which enrolment and month each line billed, so running the month again bills only what is new. A cancelled invoice frees its month. Each parent's customer row is locked while their lines are worked out, so two runs at once cannot bill one month twice.
  - **Not allowed:** future months.
  - **Fees owed:** the existing customer aging, kept to parents of students, with WhatsApp reminders.
  - **`FeeCharge.amount`** is as wide as an invoice line (14 digits), because a per-class charge is quantity × fee (Codex on #187).
- **EDU-004 (teacher pay, dashboard, month report):**
  - **New schema, education app only:**
    - `StudyGroup.teacher_pay` (none / per class held / share of the group's fees / fixed per month) and `StudyGroup.teacher_rate`.
    - A new `TeacherPayment` table: teacher, month, amount, and a one-to-one link to the `Expense` that paid it. Migration `education/0004`.
    - A new `GroupPayTerm` table (migration `education/0005`): the teacher, pay basis and rate of each group for a month, frozen when that month is first paid.
      - Editing a group afterwards changes only the months still to pay.
      - A payment can never drop out of its month or be made twice.
  - **Paying a teacher:** `expenses.services.record_expense`, called unchanged, under a new "Teacher pay" category (`TEACHER-PAY`).
    - The cash leaves the chosen cashbox through the existing cashbox operation.
    - The payment shows in the expenses list and comes off profit.
    - Cancelling the expense (expenses page, also unchanged) reverses the cash, and the month is owed again.
    - The teacher's row is locked while the amount is checked. Nothing above what is still owed can be paid, and no future month.
  - **What a teacher is owed** is read from records the system already keeps:
    - classes held (attendance);
    - the group's live fee charges (EDU-003);
    - or whether the group had students that month.
  - **Dashboard:** the education panel replaces the bookings panel for education installs, and "Today's classes" becomes the lead quick action.
    - Panel: active students, today's classes with how many have attendance taken, 30-day attendance, fees billed this month (needs sales view), parents who owe (needs the customer report).
  - **Month report:** per group: students, classes held, attendance, fees billed. Teacher pay and what is left only show for users who can see the expenses.
  - **Not touched:** sales, cashbox and expense posting, ledgers, report calculations.
- **Tests:** `education/tests.py` covers:
  - payers and siblings;
  - the course item kept in step;
  - teacher and room clashes;
  - capacity, discount, stop and re-enrol;
  - the screens in Arabic and English;
  - permissions;
  - other activities not seeing the module;
  - the demo data.
## HG-046 — service contracts billed per period (SVC-002)

Status: IMPLEMENTED for review — new tables only, no protected logic changed. Implemented in branch `claude/svc-002-contracts`. The number is HG-046 because HG-041…HG-045 are taken on open branches.

- **Asked for:** the activity scorecard left services at about 76%. What service businesses were missing most is a recurring contract: a maintenance contract or a monthly retainer billed every period, with its visits.
- **Files:**
  - new app `agreements/` (models, `services.py`, views, urls, migration `0001_initial`);
  - templates under `templates/agreements/`;
  - a menu entry and icon (`reports/navigation.py`, `templates/partials/shell_icons.html`);
  - demo seeding (`prepare_demo._contracts`, `reports/demo_catalogs.py` extras).
- **Schema:**
  - `ServiceContract`: customer, service item, amount per period, month/quarter/year, start, optional end, visits per period, status;
  - `ContractBill(contract, period_start, invoice)`, where the invoice is a one-to-one with `PROTECT`.
- **Money behaviour:**
  - "bill what is due" makes one ordinary **draft** sales invoice per due period through `taxes.services.create_sales_draft_with_tax`, with no cashbox and nothing paid;
  - posting it on the sales page is what touches the customer's ledger and the cashbox, exactly as any invoice;
  - a period whose invoice was cancelled is due again;
  - the contract row is locked while it is billed, so two clicks never bill one period twice;
  - once a contract has been billed, its start date and period cannot change (the billed periods would no longer line up). The amount, end date and status can.
- **Who:** seeing contracts needs `sales.view_sales_invoices`; adding, editing and billing need `sales.create_sales_invoice`. It is shown to services (general, maintenance, professional, digital, other) and to maintenance contractors; a salon, a clinic or a shop gets a 404.
- **After review:**
  - each contract keeps the selling location (so the entity) it was made in; it is billed there and listed only in that entity (the whole-group view shows all);
  - a contract resumed after a pause, or given a "bill from" date, never bills the periods before it;
  - the last period stops at the contract's end date, and a period starting on the end day is not billed;
  - "customer balance" comes from the customer's own ledger, so payments and returns count.
- **Not done:**
  - no proration: a short last period is billed at the full amount;
  - visits are read from the customer's done appointments for the contract's service, and a visit is not booked automatically.
- **Tests:** `agreements/tests.py` covers:
  - periods at month ends;
  - due, paused and ended contracts;
  - refusals and the billed lock;
  - drafts that leave the ledger untouched until posted;
  - a cancelled invoice freeing its period;
  - visits;
  - the screens in Arabic and English;
  - each seeded role;
  - the activities it is hidden from.

  The demo is covered in `reports/tests_demo_catalogs.py`.

## HG-047 — session packages and billable hours (SVC-003)

Status: IMPLEMENTED for review. New tables only; no protected logic changed. Implemented in branch `claude/svc-003-packages-hours`, stacked on SVC-002 (HG-046) in the same `agreements` app.

- **Asked for:** these close the rest of the services gap on the scorecard:
  - a salon sells packages of sessions;
  - an office (accountant, consultant, designer) bills the hours it worked.
- **Files:**
  - `agreements/packages.py`, `agreements/hours.py` and `agreements/package_views.py`;
  - migration `agreements/0002`;
  - templates `agreements/packages.html`, `package.html` and `hours.html`;
  - the menu and icons;
  - demo seeding.
  - `agreements/services.py` gains `draft_invoice()` and `selling_location()`, shared with the contracts. The contracts' billing behaviour is unchanged.
- **Schema:**
  - `Package` and `PackageUse` (one row per session, cancelled rather than deleted);
  - `TimeEntry`, whose invoice is nullable and `PROTECT`.
- **Money behaviour:**
  - **Packages:**
    - a package is sold on one ordinary draft sales invoice for its whole price, and its revenue counts when that invoice is posted, like any service;
    - spreading revenue over the sessions (deferred revenue) would change ledger posting and is not part of this design;
    - sessions are used only once the invoice is posted, never below zero, and never on a day after the expiry date;
    - the package row is locked, so the last session cannot be taken twice;
    - a package whose invoice was cancelled cannot be used.
  - **Hours:**
    - "bill the hours" turns all of one customer's unbilled entries into one draft invoice, with one line per entry (quantity is the hours, price is the rate);
    - the entries are locked while this happens;
    - a cancelled invoice frees its hours;
    - a billed hour cannot be removed.
- **Who:**
  - Seeing either screen needs `sales.view_sales_invoices`.
  - Selling, using sessions and billing need `sales.create_sales_invoice`.
  - An employee linked to a login can log and remove their own unbilled hours without the invoice permission, and sees only their own.
  - Where each screen shows:
    - packages: services in beauty, general and other;
    - hours: services in professional, digital marketing, general and other.
- **Known overlap:** MED-005 (#195, not merged) has physiotherapy packages in `medical/` with the same rules. Once both are merged, they can be folded into one; until then each stays in its own activity.
- **Tests:**
  - `agreements/tests_packages_hours.py`: 15 tests;
  - demo tests for a salon and a digital agency in `reports/tests_demo_catalogs.py`.

## HG-041 — implementation note: services work orders, design A (SVC-001)

- **Status:** Built as design A (the proposal is recorded under HG-041 in PR #184).
  - Parts leave stock only through the work order's sales invoice, on the unchanged sales engine.
  - `inventory.services.adjust_stock` is not used, so repair parts are never posted as stock-count loss.
- **New schema:** a new `workorders` app (migration `workorders/0001`), with three tables:
  - `WorkOrder`: customer, device, serial, accessories, problem, diagnosis, technician, status, promised date, warranty days and end, the order it came back under warranty of, the invoice, entity;
  - `WorkOrderLine`: item, quantity, price, warranty flag;
  - `WorkOrderEvent`: the timeline.
- **Delivery** (`workorders.services.deliver`):
  - the order's lines go to `taxes.services.create_sales_draft_with_tax` and then `sales.services.post_sales_invoice`, both unchanged;
  - stock lines move out at movement cost (HG-003);
  - a warranty line goes on at price 0, so its cost still lands in cost of goods sold and the receivable does not change;
  - payment at the counter is the invoice's own `paid_now` into a chosen cashbox, never more than the invoice total.
- **A cancelled delivery invoice** (cancelled on the sales page, unchanged) sends its order back to "ready" on the next look:
  - the warranty is void;
  - the timeline keeps the cancelled invoice's number;
  - the order can be delivered and invoiced again.

  The sales cancel flow is not changed or blocked. The reopening is conditional on the order still being delivered on that very invoice, so a second caller never undoes a redelivery made in between.
- **A refused quote:** its lines come off when the customer refuses (kept in the audit as `drop_quote`), so pickup never charges the rejected work; an inspection fee is added afterwards as its own line.
- **Serial-tracked parts** (while serials are on) cannot go on a work order and block delivery; they are sold on a sales invoice, which records the serial (`serials` is not touched).
- **Line amounts** round half up with `config.money.money_round`, as the invoice lines do.
- **Totals:** shown with VAT, as the invoice will come to.
- **The warranty flag:** each line carries its own; a warranty return only ticks the box by default.
- **New customers:** creating one from the work order needs `master_data.manage_parties`, the same rule as the cashier's quick add.
- **Not touched:** inventory movement types, the projector, sales posting and cancellation, average cost, cashbox and customer ledgers.
- **Tests** (`workorders/tests.py`):
  - an open job moves nothing;
  - delivery moves the exact quantity at movement cost and sets the customer balance and cashbox exactly;
  - a warranty part costs 1350 at price 0, with the ledger balanced and nothing new left in suspense;
  - cancelling the invoice brings the parts back;
  - the status path;
  - what a technician may and may not do;
  - screens, permissions, the menu, the dashboard;
  - a salon having no work orders.
## HG-043 — implementation note: dental chart and treatment plan (MED-003)

- **Status:** The dental part of the HG-043 proposal (recorded in PR #184), built as design A. The lab, physiotherapy, vet and reception-queue parts are not built yet.
- **Schema:** migration `medical/0002_dental_chart` adds two tables and touches no existing one.
  - `ToothRecord`: patient, FDI tooth number, condition, notes; one row per patient and tooth (unique constraint). A tooth with no row is healthy.
  - `TreatmentStep`: patient, tooth (empty = the whole mouth), service item, price, stage 1–20, status (planned → done or cancelled), doctor, done date, visit, invoice.
- **Money:** a step never moves money.
  - "Invoice the done steps" makes one ordinary **draft** sales invoice through `taxes.services.create_sales_draft_with_tax`, the same way appointments bill.
  - Posting it on the sales page is what touches the customer's account and the cashbox.
  - A step on a cancelled invoice can be billed again, and a step is never on two live invoices: the patient row is locked while billing.
  - Making the invoice needs `sales.create_sales_invoice`; charting and planning need the medical records permission (or being a linked doctor).
- **Not touched:** sales posting and cancellation, customer ledgers, appointments, report calculations, average cost.
- **Shown only** when the current entity (or the installation) is medical / dental.
- **Risks:**
  - FDI numbers are checked against the permanent (11–48) and primary (51–85) sets, nothing else.
  - The price is the clinic's to set per step; it defaults to the service's price.
  - Rolling back the migration drops the two tables (charts and plans); nothing else depends on them.
- **Tests:** `medical/tests.py` → `DentalChartTests`; `reports/tests_demo_catalogs.py` (the dental demo has a chart and a plan in three stages).

## HG-043 — implementation note: lab results (MED-004)

- **Status:** The lab part of the HG-043 proposal, built as design A. The physiotherapy, vet and reception-queue parts are not built yet.
- **Schema:** migration `medical/0003_lab_results` adds two tables and touches no existing one.
  - `LabTest`: name (unique), unit, normal low and/or high, optional service item, order, active.
  - `LabResult`: patient, test, sample day (one result per patient, test and day), the number or a text value, and the unit and range **copied from the test when entered**, plus the flag.
- **Rules:**
  - The boundaries count as normal.
  - Changing a test's range never rewrites an old result's flag.
  - A text result (negative, A+) has no flag.
  - Arabic digits and the Arabic decimal sign are read; a comma groups thousands.
  - Each entry and correction is audited before/after.
- **Money:** none. A test is billed like any service, on an ordinary sales invoice.
- **Not touched:** sales, ledgers, appointments, report calculations.
- **Shown only** for medical / lab.
- **Risk:** the flag compares numbers only; it does not convert between units, so a range is valid for the test's own unit.
- **Tests:** `medical/tests.py` → `LabResultTests`; `reports/tests_demo_catalogs.py` (the lab demo).

## HG-043 — implementation note: physiotherapy session packages (MED-005)

- **Status:** The physiotherapy part of the HG-043 proposal, built as design A. The vet and reception-queue parts are not built yet.
- **Schema:** migration `medical/0004_session_packages` adds two tables and touches no existing one.
  - `SessionPackage`: patient, service, number of sessions, price, and its own sales invoice (one to one).
  - `PackageSession`: package, day, therapist, optional visit, notes, cancelled flag.
- **Money:**
  - selling a package makes one ordinary **draft** sales invoice through `create_sales_draft_with_tax`;
  - its revenue counts when that invoice is posted, like any service, as the proposal says;
  - spreading package revenue over the sessions (deferred revenue) would change ledger posting and is **not** done;
  - using a session moves no money.
- **Rules:**
  - Sessions are used only once the invoice is posted.
  - A package never goes below zero: the package row is locked while a session is recorded.
  - A mistaken session is cancelled (kept and audited), which gives it back.
  - A package whose invoice was cancelled is void.
  - Selling needs `sales.create_sales_invoice`; recording sessions needs the medical write permission (or being a linked therapist).
- **Not touched:** sales posting and cancellation, customer ledgers, appointments, report calculations.
- **Shown only** for medical / physio.
- **Tests:** `medical/tests.py` → `SessionPackageTests`; `reports/tests_demo_catalogs.py` (the physio demo: 10 sessions, 3 used).

## HG-043 — implementation note: the vet's animal file (MED-006)

- **Status:** The vet part of the HG-043 proposal, built as design A with **one change from the proposal**, explained below. The reception queue is not built yet.
- **Change from the proposal:** the proposal had `PatientFile` gain a kind, species, breed and owner. But `PatientFile` is one-to-one with its customer, and a vet's customer often brings **several** animals. So:
  - the animal is its own table, `Animal`, owned by an ordinary customer;
  - the one change to an existing table is a nullable `ClinicalVisit.animal` (PROTECT), which says which animal a visit was for.
  - `PatientFile` is unchanged, and existing rows and every other medical sub-activity are unaffected: the column stays empty.
- **Schema:** migration `medical/0005_vet_animals` creates `Animal` (name, species, breed, sex, date of birth, colour, microchip, neutered, allergies, notes, active) and adds `ClinicalVisit.animal`. Rolling back drops the column and the table.
- **Rules:**
  - The owner books, is billed and pays as before; the statement stays on the customer.
  - A visit can only name one of the patient's own animals, refused in `medical.services.save_visit` and in the view.
  - The animal's card shows its own visits only.
  - The prescription names the animal and its species.
  - The patients list also finds an owner by an animal's name.
  - Every save is audited before/after.
- **Money:** none.
- **Not touched:** sales, ledgers, appointments, report calculations.
- **Shown only** for medical / vet. The vet demo uses its own `animals` extra, so owners are not given human patient profiles.
- **Tests:** `medical/tests.py` → `VetAnimalTests`; `reports/tests_demo_catalogs.py` (the vet demo: a dog with two visits and a cat).

## HG-043 — implementation note: the reception queue (MED-007)

- **Status:** The last part of the HG-043 proposal, built as design A. With MED-003 to MED-007, every part of the proposal is built.
- **Schema:** migration `medical/0006_reception_queue` adds `QueueTicket` and touches no existing table.
  - Fields: day, number (unique per day), patient, optional appointment (one to one), optional doctor, status, and the arrived/called/done times.
- **Rules:**
  - Checking in gives today's next number; a race for the same number retries on the unique constraint.
  - A patient can't hold two open turns in a day.
  - Arriving for today's appointment marks it "arrived" through `appointments.services.set_status`, unchanged.
  - A doctor calls the longest-waiting patient who is theirs or unassigned (an unassigned one becomes theirs); reception can call anyone.
  - Status moves: waiting → called → done, back to waiting if called by mistake, or left; each move is audited.
  - **The waiting-room screen shows numbers (and the doctor's name) only, never a patient's name.** It refreshes every 15 seconds.
- **Who:** reception works the queue with `sales.create_sales_invoice`, the booking permission appointments use, so a cashier at the desk can use it without opening patient files; doctors (linked employees) call their own patients.
- **Where:** medical / clinic and medical / medical_center, with a «Waiting list» entry in the menu.
- **Money:** none. The visit is billed from its appointment or on a sales invoice, as before.
- **Tests:** `medical/tests.py` → `ReceptionQueueTests`; `reports/tests_demo_catalogs.py` (the clinic demo has one patient called in and one waiting).
## HG-044 — implementation note: ingredient deduction, design C1 (RESTO-004)

- **Status:** BUILT with design C1, the default recorded in the decisions table. Design C2 (sales posting moves ingredients) was not built.
- **What was built:** `restaurant/kitchen.py`. A dish is switched to "made in the kitchen" from its page; this is opt-in, dish by dish.
  - It becomes a stock item with a manufacturing recipe (`DISH-<pk>`, one plate a batch). The recipe's lines are the dish's ingredients, re-read before every production.
  - Producing a dish goes through the unchanged `manufacturing.services.produce`. The ingredients go out of stock at their cost and the dish comes in at that cost.
  - Selling the dish then costs it like any stock item, from its movement cost (HG-003).
- **Produced before payment:**
  - **Ahead of time:** from the dish's page ("prepare a quantity"), for the day's batch.
  - **At payment:** `restaurant.services.pay` calls `kitchen.prepare_shortfall` before the cashier's checkout, inside the same transaction. Plates that are missing are made first. If an ingredient is short, the payment is refused and names the ingredient, and nothing is posted or moved.
- **A dish already sold as a service is not switched in place (new finding):**
  - The sales return and invoice cancellation code decides whether to move stock by the item's *current* `is_stock_tracked`, not by its state when the invoice was posted.
  - So a returned old invoice of a dish switched in place would bring in stock that never went out.
  - Instead, such a dish gets a new stock item with the same name, price, category, tax rate, e-invoice code, barcode and ingredients. It takes the old item's place on the menu; the old item is kept inactive for its invoices. A test pins that returning an old invoice moves no stock.
  - Reports per dish show the two items apart from the switch date.
- **Open question for Ahmed (not built): preparing at payment by a cashier.**
  - Making plates moves stock, and the stock engine requires `inventory.adjust_stock`, which a cashier does not hold. The permission core was left unchanged.
  - So a cashier sells what the kitchen prepared ahead, and a dish not ready stops the bill with a clear message. Someone who holds the permission (owner, manager) has the missing plates made on the spot.
  - **Proposed:** a new permission (for example `restaurant.prepare_at_payment`) given to the cashier role, which lets `prepare_shortfall` run the production for them. It changes the permission core (protected), so it waits for Ahmed's approval.
- **Not touched:** sales posting, returns, cancellations, the manufacturing engine, inventory movement and accounting, average cost, the permission core.
- **Tests:** `restaurant/tests_kitchen.py` (12 tests). They cover:
  - switching a dish in place;
  - the stock copy for a dish already sold, whose old invoice returns without moving stock;
  - paying makes and costs the plates, with the ledger reconciled;
  - plates prepared ahead are sold first;
  - a short ingredient stops the bill;
  - a cashier sells only what is ready;
  - changed ingredients are used from the next plate;
  - a service dish moves nothing;
  - permissions;
  - the screens.

## HG-044 — implementation note: dish cost, read only (RESTO-002)

- **Status:** The read-only part of the HG-044 proposal (recorded in PR #184), built. **Taking ingredients out of stock when a dish is sold is not built; it still waits for Ahmed's decision.**
- **Schema:** migration `restaurant/0002_dish_ingredients` adds `DishIngredient` and touches no existing table.
  - Fields: dish (a menu item), ingredient (a stocked item), quantity per plate; one row per dish and ingredient.
  - Manufacturing recipes can't hold this, because they need a stocked product and dishes are made to order (services).
- **Dish cost:**
  - the sum of the ingredients at each ingredient's current `average_cost` (the display cache, HG-003);
  - an estimate for pricing, shown with the margin against the menu price;
  - it never feeds posting, cost of goods sold or any report calculation.
- **Waste:** no new table. The report lists stock taken out by an ordinary stock adjustment in the period, with its reason, at movement cost; a cancelled adjustment is left out.
- **The restaurant report** reads paid orders whose invoice is posted, in the current entity:
  - by hour, best sellers, by order type, tables (orders a day, average order, average stay), waiters;
  - order values are net sales (`appointments.services.net_sales`: before tax, after posted returns);
  - dish values are at menu price before the order's discount.
- **Permissions:** the report needs `reports.view_all_sales_report`. Costs and waste values need `inventory.view_cost`. Editing ingredients needs `master_data.manage_items`.
- **Not touched:** inventory movement and accounting, sales posting, average-cost logic, report calculations.
- **Tests:** `restaurant/tests.py` → `RestaurantInsightTests` (including that selling a dish moves no stock); `reports/tests_demo_catalogs.py` (the restaurant demo).

## HG-045 — restaurant delivery: driver, dispatch time and fee (RESTO-003)

- **Status:** Built. Additive schema on an existing table, recorded here because it touches `restaurant.Order`.
- **Schema:** migration `restaurant/0003_delivery` adds three nullable or defaulted fields to `Order`:
  - `driver` (staff employee, SET_NULL);
  - `dispatched_at`;
  - `delivery_fee` (default 0).

  Existing rows read as "no driver, no fee", and nothing else changes.
- **Money:**
  - the fee is billed as **its own line on the order's ordinary invoice**, through the same `sales.pos.checkout` the order already uses;
  - the line uses a non-stocked service item `DELIVERY-FEE` («رسوم توصيل»), made once on first use;
  - so the fee is sales revenue like any service and moves no stock; posting, the cashbox and the customer ledger are unchanged.
- **Rules:**
  - Only delivery orders carry a fee; a dine-in order ignores it.
  - Dispatching sets the driver and the time it left, and can correct the fee while the order is open; it is audited before/after.
  - The restaurant report gains deliveries per driver: count, fees, average minutes from leaving to closing.
- **Not touched:** sales posting, cashbox and customer ledgers, inventory, report calculations outside the restaurant report.
- **Tests:** `restaurant/tests.py` → `DeliveryTests` (one invoice with the fee line, the cashbox moves by the order plus fee, the fee item made once, no fee line without a fee, the rules, the driver report); `reports/tests_demo_catalogs.py`.

## قرارات اتاخدت من غير ما نستنى (11 أكتوبر 2026)

أحمد قال: «اعمل أصح حاجة تعرف تعملها من غير ما تستنى قراري». كل قرار هنا اتاخد بالاختيار الأأمن، وتقدر تغيّره في أي وقت.

| القرار | اللي اتعمل | ليه |
|---|---|---|
| Codex خلص رصيده | مراجعة كاملة لـ #191–#202 بدله، واتصلّح فيها كل ملاحظة حقيقية | المراجعة لقت مشاكل فعلية في الفلوس والطبي والصلاحيات |
| التأمين الطبي (HG-042) | التصميم A: فاتورتين عاديتين لكل كشف (واحدة على المريض وواحدة على شركة التأمين) | مش بيلمس دفتر العملاء ولا الترحيل. اتبنى في MED-008 |
| خصم الخامات في المطاعم (HG-044) | C1: إنتاج الطبق عن طريق محرك التصنيع، اختياري لكل طبق | مش بيغيّر ترحيل المبيعات. اتبنى في RESTO-004؛ لسه قرار: صلاحية للكاشير يحضّر وقت الحساب |
| السحابة | الخيار B: كل عميل بياناته على حساب Supabase باسمه هو | ده اللي كنا ناصحين بيه، وتكلفته عليك أقل |
| تصميم البيطري (#196) | جدول حيوانات مستقل، وصاحب الحيوان عميل عادي | العميل الواحد ممكن يجيب أكتر من حيوان |
| باكدجات البيوتي والعلاج الطبيعي | كل واحد فضل في نشاطه، ونوحّدهم بعد الدمج | اتوحّدوا في PKG-UNIFY: محرك واحد وجدول واحد، والعلاج الطبيعي محتفظ بكلامه وصلاحياته |
| ربط عقود الخدمة بالكيان | كل عقد بيتسجّل فيه مخزن البيع (يعني الكيان) اللي اتعمل فيه، وبيتفوتر ويظهر فيه بس | زي الفواتير بالظبط (HG-034) |

## PKG-UNIFY — physiotherapy packages on the shared package engine (schema)

- **Status:** BUILT. This carries out the default decision "packages unify after merge" from the decisions table below.
- **Why it is a gate:** it is a schema change and it moves data. Physio's `medical.SessionPackage` and `medical.PackageSession` are replaced by the shared `agreements.Package` and `agreements.PackageUse`, which salons and service businesses already use.
- **Migrations:**
  - `agreements/0003_move_physio_packages` copies the data:
    - each physio package becomes a shared package with a new `PK-` number, numbered after the existing ones, keeping the same patient, service, sessions, price, invoice, notes, author and date;
    - each session becomes a package use, with the therapist as the employee and the same day, notes, cancelled flag, author and date;
    - the link a session could keep to a clinical visit has no column in the shared table, so it is written into the session's notes. The screen never set it.
  - `medical/0008_remove_session_packages` then drops the two old tables; it depends on the copy.
  - The reverse is a no-op for the data.
- **What does not change:**
  - the invoices, the customer's account, the sessions used and their rules (draft invoice, cancelled or returned, never below zero, no future day);
  - physio's own wording and who may do what: the counter sells; the therapist (a user linked to an employee) or anyone with the medical records permission records sessions.
  - `agreements.packages.use_session` / `cancel_use` take an `authorized` flag so the clinic can apply that rule; the services screens keep their rule.
- **Gained:** physio packages now have a number and an optional expiry date, and show on the shared packages list.
- **Tests:**
  - `agreements/tests_pkg_unify.py` runs the migration on real old rows and checks every moved field, the numbering and the state;
  - `medical/tests.py` (physio tests, now on the shared table);
  - `reports/tests_demo_catalogs.py` (the physio demo).

## HG-048 — New roles and narrow permissions (RESOLVED)

- **Status:** RESOLVED. Ahmed's word on 12 Oct 2026: "اعمل اصح حاجة تتعمل" (do the most correct thing). Built on `claude/hg-048-roles`.
- **Why:** the scorecard (`docs/ACTIVITY_SCORECARD.md`, criterion 9 "Roles") was at 7 for restaurants and contracting. A waiter had to be given the cashier role, which also takes money. A site engineer either saw everything (manager) or nothing.
- **What was built:**
  - **Roles:** two new `RoleCode` values, `waiter` and `site_engineer` (migration `permissions/0010_hg048_roles`, which also seeds the roles and their grants).
  - **Permissions:** five new permissions:
    - `restaurant.take_orders`: owner, manager, cashier, waiter;
    - `restaurant.prepare_at_payment`: owner, manager, cashier;
    - `projects.view_projects`, `projects.record_progress`, `projects.issue_to_site`: owner, manager, site_engineer.
    The site engineer also holds `inventory.view_stock` and, by the HG-031 rule (whoever sees stock sees the group's quantities), `inventory.view_group_stock`. Every existing grant of every existing role is unchanged.
  - **Waiter:**
    - may: the floor (open a table or an order, add and void lines, send to the kitchen, move a table, reprint a ticket);
    - may not: pay, dispatch or cancel an order (each refused with a 403 even when posted by hand), see the invoice number or the sales screens, pick a cashbox, see costs or reports;
    - lands on the floor after signing in.
  - **Site engineer:**
    - may: read the projects, the bill of quantities and the certificates; record the quantities done on a certificate (it stays a draft invoice); issue materials to the site;
    - may not: post or withdraw a certificate, change the contract or the bill of quantities, bill, collect, or see the payments, subcontractors, budget or profit. Collections, the contract position and the invoice list are hidden on the project page;
    - lands on the projects after signing in.
  - **Cashier:** with `restaurant.prepare_at_payment` the missing plates are made from their ingredients at payment (RESTO-004). An owner can take the permission back, and the cashier is then told the dish is not ready, as before.
- **The protected change (inventory engine):**
  - `inventory.services.adjust_stock` gained one optional argument, `stand_in`. It names a narrower permission that may stand in for `inventory.adjust_stock`.
  - Only the two permissions in `ADJUSTMENT_STAND_INS` are accepted (`restaurant.prepare_at_payment`, `projects.issue_to_site`). Any other name is refused (403).
  - Only two flows pass it: `restaurant.kitchen.prepare_shortfall` (through `manufacturing.services.produce`) and `projects.services.issue_materials`.
  - When it is passed, the engine skips its `inventory.view_cost` check on `unit_cost`. That cost is the production run's own, worked out by the engine, never typed by a person.
  - Default behaviour is unchanged: a call without `stand_in` checks exactly what it checked before. Movement, quantity, cost and period rules are untouched.
- **Also fixed:** the "Items & services" menu entry had no permission, so a role without `master_data.view_master_data` was shown a link that answered 403. The new crawl found it, and the entry now carries that permission.
- **Tests:**
  - `restaurant/tests_roles.py` and `projects/tests_roles.py`: each role's grants, what each may and may not do (403s), where each lands, and that a manager and a cashier are unchanged.
  - `restaurant/tests_kitchen.py`: plates made at payment by a cashier (cost of sales and ledger checked), and the old refusal when the permission is taken away.
  - Stand-in limits: a general adjustment and the day's batch still need `inventory.adjust_stock`, and an unknown stand-in is refused.
  - `reports/tests_crawl_matrix.py`: the waiter walks every restaurant sub-activity and the site engineer every contracting one, empty and with sample data.

## HG-050 — Asset types of the business's own (RESOLVED)

- **Status:** RESOLVED. Built on `claude/fb-own-types`.
- **Why:** Ahmed's demo note on 13 Oct 2026: "محتاج هنا برده امكانيه إضافة نوع جديد وكذلك على أي حاجة شبهه في اي مجال تاني". The asset types were a fixed list of six.
- **Schema change:** `FixedAsset.category` is no longer limited to `AssetCategory` and is longer: 60 characters instead of 20 (migration `fixed_assets/0002_own_asset_types`).
  - The built-in codes (vehicles, equipment ...) are unchanged and still shown in the screen's language.
  - A type the business adds is stored as the name it typed. It is offered again for the next asset.
  - Anything unknown that is posted falls back to "other".
  - Nothing reads the category for depreciation or the ledger; it is a label only.
- **Same idea elsewhere, no schema change:** "+ new category…" on the item form (item categories) and on the expense form (expense categories).
  - The new category is saved on the spot. An existing name is reused, not doubled.
  - The endpoints check the same permissions as the screens that manage those lists (`master_data.manage_items`, recording expenses).
- **How it works:** one small script (`static/hesba/js/quick_option.js`) turns any `<select data-quick-add>` into a list with "+ new…". More lists can opt in with one attribute.
- **Kept fixed on purpose:** lists that carry meaning in the engines stay fixed. These are movement types, ledger account types, appointment kinds (a visit needs an address), payment kinds and order kinds.
- **Tests:**
  - `fixed_assets/tests_own_types.py`: a new type is kept, shown and offered again; junk falls back to "other".
  - `master_data/tests_quick_types.py`: both category endpoints, reuse instead of duplicates, and 403 for a cashier.
## HG-049 — Roles of the owner's own (RESOLVED)

- **Status:** RESOLVED. Built on `claude/fb-roles-screen`.
- **Why:** Ahmed's demo notes on 13 Oct 2026:
  - "إدارة الادوار بتفتح صفحة قديمة": the roles screen's button opened Django Admin;
  - "امكانيه إضافة أدوار": adding roles from the new-user screen.
- **What changed in the permission core:**
  - `Role.code` is no longer limited to the `RoleCode` choices (migration `permissions/0011_role_code_custom`). The column is unchanged on SQLite and Postgres; only the model's validation is.
  - The built-in roles keep their codes. A role the owner makes gets `custom_<n>`, with `is_system_role=False`.
  - `user_has_permission` is unchanged: it still reads the role's own permission rows.
- **Rules (`permissions/role_services.py`):**
  - Only holders of `permissions.manage_roles` (the owner) make or edit roles.
  - A custom role can never contain `permissions.manage_roles`. A role made there cannot make roles itself.
  - Whoever saves a role must hold every permission in it, so no one hands out what they don't have.
  - Built-in roles are never edited (404 on their edit screen; the service refuses). Every screen and test is set up on them; for something in between, the owner copies the closest one.
  - A custom role still held by an active user cannot be switched off.
  - Every create or edit is in the audit log with the permissions before and after.
- **Screens:**
  - `/settings/roles/` lists every role with what it can do. For the owner, every role except support has "copy to a new role"; custom roles also have "edit". There are no Django Admin links any more.
  - `/settings/roles/new/` (optionally `?from=<role>`) and `/settings/roles/<pk>/`: name, description, and a checkbox per permission, grouped by section and named in words.
  - The new-user form has "+ New role" next to the role field.
- **Tests:** `permissions/tests_custom_roles.py`
  - a copied cashier with stock receiving works through `user_has_permission`;
  - every guard: name, empty, locked permission, unknown permission, not the owner, built-in role, beyond the actor, role in use;
  - the audit log entries;
  - the screens: copy, save, refused save keeps the input, 404 and 403.
  - The updated ADMIN-001 test in `settings_core/tests_ui.py` checks that no roles link goes to Django Admin.

## HG-051 — Return posting reads prior returns one line at a time (PROPOSED, not built)

- **Status:** PROPOSED, waiting for Main Control. Raised by Codex on PR #233.
- **What:** `purchases.services._prepare_purchase_return_lines` and `sales.services._prepare_sales_return_lines` run one `aggregate()` per requested line to read what is already returned (quantity and amount of posted returns).
  - Before UI-TRACK F15 the return form allowed at most 20 rows. Now the return screen lists every line of the invoice (up to 500), so a full return of a large invoice costs one query per line.
  - The screen itself no longer does this: it reads all prior returns in one grouped query (`config/return_table.py`), and row validation asks no query per row.
- **Why it is a gate:** both functions sit inside sales and purchase return posting (protected: posting, ledgers). PR #233 does not touch them.
- **Proposed fix:** before the loop, read the prior totals for all requested source lines in one grouped query (`values("source_line").annotate(quantity=Sum, amount=Sum)` over posted returns), then look each line up in that map. Same checks and the same error messages, same rounding, same allocation.
- **Tests to add:** the query count for a 2-line and a 6-line return is the same; the existing return tests (cumulative quantity limit, amount allocation, residual on the last unit) stay green unchanged.

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

## HG-040 — implementation note: the nursery's check-in and pick-up (EDU-005)

- **Status:** BUILT. New tables only, in the education app's own module, through migration `education/0006_nursery_pickup`. No change to an existing table.
- **New tables:**
  - `PickupPerson`: who may pick a child up (name, relation, phone, optional ID number). A person is switched off, never deleted, so past pick-ups still show who it was.
  - `DayRecord`: one per child per day, holding the arrival time and who brought the child, the departure time, who picked the child up, and who checked the child in and out.
- **Rules (`education/nursery.py`):**
  - A child leaves only with an active person on their own list. Any other choice is refused with a clear message.
  - Checking in twice on one day is refused; the row lock plus a unique constraint enforce this.
  - Leaving needs an arrival first.
  - The parent gets a one-tap WhatsApp saying who picked the child up and when.
- **Who may do what:**
  - The office (`master_data.manage_parties`) or any staff member linked to an employee checks children in and out.
  - Only the office changes the list of who may pick a child up.
- **Screens:**
  - A "Check-in & pick-up" tab and page, for nurseries only: today's board with counts for inside, gone home and not here yet.
  - On the child's file: the pick-up list and the recent days.
- **Not touched:** fees and invoices, ledgers, attendance of classes, permissions core.
- **Tests:** `education/tests_nursery.py`; the nursery demo in `reports/tests_demo_catalogs.py`.
## HG-046 — implementation note: contract visits booked automatically (SVC-004)

- **Status:** BUILT, no schema change.
- **What:** `agreements.visits` books a contract's missing visits for the current period with one click: "Book this period's visits" on the contracts list (every active contract) and on a contract's page (with an optional technician).
  - The visits go through the unchanged `appointments.services.book`, as visits to the customer's address at no price, since the contract bills the period. They are spread evenly over the rest of the period, and a Friday moves to the next day.
  - A busy technician gets the next free hour, 10:00 to 17:00.
  - A visit counts when it is booked, confirmed, in progress or done; a cancelled or no-show one frees its place. Booking twice never doubles the visits: the contract row is locked.
  - A customer without an address is skipped with a clear message.
- **Not touched:** billing (`bill_due`), the sales engine, ledgers, reports. Permission: `sales.create_sales_invoice`, the same as billing contracts and booking appointments.
- **Tests:** `agreements/tests_visits.py`.
## HG-042 — implementation note: insurance on dental steps and physio packages (MED-009)

- **Status:** BUILT on design A, as for visits. A patient's done dental steps, or a physio package, can now be billed through the patient's insurance: the insurer's invoice and the patient's share, two ordinary draft invoices through the unchanged sales engine.
- **Schema change, migration `medical/0009_insured_bill_kinds`** (only the medical app's own `InsuredBill`):
  - `appointment` becomes optional, since dental steps and packages have no appointment;
  - new `kind` (visit / dental / package), `patient` (the patient billed) and `description` (what was billed, shown on the claim);
  - the migration fills `patient` from the appointment for bills made before; it skips a database without the table (demo restart replay).
- **Billing:** `medical.insurance.insured_drafts(patient, parts, what, kind, user)`.
  - Each line is split by its own service's share (`percent_for`, `split`), so a service the contract does not cover goes on the patient's invoice in full. If nothing is covered, nothing is made.
  - Numbers come from the ordinary SI- series (`agreements.services.draft_invoice`). The check for an expired or missing cover is shared with visits (`live_cover`).
  - **Dental:** `dental.bill_done_insured`, with the same row locks as `bill_done`. The steps point at the patient's invoice, or at the insurer's when it pays it all.
  - **Packages:** `physio.sell_insured`, through a new optional `make_invoice` hook on `agreements.packages.sell_package`; without the hook the engine is unchanged. The package rides on the patient's invoice (the insurer's at 100%), so it opens once that is posted, like any package.
- **Double-claim guard:** `dental.unbilled` no longer counts a step as unbilled when its patient's invoice was cancelled but the insurer's still stands. That bill shows on the insurance page's list of bills with only one invoice cancelled; once both are cancelled, the steps can be billed again.
- **Known limit:** an insurer rejecting a package (a sales return on the insurer's invoice) does not close the package, because only the package's own invoice decides that. The clinic bills the patient for the rejected part, as for a visit.
- **Not touched:** sales posting, the customer ledger, aging, returns, report calculations, permissions. Billing needs `sales.create_sales_invoice`, as before.
- **Tests:** `medical/tests_insurance_more.py`.

## HG-040 — implementation note: language levels and the placement test (EDU-006)

- **Status:** BUILT through migration `education/0007_language_levels`:
  - one new optional column, `StudyGroup.level` (1 to 20, blank when a course has no levels);
  - one new table, `PlacementTest` (student, course, day, mark out of, the level given, notes).
- **Placement:** `education.levels.record_placement` records the test. The student's file then lists the course's active groups at that level that still have a free seat.
- **Moving up:** `levels.move_up(group, students, target, user, on_date)`, all or nothing.
  - The target must be the same course at a higher level and have the seats.
  - Each student's enrolment in the old group stops the day before (`services.stop`), and a new one starts in the target (`services.enroll`) with the same discount.
  - For a monthly course the move must start on the first of a month, so no month is billed twice. The billing test pins this down: next month bills only the new group.
- **Screens, for language centres and training only:**
  - a "Level" field on the group form;
  - "Move students up a level" on a group page (tick who passed, choose the next group);
  - the placement test and the suggested groups on a student's file.
- **Not touched:** fee billing (`billing.due_lines` unchanged), invoices, ledgers, attendance, permissions core. Permission: `master_data.manage_parties`, as for enrolment.
- **Tests:** `education/tests_levels.py`; the language-centre demo in `reports/tests_demo_catalogs.py`.

## HG-052 — a sold-out item shows an average cost of 0.00 (TRACK-ACTS W07) (RESOLVED, display only)

- **Status:** RESOLVED, display only (Ahmed, 19 Oct: "كمل" with merge rights, so the proposed fix was applied). `inventory.views.last_costs` reads each item's latest purchase or opening-stock movement with a unit cost. When nothing is on hand, the stock list and the item card show that cost, labelled «آخر تكلفة». `Item.average_cost`, the averaging code and sales costing are unchanged. Tests: `inventory/tests_ui.py` `LastCostTests`.
- **Seen:** the pharmacy demo's stock screen. "كمامات طبية (50)" has quantity 0 and an average cost of 0.00, even though it was bought before and sold out.
- **Why it is protected:** the figure is `Item.average_cost`, the display cache refreshed by the average-cost logic (HG-003). Posted sales cost still comes from the stock movements, so no money figure is wrong. Only the display misleads: an owner reading 0.00 may think the item cost nothing.
- **Proposed fix:** when nothing is on hand, show the last known unit cost (the latest inbound movement's `unit_cost`), labelled «آخر تكلفة». Leave the cached field and the cost logic as they are.
- **Files if approved:** the stock list and the item card templates, plus a small selector for the last inbound cost.
- **Tests if approved:**
  - buy, sell everything, then check that the card shows the purchase cost labelled as the last one;
  - check that sales cost is unchanged.

## HG-053 — Options that ride on a module (settings track)

- **Status:** IMPLEMENTED NOTE (settings gating only; no model, posting, costing or permission change).
- **What:** expiry dates and serial numbers need the inventory module; instalments and e-invoicing need the customers module (`settings_core.capabilities.NEEDS`). A capability is at work only while its own switch **and** those modules are on (`enabled_capabilities`). Switching it on switches the module on. Switching the module off pauses it and keeps its switch, so it comes back with the module.
- Modules too (`setup_services.MODULE_NEEDS`):
  - purchases need suppliers;
  - manufacturing needs inventory;
  - appointments need customers.
  Switching one on brings what it needs. What a switched-on module needs cannot be switched off. Setup applies the same rule to its choice.
- **Effect to know about:** while inventory is off, sales and purchases of serial-tracked items no longer ask for serial numbers, and purchases record no expiry dates. That is the same as the capability being off, which is what the screens already show. The stock movements themselves are unchanged.
- **Tests:** `settings_core/tests_settings_track.py`.


## Market parity: commercial gate (MARKET-PARITY, `docs/MARKET_PARITY_ROADMAP.md`)

Ahmed approved the market-parity roadmap on 21 October 2026 («ابدأ»).

The entries below are the protected-logic decisions in the commercial gate, recorded before any code. Each one waits for Ahmed's approval. HG-054 is the exception: it only adds new tables, the same way the education, work orders and medical specialities did (HG-040, HG-041, HG-043).

## HG-054 — Quotations and sales orders (T1) and purchase orders (T2)

- **Status:** IMPLEMENTED NOTE when T1 and T2 merge. Additive tables only.
- **What:**
  - A new `orders` app with quotation/order documents and their lines.
  - A link table that records which ordinary sales (or purchase) invoice took which order quantities.
- **Not changed:**
  - Sales and purchase posting.
  - Stock movement and the customer and supplier ledgers.
  - The `sales` and `purchases` models (no new column on the invoice).
- **How:**
  - "Make invoice" builds an ordinary draft with `sales.services.create_sales_draft` (purchases: the purchase draft service). That draft is posted the usual way.
  - Stock leaves only when the invoice is posted, exactly as today.
  - The delivery note is a printed document from the order. It does not move stock.
  - Quantities on linked **drafts count as taken** as well as posted ones, so "what is left" never offers them twice.
  - Only cancelled invoices give their quantities back.
  - `link_invoice` locks the order row (`select_for_update`) and recomputes what is left inside the transaction that saves the draft. Two drafts made at the same moment, or the same "invoice what is left" pressed twice, therefore cannot both take the same quantity: the second links only what the first left.
  - Anything sold beyond that is sold on the invoice as typed, but not linked to the order.
- **Risk:**
  - Nothing blocks posting an invoice for more than the order: sales posting stays order-unaware by design. The order only ever counts up to its own quantities.
  - The "invoiced" figure is computed on read, never stored, so a cancelled invoice cannot leave it stale.
- **Later, only if asked:** stock leaving at delivery before the invoice would need a new movement type. That would be its own Hard Gate.
- **Tests:**
  - quote → order → partial invoice → remaining;
  - cancelling the invoice frees the quantity;
  - permissions;
  - entity scope;
  - posting totals equal the order.

## HG-062 — The pharmacy pack: drug data and prescriptions (T10)

- **Status:** IMPLEMENTED NOTE when T10 merges. Additive tables only, like HG-054.
- **What:**
  - A new `pharmacy` app.
  - `DrugInfo` sits beside an item, one to one: active ingredient, strength, form, maker, and "prescription only".
  - `Prescription` and its lines record the paper as the patient brings it.
  - `PrescriptionInvoice` records which ordinary sales invoice dispensed it.
- **Not changed:**
  - The `Item` model (no new column), sales posting, stock movement, batches and expiry, and the customer ledger.
- **How:**
  - Alternatives are read, never stored: the other active items with the same normalised active ingredient.
  - "Dispense" opens the ordinary sales invoice form with `?prescription=<pk>`. The draft is saved by `create_sales_draft_with_tax` and posted the usual way, so stock, the batch that expires first, the cashbox and the ledger behave as on any sale.
  - `link_dispense` locks the prescription row inside the transaction that saves the draft. A one-off prescription that another invoice already dispensed is refused, so the same paper is never dispensed twice.
  - A linked draft counts as dispensed; only a cancelled invoice frees the prescription.
  - A chronic prescription (`refill_days`) may be dispensed again and shows as due a few days before its next date.
  - The items import takes the drug columns (active ingredient, strength, form, maker, prescription only) while the capability is on, and writes them through `pharmacy.services.from_import` after the item row is saved.
- **Risk:**
  - Nothing stops selling a "prescription only" drug without a prescription: it is shown, not enforced. Enforcing it would touch the sales screens and is a choice for Ahmed.
  - Ingredient matching is by spelling. "Paracetamol" and "باراسيتامول" are two ingredients until written the same way. The form suggests the spellings already used.
- **Tests:** `pharmacy/tests.py` covers:
  - matching keys and alternatives (same strength and form first, then in stock);
  - prescription → invoice → posted, with stock leaving once;
  - a second dispense refused;
  - a cancelled invoice frees the prescription;
  - another customer's invoice refused;
  - a chronic refill coming due;
  - the import columns;
  - permissions, the capability gate, and the pages in Arabic and English.

## HG-063 — Staff attendance and leave (T11)

- **Status:** IMPLEMENTED NOTE when T11 merges. Additive tables only, like HG-054 and HG-062.
- **What:**
  - A new `attendance` app, mounted under `/staff/attendance/`, so the employees module opens and closes it.
  - `WorkSchedule` sits beside an employee, one to one: working hours, days off, the grace before a late arrival, the leave days a year, and the fingerprint-device number.
  - `AttendanceDay` holds one employee's day: the time in and the time out, or absent.
  - `LeaveRequest` holds leave asked for, then approved, rejected or cancelled.
  - `Holiday` holds the public holidays.
- **Not changed:** the `Employee` model (no new column), the permission core, pay, expenses and every posting.
- **How:**
  - **Permissions:** the screens use existing permissions only. The team's pages are for `sees_staff` (owner, manager, accountant). Recording and approving needs `master_data.manage_parties`, the permission that already edits employees. An employee whose login is linked to them gets "my attendance", with one check-in/check-out button and their own leave requests.
  - **What a day counts as:**
    - a time in after the grace → late;
    - a time out past the end of the day → overtime;
    - leaving before the end of the day → early;
    - a day off or a holiday → not counted;
    - approved leave → leave;
    - a past working day with nothing recorded → absent (but not before the employee was added).
  - **Leave balance:** annual and casual leave come off the yearly balance; sick and unpaid leave do not. Overlapping requests are refused, and so is an approval beyond the balance.
  - **The fingerprint file (CSV):** the first punch of a day is the time in and the last is the time out. Loading the same file twice changes nothing. Numbers that match no employee are listed back.
  - The monthly sheet only counts days and minutes. Turning them into pay is payroll (HG-060), which still waits for Ahmed.
- **Risk:**
  - A day with nothing recorded reads as absent. A shop that does not record attendance every day will see absences; the sheet says so.
  - A day's times are wall-clock times with no night shift across midnight. A shift that ends after midnight would need its own design.
- **Tests:** `attendance/tests.py` covers:
  - late, early, overtime, days off, holidays, leave and absence;
  - the manager's corrections;
  - self check-in;
  - the menu per role;
  - leave days, balance, overlaps and decisions;
  - an employee can only ask for their own leave;
  - the device file (English and Arabic headers, run twice, unknown numbers, unreadable lines);
  - the screens in Arabic and English;
  - the module switch.

## HG-064 — Customer messages outbox (T9, offline part)

- **Status:** IMPLEMENTED NOTE when T9 merges. Additive tables only.
- **What:**
  - A new `messaging` app under `/messages/`. It follows the customers module.
  - `MessageTemplate` holds the wording of each kind of message, whether it is sent, and how often.
  - `OutboxMessage` holds one message waiting today, then sent or skipped.
  - A `gather_messages` command queues the day's messages, the way the daily digest runs.
- **Not changed:** balances, invoices, instalments, appointments and prescriptions. They are only read.
- **How:**
  - Each day the outbox gathers:
    - customers past their credit days, with the overdue amount, from the aging report;
    - instalments due in the next two days or late;
    - tomorrow's booked or confirmed appointments;
    - chronic prescriptions due for a refill.
  - `key` + day is unique, so nothing is queued twice. A customer reminded of a kind is not reminded again within that kind's `every_days`.
  - A message left waiting moves to the next day with that day's figures; the `every_days` wait starts only once it is sent.
  - Each entity is gathered on its own and every message keeps its `entity`; the outbox, the counts and send/skip show only the entity being worked in (HG-034). Appointments have no entity, so their messages belong to the main entity. The main entity is gathered as itself from the start, so adding a second entity changes no existing key.
  - A message still waiting is rewritten with each day's figures. One whose source no longer qualifies (paid, cancelled, moved) is withdrawn; send and skip gather first, so a stale page cannot send it.
  - "Send" marks the message sent and opens WhatsApp with the text written. "Skip" moves it aside.
  - Each message moves once. A second click is refused.
- **Permissions:** existing ones only. The outbox is for the customer-facing roles (`sees_customers`); the wording is edited with `settings.manage_settings`.
- **Live part still outside:** sending without a click needs the shop's own WhatsApp Business (Cloud API) account. `api_connected()` returns False, and nothing calls an outside service.
- **Risk:** "sent" is recorded when the person opens WhatsApp, not when the message is delivered. The live API part would record delivery.
- **Tests:** `messaging/tests.py` covers:
  - one message per overdue customer, with the amount;
  - nothing twice the same day, and the `every_days` wait;
  - a kind switched off;
  - unknown words refused, and only the owner edits the wording;
  - appointments and refills gathered;
  - the daily command;
  - "send" opens wa.me with the text and refuses a second send;
  - skip, templates in both languages, the menu item;
  - a stock keeper is refused.

## HG-055 — Bank accounts (T3)

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق», together with HG-056 to HG-061 as written below). BUILT in T3 (`claude/t3-banks`).
- **What was built:**
  - `Cashbox.kind` (cash / bank, default cash) with `bank_name`, `account_number` and `iban`. The kind is locked once the cashbox has movements.
  - A `bank_fee` operation and movement type: only from a bank account, never below zero, reversed like any operation.
  - The projector puts a bank account's movements and opening balance in 1102 Banks, and a fee in 5212 Bank charges (control `bank_charges`; a ledger data migration gives the existing 5212 its key). The reconciliation's cash check compares cash + banks with every cashbox.
  - A new `banking` app: upload the bank's statement (CSV or Excel, Arabic or English headers, an amount column or debit and credit columns). A line matches by itself when the amount is the same and the date is within 3 days; it can also be matched or unmatched by hand. A fee line not in the books is recorded as a bank fee and matched in one step. The page shows the books balance, what the bank has not shown yet, what the books do not have yet, what the statement should say, and the difference. Matching never changes an amount or a balance.
  - Viewing needs `cashboxes.view_finance`; uploading and matching also need `cashboxes.move_cash`. Bank accounts follow the entity scope (HG-034).
- **Why:** a cashbox today has no kind, so a bank account is just a cashbox with a bank's name. Bank fees, a statement match and the trial balance need to tell them apart.
- **Proposed:**
  - add `Cashbox.kind` (cash / bank) with bank details (bank, account number, IBAN);
  - add a bank-fee movement type;
  - a "match the statement" screen that marks movements as cleared against an imported CSV (no change to the movement amounts);
  - the GL projector maps bank cashboxes to the bank account in the chart.
- **Files:** `cashboxes/models.py` + migration, `cashboxes/services.py`, `ledger/projector.py`, `ledger/chart.py`.
- **Risk:**
  - existing cashboxes default to cash, so nothing moves;
  - the projector mapping must keep the trial balance equal before and after (test).
- **Tests:** a migration keeps balances; transfer cash ↔ bank; the fee movement; matching does not change balances; the trial balance is unchanged for old data.

## HG-056 — Cheques: notes receivable and payable (T4)

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below. BUILT in T4 (`claude/t4-cheques`).
- **What was built:**
  - Two cashbox kinds:
    - `cheques_in` (cheques in hand) posts to 1104 Notes receivable;
    - `cheques_out` (cheques issued and not yet paid) posts to 2102 Notes payable.
  - Data migration `ledger/0004` gives the default 1104 and 2102 their control keys.
  - Each entity gets one box of each kind, made on first use. `entities.scope.cashboxes()` leaves the cheque boxes out, so no invoice, payment or report picks them and cash on hand never counts a cheque. The ledger reconciliation's cash check counts cash, banks and both cheque accounts against every cashbox.
  - A new `cheques` app (`/cheques/`) with received and issued cheques and what falls due within 7 days. The steps:
    - **Receive:** an ordinary `record_customer_payment` into the box.
    - **Deposit:** a transfer from the box into a bank.
    - **Clear:** a status change only for a received cheque. For an issued cheque it is a transfer from the bank into the issued box.
    - **Bounce:** dated the bounce day, in an open period. For a deposited cheque, a transfer back from the bank comes first. Then a `cheque_bounced` movement out of the box and a customer ledger adjustment that puts the amount back on the customer, both linked to the original payment so the journal pairs them. An issued cheque mirrors this on the supplier.
    - **Cancel:** only while nothing has happened to the cheque yet; it goes through `cancel_customer_payment` / `cancel_supplier_payment`.
  - `cancel_customer_payment` and `cancel_supplier_payment` now refuse a cheque's payment unless the cheques screen is cancelling it. This is a three-line guard; nothing else in them changed.
  - Permissions: receiving needs `sales.receive_customer_payment`, issuing needs `purchases.pay_supplier`, and deposit, clear and bounce need `cashboxes.move_cash`.
- **Why:** wholesale and contracting in Egypt run on post-dated cheques. Today a cheque can only be recorded as cash on the day it is received.
- **Proposed:**
  - a `Cheque` (in/out) with number, bank, due date and status (in hand → deposited → cleared, or bounced; issued → cleared);
  - receiving a cheque reduces the customer's balance through an ordinary customer payment into a new "cheques in hand" cashbox of kind `cheques`;
  - clearing moves it from that box into the bank by a transfer;
  - a cheque that bounces **before** it is deposited: a dated "bounced" reversal on the bounce date, which takes it out of the cheques box and puts the amount back on the customer's account;
  - a cheque that bounces **after** it was deposited: the same, plus a dated reversal of the deposit transfer (bank → cheques box) on the bounce date first, so the bank is not overstated and the cheques box never goes negative;
  - both reversals are new dated movements in the open period. The original payment stays as it was, and `cancel_customer_payment` (which reverses on the payment's own date) is not used for bounces;
  - outgoing cheques are the mirror on the supplier side.
- **Files:**
  - a new `cheques` app;
  - `cashboxes` (kind `cheques`, from HG-055);
  - `sales.services.record_customer_payment` and `cancel_customer_payment` (called, not changed);
  - the purchases equivalents.
- **Risk:**
  - a cheque in hand counts as money in the cashbox list. It is shown separately and kept out of "cash on hand" figures.
- **Tests:**
  - receive → deposit → clear;
  - receive → bounce before deposit: the customer balance returns and an audit row is written;
  - receive → deposit → bounce: the bank and the cheques box end where they started, and the customer owes again;
  - a bounce after the payment's period has closed is dated in the open period and is accepted;
  - due-date alert;
  - closed-period rules;
  - an outgoing cheque.

## HG-057 — Promotions and coupons (T5)

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below. BUILT in T5 (`claude/t5-promotions`).
- **What was built:**
  - A `promotions` app and the capability «العروض والكوبونات». It is optional, and is not offered to contracting, education or medical.
  - Three kinds:
    - % off an item, a section or everything;
    - buy X get Y free;
    - an amount off once the eligible lines reach a total, spread over them.
  - Each promotion can have dates, a time window (one that runs past midnight works), and a coupon code. A coupon counts only when typed; an unknown code is refused.
  - `promotions.apply` fills the line discount fields before `create_sales_draft`, on the sales invoice form and at the till. The best single promotion wins. A line with a discount typed by hand keeps it. Posting is unchanged.
  - `InvoicePromotion` records the promotion, discount and coupon of each invoice for the report on the promotions screen, and the invoice page shows it.
  - The till asks the server for the cart's promotion before payment, and a payment for another total is refused. An offline sale syncs with the promotion that was running at its recorded time, so it posts at the total shown and collected (Codex #268). The report follows the entity scope.
  - Managing promotions needs `master_data.manage_items`, the same as price lists. The manual discount stays open to invoice creators, as proposed.
- **Why:** an automatic promotion changes the invoice price. Sales posting must keep using the line amounts it is given.
- **Proposed:**
  - a promotion engine that only fills each line's existing discount field before the draft is saved (rules: buy X get Y, % off a section, fixed off over an amount, coupon code, date and time window);
  - posting stays untouched;
  - the invoice remembers which promotion gave each discount (a new link table) for the report.
- **Files:** a new `promotions` app; the hooks are the POS and the invoice form, which call it before `create_sales_draft`.
- **Risk:**
  - promotion + price list + manual discount stacking: the rule is that the best single promotion wins;
  - manual discounts: today anyone with `sales.create_sales_invoice` may type one (there is no discount permission). The proposal keeps that unchanged in T5. A separate `sales.apply_discount` permission would be a change to the permission core, with its own grants, migration and tests. It is offered to Ahmed as an option, not part of this gate;
  - returns refund the discounted amount, as today.
- **Tests:** each rule; stacking; returns; the offline POS keeps the price shown when the sale was made.

## HG-058 — Loyalty points (T6)

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below.
- **Why:** redeeming points is a discount that is paid for by the business. It touches the customer card and the sales totals.
- **Proposed:**
  - points are earned on posted invoices (a rate per pound) into a points ledger per customer;
  - redemption fills the invoice's discount like HG-057, and writes a points "spend" row;
  - cancelling or returning the invoice reverses its rows (append-only);
  - no money movement, no change to posting.
- **Files:** a new `loyalty` app; it listens to posting and cancellation through the existing audit hooks (no change to the posting function itself).
- **Risk:** points earned on an invoice later returned. The return writes the reversal rows.
- **Tests:** earn; spend; return; cancel; expiry; the customer card shows the balance.

## HG-059 — E-invoice live and the credit note for returns (T7)

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below. The part that needs no outside account is BUILT in T7 (`claude/t7-credit-note`). Going live still needs Ahmed's preprod credentials and the signing method (the `ETA_*` settings).
- **What was built:**
  - `build_credit_note(sales_return)`: an ETA credit note (`documentType` "c") with the issuer and receiver of the original invoice and `references` set to the original's accepted UUID. Each returned line gives back its refund:
    - the price × quantity returned;
    - less the line's own discount share: that is the VAT base (`netTotal`);
    - plus the tax given back (HG-015).
    The lines' share of the invoice discount is taken after tax, as on the invoice, in `extraDiscountAmount` (Codex #270). The total must equal the return's.
  - `Submission.sales_return` (nullable, migration 0003) marks a credit note's sending. The invoice's own sending, history and the dashboard count ignore those rows.
  - `send_credit_note` claims a `sending` row under the return's lock and goes through the same sign → submit → record path as an invoice. `send_invoice`'s tail became `_deliver`, shared by both, so their behaviour is unchanged. Refresh, reconciliation (by the return number) and the public link work as for an invoice; a credit note is not cancelled from Hesba.
  - The queue: `credit_notes_due()` lists the posted returns of accepted invoices with no credit note yet, or with a sending cut off long enough ago to be checked again. `manage.py send_credit_notes` sends them, run from cron every few minutes; one that fails stays queued, and a rejected one waits for a manual send after its data is corrected.
  - The return page links to its credit-note page (document, JSON, send, refresh) when e-invoicing is on.
  - Posting a return is unchanged and never waits for the authority.
  - **Protected change (Codex #270):** `sales.services.cancel_sales_return` now makes one call, `einvoice.guards.before_sales_return_cancel`, under the return's row lock. The call refuses to reverse a return whose credit note the authority holds (being sent, under review or accepted), as `before_sales_invoice_cancel` already does for invoices. A return never sent reverses as before. The invoice guard now looks at the invoice's own sendings only, never a return's credit note.
- **Why:** a sales return must be sent to the Tax Authority as a credit note that points to the original document. Signing must not block posting.
- **Proposed:**
  - the return builds an ETA credit note from `einvoice.services` and queues it;
  - signing is done by a signer service (USB token or the authority's signing tool) outside the request;
  - the queue retries;
  - posting and the return themselves do not change and never wait for the authority.
- **Files:** `einvoice/services.py`, `einvoice/portal.py`, `einvoice/models.py` (credit-note kind on `Submission`) + migration.
- **Needs:** Ahmed's preprod credentials and the signing method.
- **Tests:** the credit-note document; the link to the original UUID; the queue and retry; posting a return still works with the authority down.

## HG-060 — Payroll (T12)

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below. BUILT in T12 (`claude/t12-payroll`).
- **What was built:**
  - A new `payroll` app under the employees module (`/staff/payroll/`):
    - each employee's salary (basic, allowances, insured wage, insured or not, taxed or not);
    - the month's run, one line per employee;
    - the rules screen.
  - **Prepare:**
    - gross = basic + allowances − absence − other deductions. Absent and unpaid-leave days come from the T11 attendance sheet, a day being a thirtieth;
    - insurance is counted on the insured wage, held within the legal floor and ceiling;
    - income tax is the yearly tax on (gross − employee insurance) × 12 − the exemption, through the brackets, divided by 12;
    - net = gross − employee insurance − tax;
    - a draft line can be changed by hand.
  - **The rules are data** (`PayrollSettings`): 11% and 18.75%, the 2,300 / 14,500 wage floor and ceiling, the 20,000 exemption, and the brackets 0 / 10 / 15 / 20 / 22.5 / 25 / 27.5%. **They need an accountant's check every January.** The rule that drops the lower brackets for very high incomes is not modelled.
  - **Posting** (no money moves):
    - the projector books salary cost (5201, now keyed `salaries` by data migration 0005) = gross + company insurance;
    - against it: salaries payable (2107), social insurance payable (2108, both shares) and payroll tax payable (2109). These are new accounts that `ensure_chart` adds by itself.
  - **Paying:**
    - each payment is a new cashbox operation of kind `payroll` (it pays out like a bank fee) against one debt, never more than is left, dated when paid;
    - the projector puts it against that debt;
    - a payment is cancelled from the payroll screen by a dated reversal of its operation. The cashbox screen refuses to cancel it, and its manual form does not offer the kind.
  - **Reversal:** a posted run with nothing paid is reversed on a date of its own. Its accrual entry stays and the opposite one is added.
  - **Permissions:** viewing needs `cashboxes.view_expenses`; preparing, posting, paying and the rules need `cashboxes.record_expenses`; paying also needs `cashboxes.move_cash`, through `create_cashbox_operation`. A closed month stays closed (HG-011).
  - **Reports (Codex #269):** `expenses.services.expense_total`, behind the dashboard's expenses and net profit, the daily digest and the functional report, adds the payroll cost the ledger books: gross + company insurance on the posting date, less a reversed run on its reversal date (`payroll.services.cost_total`). Payroll belongs to the main entity.
  - **Changed protected files:**
    - `cashboxes/models.py` + migration 0015: the operation and movement kind;
    - `cashboxes/services.py`: create and reverse that kind, like a bank fee;
    - `cashboxes/forms.py` and `cashboxes/views.py`: the guards;
    - `ledger/chart.py`, `ledger/projector.py` and ledger migration 0005;
    - `expenses/services.py`: `expense_total` adds the payroll cost.
  - **Corrections (Codex #269):**
    - A run with a negative net pay is not posted; it would lower what the other employees are owed.
    - A reversed month can be made again, corrected. `payroll/0002` replaces the unique month with "one run a month that is not reversed", and the reversed run stays for the record.
- **Why:** the salary run creates expenses and money movements, and it needs its own control accounts (salaries payable, social insurance, payroll tax).
- **Why the expense service cannot be reused as it is:** `expenses.services.record_expense` pays cash out for exactly the expense amount at once. Gross pay would overpay the cash; net pay would understate the salary cost and leave no liability for what is withheld.
- **Proposed: a payroll accrual and payment of its own.**
  - A `PayrollRun` (month) with one line per employee: gross, employee insurance share, company insurance share, income tax, net. These amounts are kept as rows.
  - **Posting the run (accrual):** salary cost = gross + company insurance. It is owed as net pay to the employees, insurance (both shares) to the authority, and tax to the authority. No cash moves.
  - **Paying:**
    - net pay to the employees out of a cashbox or bank;
    - the insurance and the tax to the authority later;
    - each payment is a cash movement against its liability, dated when paid.
  - The GL projector reads these rows into salary expense, salaries payable, social insurance payable and payroll tax payable.
  - A posted run is reversed by a dated reversal run, append-only.
  - Attendance and leave (T11) only feed the amounts.
- **Files:**
  - a new `payroll` app (models, accrual and payment services);
  - `cashboxes` (payment movements, called);
  - `ledger/chart.py` and `ledger/projector.py` (the new controls and their mapping);
  - `expenses` is not used for salaries.
- **Risk:**
  - Egyptian social-insurance and tax tables change every year, so they are kept as data, not code;
  - the defaults need Ahmed's or an accountant's review.
- **Tests:**
  - one run with insurance and tax: cost = gross + company share, and the liabilities equal the withholdings;
  - paying the net moves only the net in cash;
  - paying the authority clears its liability;
  - reversal of a run;
  - the trial balance balances after each step.

## HG-061 — Shipping companies and cash on delivery (T14)

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below.
- **Why:** with cash on delivery, the shipping company collects the money and settles it later minus its fees. That is a receivable from the courier, not cash.
- **Proposed:**
  - the courier is a customer-like party with its own ledger;
  - the online order's invoice is paid "on account of the courier" by a transfer of the receivable (customer payment from a courier clearing cashbox of kind `courier`);
  - the settlement moves the money to the bank and books the fee as an expense;
  - returned parcels use the ordinary sales return.
- **Files:** a new `shipping` app; `cashboxes` (kind `courier`); `sales.services` (called, not changed).
- **Risk:** a parcel lost by the courier is written off as an expense with a reason.
- **Tests:** COD sale → courier settles → bank; fee; returned parcel; lost parcel.

## HG-065 — Selling an item that is not in stock (Ahmed's notes #20, #21, #24)

- **Status:** PROPOSED, waiting for Ahmed's approval. Nothing is built.
- **What Ahmed asked:** at the till and on the sales invoice, allow selling an item whose stock is not enough, with a box asking the seller to agree to selling what is not there.
- **What happens today:** `sales.services._validate_stock_available` refuses to post a sale above the warehouse's stock, at the till, on the invoice, and on an offline sale when it syncs.
- **Why this is protected:** it touches inventory movement and accounting, the average cost, and posted sales cost (HG-003). A sale below zero has no real cost to take: the authoritative cost comes from the stock movements that are not there yet.
- **Proposed:**
  - a setting "Allow selling without stock", off by default, that only the owner can switch on (`settings_core`);
  - when it is on, the till and the invoice show a box naming each short item and how much is missing; posting goes ahead only after the seller agrees, and the agreement is written in the audit log with who agreed;
  - the missing quantity takes the item's last known cost (the last purchase, else its default purchase price) and is marked "cost to settle";
  - when stock arrives, a dated cost adjustment settles the difference between that cost and the real one, so the profit report ends up right; nothing already posted is rewritten;
  - the stock report shows items below zero in red, with a "sold before it arrived" list;
  - with the setting off, nothing changes.
- **Files:** `sales/services.py` (the stock check), `sales/pos.py`, `inventory/services.py` (the settling adjustment), `settings_core` (the setting), `reports` (the below-zero list), and the till and invoice templates.
- **Risk:**
  - the average cost and the profit for the days between the sale and the purchase are estimates until settled;
  - a warehouse can show a negative quantity, which some reports and checks assume cannot happen; each must be checked;
  - an offline sale that syncs after stock ran out must follow the same rule.
- **Tests:** off by default refuses as today; on, a short sale posts only with the agreement and logs it; the settling adjustment brings the cost of sales and the stock value to what they would be had the purchase come first; returns of a short sale; the trial balance stays balanced throughout.
