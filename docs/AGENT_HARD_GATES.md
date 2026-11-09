# Agent Hard Gates

Status: HG-001–HG-037 RESOLVED. Later entries carry their own status: an OPEN PROPOSAL (HG-041–HG-044) is a decision still waiting, APPROVED UNDER DELEGATION (HG-069–HG-071) was approved by the Maestro under Ahmed's 8 Nov go-ahead and waits for his review, and an IMPLEMENTED note is built and waiting for review. Read each entry's status before protected work.
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
  - Each entity is gathered on its own and every message keeps its `entity`; the outbox, the counts and send/skip show only the entity being worked in (HG-034). Appointments had no entity then, so their messages belonged to the main entity; since HG-074 each entity's appointments are gathered as its own, and the main entity's keys are unchanged. The main entity is gathered as itself from the start, so adding a second entity changes no existing key.
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

- **Live part (sending by WhatsApp Business), 27 Oct 2026:** after Ahmed said not to wait for him on what is plainly right. No balance, booking or posting is touched.
  - **API:** `messaging/whatsapp.py` sends through the WhatsApp Cloud API (`POST graph.facebook.com/{version}/{phone_number_id}/messages`, a template message).
  - **Secrets:** they come from the environment (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, optionally `WHATSAPP_API_VERSION` and `WHATSAPP_LANGUAGE`), never the database.
  - **Templates:** a business may open a chat only with a template Meta approved. So each kind names its approved template on the outbox screen (`MessageTemplate.wa_template`). The template's body takes `{{1}}`, the company, and `{{2}}`, the message the shop wrote. Suggested wording: «رسالة من {{1}}: {{2}}».
  - **Sending:** «ابعت أوتوماتيك» on the outbox, or the command `send_messages` each morning, sends today's waiting messages of those kinds.
    - One click sends at most 40 messages or 25 seconds' worth, so the request ends inside the web server's timeout (Codex #282). It says how many are left; the next click starts with those never tried, and the command sends the rest unbounded.
  - **Results:** a sent message keeps WhatsApp's id (`provider_id`, channel `api`). A refused one stays waiting with the reason (`error`), to go by a click. A kind with no template stays a one-click send, as before.
  - **Files and tests:** migration `messaging/0002`; tests in `messaging.tests_whatsapp`.

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

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below. BUILT in T6 (`claude/t6-loyalty`).
- **What was built:**
  - A new `loyalty` app: the rules (pounds per point, a point's value, the fewest points spent at a time, months before points run out), a points ledger per customer (earned, spent, taken back), and the points a draft sets aside.
  - Earning and taking back follow the sales audit rows (`post_sales_invoice`, `cancel_posted_sales_invoice`, `create_sales_return`, `cancel_sales_return`) through a signal on `AuditLog`; no posting, cancelling or return function was changed. The walk-in customer of the till earns nothing.
  - Spending: a "Spend points" field on the sales invoice and at the till. The points are checked (whole number, the minimum, the customer's balance after other drafts, not more than the lines) and their value is added to the invoice's own discount before the draft is saved; the spending row is booked when the invoice is posted.
  - A cancellation gives back what the invoice spent and takes back what it earned; a return takes back the earned points in proportion to what came back (rounded down), and cancelling the return gives them back. Every one is a new row.
  - Expiry: the oldest points are spent first; what is left of points past their date no longer counts.
  - The customer card shows the balance, its value and the last movements; the invoice's balance check shows the points too. The rules need `settings.manage_settings`; the screen needs `sales.view_sales_invoices`.
  - A sale made offline at the till spends no points (the sync path applies none), as with promotions.
  - Codex #274: a draft left unfinished gives its points back from "Drafts holding points" on the loyalty screen (needs `sales.create_sales_invoice`). The draft is left as it is, but posting it is refused, because its discount would have no points behind it; the sale is made again. No sales function was changed.
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
  - **The rules are data** (`PayrollSettings`): 11% and 18.75%, the wage floor and ceiling, the 20,000 exemption, and the brackets 0 / 10 / 15 / 20 / 22.5 / 25 / 27.5%.
  - **Checked against the published figures on 27 Oct 2026**, after Ahmed said not to wait for him on what is plainly right (migration `payroll/0003_rules_2026`):
    - **Insured wage:** from 1 January 2026 the floor is 2,700 and the ceiling 16,700, up from 2,300 and 14,500 (Law 148/2019 raises them every January; announced by the National Organization for Social Insurance). A shop still on the old figures moves to the new ones; a figure someone typed stays.
    - **Shares:** 11% for the employee and 18.75% for the company, unchanged. The 9% / 12% sometimes quoted is only the old-age, disability and death part.
    - **Exemption and brackets:** the 20,000 personal exemption and the brackets match Law 7/2024 (in force 1 March 2024).
    - **Higher incomes:** the gazette's tables for taxable incomes above 600,000 are now modelled (`high_income_steps`, data like the brackets). Above 600,000, 700,000, 800,000, 900,000 and 1,200,000, one more of the lowest brackets is lost and its slice takes the next rate. So above 1,200,000 the first 1,200,000 is taxed at 25%.
    - **Rounding:** the yearly taxable income is rounded down to ten pounds.
    - **Still to check every January:** these figures change by law or decree.
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

- **Status:** APPROVED by Ahmed on 21 Oct 2026 («موافق»), as written below. The manual part (no courier API) is BUILT in T14 (`claude/t14-shipping`); a live link to Bosta or another courier still needs its account.
- **What was built:**
  - A new `shipping` app and the capability «الشحن والتحصيل عند الاستلام». It is not offered to contracting, education or medical.
  - Each courier gets its own box of a new cashbox kind, `courier` (migration 0015), which the projector books in 1120, courier collections receivable. Like the cheque boxes, it is kept out of every cashbox picker (`entities.scope.cashboxes`).
  - **Ship:** a posted invoice goes out with a courier. The amount to collect defaults to what is left on the invoice, never more; one open shipment per invoice.
  - **Deliver:** the customer's payment is recorded into the courier's box (`record_customer_payment`, called, not changed), so the customer owes nothing and the courier owes the money. A delivery marked by mistake is undone from the shipments screen before settlement. The sales payment cancel refuses such a payment, the way it does a cheque's.
  - **Settle:** the courier hands over the chosen delivered parcels, less the fees (the parcels' own, unless typed). The net is a transfer from its box to a cashbox or bank; the fees are an expense (transport, 5205) paid from its box. The cashbox screen will not cancel the transfer, and `cancel_expense` refuses the fee and loss expenses, so a settlement never disagrees with its parcels.
  - **Came back:** the parcel is marked as returned, and the goods come back through the ordinary sales return.
  - **Lost:** what should have been collected is recorded as collected and written off at once as an expense with the reason.
  - **Permissions:** shipping needs `sales.create_sales_invoice`; delivery, undo and loss need `sales.receive_customer_payment`; couriers and settlements need `cashboxes.move_cash`. The expense steps also need `cashboxes.record_expenses`.
  - The sales invoice page has «ابعت مع شركة شحن».
  - **Entities (Codex #272):** a courier's box belongs to the entity it was added in. An invoice ships only with a courier of its own entity, and a settlement lands only in that entity's cashbox or bank. So a collection is credited in the same entity as the invoice's debt; a business whose branches use the same company adds it once per branch. Shipments are read and acted on only within the entity being worked in.
  - **Second shipment, returns:** what earlier shipments of the invoice collected, and what its posted returns took off the debt, are subtracted from what is left to collect (neither a customer payment nor a return changes `remaining_due`).
  - **Cancelled invoices (Codex #272):**
    - **Protected change:** `sales.services.cancel_posted_sales_invoice` now refuses an invoice with a parcel still with a courier. It is marked came back or lost on the shipments screen first.
    - Delivery and loss lock the invoice too, and refuse one that is no longer posted, so a cancelled sale is never collected.
  - **Reconciliation:** the ledger check of cash against the cashbox report now counts `courier_receivable` with cash, bank and the cheque boxes, since the cashbox formula counts the courier boxes too.
  - **Fees above what was collected** (a prepaid parcel, a small COD): the shortfall is paid into the courier's box from the chosen cashbox or bank, then the whole fee leaves the courier's box as before.
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

- **Status:** APPROVED by Ahmed on 26 Oct 2026 («موافق انك تعمل الصح بظبط») and BUILT (`claude/hg065-sell-short`).
- **What Ahmed asked:** at the till and on the sales invoice, allow selling an item whose stock is not enough. A box asks the seller to agree to selling what is not there.
- **The switch:** «البيع من غير رصيد» (`SystemSetting` `sales.sell_short`).
  - Off by default. With it off, a short sale is refused with the same message as before.
  - A forged `allow_short=1` does nothing while it is off.
  - Only the account owner can switch it: the owner account, or a user with the owner role (`sales.short_sales.is_owner`). Each change is audited.
  - Its page is `/sales/short-sales/`, linked from the capabilities page and the stock screen. The page also lists what was sold before it arrived.
- **Selling:**
  - `post_sales_invoice(..., allow_short=False, short_source=...)` raises `StockShortage`, a `ValidationError` that names every short item with what is there and what is needed.
  - The lines of one item are now checked together. Before this, two lines that each fit could together take the stock below zero unnoticed.
  - The till and the invoice turn the refusal into a box naming the short items. «أيوه، بيع من غير رصيد» sends the same sale again with the agreement.
  - The agreement is written in the audit log (`sell_short`) with who agreed and where: `pos`, `invoice` or `offline`.
  - An offline till sale that syncs after the stock ran out posts below zero while the switch is on, logged as `offline`, because the goods already left with the customer.
  - Serial-tracked items still refuse: their serials must be in stock.
- **Costing:**
  - The part of a line that the item's stock covers takes the authoritative average. The stock counted is all warehouses, because the average is item-wide.
  - The rest takes an estimate: the last purchase or opening-stock cost, else the item's purchase price.
  - The line's unit cost is the blend of the two. The line keeps it; nothing posted is rewritten.
  - `sales.ShortSale` (migration `sales/0011_short_sales`) records the line's location shortage and the estimated quantity and cost.
  - A sale that is short only at its warehouse, while the item has stock elsewhere, needs no estimate. A transfer later fixes the warehouse.
- **Settling** (`sales.short_sales.rebalance`):
  - Runs after every stock change that can bring stock in or take it out: purchase posting and cancellation, purchase returns and their reversal, stock adjustments and their reversal, opening-stock import, and sales cancellations, returns and their reversal.
  - The open estimated units should match the item's units below zero. When stock arrives beyond that, the oldest open units are settled at the cost of the stock really there: the stock value with the open estimates put back, over the quantity with the open units put back. That is the arriving cost when the item was at or below zero.
  - A settlement is a dated pair of stock movements on the sale itself: `SALE_RETURN_IN` at the estimate and `SALE_OUT` at the real cost. Its net quantity is zero.
  - The ledger's sales-invoice entry for that date books the difference to cost of sales, so `ledger/projector.py` is unchanged.
  - The stock left is valued as if the purchase had come first. The difference is kept in `sales.ShortSaleEntry` (append-only).
  - When stock leaves again and the hole grows back (for example, a purchase cancelled), the latest settlements are reopened with reversal pairs.
  - A settlement is dated the later of the arrival and the sale. One that would fall in a closed month waits for the next stock change.
- **Reversals:**
  - Cancelling a short sale reverses its settlements on their own dates, then returns the stock at the line's cost. The books end as if it was never sold.
  - A return of a short line comes back at its share of what the units still sold cost, settlements included. It closes the same share of the open estimate; reversing the return reopens it.
- **Reports:**
  - `profit_report` shows each settlement as a cost row on its date. `profit_totals` includes the settlements, so the ledger's cost-of-sales check stays reconciled.
  - The stock screen shows quantities below zero in red.
  - Other screens that read a line's cost (an invoice's own profit, item margins) show the estimate, because the line is never rewritten.
- **Known limits:**
  - Between the sale and the settlement, the cost and profit of that sale are an estimate.
  - While an item is below zero overall, a stock-out adjustment at another warehouse is taken at a zero average (the existing rule for an empty item). The settlement later puts that value in cost of sales instead of stock loss.
  - After a return of a partly settled line is reversed, the re-settlement uses the average of that moment. This is how an average-cost system works without re-costing history.
  - A purchase cancellation already had no stock check before this change; that is unchanged.
- **Files:**
  - `sales/short_sales.py`, `sales/short_sales_views.py`, `sales/models.py` with migration `0011`, `sales/services.py`, `sales/pos.py`, `sales/views.py`, `sales/urls.py`;
  - `purchases/services.py`, `inventory/services.py`, `imports/screen_services.py`, `offline_pos/views.py`, `reports/selectors.py`;
  - templates: the till, invoice detail, stock, capabilities and the new page.
- **Tests:** `sales/tests_short_sales.py` (27 tests). Every test that touches money checks that the ledger reconciles with the reports and that the trial balance balances. They cover:
  - off by default refuses as before;
  - only the owner switches it;
  - lines of one item checked together;
  - the agreement is logged;
  - the estimate from the last purchase, else the purchase price;
  - settlement by a purchase, a stock count and an opening-stock import;
  - partial arrivals, oldest first;
  - a warehouse-only shortage;
  - a sale from stock while the item is short elsewhere;
  - cancelling before and after settlement;
  - partial and full returns, and reversing a return;
  - cancelling the purchase that settled;
  - the till box, the invoice box and the offline sync, each with the switch off and on.

## HG-074 — Appointments carry their entity (HG-034 gap)

- **Status:** IMPLEMENTED (`claude/maestro-appointments-entity`, 8 Nov 2026) under Ahmed's 8 Nov standing go-ahead to build and merge with an independent review; pending his review.
- **Problem:** `appointments.Appointment` had no entity, so HG-034's one rule did not reach it:
  - **wrong books:** `bill()` took the selling store of the entity the *biller* was working in. The owner, working in the main shop, billing a branch's visit put the invoice, its stock movement, its cash and its customer ledger row in the main shop's books, and the branch's performance report never saw the sale;
  - **URL leak:** the agenda, the detail page and every action on it (`/appointments/<pk>/`), the dashboard's bookings panel, the follow-up digest, the clinic's queue and insights, and the service contracts' visit counts read every appointment whatever entity was chosen, so a branch member saw and could move, bill or reschedule another entity's appointments by their number;
  - the messages outbox worked around it by gathering appointments for the main entity only (`UNSCOPED`).
- **Change:**
  - **Schema:** `Appointment.entity`, a nullable `PROTECT` FK to `entities.Entity` (migration `appointments/0002_appointment_entity`, after `entities/0001` and `master_data/0002`), as `workorders.WorkOrder.entity` is. NULL belongs to the main entity (HG-034), so a single-entity install and every pre-HG-074 row read exactly as before and no back-fill is needed. The migration also copies, for **billed** rows only, the entity of the invoice's selling store (where the bill actually landed), with a no-op reverse; unbilled rows stay NULL.
  - **Services** (`appointments/services.py`): `scoped()`; `book()` stamps `entity_scope.entity() or None`; `day_agenda()` and `performance()` read through `scoped()`; **`bill()` takes a selling store of the appointment's own entity** (`selling_location_of`, which reads with `working_in(appointment.entity or main_entity())` so NULL takes a main-entity store), whatever entity the biller is working in. An entity with no active selling store refuses with a bilingual message naming the entity, and nothing is made.
  - **Unchanged by design:** the overlap check and the day's numbering stay group-wide, so one employee has one diary across the group; customers and employees stay shared (HG-034); posting, stock, cost and the ledgers are untouched.
  - **Views:** `detail` (and so every POST on it: status, bill, insured bill, reschedule) reads through `entity_scope.get_or_404(Appointment, "entity", pk=pk)` before any action runs, so another entity's appointment answers 404, as every scoped document does. There are no other appointment endpoints on `develop`.
  - **Other readers**, each now through `scoped()` / `entity_scope.scope(..., "entity")`: `reports/activity_panel.py` (today's bookings, the 30-day no-show rate), `reports/followup.py` (today's and tomorrow's appointments), `medical/views.py` (queue check-in from an appointment, the desk's list), `medical/insights.py` (`_booked_patients`), `agreements/visits.py` (`booked_in`), `agreements/services.py` (`visits_in`).
  - **Messaging:** `UNSCOPED` is empty; `day_agenda()` is scoped, so each entity's appointments are gathered as its own under `working_in(entity)`. The main entity's keys are unchanged (`appointment:<pk>`); a branch's carry `@<entity pk>` as every other kind does.
  - **Cross-entity visibility** stays exactly what `entities/current.py` allows: the owner sees the whole group or one entity; a member never leaves their entities.
- **Risk:**
  - a billed appointment and its invoice now agree on the entity by construction; `medical/insights.py` still reads done appointments by the *invoice's* entity, which is the same answer;
  - a branch's appointment billed while the branch has no selling store is refused rather than put in the main shop's books, so a new branch must get a selling store before its first bill;
  - `agreements` visit counts are now per entity: a contract viewed in the whole group counts every visit as before; viewed in one entity, only that entity's.
- **Follow-ups (out of scope here, recorded for the next task):** the same "store of the entity the user is working in, not the document's" pattern remains in `workorders/services.py` (`_location()` in `deliver`, ~368), `medical/insurance.py` (`bill_parts`, the multi-service path not tied to one appointment) and `agreements/services.py` (`selling_location()`, ~238). `bill_insured`, the second bill button on the appointment page, was fixed in this PR after the independent review, and so were the patient file's upcoming appointments and the visit-to-appointment link (`medical/views.py`). Each should take the store of its own record's entity the way `selling_location_of` does here.
- **Tests:** `appointments.tests.EntityScopeTests` (8 tests, two entities with their own store and cashbox, a branch member and the owner):
  - booking stamps the entity; the agenda per entity (service and screen), NULL rows under the main entity, the whole group sees all; booking from the branch's screen is the branch's;
  - the detail page and every POST action on another entity's appointment answer 404 and leave it unchanged, for the branch member and the owner working in the branch; the whole group and the main entity see it;
  - billing a branch appointment while working in the main shop: the invoice's selling location and its entity are the branch's; after posting with cash in the branch till, the stock movement's location, the cash movement's cashbox, the customer ledger row (in the branch's `customer_entries_q`, not the main shop's) and every projected journal line are the branch's; a NULL row billed from the branch takes a main-entity store;
  - an appointment in an entity with no selling store is refused in Arabic and English, and no invoice is made;
  - performance per entity (main 2 / 600, branch 1 / 300) adds up to the group (3 / 900), and the branch manager's report shows the branch's total;
  - the same employee still clashes across entities, and numbering runs group-wide;
  - messaging gathers the main entity's appointments with unchanged keys and the branch's with `@<pk>` under the branch;
  - the migration's back-fill copies the entity from the bill for billed rows and leaves unbilled rows NULL.
  - Existing suites `appointments`, `entities`, `messaging`, `reports`, `medical`, `agreements`, `sales` stay green: a single-entity install is unchanged.

## HG-068 — Second-person approval for cancellations and reversals (Ahmed's note #27)

- **Status:** BUILT (`claude/fb27-approvals`). Ahmed asked for «موافقة مستوى تاني» in note #27, and on 27 Oct 2026 said not to wait for him on what is plainly right («لو مهم وحاجة صح وهتنجح البرنامج اعمله علي طول»).
- **Why it is recorded here:** it touches the permission layer. It adds the permission `approvals.approve` (seeded to the owner and manager roles), and nine screens now consult it before they act. No service, posting, ledger or report changed.
- **What the owner holds:** the owner picks on `/approvals/` which actions wait for approval. Nothing is held at first. The actions on offer are:
  - cancelling a posted sales or purchase invoice;
  - reversing a sales or purchase return;
  - cancelling a customer collection or a supplier payment;
  - cancelling an expense;
  - reversing a stock operation or a cashbox operation.
- **How it works:**
  - A person with `approvals.approve` does a held action directly, as before.
  - Anyone else gets a waiting request (`approvals.ApprovalRequest`) holding the reason and the reversal date they chose. Nothing moves, and asking twice makes one request.
  - Someone with `approvals.approve` approves or rejects it. The requester never decides their own request, and only the requester can withdraw it.
  - On approval, the same service the screen calls runs then, in the requester's name and inside its own transaction. The request keeps who approved.
  - If the service refuses (the document changed, the month closed), the request is marked as approved but not done, with the reason, and nothing moved.
  - A request is decided only where its document can be seen (entities scope).
  - Approvers get a dashboard alert with the count waiting for them. Each step is audited.
- **Files:**
  - `approvals/` (new app, migrations `0001` and `0002` for the permission seed), `config/settings.py`, `config/urls.py`;
  - the cancel and reverse views in `sales`, `purchases`, `expenses`, `inventory` and `cashboxes`;
  - `reports/dashboard_views.py`, `templates/approvals/inbox.html`, `templates/settings_core/capabilities.html`.
- **Tests:** `approvals/tests.py` covers:
  - nothing held by default;
  - a held cancellation waits, with no duplicate request;
  - approvers act directly;
  - only the owner chooses what is held;
  - approval runs in the requester's name and shows on the dashboard;
  - no self-approval, and no approval without the permission;
  - reject, and withdraw by the requester only;
  - an approval that can no longer be done says why;
  - a dated stock reversal keeps the date it was asked for.

## HG-067 — Online store by file: a new table for imported orders (T13)

- **Status:** RECORDED (Codex #273). This is a new, additive table in a new app. No existing model, migration, posting or report changed.
- **Change:** `onlinestore.StoreOrder` (migration `onlinestore/0001_initial`) holds:
  - `source` (`shopify` or `template`), `order_number`, a one-to-one link to the draft `sales.SalesInvoice` it made, `store_total`, `imported_by`, `imported_at`;
  - a unique constraint on (`source`, `order_number`).
- **Why:** the same orders file uploaded twice must not make the same invoices twice. The draft also needs to show the store's own total, to compare with what Hesba computed.
- **Accounting:** none. Orders become **drafts** through `taxes.create_sales_draft_with_tax` (the invoice form's path). Stock, cash, the customer ledger and the ledger move only when someone posts the draft as usual.
- **Correcting an order (Codex #273):** a draft has no edit screen. So the board can:
  - put the store's shipping charge on a line of a chosen service item;
  - take VAT-inclusive store prices back to before tax;
  - when a corrected file is uploaded again with «بدّل الطلبات اللي لسه مسودة», delete the old **draft** with its `StoreOrder` and make it again. A draft has no stock, cash or ledger effect, and its lines, tax rows and serial links go with it. A posted invoice is never replaced.
- Serial-tracked items go through `prepare_sale_serials` and `attach_sale_serials`, as on the invoice form, with the serials taken from the template's «السيريال» column. Invoice numbers are Hesba's own `WEB-` series; the store's number stays in the notes and on `StoreOrder`.
- **Risk:**
  - two branches with separate stores whose order numbers coincide share one namespace per source, so the second branch's order would be reported as imported already;
  - the invoice link is `PROTECT`, so a draft that came from the store is cancelled, not deleted.
- **Files:** `onlinestore/` (new app), `config/settings.py`, `config/urls.py`, capability wiring (`settings_core/capabilities.py`, `module_gate.py`, `operational_views.py`, `display_labels.py`, `reports/navigation.py`).
- **Tests:** `onlinestore/tests.py`. They cover once-only import, drafts only (status `draft`), the phone match, the walk-in fallback, the template, and permissions.
- **Live part (pulling from the store's API), 27 Oct 2026:** after Ahmed said not to wait for him on what is plainly right. Nothing new is written beyond what an upload writes; orders still become drafts only.
  - **Code:** `onlinestore/connect.py` reads the last days' orders from a connected store.
  - **Shopify:** the Admin REST API (`orders.json`, token in `X-Shopify-Access-Token`, paged on `since_id`).
  - **WooCommerce:** the REST API v3 (`wp-json/wc/v3/orders`, key and secret as HTTP Basic).
  - **Secrets:** they come from the environment (`SHOPIFY_STORE`, `SHOPIFY_ACCESS_TOKEN`, `SHOPIFY_API_VERSION`, `WOOCOMMERCE_URL`, `WOOCOMMERCE_KEY`, `WOOCOMMERCE_SECRET`), never the database.
  - **The import:** each order takes the same shape an upload makes and goes through the same loop (`services.import_parsed`).
    - It keeps the once-only rule.
    - A WooCommerce order uses the template's source, so an order a shop uploaded before by file is not made twice.
    - Coupons come in as the order's discount.
    - VAT is each order's own (Codex #283). A Shopify order's `taxes_included` says whether its prices hold the VAT; when the shop charges VAT, the VAT is taken back out of that order only.
    - WooCommerce's REST amounts (line subtotals, shipping, discount) are always before tax, so they come in tax-exclusive and the VAT goes on top (Codex #283).
    - A WooCommerce line keeps its exact subtotal (the unit price carries six decimals, and the import's rounding brings the line back to it). Each line's coupon is what WooCommerce took off it (`subtotal − total`); the order's coupon is not spread again over every line (Codex #283).
    - A Shopify line takes its own `discount_allocations`, and its shipping is the `discounted_price`; the order's discount is spread only for orders that do not give them (Codex #283).
    - A WooCommerce order with `fee_lines` (handling, payment surcharges) is skipped with the reason and tried again on the next pull, never imported short of its fees (Codex #283).
    - A WooCommerce order with `refunds` is refused as partly refunded, as Shopify's `partially_refunded` is (Codex #283).
    - A Shopify order edited after it was placed comes in as it is now: `current_quantity` per line (a line edited to nothing is left out, its discount scaled to what is left), and `current_total_price` / `current_total_discounts` (Codex #283).
    - **A pulled order comes in only when Hesba's invoice comes to the store's total** (within 0.05; a shipping charge only noted on the invoice counts). Otherwise it is skipped with both totals and tried again on the next pull. This catches a tax or fee the store worked out differently (a tax-exempt customer, a surcharge), so none is imported silently (Codex #283). File uploads keep showing the two totals side by side for checking, as before.
  - **Paging (Codex #283):**
    - The board's button reads at most 20 pages, so the request stays short.
    - When the store has more orders, the result says so and offers «كمّل السحب». That button carries on in the same window from the next page; nothing is cut silently. It keeps the location, shipping item and «بدّل» choice of the first pull.
    - The command reads every page.
  - **Where:** the board gets «اسحب من Shopify / WooCommerce», and the command `pull_store_orders --location CODE` can run from cron.
  - **Tests:** `onlinestore/tests_connect.py`.

## HG-073 — S2: the customer's decision on a work order, from a secure link

- **Status:** OPEN PROPOSAL (needs Main Control's review before merge). It is additive: two column changes on one table, no data moved.
- **Change:** `workorders.WorkOrderEvent` (migration `workorders/0002_workorderevent_by_customer`):
  - `user` becomes nullable (`null=True, blank=True`, still `PROTECT`);
  - `by_customer` is a new `BooleanField(default=False)`.
- **Why:** the customer approves or refuses a QUOTED order from a signed link, with no account. `WorkOrderEvent.user` was required and the repo has no system-actor pattern. The alternative was to file the event under the order's creator, which states a false actor in the audit trail and shows that employee's name on the staff timeline. So the customer's event is saved with `user=None, by_customer=True`, and the staff timeline shows «العميل من اللينك» / "Customer (via link)".
- **Accounting:** none. A decision changes the order's status only, through `workorders.services.customer_decide()`. No invoice, cash, stock or ledger row is written. A refusal takes the quote's lines off, as a staff refusal does; the lines are kept in the audit.
- **Audit:** an `AuditLog` row with `actor=None`, the order id and the status change only (no name, phone, IP or user agent).
- **Risk:**
  - code that assumes `event.user` is set. The only reader today is the staff timeline (`templates/workorders/detail.html`, through the `person` filter, which already returns an empty text for no user); the other writers always pass a user. Any new reader must check `by_customer` first;
  - existing rows keep their user and `by_customer=False`; the migration changes nothing in them;
  - the public link can only approve or refuse a QUOTED order. It is signed, expires after `SHARE_LINK_DAYS`, and is built from `PUBLIC_BASE_URL`, never the request host.
- **Rate limiting:** none added; the repo has no reusable helper, the token is signed and a repeated answer is a no-op.
- **Files:** `workorders/models.py`, `workorders/migrations/0002_workorderevent_by_customer.py`, `workorders/services.py`, `workorders/public.py` (new), `workorders/views.py`, `workorders/urls.py`, `templates/workorders/detail.html` (one label), `printing/share.py`, `printing/views.py`, `config/urls.py`.
- **Tests:** `workorders/tests_customer_link.py`. They cover wrong-kind, other-order, forged and expired tokens; no cost or notes on the page; approve once and idempotent; refuse only from QUOTED; no other status allowed; CSRF enforced; `user=None` and `by_customer=True` on the event; the staff timeline label in both languages; existing staff events unchanged; the audit row with the order id only; no invoice, cash, stock or ledger row; the link from `PUBLIC_BASE_URL`; Arabic and English messages. Added after the security review: only counter staff (the staff approval rule, `can_manage`) get the link (technician and view-only user: 403; cashier, manager and owner: 200); the quote fingerprint (total plus a hash of item, quantity and price per line) that the POST must echo, so a quote changed after the page was read is refused and the status stays; and a repeat is "already recorded" only when the customer gave the first answer, otherwise "no longer waiting".

## HG-066 — E-receipts for till sales (T8, offline part)

- **Status:** BUILT in T8 (`claude/t8-ereceipt`); additive only. Sending to the authority still needs the device's credentials on the receipt portal.
- **What changed:**
  - **Schema:** two new tables, nothing changed in existing ones:
    - `ereceipt.Device`: the till's serial number, branch code and the last UUID of its chain;
    - `ereceipt.Receipt`: one per till sale, holding its UUID, the previous UUID, its status and what is missing. `document_text` keeps the receipt exactly as it was hashed, because a JSON column may reorder keys.
  - **Hook:** a `post_save` signal on `AuditLog`, for `post_sales_invoice` only. It issues the receipt of a till sale (`POS-…`) when the capability `e_receipt` is on. It runs inside the posting's transaction and reads the posted sale; the posting function itself is not changed. A receipt whose data is incomplete is kept as "not issued", never blocking the sale.
  - **Chain:** the device row is locked while a receipt is issued, so two sales never take the same `previousUUID`.
  - **Reads:** follow the entity scope (HG-034), through the invoice's selling location.
- **Risk:** a sale posted while the device is missing or data is incomplete waits as "not issued". Issuing it later puts it at the end of the chain, with the issue time of its sale, so the 24 hours count from that time; the queue shows it as late.
- **Tests:** `ereceipt.tests`.
- **Live part (sending), 27 Oct 2026:** after Ahmed said not to wait for him on what is plainly right. No posting, chain or hashing changed; this only reads the queue and records the authority's answer.
  - **Code:** `ereceipt/portal.py` and the command `send_ereceipts`.
  - **API:** it follows the ETA eReceipt SDK: the POS login with the device in the headers, the batch submission, then the submission's details.
  - **Secrets:** the POS client ID, secret and pre-shared key come from the environment (`ETA_RECEIPT_*`), never the database.
  - **Device fields:** the OS version and model / framework are two new fields on the device (migration `ereceipt/0002`).
  - **What is sent:** receipts go oldest first, as the exact text they were hashed from. `einvoice.portal._http` gained a `raw` argument for that.
  - **Outcomes:** an accepted receipt keeps its submission UUID and long id; a rejected one keeps the authority's reason; a refused or unreachable batch stays queued.
  - **Tests:** `ereceipt.tests_portal`.

## HG-069 — Deposit at booking and the cancellation policy (S5)

- **Status:** IMPLEMENTED (S5); merged under Ahmed's 8 Nov go-ahead after independent Fable review; pending his review (branch `claude/maestro-s5-deposit`). Built as design A below; `settlements` and `deposits` are new apps, the four "due" totals subtract `applied`, and the projector books deposits in 2105. Deviations from the text below, recorded here: (1) one additive guard inside `sales.services.cancel_customer_payment`, in the pattern of the cheque and courier guards, refuses to cancel the payment of a deposit that already settles a posted invoice («cancel that invoice first»), because a cancelled payment behind a settlement row would show an invoice as paid with the money reversed; it raises in English like its sibling guards (that function takes no language), and its Arabic version is in `settings_core.ui_messages.EXACT`, so the screen shows it in Arabic; (2) the deposit is attached to the draft when `appointments.services.bill` makes it, and to the patient's draft when `medical.insurance.bill_insured` makes one (one call to `deposits.services.attach` each), and applied by the `post_sales_invoice` audit row, the `ereceipt` hook pattern, so posting itself is unchanged; cancelling the invoice releases it the same way; (3) the «عربون» capability (`deposits`, «العربون وسياسة الإلغاء») exists in `settings_core/capabilities.py`: OPTIONAL for every activity and not `legacy_on`, so it is off until switched on from Settings → Features (as loyalty, promotions and shipping are for most services sub-activities; a SUGGESTED preset would also need a setup-interview question), and it needs the appointments module. Off, no deposit can be taken (`deposits.services.take` refuses in both languages), the appointment page shows no deposit box, the agenda shows no deposit column or policy form, the take / forfeit / refund / policy posts answer 403, and the cancel button runs no policy. What follows the sales engine (apply on posting, release on cancelling the invoice, refund on a cancelled payment) is not gated: a deposit still held when the capability is switched off stays the customer's money on account (2105, customer balance −D), is still applied if its booking is billed, and is settled from the page once the capability is on again; demo data was not added; (4) the policy setting (`deposits.cancel_hours`, default 24, `SystemSetting`) is set by the owner on the agenda page; (5) a deposit larger than its invoice settles the invoice in full and the rest stays on account (in 2105 and as the customer's credit); (6) an insured visit with a 0% patient share (100% cover) has no patient invoice, so its deposit stays held: the appointment page shows it as held with a note («التأمين غطّى الخدمة كلها…») and offers the refund; (7) **signal-driven side effect inside protected posting:** apply, release and the refund mark run from a `post_save` receiver on `audit.AuditLog` (`deposits.services._on_audit`, connected in `DepositsConfig.ready`), inside the transaction of `post_sales_invoice`, `cancel_posted_sales_invoice` and `cancel_customer_payment`. Those services are not edited, but writing their audit row now also writes settlement rows; a failure in the receiver rolls the posting back. Reviewers of those services should know the hook is there; (8) the ledger projector reads only settlement rows backed by a posted payment (`customer_payment__status="posted"`), as `settlements.services.applied` does; (9) deposit receipt numbers (`DEP-YYYYMMDD-NNN`) follow the highest number used (`config.numbering.next_in_series`) and a number taken in the same instant is retried in a savepoint; (10) the deposit's cashbox (the form's list and the action's check) and the forfeit invoice's store come from the *booking's* entity (HG-074, as `appointments.services.selling_location_of`), even for an owner working group-wide, so the deposit, its bill and its fee land in one entity's books; (11) **extension into two protected collection limits (Codex #313, P1):** what is still due on an invoice is `remaining_due − applied` everywhere money is still to be collected, not only in the four reports. `installments.services.create_plan` finances `remaining_due − applied` (a 300 invoice with a 100 deposit applied finances 200; a deposit covering it all leaves «nothing to pay»), and `schedule`/`collect` take off a settlement made after the plan as they take off a return (`settled_since`, a separate `settled` total); `shipping.services.ship` caps the cash-on-delivery amount at `remaining_due − applied − earlier collections − returns`. Both read `settlements.services.applied_to`; nothing else in those services changed; (12) **the cancel step is one service:** `appointments.services.cancel_with_deposit_policy` locks the booking row, then reads its held deposit, moves the status and runs the policy in one transaction, and the screen only shows its outcome. `deposits.services.take` locks the same row, so a deposit taken while the booking is being cancelled is either read and settled by the policy or refused because the booking is no longer open (Codex #313: the screen used to read the deposit before the lock). The status is set before the policy runs inside that transaction, so an invalid move touches no money and a no-show forfeits whatever the hours. Tests: `deposits.tests`, `settlements.tests`. This records the money decision before S5 (`docs/MARKET_PARITY_ROADMAP.md`, «بداية خدمي», PR order 8) touches the customer ledger. It also defines the one shared table (`settlements.InvoiceSettlement`) that HG-070 and HG-071 reuse.
- **The question for Ahmed:** «العربون يتسجل فلوس داخلة من العميل يوم ما يتاخد، ويظهر على فاتورة الخدمة كمبلغ مدفوع مقدم والباقي بس هو اللي يتحصل؛ ولو العميل لغى متأخر العربون يتحوّل لفاتورة «رسوم إلغاء» بدل ما يرجع له. موافق؟»
- **Why it is a gate:** a deposit is the customer's money held before any service is given. Today `appointments.Appointment` never moves money (its docstring says so) and `appointments.services.bill` makes the invoice on credit (`cashbox: None`, `paid_now: 0`). A deposit is a cash-in before the invoice, and its forfeit or refund are reversals: customer ledger, cashbox and the ledger projection, all protected.
- **What the code does today (checked):**
  - `sales.services.record_customer_payment` is a payment on account: customer due down, cashbox in, no invoice link. It is never applied to an invoice. A project owner's advance already works this way (HG-038).
  - **The invoice's figures are frozen at posting.** `SalesInvoice.clean()` (`sales/models.py`) requires `remaining_due == total_amount − paid_now`, and `calculate_payment_status` reads `paid_now` only. So after a deposit the invoice stays `credit` with its full `remaining_due` while the customer ledger nets to zero. Four reports sum `remaining_due` as money still due: the invoice list total (`reports/functional_views.py` line 131), the daily digest (`reports/digest.py` line 32), the sales-rep report (`reports/reps.py` line 59, `credit_sold`) and the till's day totals (`sales/pos.py` line 322). Left alone, they overstate receivables against `ledger.reports.reconciliation`, which reads the ledger rows.
  - `sales.services.cancel_customer_payment` writes its reversal rows on the **original** `payment_date` and checks that month is open. A deposit taken in a month that has since closed cannot be refunded through it.
  - `ledger/projector.py` already has the hook: the ids in `_advances` (`projects.ProjectPayment`, kind `advance`) make `_place_customer_payment` book the payment in `2105 دفعات مقدمة من العملاء` instead of receivables, and a certificate's `recovery_amount` moves it back on the invoice date. `ledger/reports.py` counts `customer_advances` with receivables in the reconciliation.
  - `taxes/` knows nothing of prepayments: VAT rows exist only per invoice line (`SalesLineTax`, `taxes.services.compute_lines`). A customer payment carries no VAT, so VAT on the deposited service is charged when the service invoice posts, not when the deposit is taken.
- **Proposed design (A, recommended): the deposit is a customer payment on account, linked by a new table, booked as a customer advance, and shown on the invoice through a shared "applied" table.**
  - A new `deposits` app, generic: `Deposit` has a OneToOne to `sales.CustomerPayment` (the pattern of `projects.ProjectPayment`, `cheques.Cheque.customer_payment`, `shipping.Shipment.payment`), a generic link (content type + object id) to the booked thing (an `appointments.Appointment` now; a patient's online booking in M4 and a bakery pre-order in R10 later), a status (held / applied / forfeited / refunded), and the invoice it was applied to.
  - **The shared table (new app `settlements`):** `InvoiceSettlement(invoice FK, customer_payment OneToOne, amount, source: deposit / payment_link / gift_card)`. One helper, `settlements.services.applied(invoices)` → `{invoice_id: amount}`. **Outstanding and collections are handled apart**, because every settlement row is backed by a posted `CustomerPayment` (the deposit, the `PAYMOB-` payment, the redemption from the gift-card box) that the payment-based figures already count; adding `applied` to a collections figure would count that money twice.
    - **Invoice outstanding:** the four reports above show `due = remaining_due − applied` per invoice. `paid_now` sums stay `paid_now`; where a screen has room, `applied` is shown as its own figure («مدفوع من عربون / لينك / كارت»), never added to a payment sum.
    - **Period collections stay payment-based and unchanged.** Checked per file: `reports/reps.py::_row` already adds posted `CustomerPayment.amount` for the rep's customers (`customer__sales_rep=rep`, `payment_date` in the period) to `paid_now` in `collected`, so a settlement's payment is already there on its payment date; only `credit_sold` (line 59) changes, to `Σ(remaining_due − applied)`. `reports/digest.py`: `sales.credit` (line 32, `Sum("remaining_due")`) becomes `Σ(remaining_due − applied)` over the day's invoices; `sales.cash` (`Sum("paid_now")`) and `customers.collected` (line 52, `Sum(CustomerPayment.amount)` for the day, which already holds the settlement payments) are untouched. `reports/functional_views.py::_posted_totals` (line 131): `due` becomes `Σ(remaining_due − applied)`, `paid` stays `Sum("paid_now")`, and a separate `applied` total is added so `total = paid + applied + due` still holds under the list. `sales/pos.py::today_summary` (line 322): `credit` becomes `Σ(remaining_due − applied)` over the cashier's own posted invoices; `cash` stays `paid_now − card`, because a deposit was taken on another day and a card part is no cash in the drawer; an `applied` key is added for the screen.
    - The invoice page and print show «مدفوع مقدم» and «الباقي» from the same helper. The frozen fields are not touched; this is a report-layer subtraction, counted here as a change to report calculations (protected) and named in each of the three gates.
  - **Take:** `record_customer_payment(..., cashbox=cash or bank)` called, not changed; the deposit row is written in the same transaction. Cash or bank up, customer balance down, the day it is taken. Taken only while the booking is `booked` or `confirmed`; one held deposit per booking.
  - **Apply:** when `appointments.services.bill` makes the invoice and it posts, the deposit row records the invoice, becomes applied, and writes its `InvoiceSettlement`. Nothing changes on the invoice's own fields: the customer's balance nets it, and the settlement row is what the screens and reports show.
  - **Late cancellation (forfeit):** a posted sales invoice for a non-stock service item «رسوم إلغاء» (`is_stock_tracked=False`), customer = the depositor, on credit, made through `taxes.services.create_sales_draft_with_tax` + `sales.services.post_sales_invoice`, with its own settlement row. The deposit on account settles it; the row becomes forfeited. **The forfeited amount D is tax-inclusive: the invoice total must equal D exactly, so the customer owes nothing after it.** `taxes.services.compute_lines` treats the unit price as tax-exclusive — `taxable = money_round(quantity × price − discount)`, `tax = money_round(taxable × rate / 100)`, both `ROUND_HALF_UP` to the piastre through `config.money.money_round` — and `create_sales_draft` adds `tax_amount` to `total_amount`, so passing D as the price would bill D × 1.14 and leave the customer owing the VAT (100 → 114, 14 owed). The `deposits` service therefore back-computes the line: `rate = rate_for(item).rate` (0 when VAT is off or the item carries a 0% `ItemTaxRate`), `net = round_half_up(D / (1 + rate / 100), 2)` with the same `money_round`, `tax = D − net`, and it passes `quantity=1, unit_sale_price=net`. `compute_lines` then re-derives the tax from `net` as `money_round(net × rate / 100)`; the total is exactly D only when that equals `D − net`. For 100 at 14%: net 87.72, tax 12.28, total 100.00. For 99.99: net 87.71, tax 12.28, total 99.99. The two roundings do not always agree (100.03 → net 87.75, re-derived tax 12.29, total 100.04), so the service checks `net + money_round(net × rate / 100) == D` and otherwise tries `net ± 0.01`. Totals reachable through one 14% line step by one or two piastres, so some amounts have no exact line at all (9.00, 16.00 and 100.03 among them: 87.74 → 100.02, 87.75 → 100.04). Then the fee invoice is made for the nearest reachable total **below** D (one piastre less at most), the settlement row is for that invoice total, and the residual piastre stays as a credit on the customer's account, shown on the deposit row as «فرق تقريب». The fee is never above D; `taxes` is called, not changed. The policy (free until N hours before, then forfeit) is one additive setting per activity.
  - **Early cancellation (refund):** `cancel_customer_payment` called, not changed, from the deposits screen through `approvals.gate.held` with the existing action `sales.cancel_payment`, so HG-068 holds it when the owner has chosen to. The row becomes refunded.
  - **Ledger:** the projector learns the deposit payment ids the way it learns `_advances`: a held deposit sits in 2105 from its payment date and moves to receivables on the date of the posted invoice it was applied to or forfeited into. If that invoice is later cancelled, the settlement row is removed and the deposit goes back to held, so it sits in 2105 again. Reconciliation already adds 2105 to receivables.
- **Alternatives rejected:**
  - **B — a deposit field on the invoice (`paid_now` at posting):** the cash would move on the invoice date, not the day it was taken, and a booking could be cancelled with cash in the drawer and nothing in the books. It also changes `post_sales_invoice` and `clean()`.
  - **C — a new deposit movement type in `cashboxes`:** a second money path for the same thing; it would need its own reversal, approvals action, closed-period rule and report columns. The customer payment already has them all.
  - **D — lower `remaining_due` when the deposit is applied:** breaks `clean()`'s invariant and every test that pins it, and rewrites a posted document.
  - **E — no projector change (the deposit stays a credit in 1103):** zero protected code, but the balance sheet shows customer money as negative receivables rather than a liability. The fallback if Ahmed wants nothing protected touched in S5; the settlement table is still needed.
- **Files:** new apps `deposits` and `settlements` (models, migrations, services, views, templates, tests); `appointments/views.py` and `templates/appointments/` (the deposit box and the cancel step); `templates/sales/detail.html` and the invoice print (the applied amount); `reports/functional_views.py`, `reports/digest.py`, `reports/reps.py`, `sales/pos.py` (subtract applied amounts); `ledger/projector.py` (one additive id set beside `_advances`, and the recovery line on the invoice date); `settings_core/capabilities.py` (the capability «عربون»); demo data. `sales.services`, `cashboxes.services`, `taxes`: called, not changed.
- **Risks:**
  - **Closed month:** a deposit taken in a month that has since closed cannot be refunded on an early cancellation, because `cancel_customer_payment` reverses on the original date. The screen must say so and offer the forfeit invoice (dated today) or a credit left on the customer's account; a dated refund service does not exist and would be its own gate.
  - **Double posting:** one held deposit per booking; the OneToOne to the payment stops a second application; one settlement row per payment.
  - **VAT on the deposit:** none; the service invoice charges it in full later. Egyptian VAT treats an advance as a taxable event; charging it at receipt would be a `taxes` change and a separate gate.
  - **VAT on «رسوم إلغاء»:** the fee invoice is taxed at the item's rate when VAT is on, like any service, unless the accountant gives the item a 0% `ItemTaxRate`. Default: taxed, **inside** the forfeited amount (the back-computed line above), so the deposit covers the whole invoice and the customer is never billed VAT on top of it.
  - **Rounding:** only in the fee line's back-computation (two half-up roundings to the piastre; a one-piastre residual at most, left as a customer credit and shown). Deposits themselves are whole amounts with no allocation. **Walk-in:** the till's walk-in customer cannot hold a deposit (as with loyalty).
- **Hidden decisions stated:** the deposit is money of the customer (a liability), not a sale; the cancellation fee is a sale with VAT included in the forfeited amount; a refund is a cancellation of the payment, approvable under HG-068; the printed invoice shows the deposit as a note from the settlement table, not as `paid_now`.
- **Tests to write:**
  - take: customer balance −D, cashbox or bank +D on the payment date, the row held, trial balance balanced, 2105 = D;
  - apply: the invoice posts on credit for P, customer balance = P − D, 2105 = 0 and receivables = P − D on the invoice date, no stock movement for the service item; the invoice list total, digest, reps report and till day totals show due = P − D while `remaining_due` on the row is still P; `paid_now` sums are unchanged and the invoice list's `applied` total is D;
  - **regression, one count only:** the rep's customer pays a deposit D on day 1, the rep's invoice of P posts on day 3 and the deposit is applied; `rep_performance(day 1, day 3)` gives `collected = paid_now + D` exactly once (the `CustomerPayment` on day 1), `credit_sold = P − D`, and `rep_performance(day 3, day 3)` gives `collected = paid_now` with `credit_sold = P − D` (the deposit was collected on day 1, not on day 3); the digest for day 1 shows `customers.collected = D` and for day 3 `sales.credit = P − D` with `customers.collected = 0`;
  - cancel the applied invoice: the settlement row is gone, the deposit is held again and 2105 = D on the cancellation date;
  - forfeit, VAT on at 14%: D = 100 posts a fee invoice with `taxable_amount` 87.72, `SalesLineTax.tax_amount` 12.28, invoice `tax_amount` 12.28 and `total_amount` 100.00; D = 99.99 gives 87.71 / 12.28 / 99.99; in both the customer balance is exactly 0 after the settlement row, revenue +net, output VAT +tax, cashbox unchanged; D = 100.03 posts 100.02 and leaves a 0.01 credit on the customer's account; VAT off: the line is D with no `SalesLineTax` row and the total is D;
  - refund: payment cancelled, customer balance 0, cashbox −D, 2105 = 0; held by HG-068 when the owner holds `sales.cancel_payment`;
  - closed month: refund refused with the message, forfeit allowed today;
  - a second deposit on the same booking refused; `ledger.reports.reconciliation()["ok"]` after every step; permissions (`sales.receive_customer_payment` to take and to refund, as for any collection).
- **Out of scope:** paying the deposit online (HG-070 reuses this table), partial deposits per line, deposits on sales orders (T1), a refund service with its own date.

## HG-070 — Online payment link, Paymob first (S4)

- **Status:** IMPLEMENTED (S4); merged under Ahmed's 8 Nov go-ahead after independent review; pending his review (branch `claude/maestro-s4-paylink`, built on S5's `claude/maestro-s5-deposit` and merged after it). Approved on 8 Nov 2026 under Ahmed's blanket go-ahead («متساناش علي موافقتي خالص واعمل الصح كله … وبعد كدة هنبقي نراجع واحدة واحدة»), after independent review by Fable and Codex; listed for Ahmed's later review in `docs/MAESTRO_LOG.md`. It reuses `settlements.InvoiceSettlement` from HG-069. Built as design A below (`GatewayTransaction` pending / confirmed / failed, money only on new-or-pending → confirmed under row locks on the invoice, the link and the transaction row, `record_customer_payment` called unchanged with the unique `PAYMOB-<transaction id>` as the second guard, `InvoiceSettlement` source `payment_link` for `min(taken, remaining_due − applied)`, the fee booked as `bank_fee` (5212) at payout); no money posts from the return page. Deviations from the text below, recorded here: (1) the app is `payments` (not `payment_links`), with the adapter interface in `payments/providers/` (`base.py`, `paymob.py`, `mock.py`): the mock **is** the Paymob adapter with only its one HTTP helper and checkout address swapped, so tests exercise Paymob's real HMAC check and callback reading; the mock's payments are numbered `MOCKPAY-<id>`; (2) **no `Gateway` table:** the gateway account is the invoice entity's one active bank-kind cashbox whose Arabic name starts «بوابة الدفع» (none or two refuse in both languages), fixed on the link when it is made (HG-034); the provider is a code on the link and the transaction row, and transaction ids are unique per provider; (3) a real Paymob call needs `PAYMOB_LIVE=True` **and** the four keys in the server's environment (`config()` in `config/settings.py`; `.env.example` lists the names empty); `PAYMOB_LIVE` with a key missing connects nothing (never the mock); `PAYMENT_GATEWAY_MOCK=True` (development) selects the mock; otherwise the feature shows «غير موصّلة», no link is made and every callback is refused; an empty HMAC secret verifies nothing; (4) S4 builds **invoice links only**: a deposit link (HG-069) and an on-account link are not built yet; (5) the link is sent by a WhatsApp click and a copy button on the invoice page (as the invoice's own share link), not through the messaging outbox, which would need a new `MessageKind` and a `messaging` migration; (6) the staff box «ابعت لينك دفع» is on the posted invoice page and on the staff work-order page once the order has an invoice; the customer's work-order page (S2) shows «ادفع أونلاين» only when the shop already sent a live link; no public page makes a gateway order; (7) the capability `payment_links` («لينك الدفع أونلاين», OPTIONAL everywhere, not `legacy_on`, off by default) gates making links, the pay button and the `/payments/` staff screen; **the webhook is not gated**: money the gateway confirms for a link already sent is recorded whatever the switch says now (the deposits pattern); (8) the payout is a small staff screen (`/payments/`, `cashboxes.move_cash`), not the banking screen: it books `TRANSFER` (gateway → a bank of the same entity, net) and `BANK_FEE` (fee) through `create_cashbox_operation`, each with its own reference because `reference_number` is unique; (9) a void, refund or child transaction (`is_voided`, `is_refunded`, `has_parent_transaction`) is read as failed: it never posts money and, after a confirmation, flags the row for review; a second confirmed transaction on a link already paid records the money, settles only what room is left, and is flagged; a transaction already failed stays failed: a later pending is a logged no-op and a later confirmed is flagged for review, never posted; a posting that cannot be made (for example the unique payment number already taken, or a closed month) rolls back to a savepoint, flags the row and keeps the callback's audit row, and the row stays without a payment, so the gateway's next confirmed retry of that transaction tries the posting again (still at most once); (10) cancelling a link-paid invoice removes its settlement row through S5's existing hook (`settlements.release`), so the payment stays as the customer's credit on account; the link keeps its «paid» status; (11) the pay page refuses to send the customer on when the link's amount is above what the invoice still asks for (collected at the counter meanwhile) and the gateway's fixed return URL (`/pay/return/paymob/`) finds the link by our random `merchant_order_id` only, then shows the status page (without it, a neutral «بنستنى التأكيد» page; whether Paymob's return carries `merchant_order_id` is to be checked on the live account); (12) the rate limit on the pay pages (60 a minute) and the webhook (600 a minute) is a per-process cache counter per address; (13) the HMAC field list is Paymob's documented "transaction processed callback" order, pinned by a known-vector test; the Paymob developer site was not reachable from the build session, so check the list against the dashboard's docs before switching `PAYMOB_LIVE` on; (14) after Codex's review of PR #315: a callback finds its link **only** by the gateway order id stored when the link was made (`order.id`, which the HMAC covers), never by `merchant_order_id` (not signed), so a valid callback for another order cannot be steered to one of our invoices; an unknown order is audited and flagged, with no money; and, unlike «Amount mismatch» under Risks below, a confirmation whose `amount_cents` or currency differs from the link's records **no money**: the row is flagged for review (the gateway order was made for exactly the link's amount, so a different figure is a person's job, not the machine's); the «مبلغ مختلف» link status is therefore not set by S4; (15) expiry is a conditional update (`status = new` in the WHERE), so it never overwrites a link a confirmation marked paid meanwhile, and only an expiry that happened is audited; (16) after the independent review of PR #315: a system check refuses to start with `PAYMENT_GATEWAY_MOCK` on and `DEBUG` off (`payments.E001`) or with `PAYMOB_LIVE` on and a key missing (`payments.E002`); the mock checkout page answers 404 for a link that is not payable and uses one transaction id per link, so a second press is a repeat; a callback whose `merchant_order_id` is present but not the link's own is an unknown order, and a transaction row first logged for no link (or another link) never posts on a replay; a child transaction (`has_parent_transaction`: refund, void or capture) is flagged for review and moves nothing, and a failure on a link already paid is flagged too; an authorisation without capture (`is_auth` true, `is_capture` false) is pending, not money; the payout needs the gateway's payout reference and the same reference is refused a second time. Noted, not changed: the rate limit counts per `REMOTE_ADDR`, so behind a proxy all visitors share one bucket (the limits are generous); `create_link` holds the invoice's row lock while it calls the gateway (a few seconds at worst, and it keeps the one-live-link rule simple). Tests: `payments.tests`.
- **The question for Ahmed:** «فلوس لينك الدفع تتسجل بس لما Paymob تأكد إنها اتدفعت، وتنزل في حساب بنكي اسمه «بوابة الدفع»؛ ولما Paymob تحوّل لنا الفلوس نسجل عمولتها مصروف بنكي. موافق؟»
- **Why it is a gate:** money recorded from a message sent by a machine outside Hesba. If a webhook is forged, replayed or arrives twice, a customer's balance and a bank balance are wrong. The recording itself is a customer payment (a protected path, called).
- **What the code does today (checked):**
  - `cashboxes.CashboxKind.BANK` exists (T3, HG-055); the projector books a bank box in 1102 (`Projector.KIND_CONTROLS`). A `bank_fee` cashbox operation is booked in 5212 bank charges by `_place_cashbox_operation`. `banking.services.record_fee` only works from an imported `StatementLine`, so the settlement step calls `cashboxes.services.create_cashbox_operation(..., CashboxOperationType.BANK_FEE, ...)` directly; it requires `cashboxes.move_cash`.
  - `record_customer_payment` has no idempotency of its own; its unique `payment_number` is the hook. `CustomerPayment.customer` is required, so a link always names a customer; the till's walk-in customer gets no link (its balance must never carry money).
  - **The invoice's figures are frozen at posting** (HG-069): `remaining_due` never falls when a link is paid, so without a cap two links on one invoice could each collect the full amount, and the four `remaining_due` reports would overstate what is due.
  - `PUBLIC_BASE_URL` exists (`config/settings.py`, `render.yaml`), and `settings_core/daily_email.py` already builds links from it.
  - Secrets come from the environment through `config(...)` (`einvoice/portal.py`, `ETA_*`); `einvoice/tests_portal.py` tests against a `FakePortal` shaped like the real API, patched over `einvoice.portal._http`, the one HTTP helper.
- **Proposed design (A, recommended): a gateway bank account; a payment only on a verified, confirmed webhook; one live link per invoice, capped.**
  - A new `payment_links` app: `Gateway` (Paymob now: name, the bank-kind `Cashbox` «بوابة الدفع – Paymob» it settles into, active), `PaymentLink` (customer, amount, what it pays: a posted invoice, a deposit (HG-069) or on account; our order id; status new / paid / failed / expired; gateway order id; random token), `GatewayTransaction` (gateway, transaction id **unique**, outcome pending / confirmed / failed, last raw payload, HMAC ok, OneToOne to the `CustomerPayment` it made; every callback received is also kept as an audit row, so the unique transaction row is the state and the audit rows are the log).
  - **Cap and one live link:** for an invoice, `amount ≤ remaining_due − applied(invoice)` where `applied` is the HG-069 helper (paid links, deposits and card redemptions together); a second link while one is `new` is refused (a partial unique index on `(invoice, status="new")`). An unpaid link is expired, not deleted.
  - **Make a link:** the server creates the Paymob order and payment key through an adapter (`paymob.py`: `create_order`, `payment_key`, `verify_hmac`, `parse_callback`) and returns `PUBLIC_BASE_URL + /pay/<token>/`, never `request.get_host()`. The link goes out through the messaging outbox (T9).
  - **Webhook (`POST /pay/webhook/paymob/`):** CSRF-exempt, no login. Verify the HMAC (SHA-512 over Paymob's documented field order with `PAYMOB_HMAC_SECRET`); on mismatch answer 400 and write an audit row. Then, in one transaction, with `select_for_update` on the link and on the `GatewayTransaction` row for that transaction id: **what is deduplicated is the financial posting, not the notification.** Paymob sends more than one callback per transaction (`pending=true` first, then `success=true`, and retries of either), so a first-callback-wins rule would store the pending one and throw the confirmation away: the customer pays and nothing is recorded. Instead, `get_or_create` the transaction row by its unique id and read the payload's outcome: `pending` (success false, pending true), `confirmed` (success true, pending false) or `failed` (success false, pending false). The money moves on exactly one transition, new or pending → confirmed, taken under the lock: there `record_customer_payment(payment_number=f"PAYMOB-{txn_id}", payment_date=today, customer, cashbox=gateway box, amount=Decimal(amount_cents) / 100)` is called, not changed, the row stores the payment, the link becomes paid, and for an invoice link its `InvoiceSettlement` is written (a deposit link writes the HG-069 row too). The unique `payment_number` is a second guard: a double posting raises instead of duplicating. Everything else is a recorded no-op 200: a repeat `confirmed` (the row already holds a `customer_payment`), `confirmed → pending` (a confirmed row never goes back; the late pending callback goes to the audit log only), a repeat `pending`, and `failed` (recorded; the link becomes failed only while it is still `new`; a `failed` after `confirmed` is logged and flagged for review, never reversed by the machine).
  - **Return page (`/pay/<token>/done/`):** reads status only; it never records anything, because the customer's browser is not trusted.
  - **Settlement:** when Paymob pays out, from the banking screen: `create_cashbox_operation(TRANSFER, gateway box → real bank, net)` and `create_cashbox_operation(BANK_FEE, gateway box, fee)`, both under `cashboxes.move_cash`. The gateway box's balance is what Paymob still owes. Existing services, called.
  - **Secrets:** `PAYMOB_API_KEY`, `PAYMOB_HMAC_SECRET`, `PAYMOB_INTEGRATION_ID`, `PAYMOB_IFRAME_ID` through `config()`, never a model field. With a key missing the capability shows «غير موصّلة» and no link is made.
  - **Mocks:** a `FakePaymob` returning Paymob's real JSON shapes (auth token, order, payment key, callback) for every test; a live call needs Ahmed's account.
- **Alternatives rejected:**
  - **B — record the payment when the customer lands on the return page:** forgeable; a customer could mark their own invoice paid.
  - **C — a cashbox kind `gateway`:** nothing distinguishes it from a bank account in the books; a bank box named per gateway gives the same report and reuses the T3 statement import for the payout.
  - **D — book the fee per transaction:** Paymob states the fee at payout, and per-transaction fees would need a fee per webhook with rounding; at settlement the fee is one known figure.
  - **E — lower `remaining_due` when the link is paid:** breaks `clean()`'s invariant (HG-069, alternative D).
- **Files:** a new `payment_links` app (models, migration, adapter, services, views, urls, templates, tests); `config/urls.py`, `config/settings.py` (the four `config()` reads); `settings_core/capabilities.py`; `templates/sales/detail.html` («ابعت لينك دفع», beside «ابعت مع شركة شحن»); the messaging outbox for sending; `settlements` (HG-069) for the applied amount. `sales.services`, `cashboxes.services`: called, not changed.
- **Risks:**
  - **Replay and duplicates:** the unique transaction id, the lock on the link and on the transaction row, and the single new/pending → confirmed transition; a repeat of any callback returns 200 and posts nothing, while a confirmation that follows a pending callback still posts, once. A webhook for an unknown order is recorded and ignored.
  - **Amount mismatch:** Paymob confirms `amount_cents`; if it differs from the link, record what was taken, mark the link «مبلغ مختلف», never adjust the invoice.
  - **Closed month:** the payment is dated the day the webhook arrives, so the month is open; an old webhook replayed after month end is a no-op, not a reversal. A refund is `cancel_customer_payment` (HG-068 can hold it; it reverses on the payment's own date), with the gateway refund done by hand from the Paymob dashboard.
  - **VAT:** none here; the invoice was taxed when posted. Paymob's own fee invoice carries VAT the shop pays; it is booked inside the fee in 5212 unless the accountant wants the input VAT split (a `taxes` change, separate).
  - **Rounding:** `amount_cents` is an integer and the conversion is `Decimal(amount_cents) / 100`, so none. **Currency:** EGP only in S4.
  - **Public endpoints:** rate-limit the webhook and the pay page; the token in the URL is the only key, so a link expires (default 72 hours).
- **Hidden decisions stated:** the gateway's money is a bank account of ours from the moment Paymob confirms, not when it pays out; the fee is a bank charge, not a sales discount; a walk-in sale cannot be paid by link.
- **Tests to write:**
  - a valid confirmed webhook: customer balance −A, gateway bank box +A, one `CustomerPayment`, trial balance balanced, `ledger.reports.reconciliation()["ok"]`;
  - pending then confirmed for one transaction id: nothing recorded after the pending callback, exactly one `CustomerPayment`, a paid link and one settlement row after the confirmed one, 200 both times;
  - confirmed then pending: the payment, the link and the settlement row are unchanged, the row stays confirmed, 200;
  - a double confirmed (the same webhook twice): one payment, one settlement row, 200 both times; pending twice: nothing recorded;
  - bad HMAC: 400 and nothing recorded; `success=false`: link failed and nothing recorded; `pending`: nothing recorded; `failed` after `confirmed`: nothing reversed, the row is flagged for review;
  - the link URL starts with `PUBLIC_BASE_URL`, not the request host, under `override_settings`;
  - cap: a second link while one is live is refused; after a paid link of A, a new link may be at most `remaining_due − A`; a link above the remaining due is refused;
  - the invoice list total, digest, reps report and till day totals show due = `remaining_due − A`; the reps report and the digest count A once, as the `PAYMOB-` payment on its payment date, and `paid_now` sums are unchanged (the HG-069 one-count regression test, with the link's payment in the deposit's place);
  - settlement: gateway box 0, real bank +net, 5212 +fee;
  - a deposit link writes the HG-069 row; a link for the walk-in customer is refused;
  - no secret in any model, migration or fixture (a grep in the test); permissions (`sales.receive_customer_payment` to make a link, `cashboxes.move_cash` to settle; the webhook needs none).
- **Out of scope:** card-present terminals, instalment plans through the gateway, automatic refunds through the Paymob API, Fawry and other gateways (the adapter interface is written so they slot in), multi-currency.

## HG-071 — Gift cards and memberships (S6)

- **Status:** implemented (S6); merged under Ahmed's 8 Nov go-ahead after independent review; pending his review. Deviations D1–D6 and open questions are under «As built (S6)» at the end of this entry. Approved on 8 Nov 2026 under Ahmed's blanket go-ahead («متساناش علي موافقتي خالص واعمل الصح كله … وبعد كدة هنبقي نراجع واحدة واحدة»), after independent review by Fable and Codex; listed for Ahmed's later review in `docs/MAESTRO_LOG.md`. This records the money decision before S6 (PR order 10). It reuses `settlements.InvoiceSettlement` from HG-069. It is the only one of the three that asks for changes inside protected services: three of them, listed below.
- **The question for Ahmed:** «فلوس كارت الهدية مش مبيعات يوم البيع، بتتحسب لما حامله يدفع بيه فاتورة؛ والكارت اللي ميعاده عدّى مش بيتحول إيراد غير بقرار منك. موافق؟»
- **Why it is a gate:** selling a gift card takes cash for nothing delivered yet; redeeming it pays an invoice without cash. Today every pound an invoice collects is `paid_now` into one cashbox (`post_sales_invoice`), and `agreements.Package` books a prepaid package as revenue the day it is sold (its docstring says so). A gift card must not be a sale, and its redemption must not be a discount (4103) or loyalty points (HG-058).
- **What the code does today (checked):**
  - A `Cashbox` kind decides which account its money sits in (`Projector.KIND_CONTROLS`): courier boxes in 1120, cheque boxes in notes. **Precedent for a box that runs negative:** issuing a cheque (`cheques/services.py`, `issue`, around line 293) records a supplier payment *out of* the `cheques_out` box with no balance check, so that box's negative balance is the notes-payable liability (`KIND_CONTROLS["cheques_out"] = "notes_payable"`). The gift-card box follows it.
  - `HELD_KINDS` (`cashboxes/models.py`) is read in one place, `entities.scope.cashboxes` (`entities/scope.py` line 78), and every picker goes through it: the cashbox report (`reports/selectors.py` line 256, which `closing/services.py` line 153 sums), the shifts screens (`shifts/templatetags/hesba_shifts.py`, `shifts/views.py`), the till, the invoice and payment forms. One exception: `ledger/reports.py` line 110 builds `cash_report` from **every** `Cashbox` row, so the ledger side of that check must add the new control, as T14 did for couriers.
  - `cashboxes.services.create_cashbox_operation` and `cancel_cashbox_operation` both start with `_require_move_cash_permission` (`cashboxes.move_cash`). The first refuses a transfer whose source would go negative («The source cashbox cannot become negative»); the second refuses reversing a `direct_in` when the box would go negative (around lines 401–403). On a box that is negative by design, both refuse. `cancel_cashbox_operation` takes a `reversal_date` (a dated reversal, checked against its own month), unlike `cancel_customer_payment`, which reverses on the original date.
  - A direct in or out with nothing behind it is booked to the owner's current account (3102) by `_place_cashbox_operation`.
  - `sales/pos.py` line 237 refuses `paid_now < total` for the walk-in customer (`err_walk_in_credit`), so a walk-in cannot leave any part of a sale as `remaining_due`, which is how a redemption would be carried.
  - **The invoice's figures are frozen at posting** (HG-069): a redeemed part left as `remaining_due` stays there; the `settlements` table is what shows and reports it as paid.
  - `promotions.Promotion` has no customer or membership scope, and `promotions.services.apply(lines, now, coupon, lang)` does not take the customer. `agreements.ServiceContract` bills a fixed amount per period through the ordinary invoice.
  - `taxes.services.compute_lines` taxes every invoice line at the item's rate. A card sold as an invoice line would be taxed at sale and again at redemption.
- **Proposed design (A, recommended): a gift-card box whose negative balance is the liability, sold and redeemed through its own service.**
  - A new cashbox kind `gift_card` (one box per entity, added to `HELD_KINDS`), a chart row `2110 كروت هدايا غير مصروفة` with control `gift_card_liability`, and `KIND_CONTROLS["gift_card"] = "gift_card_liability"`; `ledger/reports.py` adds that control to the "cash" check.
  - A new `giftcards` app: `GiftCard` (code, face value, buyer customer optional, holder name and phone, expiry, status) and `GiftCardEntry` (card; kind sold / redeemed / breakage / reversed; amount; the cashbox operation or customer payment behind it). A card's balance is its entries; the box balance must always equal minus the sum of live card balances. The buyer gets a printed receipt of the card (number, value, expiry), not an invoice.
  - **Sell** (`giftcards.services.sell`): `create_cashbox_operation(TRANSFER, source=gift-card box, destination=cash or bank, amount=V)`: cash +V, box −V, booked Dr 1101/1102, Cr 2110. No invoice, no revenue, no VAT.
  - **Redeem** (`giftcards.services.redeem`, called by the till and the invoice form): the invoice posts with `paid_now` = the cash part only and the card part R left as `remaining_due`; in the same transaction `record_customer_payment(cashbox=gift-card box, amount=R)` and an `InvoiceSettlement(source=gift_card)`: customer due −R, box +R, booked Dr 2110, Cr 1103. Partial redemption is a smaller R; the rest stays on the card. The holder is whoever presents the code.
  - **Breakage** (`giftcards.services.breakage`): `create_cashbox_operation(DIRECT_IN, gift-card box, reason)` with a breakage entry; the projector books its counter-line to 4201 sundry income instead of 3102 when the operation id is in the breakage set (the pattern of `_payroll_ops`). Manual only, with a reason, through `approvals.gate.held` under a new action `giftcards.breakage`. Never automatic on expiry.
  - **Reversals:** a mistaken redemption is `cancel_customer_payment` (on the payment's own date); a mistaken sale or breakage is `cancel_cashbox_operation` on a chosen `reversal_date`; both exist, both holdable.
  - **Membership:** a `ServiceContract` (billed as today) plus a new nullable `Promotion.membership_service` (the contract's service item); `promotions.services.apply` gains an optional `customer` and counts such a promotion only for a customer with an active contract on that service. No new money path.
- **The three protected changes, counted honestly:**
  1. **The negative-balance guard** in `create_cashbox_operation` (transfer source) and in `cancel_cashbox_operation` (reversing a `direct_in`) skips boxes of kind `gift_card`, through one kind-aware flag (`LIABILITY_KINDS` beside `HELD_KINDS`), because for them a negative balance is the point. Without the second half, reversing a breakage is refused.
  2. **The permission.** Both services demand `cashboxes.move_cash`; giving that to cashiers would open arbitrary cash moves. `create_cashbox_operation` and `cancel_cashbox_operation` gain a keyword `permission_checked=False`; only `giftcards.services` passes `True`, after checking its own new permissions `giftcards.sell` (owner, manager, cashier roles) and `giftcards.breakage` (owner, manager). Two new permissions and a seed migration are a change to the permission core.
  3. **The walk-in at the till.** Recommended: relax `sales/pos.py` so the walk-in rule reads `paid_now + card part < total`, with the redemption written in the same transaction, so the walk-in still never owes anything at the end of a checkout. Trade-off: one more condition inside the till's protected checkout, and a test that the walk-in's balance is exactly zero after every checkout. The alternative, a named customer «حامل كارت هدية» per entity, needs no till change but puts every card sale on one receivable party, shows that party in aging, the digest and the customer report, and loyalty and promotions must learn to skip it like the walk-in; it was rejected for that.
  Everything else is additive: a kind, a chart row, one `KIND_CONTROLS` entry, one projector branch, one control in the reconciliation sum, the settlement rows.
- **Alternatives rejected:**
  - **B — the card as a tax-exempt service item on an invoice, re-placed to 2110 by the projector:** the sales reports, the profit report and the e-invoice would still count it as a sale, and the projector would be fighting the sub-ledger.
  - **C — redemption as a discount or as loyalty points:** 4103 falls while 2110 stays; the liability is never released and revenue is understated.
  - **D — a customer payment from the buyer booked as an advance (the HG-038 pattern):** the buyer's card shows a credit they do not hold, and a holder who is not the buyer cannot use it without a ledger transfer between two customers, which no service does.
  - **E — a brand-new money module with its own movement tables:** a second reversal, closed-period and report path for what the cashbox services already do.
  - **F — give cashiers `cashboxes.move_cash`:** opens direct cash in and out and transfers to everyone who sells a card.
- **Files:** `cashboxes/models.py` (the kind, `HELD_KINDS`, `LIABILITY_KINDS`, migration), `cashboxes/services.py` (the two guards, `permission_checked`), `permissions/` (the two permissions and their seed migration), `ledger/chart.py` (2110), `ledger/projector.py` (`KIND_CONTROLS`, the breakage branch), `ledger/reports.py` (the "cash" sum gains `gift_card_liability`), a new `giftcards` app (models, migration, services, views, templates, the card receipt, tests), `sales/pos.py` (the walk-in rule, the card as a payment method) and `templates/sales/pos.html`, `sales/views.py` and `templates/sales/form.html` (the invoice form), `promotions/models.py` and `promotions/services.py` (the membership scope, migration), `approvals/actions.py` (`giftcards.breakage`), `settings_core/capabilities.py`, `settlements` (HG-069).
- **Risks:**
  - **Double posting:** redeem and post are one transaction; the card row is locked (`select_for_update`), so two tills cannot spend the same balance, and a card is spent at most to its balance.
  - **Closed month:** sale, redemption and breakage are dated today. A redemption is reversed on the payment's own date (refused once that month closes); a sale or breakage is reversed on a chosen open date.
  - **VAT:** none at sale (no invoice); the invoice at redemption is taxed as any sale. That is the usual treatment of a multi-purpose voucher; the accountant confirms it for the business. Breakage income carries no VAT.
  - **Reports:** the gift-card box is kept out of the cashbox report, closing and shifts by `HELD_KINDS`; the ledger "cash" check adds its control. The till day totals, digest, reps report and invoice list subtract the redeemed part from what is due through `settlements`; it is collected once, as the redemption payment on the gift-card box, which the reps report and the digest already count. A walk-in's redemption payment belongs to the walk-in customer, who has no rep, so it is in no rep's `collected`, as for any walk-in payment today.
  - **Rounding:** none; whole piastres. **Expiry** is shown, not enforced, until breakage is approved.
- **Hidden decisions stated:** cashiers get a new permission to sell and redeem cards, not the cash-moving one; a card's expiry alone never makes income; the walk-in rule at the till is loosened only by the card part of the same checkout; the buyer gets a receipt, not an invoice, and no VAT at that moment.
- **Tests to write:**
  - sell V: cash +V, box −V, 2110 credit V, no revenue, no `SalesLineTax` row, trial balance balanced, `ledger.reports.reconciliation()["ok"]`; a cashier with `giftcards.sell` and without `cashboxes.move_cash` can sell; one with neither cannot;
  - redeem R < V on an invoice of P with cash C = P − R: cashbox +C, box −(V − R), customer balance 0, 2110 credit V − R, revenue P net of VAT, stock out as usual; the invoice row keeps `remaining_due = R` while the invoice list total, digest, reps report and till day totals show due 0, with R as the applied figure and `paid_now` sums still C; for a named customer of the rep, the rep's `collected` is C + R counted once (the HG-069 one-count regression test, with the redemption payment in the deposit's place);
  - the walk-in: a checkout of P with cash C and card R posts, and the walk-in's balance is exactly 0 after it; a checkout short of the total with no card is still refused;
  - redeem above the balance refused; two tills on one card: one wins;
  - breakage of the rest: box 0, 2110 = 0, 4201 +(V − R); held when the owner holds the action; reversing it on a later date is allowed and puts the liability back;
  - cancel the redemption: box and customer due back; cancel the sale: cash back;
  - the box never appears in a cashbox picker, as a sales invoice's `cashbox`, in the cashbox report, in closing's cashbox total or in a shift;
  - membership: a customer with an active contract gets the promotion's discount; one without, or with a paused contract, does not; the invoice records it in `InvoicePromotion`;
  - permissions: `giftcards.sell` for selling and redeeming, `giftcards.breakage` for breakage; a direct cash operation still needs `cashboxes.move_cash`.
- **Out of scope:** physical card printing beyond the receipt, buying a card online (HG-070 could sell one later), reloading a card, membership tiers, moving balance between cards, automatic expiry.
- **As built (S6), branch `claude/maestro-s6-giftcards`:**
  - **Design A as approved.** A cashbox kind `gift_card` (in `HELD_KINDS`, and in a new `LIABILITY_KINDS`), one box per entity made on its first card sale (`GIFT-CARDS-<entity>`); chart row `2110 كروت هدايا غير مصروفة` (control `gift_card_liability`); `KIND_CONTROLS["gift_card"]`; the projector books a breakage operation's counter-line to 4201 (`other_income`), keyed on the operation id whatever its status, so a reversed breakage's entry also lands there; the fingerprint follows breakage entries; `ledger/reports.py` adds the control to the "cash" check.
  - **The `giftcards` app:** `GiftCard` (sequential `GC-00001` number, a random 12-character code from an unambiguous alphabet that the holder presents, entity, face value, optional buyer, holder name and phone, expiry, status) and `GiftCardEntry` (sold / redeemed / breakage / reversed, with the cashbox operation, customer payment and invoice behind it). A card's balance is read from the entries whose backing document is still posted, so every reversal path keeps it right on its own; a reversed entry is history only. The box always equals minus the live cards' balances (asserted in the tests).
  - **Services:** `sell`, `redeem` (lock order: the invoice, then the card with `of=("self",)`; balance and due checked under the lock; payment numbers `GCR-YYYYMMDD-NNN` from `next_in_series` with a retry), `cancel_redemption` (`cancel_customer_payment`, on the payment's own date), `breakage` (the whole rest, with a reason), `reverse` (a sale only while the card is untouched; a breakage on a chosen date). The capability `gift_cards` (off by default) gates every new action and screen.
  - **The three protected changes, exactly:** (1) `create_cashbox_operation` skips the negative-source guard for a *transfer* out of a `LIABILITY_KINDS` box, and `cancel_cashbox_operation` skips the guard when reversing a `direct_in` into one (the transfer-reversal guard on the real cash side is unchanged); (2) both gain a keyword `permission_checked=False`, passed `True` only by `giftcards.services` after `giftcards.sell` (owner, manager, cashier) or `giftcards.breakage` (owner, manager), seeded by `permissions/0012`; (3) `sales/pos.py`: the walk-in rule reads `paid_now + card part < total`, the card part is left as `remaining_due` and `redeem` runs inside the checkout's transaction, so a refused card takes the whole sale back. With no card the till computes exactly as before.
  - **Membership:** `Promotion.membership_service` (nullable, `promotions/0002`); `promotions.services.apply(..., customer=None)` counts such a promotion only for a customer with an `ACTIVE` `ServiceContract` on that service whose dates cover the day. The till's quote, the till's checkout, the invoice form and the offline sync all pass the customer, so the till's confirmed quote and its checkout agree.
  - **Migrations:** `cashboxes/0017_gift_card_kind` (choices), `giftcards/0001_initial`, `permissions/0012_seed_giftcard_permissions`, `promotions/0002_membership_service`. No data migration: a company with the capability off gets no box and no row.
  - **Deviations (recorded under the delegation, for Ahmed's review):**
    - **D1, no redemption across entities.** HG-071 is silent; a card pays invoices of the entity whose cashbox took its money only, refused in both languages otherwise, so 2110 and its box never straddle two entities' books.
    - **D2, the invoice side is the posted invoice's page,** a «ادفع بكارت هدية» box (same `remaining_due − applied` as payment links), not the draft invoice form; the till redeems in its checkout transaction.
    - **D3, cancelling an invoice paid by card cancels its redemption payments** in the same transaction (the deposits hook drops the settlement rows; without this the card would stay spent and the customer, the walk-in included, would keep a credit). A closed month refuses the whole cancellation, as for any payment.
    - **D4, a second approval action `giftcards.reverse`** for reversing a card's sale or breakage through the card's page (so the untouched-card check always runs), beside the approved `giftcards.breakage`; a redemption's cancellation is held as the existing `sales.cancel_payment`. The generic cashbox-operations screen refuses to reverse a gift-card operation and points to the card's page.
    - **D5, the offline till** refuses to save a sale with a gift card on the device (as it does for points): a card is spent under its row lock, online only.
    - **D6, the cashbox form** no longer offers the `gift_card` kind: only the gift-card screens make that box.
    - **D7, no sales return on an invoice a gift card paid (a fourth protected change, from the independent review).** No return-to-card path exists yet: the unchanged return service would refund the card's share as cash or as the customer's credit (on a walk-in, a credit nobody can spend) while the card stays spent. `create_sales_return` now calls `giftcards.guards.before_sales_return(invoice)` right after its posted check, under the invoice's lock (as the e-invoice guards are called), and refuses while a redemption on the invoice is live, with an Arabic version in `settings_core/ui_messages.py`. The route: a named customer's card payment is cancelled first, then the return goes through as for any credit sale; a walk-in's sale is cancelled whole (D3 gives the card its money back). `cancel_customer_payment` likewise calls `giftcards.guards.before_customer_payment_cancel` and refuses a walk-in's card payment while its invoice is posted (the invoice's own cancellation still cancels it). Tests: `giftcards.tests.ReviewFixTests`.
    - **D8, no card on an invoice with an active instalment plan (Codex on #323).** Instalment collections are ordinary customer payments linked to the plan: they neither lower `remaining_due` nor settle the invoice, so a card there could pay what was already collected (a 100 invoice with 60 collected still accepted 100). `redeemable_due` returns 0 while an active plan exists; such an invoice is paid by its instalments. A card used before a plan is made is already subtracted by `create_plan` (`settled_on`). Test: `test_a_card_cannot_pay_an_invoice_on_an_instalment_plan`.
  - **Open questions:** the S6 design canvas has no gift-card board, so the till got one «كارت هدية» row (code and amount) beside «صرف نقاط», shown only while the capability is on, with existing classes and tokens; Ahmed or design to confirm its place. A return that refills the card is a decision still to make; until then D7 refuses the return (without D7, a walk-in sale of 300 paid 100 cash + 200 card, returned in full, refunded 100 cash and left a 200 walk-in credit nobody could spend, the card staying spent). A till sale paid wholly or partly by card still shows its `payment_status` as credit or partial: `SalesInvoice.clean()` ties that status to `paid_now`, so counting the card part as paid means a change inside sales posting, left for a decision (the till's own screen shows no «آجل» for the card part). "Active contract" means status active and inside its dates, not "its latest period is paid". The accountant confirms the multi-purpose-voucher VAT treatment.
  - **Independent review of `7a4e15a` (fixed):** returns refused (D7); the redeemable due is `remaining_due − applied − posted returns' due` in `redeem` and the invoice-page box (hidden at 0); `cancel_redemption` refused after a posted return, on a walk-in invoice, and without `sales.receive_customer_payment` beside `giftcards.sell`; codes masked to their last four on every screen, the full code shown once on the receipt right after the sale (a one-time session flag) and later only to `giftcards.breakage`, audited (`reveal_gift_card_code`); the offline sync refuses a `HELD_KINDS` box; a payment link settles under the invoice's row lock; `giftcards.reverse` is held per entry (its object is the `GiftCardEntry`); the breakage permission is checked before a request is held; the receipt follows the capability; the liability account's code is read from the chart; an import row never changes a `HELD_KINDS` box. Re-review (`e39e65b`): APPROVE WITH FIXES; its finding B fixed in `a7a1d3f` (the receipt is sent no-store; one-time reveals are kept per card, so two sales in two tabs each show their own code). Codex on #323: D8 above, and the till's «New cart» clears the gift-card code and amount.
  - **Tests:** `giftcards.tests` (41) and `giftcards.tests_concurrency` (PostgreSQL only: two tills on one card, one wins).
  - **Existing tests touched:** `ledger.tests` (a user's own 2106 now moves retention payable to 2111, since 2110 is taken), `promotions.tests` (the invoice form's script version). Also: the sidebar entry «كروت الهدايا» with its icon, the till and invoice-form scripts send the customer to the promotion quote; the till shows the gift-card row only to a user holding `giftcards.sell` (any other seller is refused with a message).

## HG-072 — S1 attachments, a new table for photos, signatures and files

- **Status:** OPEN PROPOSAL. Built on `claude/maestro-s1-attachments` and waiting for Ahmed's approval of the storage decision. It is additive only: one new table in a new app. No existing table, model, posting or report changed.
- **Schema (`attachments.Attachment`, migration `attachments/0001_initial`):**
  - owner: `content_type` (FK to `ContentType`, PROTECT) + `object_id` (`PositiveBigIntegerField`), with an index on both; a generic owner, so appointments and customers can use it later. Only work orders are wired now.
  - `entity` (FK to `entities.Entity`, null, PROTECT): copied from the owner, so the entity scope (HG-034) holds on the attachment itself.
  - `kind` (`photo` / `signature` / `document`), `stage` (`receive` / `deliver` / `other`).
  - `original_filename` (255, path parts stripped), `content_type_name` (sniffed from the bytes, never the client's claim), `size`, `sha256` (indexed).
  - `data`: `BinaryField`, the file's bytes.
  - `uploaded_by` (FK to user, PROTECT), `created_at`.
- **Why the bytes are in the database:** Render's disk is ephemeral and wiped on every deploy, so files on disk would vanish. Keeping them in the database needs no external account and is backed up with everything else. The admin shows metadata only (`data` excluded, no add/change/delete there).
- **Limits:**
  - `ATTACHMENTS_MAX_BYTES` (env, default 5 MB) per file; `ATTACHMENTS_SIGNATURE_MAX_BYTES` (default 512 KB) for a signature. Both are read at call time.
  - `ATTACHMENTS_ALLOWED_TYPES` (env, default `image/jpeg,image/png,image/webp,application/pdf`) can only narrow what the code can verify by magic bytes; it can never add a type.
  - Magic bytes are checked (JPEG, PNG with IHDR and IEND for a signature, WEBP, `%PDF-`). SVG and HTML are refused by name, declared mime and content.
  - **Review fix, signature PNG:** a signature must now be a displayable PNG, checked with the standard library only (Pillow is not a dependency): every chunk CRC, IHDR first and IEND last, at least one IDAT, width and height within 1 to 4000, and the IDAT stream inflating (capped at the expected size) to exactly the scanline bytes the header implies. Both signature paths (`add_signature_png` and `add_attachment` with kind signature) go through it, so an IHDR+IEND-only or corrupt-IDAT file is refused instead of becoming an undeletable, undisplayable signature.
  - A PDF containing `/JavaScript`, `/OpenAction`, `/Launch` or `/EmbeddedFile` is refused. `/JS` is left out, because it turns up by chance in compressed image data and would refuse real scans (a plain, case-sensitive bytes search; a name hidden with `#xx` escapes is not caught, which is why a PDF is only ever served as a sandboxed download).
  - The stored filename always takes the extension of the detected type (`report.hta` holding a PDF is kept and served as `report.pdf`).
  - The upload is capped before it is spooled: a `Content-Length` above the cap (plus 64 KB of framing) answers 413 without reading the body, and an upload handler stops one that passes the cap while streaming. CSRF is still enforced (the upload view is exempt only to install the handler, then checked by an inner `csrf_protect`).
  - **Storage caps:** `ATTACHMENTS_MAX_PER_OWNER` (default 20 files per record), `ATTACHMENTS_MAX_BYTES_PER_OWNER` (default 25 MB per record), and `ATTACHMENTS_GLOBAL_SHARE` (default 0.2): all attachments together may not pass that share of `DATABASE_SIZE_LIMIT_MB` (0 switches the guard off). The caps are checked under a lock on the owner row, in the same transaction as the insert.
  - **Review fix, global cap across owners:** on PostgreSQL the global check and insert also take `pg_advisory_xact_lock(7207201)` (after the owner row lock, released at commit), so concurrent uploads to different records can no longer both pass `Sum(size)`; SQLite already serializes writers, noted in the code.
- **Access:**
  - **See / list / read:** the user must be able to see the owner. For a work order this is `workorders.services.visible_to` (the sales-view permission, the technician scoping, the entity scope) in a workshop.
  - **Add (upload, signature):** seeing is not enough. It needs the work orders' own rule for any change: `sales.create_sales_invoice` (the counter), or the technician who owns the order. A view-only user gets 403.
  - **Closed orders:** a delivered or cancelled order takes no more attachments. An order that is still open, a ready one waiting to be handed over included, does, so the hand-over photo and signature (stage `deliver`) are taken before the delivery.
  - **Delete:** managers only (`sales.create_sales_invoice`); the owning technician may add but not delete. It is audited (`attachments / delete_attachment`, with the file's metadata and sha256 in `before_data`). The signature of a delivered order is never deleted.
  - Anyone else gets 404 (not found for them) or 403 (no right at all); anonymous is sent to login. The file is served with `nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, `Cache-Control: private, no-cache`, an ETag of the sha256, inline for images and as a download for PDF. Every upload writes an audit entry (`attachments / add_attachment`).
- **Money, stock, balances:** none. No invoice, cashbox movement, stock movement, journal entry or customer/supplier ledger row is created or changed. The tests assert the counts of all six are unchanged.
- **Risks:**
  - **Database growth.** At up to 5 MB a file, photos eat the plan fast: `DATABASE_SIZE_LIMIT_MB` is 500 by default, and `settings_core/storage.py` already shows usage, so 100 full-size photos is the whole plan. Phones should send downsized images (the later UI should resize on the client). The caps above (20 files and 25 MB per record, 20% of the plan overall) bound it, and the global guard measures `Sum(size)` of this table only. Recommended next step: a later move to object storage chosen from the environment (the service layer is the only code that touches `data`, so the switch is local).
  - There is no retention rule yet (only the audited manager delete), to be decided with the UI.
  - Postgres returns `data` as a `memoryview`; the serve view converts it with `bytes()`.
  - A PDF is served as a download with a sandboxing CSP, never rendered inside the app's origin.
- **Files:** `attachments/` (new app: models, services, views, urls, admin, migration, tests), `config/settings.py` (app and the `ATTACHMENTS_*` settings), `config/urls.py` (`/attachments/`).
- **Tests:** `attachments/tests.py`. They cover:
  - jpeg, png, webp and pdf kept with the right sha256, size and type;
  - oversize, a `.png` that is really HTML, SVG under any name, and a settings list that tries to allow SVG, all refused;
  - the signature data URL decoded, a non-PNG, truncated PNG or bad base64 refused, and the signature size cap;
  - another entity's order and file answer 404 (with a positive control for the user's own entity), a technician reaches only their own orders, a user with no right gets 403, an anonymous request goes to login;
  - a view-only user refused on upload and signature (with a positive control), a delivered or cancelled order refused, each storage cap, the audited delete and who may do it, the delivered order's signature kept;
  - the extension forced to the detected type, active PDFs refused, an oversize `Content-Length` and a streaming overrun refused with 413, CSRF still enforced;
  - the serve headers, the audit entry, and the unchanged money, stock and ledger counts;
  - Arabic and English error messages.

## HG-075 — S8: loyalty and message campaigns (a new table and a new outbox kind)

- **Status:** implemented (S8); merged under Ahmed's 8 Nov go-ahead after Codex review; pending his review. Additive only.
- **Reason:** services activity 17 («ولاء وحملات رسائل») had loyalty points but no way to write one message to a slice of customers. Queuing a campaign puts one message per customer on the existing outbox (T9, HG-064); sending stays the outbox's job.
- **Schema (migration `messaging/0004_campaigns`):** a new table `messaging.Campaign` (name, text, `audience`, `threshold`, nullable `category` FK to `master_data.Category` PROTECT, `scheduled_day`, `status` draft / queued / done, nullable `entity` FK PROTECT, `created_by`, `queued_by`, `queued_at`, `queued_count`, timestamps) and the new choice `campaign` on `MessageKind` (choices-only `AlterField` on `MessageTemplate.kind` and `OutboxMessage.kind`; no column or data change). No existing table, posting, ledger, stock, cash or permission row is touched; the audience is only read from customers, posted sales and the loyalty ledger.
- **Files:** `messaging/models.py`, `messaging/campaigns.py` (service layer), `messaging/campaign_views.py`, `messaging/services.py` (kind tables, `build()` carry, `mark()` day check), `messaging/urls.py`, `messaging/views.py`, `config/settings.py` (`MESSAGING_CAMPAIGN_DAILY_CAP`, env, default 50), templates `messaging/campaign*.html`, `static/hesba/css/campaigns.css`, `static/hesba/js/campaign_composer.js`, `messaging/tests_campaigns.py`.
- **Design:**
  - Audiences: every customer with a valid mobile; loyalty points at least N (SQL prefilter, then the spendable balance); no posted purchase in N days; bought a category (sub-categories included) in the last N days; bought it but not in the last N days. Never the inactive or walk-in customer; one message per mobile number.
  - Once only: key `campaign:<id>:<customer id>` and a locked draft-to-queued transition. The CAMPAIGN on/off switch is the kind's `MessageTemplate.active` (on the campaigns screen); off queues nothing and withdraws what waits.
  - Daily cap: shared by all campaigns of the entity; the overflow is given future outbox days, and `build()` moves unsent messages forward under the same cap. On PostgreSQL both allocation paths take `pg_advisory_xact_lock(7208001, entity id)` first, so concurrent requests cannot both spend the same room (no-op on SQLite).
  - A message for a later day cannot be sent early: `mark()` refuses `SENT` for a campaign message whose day is after today (the only kind ever queued ahead) (bilingual message, no WhatsApp redirect); `send_by_api` already sends only today's.
  - Permissions: editing and queuing need `settings.manage_settings` (the permission that edits the outbox wording); viewers with a customer-facing permission read the list and queued campaigns only. Entity: the campaign and its messages carry the entity it was made in (main when group-wide) and are visible only there (HG-034). Audit: `queue_campaign`, `save_campaign_settings`, `delete_campaign`.
- **Risks / open questions:**
  1. `master_data.Customer` has **no opt-out or consent field**, so none is honoured; adding one is a schema change left to Ahmed's decision.
  2. Customers are group-wide: the "all" and "points" audiences ignore the entity, while the purchase-based audiences read only the campaign's entity. A group-wide owner's campaign is a main-entity campaign.
- **Tests (`messaging/tests_campaigns.py`):** explicit audience counts and the exact day boundary; placeholders rendered; no duplicate queueing; cap, shared cap and carry-forward; kind off queues nothing; permissions (viewer 403, stock keeper 403); entity isolation; Arabic and English screens; no WhatsApp call while queuing; the cap lock SQL (mocked) on queue and carry; a deferred message refused by `mark()` and by a stale outbox page.

## HG-076 — Commission on delivered work orders in the technicians' report (S9)

- **Status:** implemented (S9); merged under Ahmed's 8 Nov go-ahead after Codex review; pending his review. A report calculation only: no schema, posting, stock, cashbox or ledger change.
- **Reason:** the technicians' report (`workorders:report`) showed productivity but no commission for the workshop technician; commission existed only for appointments (`docs/MARKET_PARITY_ROADMAP.md`, item 5). Report calculations are protected logic, so the change is recorded here.
- **Files:** `workorders/services.py` (`technician_report`), `workorders/views.py` and `templates/workorders/report.html` (three new columns and a note), `workorders/tests_technician_report.py`.
- **Design:** per technician, for orders delivered in the month, `commission = money_round(Σ appointments.services.net_sales(delivery invoice) × Employee.commission_percent / 100)`. `net_sales` is the helper appointments already use: the posted invoice before tax, less its posted returns (at the invoice's own tax rate). Only posted invoices count; a cancelled delivery reopens the order and earns nothing. An order with no technician is its own row and earns no commission. New row keys: `net_sales`, `rate`, `commission`. The existing keys (`count`, `billed`, `avg_days`, `returns`) are untouched; `billed` still does not net returns.
- **Open question for Ahmed:** the base is the whole delivery invoice, so parts are included, not only labour. Should commission be on labour (non-stock lines) only? Changing it later is local to `technician_report`.
- **Risk:** low and display-only. Nothing is paid or posted from this figure (payroll is separate). The overstatement risk is the parts-in-base question above.
- **Characterization tests (written first, passing before and after):** `ExistingFiguresAreUnchangedTests` pins count, billed before VAT, average days, warranty returns, that billed ignores a posted return, and the no-technician row.
- **New tests:** `CommissionColumnTests` with explicit amounts at 10 %: 2100 gives 210.00 and 300 gives 30.00; a posted return of a 300 labour line gives net 1800.00 and commission 180.00 with billed still 2100.00; a 0 % employee earns 0.00; no technician earns none; a cancelled delivery earns nothing; the page and its English version show the column.

## HG-077 — S3 online booking and working hours (additive tables)

- **Status:** implemented on `claude/maestro-s3-booking`, pending review. Additive only: four new tables in `appointments`, no existing table or column changed. No posting, stock, cashbox, ledger, cost or report logic touched; booking goes through the existing `appointments.services.book`.
- **Reason:** services item 14 («حجز أونلاين ٢٤ ساعة», `docs/MARKET_PARITY_ROADMAP.md`) needs working hours and a public page; neither existed. Schema changes are protected, so they are recorded here.
- **Schema (migration `appointments/0003_online_booking`):**
  - `BookingSettings` (one per entity): random `public_key` (the public link), `accepting` (default off), `slot_minutes` (30), `min_notice_minutes` (60), `max_days_ahead` (30), `show_prices` (default **off**), `updated_by`.
  - `WorkingHours`: entity, optional employee, weekday, opens, closes. Unique per (entity, employee, weekday) plus a conditional unique for the entity's own row (NULL employee), and a check `closes > opens` (no overnight windows). An employee's own week is clipped to the entity's.
  - `BookableService`: the services an entity offers online, each with its duration (5–720 min, the booking service's own bounds). Owners pick from non-stock items only.
  - `BookingStaff`: the employees who take online bookings in an entity.
- **Capability:** `online_booking` («الحجز أونلاين»), off by default (no preset), needs the appointments module. Its settings screen is `/appointments/booking/` (sales.create_sales_invoice **and** settings.view_settings: owners and managers, not cashiers).
- **Public page:** `/book/<key>/` and `/book/<key>/done/<signed>/`, link built from `PUBLIC_BASE_URL` only. 404 unless the capability and the module are on and the entity accepts. CSRF, honeypot, throttles (page views per address; bookings per address and per phone), no-store and noindex headers. The posted time is re-checked against a fresh slot list, then `services.book` runs while working in the page's entity, so `_check_overlap` locks the employee row and refuses a time taken meanwhile. "Any" tries each free employee in turn; a booking never goes out without an employee. The overlap message, which names the employee and the other times, is never shown to the public.
- **Customer:** matched by phone (`whatsapp_number` on both sides, phone or WhatsApp, active, never the walk-in customer) and left unchanged; else created with the usual `C-` series.
- **System user:** `created_by` must be a user, so public bookings are made by `hesba-online-booking`, created on first use, inactive, no usable password. It shows as a disabled user in the users list. Audit rows `book_online` carry no personal data.
- **Not done here:** no money online (S5's deposit policy is shown as text only); no confirmation message, because no outbox kind fits (the existing "appointment tomorrow" reminder already picks up these bookings).
- **Open questions for Ahmed:** (1) show service prices on the public page? Default hidden; one tick per entity. (2) Offer every non-stock service, or keep the explicit per-entity list (built)? (3) Add a confirmation-message kind to the outbox (an additive choice)?
- **Risk:** low. New tables only; the slot engine reads appointments; writes go through the existing booking service and its lock.
- **Tests:** `appointments/tests_booking.py` (36): slot engine (grid, closed day, overlaps by status, group-wide, previous-evening appointment, notice, window, employee hours, any, Cairo midnight, DB constraints), the booking service (entity, customer reuse and creation, walk-in, crafted times, stale slot under the lock), the public flow (404s, steps, prices hidden, end-to-end, race, any, crafted time, honeypot, CSRF, throttles, ar/en, no staff links or other customers' data, signed confirmation, deposit text only, next free time) and the settings screen (roles, save, refusals, link from PUBLIC_BASE_URL and renewal, feature off, branch picker).

## HG-078 — AI track: one provider layer, data leaving the server, spend and caps (A0)

- **Status:** IMPLEMENTED on claude/maestro-ai-a0-provider, pending review (independent review round 1 addressed, see below). A0 only: no A1–A3 feature and no language-model client (A2 adds `ANTHROPIC_API_KEY` and its provider). No model, no migration: it uses `AuditLog`, `SystemSetting` and `FeatureFlag`. No protected logic touched.
- **Files (A0):**
  - `ai/apps.py`, `ai/checks.py` (system checks `ai.E001`–`ai.E005`);
  - `ai/providers/__init__.py` (mode and the provider registry), `ai/providers/base.py`, `ai/providers/elevenlabs.py` (the live client), `ai/providers/mock.py`;
  - `ai/services.py` (the one door: transaction guard, kill switch, capability, request size, connection, locked cap reservation, audit);
  - `ai/no_network.py` (the AI tests' strict guard, loopback included), `ai/tests.py`;
  - `hesba_testing/network.py` (the run-wide guard: nothing but loopback), `hesba_testing/runner.py` (installs it, in spawned workers too, and puts the run on the AI mock with no key), `hesba_testing/tests_network.py`;
  - `config/settings.py` (`"ai"` in `LOCAL_APPS`; `AI_MODE`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID`, `AI_DAILY_CALL_CAP_MAX` via `config()`), `.env.example`;
  - `settings_core/capabilities.py` (`ai_voice_summary`), `settings_core/ui_messages.py` (Arabic refusals);
  - `settings_core/tests_ui_messages.py` (`ai` added to the scanned service apps), `settings_core/tests_capabilities.py` (one assertion).
- **As built:**
  - **Mode:** `AI_MODE` = `mock` | `live` | `off`. Empty means `mock` while `DEBUG` is on and `off` once it is off. `live` with the key, a real voice or the model missing connects nothing (never the mock), as Paymob does.
  - **Checks:** `ai.E001` an explicit `AI_MODE=mock` with `DEBUG` off (the production signal payments uses); `ai.E002` live without `ELEVENLABS_API_KEY`; `ai.E003` an unknown mode; `ai.E004` live with the voice empty or still the placeholder; `ai.E005` live with `ELEVENLABS_MODEL_ID` empty.
  - **One door (`ai.services.text_to_speech(text, feature=..., user=..., entity=...)`):**
    1. Inside a caller's `transaction.atomic()` it raises `AiCallInTransaction` (a developer error, no audit row), so a rollback can never erase the row of a call that may have been paid for. A Django `TestCase`'s own wrapping blocks are exempt, as Django exempts them for durable blocks.
    2. It refuses, in order: the kill switch (`SystemSetting ai_disabled`), the feature's capability, an empty text or one over 5000 characters, no provider connected.
    3. In a durable transaction holding `SELECT … FOR UPDATE` on a dedicated lock row (`SystemSetting ai_call_lock`, inactive, no value, created on demand), it reads today's count and either refuses at the cap or writes a `call_started` row. Check-and-reserve is therefore one at a time.
    4. The provider is called outside any transaction; then a separate `call_ok` or `call_error` row is appended, with `before_data={"started": <started row id>}`. **Any** exception from the provider is recorded as `call_error` first: a `ProviderError` becomes the user-facing `ValidationError` («خدمة الذكاء الاصطناعي مردّتش»), anything else is re-raised as it came.
    Every refusal is a `ValidationError` in English with its Arabic in `ui_messages`, and writes its own `call_refused` row.
  - **Registry:** `ai.services.FEATURES` maps each feature to the capability every one of its calls requires and to its provider; `ai.providers.PROVIDERS` maps each provider to its mock/live factory.
  - **Daily cap:** `SystemSetting ai_daily_call_cap`, default 50 (a bad value falls back to 50, a negative one to 0), never above `AI_DAILY_CALL_CAP_MAX` from the server's environment (default 500). Counted over the Cairo calendar day from the audit rows: only `call_started` rows count, one per call that reached the provider, so a call still in flight counts. Outcome rows and refusals do not.
  - **Audit (append-only, as `AuditLog` requires):** a refused call leaves one `call_refused` row. A call that reached the provider leaves its `call_started` row, never changed afterwards, plus one `call_ok` / `call_error` row pointing back to it (`before_data={"started": <id>}`). An unfinished call is a started row with no outcome row. Every row has `event_type=export`, module `ai`; actor; the entity in `object_type` / `object_id`; and in `after_data` the feature, provider, mode, entity, request size in characters and bytes, result and refusal reason; the outcome row adds the provider status, response size and duration in ms. The mode on started/ok/error rows is the provider actually used (`provider.live`), not the setting. Never the text, the audio, names, phones or amounts. No AI audit row is ever updated.
  - **ElevenLabs:** `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}` (voice quoted), header `xi-api-key`, JSON `{text, model_id, voice_settings?}`, `Accept: audio/mpeg`, over the standard library.
    - An opener that refuses every redirect: a 3xx becomes `ProviderError("ElevenLabs answered 3xx.")`, and the key is never re-sent elsewhere.
    - A 10 s timeout per socket operation, and a 20 s deadline checked against `time.monotonic()` after each read of the body. The body is read with `read1` (at most one socket read, whatever has arrived, up to 64 KB), so a slow drip cannot keep a read waiting for a full chunk. The clock starts before connecting, but the connect and the headers come before the body loop and are bounded only by the per-operation timeout, not by the deadline. With headers that arrive promptly the worst case is about 30 s (the deadline plus one read), under gunicorn's 60 s.
    - HTTP errors, network errors and cut-off answers (`http.client.HTTPException`) become `ProviderError` without the key, the text or the body. An answer that is not audio, or is over 10 MB, is refused.
    - The mock is the same class with only `_http` swapped, answering a fixed 1440-byte silent MP3 (ten MPEG-1 Layer III frames, 0.36 s; checked once with ffprobe).
  - **Capability:** `ai_voice_summary`, off by default, `available=False` and offered to no activity (`ONLY_FOR` empty), so no screen shows it and every A0 call is refused until A1 makes it available. *(Superseded by A1, HG-079: now `available=True`, still off by default, offered to every activity.)*
  - **Tests and the network:**
    - The whole run (`hesba_testing/runner.py`) installs a guard that makes `socket.create_connection`, `socket.socket.connect` / `connect_ex` and `urllib.request.urlopen` raise `ExternalNetworkBlocked` (not an `OSError`) for anything but loopback. It is installed again in each spawned parallel worker (`_pin_worker`); forked workers inherit it.
    - After the system checks, the run is put on `AI_MODE=mock` with an empty `ELEVENLABS_API_KEY` (settings overridden, and the environment set for spawned workers). The checks themselves still see the server's real settings, so `ai.E001` does not fire on the runner's own `DEBUG=False`.
    - Every AI test also runs under `ai/no_network.py`, which refuses loopback too and fails the test at cleanup if any attempt was made, even a caught one. A meta-test checks that every test case in `ai/tests.py` uses it.
- **Deviations from the brief and the proposal:**
  1. **Mock is the default only while `DEBUG` is on.** An unconditional mock default plus "refuse mock in production" would stop every Render build (`build.sh` runs `check --deploy` with `DEBUG=False`) and the test suite (Django's runner turns `DEBUG` off before the checks). So an empty `AI_MODE` is `off` in production, and only an explicit `mock` is refused there. The AI tests set `AI_MODE=mock`.
  2. Two extra checks: `ai.E004` refuses live with the placeholder voice and `ai.E005` with no model, as payments refuses live with any Paymob key missing.
  3. The cap is **per installation** (one client company, one key), not per entity. Each row records the entity, so a per-entity cap is a filter away. See open questions 1 and 4 below.
  4. `ai_voice_summary` is `available=False`, so `settings_core/tests_capabilities.py`'s "every capability is available" assertion now excludes it.
  5. ~~A refusal's audit row is written in the caller's transaction.~~ Resolved: a call inside a caller's `transaction.atomic()` is refused outright (`AiCallInTransaction`), and the reservation is its own durable transaction, so no caller can roll back an AI audit row.
  6. ~~Two calls racing at the cap can both pass.~~ Resolved on PostgreSQL: check-and-reserve runs under a row lock, and the started row counts at once. SQLite has no row locks and uses deferred transactions, so of two simultaneous reservations the second fails with «database is locked» (a 500 for that request) instead of a clean refusal. There is still no overshoot. Only the demo and the codespace run on SQLite; the database settings are unchanged.
  7. **AI demo (branch `claude/maestro-ai-demo`, after A1–A3 merged): the mock runs on a showcase install.** The public demo runs with `DEBUG` off, so until now AI was `off` there and A1–A3 had no demo. Now:
     - `ai.providers.mock_allowed()` is `DEBUG or DEMO_MODE`. An empty `AI_MODE` resolves to `mock` when it is true, else `off`; `ai.E001` refuses an explicit `mock` only when it is false. A real server (`DEBUG` off, `DEMO_MODE` off) is unchanged: empty means off, explicit mock refuses to start. `DEMO_SANDBOXES` alone is not a showcase. `ai.E006` still refuses live on a sandboxed showcase.
     - **Every mock answer is labelled** where a person sees it (`ai/sample.py`, `ai.providers.is_mock()`): «رد تجريبي — مفيش اتصال حقيقي» / "Sample reply — no real AI call", with one sentence per surface saying what is sample and what is real: the A1 card (silent audio; text and figures from the reports), the A2 page (routing by keywords; figure from the reports), and the A3 upload and review pages (a fixed sample invoice whatever the photo, with a link to a sample photo, `static/hesba/img/demo/sample-invoice.jpg`, generated, marked «نموذج · SAMPLE», no real data). No label in live or off, nor on a refusal page. No label existed before; this is new.
     - **Demo data** (`settings_core/demo_ai.py`, through the real services only): the three capabilities on in the ready-made shop (`prepare_demo`'s showcase list) and kept on when the visitor switches activity (`config.demo.switch_activity`: the preset plus each of the three whose sections the activity already has, so a clinic gets A1 and A2 but never purchases for A3; demo-only, beyond "commercial at least"). For A3: the supplier «مؤسسة الأمل للتوريدات» and the parts «زيت محرك 4 لتر», «فلتر زيت تويوتا» (matched by name) and «تيل فرامل أمامي (كود BP-22)» (the reading's «تيل فرامل أمامي K-22» does not match it, so it is mapped by hand), bought once 20 days ago on credit (posted by `post_purchase_invoice`, so they are in stock and never show as sold out). For A1: one collection dated yesterday from the customer who owes the most (`record_customer_payment`), since the sample month had collections only today. A2 needed nothing new. A restarted demo seeds these only if the visitor ticked the capabilities in the wizard.
     - The caps are unchanged (company 50 a day; per person 20 questions, 10 photos, 5 voicings). On the sandboxed showcase each visitor's copy has its own; on a non-sandboxed demo every visitor signs in as the same `owner` and shares the per-person limits.
     - The demo has no user restricted to one branch (no screen creates a branch membership, and a seeded second entity would change every demo screen). The «جرّب» steps show branch scoping by the owner adding a branch and switching to it; the restricted-member case stays covered by the A1/A2 tests.
     - The mock voice is the 0.36 s silent MP3: the player plays it to the end, shows `0:00`, and reports no error (checked in Chromium at 390 px and desktop).
     - No protected logic touched: no posting, ledger, stock, cashbox, report or permission code changed; the demo data goes through the existing services.
     - Tests: `ai/tests_demo.py` (resolution and checks with and without `DEMO_MODE`; the label present in mock and absent in live and off on all three surfaces), `settings_core/tests_demo_ai.py` (`prepare_demo` with `DEBUG` off and an empty `AI_MODE`: the three capabilities on, non-zero A1 and A2 answers, a cashier refused, the sample photo read, two lines matched and one mapped, saved as a draft with no stock, cashbox or supplier-ledger movement, idempotent seeding).
- **Independent review round 1 (Opus, REQUEST CHANGES at `3861cbe`), as addressed:**
  1. HIGH, every call that reached the provider leaves a row: fixed (`except Exception` records `call_error`, then re-raises).
  2. MEDIUM, a bounded cap: fixed (locked reservation, `call_started` counted, refusal inside an outer atomic).
  3. MEDIUM, a total deadline: fixed (10 s per operation, 20 s total, chunked read).
  4. MEDIUM, a run-wide guard: fixed (`hesba_testing/network.py` and `runner.py`).
  5. LOW–MEDIUM, redirects: fixed (an opener that refuses every 3xx).
  6. LOW, the model id: fixed (`ai.E005`; live without a model connects nothing).
  7. Empty `AI_MODE` means mock while `DEBUG` is on: **accepted**, kept as designed (development only; production is `off`).
  8. NIT: the mode is recorded from the provider used; the cap has a server ceiling (`AI_DAILY_CALL_CAP_MAX`).
- **Re-review (APPROVE WITH FIXES at `cff6686`), as addressed:**
  - A. MEDIUM, audit rows are append-only: fixed. The started row is never changed; the outcome is a separate row pointing back to it; only started rows count.
  - B. LOW–MEDIUM, the deadline against a slow drip: fixed with `read1`, proven by a real `http.client.HTTPResponse` over a socket pair (with `read` the same test hangs).
  - C. LOW: deviation 6 reworded for SQLite («database is locked», no overshoot).
  Also from Codex on #329: a cut-off answer (`http.client.IncompleteRead`) is a `ProviderError` (`c81acf1`).
- **Remaining risks:**
  - A process killed mid-call leaves a `call_started` row with no outcome row. It keeps counting for that day (the safe direction).
  - The deadline is checked after each read, so one read can overrun it by up to the 10 s per-read timeout. The connect and the headers are bounded only per operation: a server that drips its headers is limited by `http.client`'s header limits and the per-read timeout, not by the deadline.
  - The serialisation is proven by a query-shape test on PostgreSQL (CI) and a lock-path unit test on SQLite. There is no two-connection race test.
  - The run-wide guard covers Python sockets and urllib. A C extension opening its own sockets would not be caught; the only one in the requirements is libpq, which reaches only the test database.
  - DNS is not guarded: the patched paths refuse before resolving, but a client that calls `getaddrinfo` itself first (urllib3, requests) could still send one DNS lookup before its connect is refused.
  - A1 must call the AI layer outside any `transaction.atomic()`. Today no middleware wraps requests in a transaction (`ATOMIC_REQUESTS` is not set), so a view can call it directly.
- **Tests:**
  - `ai/tests.py`, all on `ai.no_network.AiTestCase` / `AiSimpleTestCase`:
    - the checks: mock in production; live without a key, with the placeholder voice or with no model; unknown mode; registered. Provider choice per mode.
    - the mock's audio: type, length, frames, determinism;
    - the live request with the opener mocked: method, URL with a quoted voice, `xi-api-key`, content type, accept, body, timeout;
    - HTTP, network and cut-off errors mapped without the key or text; non-audio answers refused; chunked read and size cap; a trickling answer stopped at the deadline, including a real `http.client.HTTPResponse` over a socket pair dripping one byte every 50 ms (stopped within 0.5–1.5 s of a 0.5 s deadline); a 302 refused and never followed, with the key sent only to ElevenLabs;
    - the guard refuses and records, and the live client cannot swallow it;
    - capability off, and unavailable in A0; the kill switch, and that it comes first; not connected (`off`, half-configured live); empty and long text;
    - a provider error audited and counted; any other exception audited, then re-raised; a call inside `transaction.atomic()` refused with no row; the mode taken from the provider used; live end to end with the opener mocked;
    - the started and outcome rows, linked by `before_data`, with a sentinel name, phone and amount found in no field of either; the started row unchanged after the call;
    - the cap: reached; refusals not counted; cap 0; the server ceiling; a call in flight counted (a second call made during the first is refused); check-and-reserve under the lock row; `FOR UPDATE` before the started row (PostgreSQL only); the reset the next Cairo day; a Cairo-versus-UTC boundary; outcome rows not counted; an unfinished started row counted;
    - the Arabic of every refusal; every test case uses the no-network base.
  - `hesba_testing/tests_network.py`: the guard installed for the run; external addresses refused through every path; loopback allowed; what counts as loopback; not an `OSError`; the run on the AI mock with no key; spawned workers set up; idempotent install.
- **Reason:** A1–A3 send business data to outside services (ElevenLabs for speech; Anthropic for A2/A3) and can cost money per call. Both are outside every earlier decision, so they are recorded here before any code.
- **Proposal (the S4/Paymob pattern):**
  - one `ai` provider layer with a mock mode (fake responses in the real API's shape) and a live mode;
  - system checks refuse live without a key and refuse mock in production;
  - keys only in server env (`ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY`), never in the DB or the repo;
  - each feature behind its own capability, off by default;
  - a per-company daily call cap and a kill switch in settings;
  - one audit row per call (feature, user, entity, size, result), with no customer names, phones or amounts;
  - a test that fails on any outbound HTTP during the test run.
- **What leaves the server:**
  - A1: only the summary text, which holds totals with no customer names.
  - A2: the question only. *(Changed by A2, HG-080: the report's figures are never sent; the model only routes.)*
  - A3: the invoice photo.
  - Nothing is sent while the capability is off.
- **Recommendation:** approve the layer as above. Live use stays Ahmed's decision per company: the key, the spend, and the provider's data-retention terms.
- **Open questions for Ahmed:**
  1. The default daily cap per company.
  2. Is sending invoice photos (A3) to an outside model acceptable, or only after each company opts in?
  3. Which ElevenLabs voice to use (`ELEVENLABS_VOICE_ID`; live refuses the placeholder).
  4. (A0) Cap scope: per installation, as built, or per entity?
  5. (A0) Showcase and demo: a demo server runs with `DEBUG` off, where `ai.E001` refuses mock and an empty `AI_MODE` means off. A1's demo data and «جرّب» steps need a choice: exempt `DEMO_MODE` from the check, or use a live key on the demo.
     - **A1 review:** a live key on a **sandboxed** showcase is now refused at start (`ai.E006`, `AI_MODE=live` with `DEMO_SANDBOXES` on). Each visitor's sandbox is its own database, with its own audit log and so its own daily cap: spend there would be effectively uncapped. A live key on a non-sandboxed demo is still allowed and remains Ahmed's call.
     - **AI demo (deviation 7):** built as "exempt `DEMO_MODE`": the showcase runs the mock with every answer labelled as a sample. **Still open for Ahmed:** should the public demo show mock AI at all, or should the demo set `AI_MODE=off` until he decides on live? Switching it off needs only that one environment variable; nothing else changes.
- **Risk:** medium (data leaving the server, cost). It is mitigated by off-by-default switches, caps, mock in tests, and the audit.

## HG-079 — A1 voice summary of the day

- **Status:** IMPLEMENTED on `claude/maestro-ai-a1-voice-summary`, pending review. Independent review round 1 (APPROVE WITH FIXES) is addressed; see below. Built on A0 (HG-078). No protected logic touched: no report calculation, posting, stock, cashbox or ledger code changed. The summary only reads the dashboard's existing figures.
- **Migration:** `ai/migrations/0001_initial.py`, the AI app's first. It creates `ai.VoiceSummaryAudio`, the audio cache, and nothing else.
- **Files:**
  - `ai/summary.py`: builds the text and the figures (no language model);
  - `ai/voice_summary.py`: the cache, the claim, the call through `ai.services.text_to_speech`, pruning, and the card's context;
  - `ai/models.py`, `ai/migrations/0001_initial.py`;
  - `ai/checks.py` (`ai.E006`, live refused on a sandboxed showcase);
  - `ai/views.py`, `ai/urls.py` (`/ai/voice-summary/`, `/ai/voice-summary/audio/<sha256>.mp3`), and `config/urls.py` (`path("ai/", …)`);
  - `ai/management/commands/prune_voice_summaries.py`;
  - `templates/ai/voice_summary_card.html`, `static/hesba/js/voice_summary.js`, `static/hesba/css/dashboard.css` (additive `.dash-voice*` rules, tokens only);
  - `reports/dashboard_views.py` (one context key, built by `_voice_card`, which can never break the page), `templates/reports/dashboard.html` (one include after the hero, one script tag, and the CSS version bump);
  - `settings_core/capabilities.py` (`ai_voice_summary` available, offered to every activity, in a new «الذكاء الاصطناعي» group, with its icon and "where"), `settings_core/operational_views.py` (`FEATURE_LANDING` → the dashboard), `settings_core/ui_messages.py` (two Arabic messages);
  - tests: `ai/tests_voice_summary.py` (new), plus `ai/tests.py`, `settings_core/tests_capabilities.py` and `settings_core/tests_setup_wizard.py`, updated for the capability now being available and the new group.
- **As built:**
  - **The text** (`ai.summary.build_summary(user, lang)`) is built only from the dashboard's own code. It uses `reports.dashboard_data.DashboardFigures` for yesterday's date, the permissions in `reports.dashboard_kpis`, and `reports.dashboard_fit.Fit`. All of these go through `reports.selectors` and `entities.scope` (HG-034). It covers:
    - yesterday's posted sales and their count (the "Sales today" / "Invoices today" cards for yesterday; only the listener's own invoices without `reports.view_all_sales_report`);
    - yesterday's collections (the "Receipts today" card, `sales.receive_customer_payment`);
    - the items at or below their minimum and, when there are any, the items out of stock (the two stock cards, never added together);
    - today's appointments, not counting cancelled ones, when the listener has the appointments section (`appointments.services.day_agenda`, the bookings panel's count).
  - A figure the listener may not see is left out of the text and the tiles, never said as zero. A listener who may see none gets no card, and a 403 from the endpoints.
  - The day is `timezone.localdate()` (Cairo). The text has no clock time and no time-of-day greeting («أهلاً، ده ملخص النهارده.»), so it stays the same all day unless a figure changes. Egyptian Arabic, or English on `?lang=en`.
  - Amounts use `hesba_format.money`. The voice says the currency's name (`settings_core.currency.CURRENCIES`: «جنيه مصري», "Egyptian pounds"). The tiles show the screen's symbol (`currency_label`: «ج.م», "EGP").
  - Each tile links to its report (the sales report filtered to yesterday's posted invoices, the collections list, the stock report, the agenda). A link the listener could not open (`operational_views._opens_for`) is left empty.
  - **The cache** (`ai.VoiceSummaryAudio`) has one row per entity scope (`group` or `entity:<id>`), language, Cairo day and SHA-256 of the text, under a unique constraint. The bytes are kept in the database, as S1 does, because the host's disk is ephemeral. The text is stored with its audio for display, never in the audit.
  - **The call** (`ai.voice_summary.speak`):
    1. it prunes old rows;
    2. it returns a ready row for the same key as it is;
    3. otherwise it claims the key by inserting a not-ready row in its own committed transaction. A second request meanwhile gets «صوت الملخص بيتجهّز دلوقتي» rather than a second paid call, and a claim older than 2 minutes is taken over;
    4. it calls `ai.services.text_to_speech(text, feature="voice_summary", user=…, entity=…)` outside any transaction (it refuses up front inside one), then stores the audio;
    5. on any refusal or error it deletes the claim and re-raises, so the next press can try again.
    A1 adds no audit rows of its own: A0 writes the started and outcome rows, without the text.
  - **Storage is bounded:** at most the company's daily cap of rows a day (each paid call stores one MP3, a few hundred KB for a summary), times 7 days, then pruned. With the default cap of 50, that is at most 350 rows.
  - **Per-person limit:** a person may start at most 5 voicings (paid calls) a Cairo day (`AI_VOICE_SUMMARY_USER_DAILY_CAP` overrides it), inside the company cap. It is counted from that person's `call_started` rows for `voice_summary`. Listening to audio already cached is free and never counted. The refusal is «وصلت لحد تشغيل ملخص الصوت النهارده (5 مرات للشخص). جرّب تاني بكرة.».
  - **Keep 7 days:** today and the six Cairo days before. Old rows are pruned on access (every new voicing) and by `manage.py prune_voice_summaries`.
  - **The endpoints:**
    - `GET /ai/voice-summary/` returns JSON: the text, the figures with their links, and the audio URL if already cached. It never calls the provider.
    - `POST` (CSRF, sent on the first press of play) voices the summary or reuses the cache. A refusal returns 409 with the message in the page's language (`ui_messages.translate`): kill switch, daily cap, not connected, provider failure, being prepared. While the capability is off, both answer 404 with «ميزة الذكاء الاصطناعي دي مش متشغّلة».
    - `GET /ai/voice-summary/audio/<sha256>.mp3` rebuilds the listener's own summary and serves the audio only if the hash is that summary's, in the entity being worked in, today. So another entity, other permissions, another language or another day gets a 404. It sends `Content-Type` from the provider (`audio/mpeg`), `Cache-Control: private, no-store, max-age=0`, `X-Content-Type-Options: nosniff` and `Content-Length`.
  - **The card:** a section after the dashboard's hero, as in the approved artboard: title and date, a play button with progress and times, the quoted text, the figure tiles (`.dash-atile`, a link each; running low uses `is-warn`), and the note «الأرقام من تقارير حسبة نفسها. الصوت بس اللي متولّد.». It is shown only while `ai_voice_summary` is on, read from the set the dashboard already loads, so it adds no query when off. Nothing is fetched on page load.
    - On press, the JS POSTs if the page has no cached audio URL, then fetches the MP3 as a blob and plays it from an object URL. This works on iOS Safari without range requests.
    - If the browser refuses to play after the round trip, the card says the audio is ready and the next press plays it.
    - Refusals show in a status line on the card.
  - **Capability:** `available=True`, still off by default, offered to every activity. It sits in a new group «الذكاء الاصطناعي» / "AI" on «خصائص الشغل» and the setup review. Its "Open" link is the dashboard.
- **Deviations:**
  1. **A new capability group** («الذكاء الاصطناعي»). `grouped_rows` lists only grouped slugs, and none of the three existing groups fits. As a result, `settings_core/tests_setup_wizard.py`'s two exact group lists now end with `ai` / "AI".
  2. **The cache is per entity scope, language, day *and text*,** not per entity per day alone. Two listeners with different permissions hear different texts, so they get different rows. A figure that changes during the day (a new booking, an item running low) gives a new text and one more call, bounded by the daily cap.
  3. **Out of stock is said as a second figure** next to "running low" when it is not zero, exactly as the dashboard's two stock cards; the tiles keep the design's four (sales, collections, running low, appointments). A listener with the invoice count but not the sales total gets an invoice tile instead of the sales tile.
  4. **No time-of-day greeting:** the artboard's «صباح الخير» became «أهلاً، ده ملخص النهارده.». A greeting that changes at noon would change the hash and pay for a second call.
  5. `reports/digest.py` (DIGEST-001's daily summary) was **not** reused. It reads `SalesInvoice.objects` and `CustomerPayment.objects` with no entity scope, so it would let one branch hear another's figures. That pre-existing gap is left as it is (out of scope); see risks.
- **Independent review round 1 (APPROVE WITH FIXES at `71fa16d`; no leak, no spend without a press), as addressed:**
  1. **The MP3 is read only to stream it.** The card and the JSON use `is_cached()` (an `EXISTS`). `cached()` and `_claim` defer `audio`. `speak` no longer reads the row back. Only `views.audio` reads the bytes (`cached_audio()`, `.only(...)`). Tested on the SQL.
  2. **`ai.E006`:** live is refused while `DEMO_SANDBOXES` is on (see HG-078 open question 5).
  3. **Per-person daily limit** (5), as above. Cached audio stays free.
  4. **The save after a paid call:** `content_type` is cut to its type, and anything that is not `audio/…` or is longer than 60 characters is stored as `audio/mpeg`. If the save fails, the claim is deleted at once and the error logged (`ai.voice_summary`), then raised. The paid call stays on record in the audit.
  5. **Tile links:** the invoices-only tile now opens the sales report for yesterday's date range, like the sales tile. No screen lists collections by date, so that tile keeps the collections list and says so: «افتح قايمة التحصيلات كلها» / "Open the full collections list". No list filters were added.
  6. **The dashboard never breaks for the card:** `reports/dashboard_views._voice_card` logs any exception and drops only the card.
  7. **Storage growth:** the line above.
  8. **The missing tests:**
     - the invoices-only branch;
     - collections as the shop's figure;
     - both race branches of `_claim`;
     - a non-`ValidationError` deleting the claim;
     - appointments by entity;
     - the counting words;
     - a page left open across Cairo midnight.
     **Deviation:** there is no test for «تحصيلاتك» because that branch could never run. `receipts_today` has no scope permission, so the dashboard's receipts card (and the summary) is always the whole shop's figure. The dead branch was removed and the behaviour is pinned by a test instead.
- **Remaining risks:**
  - `reports/digest.py` (the WhatsApp daily summary) is not entity-scoped (HG-034). Pre-existing, not changed here.
  - Two simultaneous first presses: one voices and the other gets "being prepared". A process killed mid-call leaves a claim that blocks that text for 2 minutes. On SQLite, the claim insert can meet «database is locked» under contention, as A0's reservation can.
  - The audio endpoint rebuilds the summary on each fetch (the same queries as the GET), once per play.
  - A demo server runs with `DEBUG` off, where `AI_MODE=mock` is refused (HG-078 open question 5). The card shows there but every press is refused as "not connected" until that is decided.
  - Not done in this PR: demo data and the «جرّب» steps that the roadmap's closing conditions ask for.
- **Tests** (`ai/tests_voice_summary.py`, all on `ai.no_network.AiTestCase`, mock mode):
  - **The text:**
    - the text equals the report figures exactly, in Arabic and English (250.50 from 2 invoices; 75.25 collected; 1 running low, 1 out), checked against `selectors.sales_report`, `DashboardFigures` and `stock_alert_counts`; today's sale and collection are absent;
    - the tiles' values and links;
    - the text is the same at 08:00 and 21:00;
    - today's appointments (cancelled and tomorrow's not counted; left out while the module is off).
  - **Permissions:**
    - a stock keeper hears stock only, with no 0.00 for the hidden money;
    - a cashier hears their own sales («مبيعاتك»);
    - a user with no figure gets no card and a 403.
  - **The Cairo day:** at 23:59 Cairo "yesterday" is the day before; at 00:30 Cairo (still the previous day in UTC) it moves.
  - **Entities:**
    - a branch member hears the branch only (and a real 0.00 for its collections);
    - main, branch and group each have their own text and scope key;
    - another entity's audio URL is a 404.
  - **Calls:**
    - visiting the dashboard and GET make no call and leave no audit row and no cache row;
    - the first POST makes one call, and the second POST uses the cache (one call, one `call_started` row, `calls_today()` = 1);
    - once cached, the dashboard hands the card the audio URL;
    - each language and each new text is its own call;
    - the text is never in the audit;
    - POST is refused without the CSRF token (`enforce_csrf_checks=True`) and accepted with the page's token;
    - a fresh claim gives "being prepared" with no call, and a stale one is taken over;
    - voicing inside `transaction.atomic()` is refused and leaves no claim.
  - **Audio:**
    - the type, body, length, `nosniff`, and `private` / `no-store`; streaming makes no call;
    - other permissions or no figures get a 404, and so do the wrong language, a wrong digest, another day and the capability off; signed out redirects to the login page;
    - a cached URL stops working once a figure changes the text. The card then drops it, POSTs again and shows the new text.
  - **The card:** hidden (no markup, no script, endpoint 404) while off; shown in Arabic and English with the title, the text, the note, the play label and the figures, and no call.
  - **Refusals:** the kill switch, the cap, not connected, a provider failure (the next press then works) and the feature switched off after load each give their message in Arabic and English, and leave no claim.
  - **Pruning:**
    - the 7th day back is kept and the 8th goes;
    - pruning on access and the command;
    - every test case uses the no-network base.
  - **Updated tests:**
    - `ai/tests.py`: the capability is available, off by default and in every activity; "unavailable is refused" is kept by patching `available=False`; "off is refused" is added;
    - `settings_core/tests_capabilities.py`: every capability is available;
    - `settings_core/tests_setup_wizard.py`: the fourth group.
- **Open questions for Ahmed:**
  1. **The voice:** which ElevenLabs voice (`ELEVENLABS_VOICE_ID`; live refuses the placeholder). An Egyptian-Arabic-sounding voice that also reads English?
  2. **Auto-play:** today nothing plays, and nothing is paid for, until play is pressed. Should the card play by itself on the first dashboard visit of the day? That would spend a call per entity per language every day whether or not anyone listens, and browsers often block auto-play anyway.
  3. **The spoken currency:** «جنيه مصري» / "Egyptian pounds", or the shorter «جنيه»?
  4. **Arabic counts:** «2 فاتورة» is said as written, not in the dual («فاتورتين»). Fine, or worth a rule for two?
  5. **The greeting:** keep the neutral «أهلاً، ده ملخص النهارده.», or have «صباح الخير» (stable all day, but odd in the evening)?

## HG-080 — A2 «اسأل حسبة» (questions answered from the system's own reports)

- **Status:** IMPLEMENTED on `claude/maestro-ai-a2-ask`, pending review. T3. Built on A0 (HG-078) and beside A1 (HG-079). No protected logic touched: no report calculation, posting, stock, cashbox, ledger or permission-core code changed. Every figure comes from an existing selector. No model, no migration.
- **Files:**
  - `ai/ask.py` (new): the fixed system prompt, the fixed tool list, the tool registry (permissions, runner, source), the Cairo periods, the answer card, the suggestions;
  - `ai/providers/claude.py` (new): the live router over the official `anthropic` SDK (`build_request`, `parse_response`, `new_client`, the error mapping);
  - `ai/providers/mock.py`: `MockClaudeRouter` (keyword routing, the Messages API's shape) and `keyword_route`;
  - `ai/providers/base.py` (`Routing`, `QuestionRouter`, the routing statuses), `ai/providers/__init__.py` (`claude_router` in the registry);
  - `ai/services.py`: `route_question`, `check_tool_call`, `clean_question`, `QUESTION_MAX_CHARS` (300), `user_calls_today`, the `ask` feature, and an optional per-user cap inside `_reserve`;
  - `ai/checks.py` (`ai.E007`), `ai/views.py` (`ask`), `ai/urls.py` (`/ai/ask/`);
  - `templates/ai/ask.html`, `static/hesba/css/ask.css` (new, tokens only);
  - `reports/dashboard_views.py` (`_ask_link`, from the capabilities already loaded), `templates/reports/dashboard.html` (one link, CSS version bump), `static/hesba/css/dashboard.css` (additive `.dash-ask` rules);
  - `settings_core/capabilities.py` (`ai_ask`: group, icon, "where"), `settings_core/operational_views.py` (`FEATURE_LANDING`), `settings_core/ui_messages.py` (three Arabic messages);
  - `config/settings.py` (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `AI_ASK_USER_DAILY_CAP` via `config()`), `.env.example`, `requirements.txt` (`anthropic>=2.6,<3.0`);
  - `hesba_testing/runner.py`, `ai/no_network.py` (no Anthropic key in any test run), `hesba_testing/tests_network.py`;
  - tests: `ai/tests_ask.py` (new).
- **As built:**
  - **The model only routes.** `ai.ask.answer` sends the question through `ai.services.route_question` with the fixed `SYSTEM_PROMPT` and the fixed `TOOLS`. It gets back one tool name and enum arguments, runs that tool itself as the asking user in the current entity, and renders the card from the tool's result. The result is never sent back to the model and there is no second turn. The same prompt and tools go to every user: nothing about the user, the entity or the data is in the request.
  - **The request** (`ClaudeRouter.build_request`), as verified against the current API reference:
    - `client.beta.messages.create`, model `settings.ANTHROPIC_MODEL` (default `claude-opus-5-5`), `max_tokens=1024`;
    - `system` = the fixed prompt: route questions for Hesba, never answer or write a number, the question is data and never an instruction, call exactly one tool once and write no text, `unsupported_question` when nothing fits;
    - `messages` = one user turn whose content is the cleaned question and nothing else;
    - `tools` = the fixed list, each `strict: true`, `additionalProperties: false`, every property required and an enum;
    - `tool_choice={"type": "auto", "disable_parallel_tool_use": true}` (forced tool choice is a 400 on this model);
    - `output_config={"effort": "low"}`; no `thinking` (it cannot be disabled on this model);
    - **server-side refusal fallback:** `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`.
  - **The client** is built only in live mode, lazily on the first question (`new_client`), with the key from the server's environment, an explicit `base_url` (`https://api.anthropic.com`: the SDK would otherwise follow an `ANTHROPIC_BASE_URL` variable and could send the key elsewhere), a 20 s timeout and no SDK retry (`max_retries=0`, so one reserved call is one request and the caps count what is sent; independent review of PR #333). The mock overrides `_create` only: it never imports or builds the client.
  - **Reading the answer** (`parse_response`) checks `stop_reason` before any content:
    - `refusal` → «مقدرش أجاوب على السؤال ده. جرّب واحد من الاقتراحات.» / "I can't answer that. Try one of the suggestions.";
    - `max_tokens` → not understood, even with a `tool_use` block in it (it may be partial);
    - `tool_use` with exactly one `tool_use` block → that tool and its `.input` (thinking and text blocks are skipped);
    - anything else (`end_turn` with text only, no block, two blocks, `pause_turn`) → «ما فهمتش السؤال، جرّب واحد من الاقتراحات». The model's text is never read or shown.
  - **Never trusted:** `ai.services.check_tool_call` checks the choice against the very tools sent. The name must be one of them; the arguments a dict with exactly the required keys; each value one of its enum options of the same type (`True` is not `1`, `1.0` is not `1`, `"1"` is not `1`). Anything else is `invalid_tool` / `invalid_arguments` and runs nothing. The checked result carries the schema's own strings.
  - **SDK errors** (`APIStatusError`, `APIConnectionError` including timeouts, any `AnthropicError`) become `ProviderError` built from the status code only, `from None`: the SDK's text can carry the response body, and the key and the question are never in a message. The user sees «خدمة الذكاء الاصطناعي مردّتش».
  - **The tools** (all read-only, wrapping existing selectors; arguments are enums only, never free text). Periods: `today`, `yesterday`, `this_week` (from Saturday, the Egyptian working week), `this_month` (to date), `last_month`, all Cairo days. `limit` ∈ {1, 3, 5}.

    | Tool | Arguments | Reads | Permission(s), as its source view | Link |
    |---|---|---|---|---|
    | `sales_total` | period | `selectors.sales_report` (posted) + `functional_views._posted_totals`; own invoices only without `reports.view_all_sales_report`, as the report view | `reports.view_sales_report` | sales report, that date range, posted |
    | `invoice_count` | period | the same, the count | `reports.view_sales_report` | the same |
    | `collections_total` | period | posted customer payments in the entity (`entities.scope`), the dashboard's "Receipts today" filter over a range | `sales.receive_customer_payment` | the collections list (not filterable by date; the card says so) |
    | `profit_total` | period | `selectors.profit_totals` (the profit report's gross profit) | `reports.view_profit_report` | profit report, that date range |
    | `top_items` | period, limit (default 3) | `analytics.top_items(..., by="sales")` | `reports.view_sales_report` + `reports.view_all_sales_report` (the dashboard analytics) | the dashboard's analytics for today / this month; the sales report for the range otherwise |
    | `top_debtors` | limit (default 1) | `aging.aging_rows("customers")`, largest total first, with its overdue part | `reports.view_customer_report` | aging report (customers) |
    | `low_stock` | limit (default 5) | `selectors.stock_alert_counts` + the items behind it (same rule, same rows) | `reports.view_inventory_report` | inventory report |
    | `cashbox_balances` | none | `selectors.cashbox_report` (total and up to 5 cashboxes) | `reports.view_cashbox_report` | cashbox report |
    | `appointments_today` | none | `appointments.services.day_agenda`, cancelled not counted (A1's count); "not switched on" while the section is closed | `sales.view_sales_invoices` (+ the appointments section open) | agenda, today |
    | `unsupported_question` | none | nothing | none | none |

  - **Permissions:** before anything is read, a tool whose permission the user lacks returns a refusal card naming it from the permission table: «تقرير الأرباح محتاج صلاحية «عرض تقرير الأرباح». اطلبها من صاحب الحساب.» / "The profit report needs the “View profit report” permission. …". No figure and never a zero. The suggestion chips a user could not use are not offered (nor the appointments chip while that section is off).
  - **Entity scope (HG-034):** every tool reads through `reports.selectors`, `entities.scope` or the aging/analytics/appointments code that already scopes. No tool takes an entity argument, so another branch's data cannot be asked for. The source line names the branch («الفرع: …», or «كل الفروع» for the group view) when there is more than one.
  - **The question:** control, format (bidi overrides, zero-width), surrogate, private-use and unassigned characters are dropped, spaces collapsed; 1–300 characters after that, else refused (and audited) before anything goes out. It is never in the audit. It is shown back, escaped, to its own asker only.
  - **Spend (A0's door):** feature `ask` with capability `ai_ask` (available, off by default, offered to every activity, in the AI group, landing `/ai/ask/`, which the module gate also closes while it is off). Kill switch, company cap, not connected and the transaction guard are A0's. **Per-person limit:** 20 questions a Cairo day (`AI_ASK_USER_DAILY_CAP`), counted from that user's `call_started` rows for `ask`, checked **under the same lock** as the company cap (refusal code `user_daily_cap`, «وصلت لحد الأسئلة النهارده (20 سؤال للشخص). جرّب تاني بكرة.»). `text_to_speech` is unchanged (the option defaults to off).
  - **Audit:** A0's rows. The `call_ok` row adds `routing` (`tool`, `refusal`, `no_tool`, `truncated`, `invalid_tool`, `invalid_arguments`) and, only when valid, `tool`, `period` and `limit`: server constants, never anything the model wrote, never the question.
  - **The page** `/ai/ask/`, after the artboard: the navy header «اسأل حسبة» / «الإجابة من تقاريرك، وفي حدود صلاحياتك وفرعك», the question bubble, the answer card (label, the name for a person or item, the figure via `hesba_format.money` and the screen's currency, a sub-line, up to 5 rows, «افتح تقرير …» with the date range, and the source line with the period and branch), the gold refusal card «مش متاح ليك», the chips, and the box. GET calls nothing. Each question and each chip is a CSRF POST; no JavaScript. Arabic and English (`?lang=en`), phone width. The response is `private, no-store`. The dashboard shows an «اسأل حسبة» link while `ai_ask` is on, read from the capabilities it already loads (no extra query).
  - **Checks:** `ai.E007` refuses live with `ANTHROPIC_API_KEY` set and `ANTHROPIC_MODEL` empty. Live without the Anthropic key simply connects nothing for A2 (refused as not connected).
- **Deviations:**
  1. **Collections over a range** are summed with the dashboard card's own filter (posted customer payments in the entity), not by a new report: no screen lists collections by date. A test proves it equals the dashboard's figure day by day. The link opens the whole collections list and says it is not filtered by date, as A1 does.
  2. **Best-selling items** link to the dashboard's analytics for today and this month (the only periods it shares), otherwise to the sales report for the range, and the source line says which. Items are ranked by sales value (`by="sales"`), as the dashboard ranks them for someone without the profit permission; a profit holder's dashboard list is ranked by profit.
  3. **Top debtors** are ranked by the balance owed (largest first), not by the aging report's own order (overdue first). The figures are the report's.
  4. **Every property is required** in the strict schemas (`limit` included, with a default named in its description), so strict mode never has to accept an optional property. The mock fills the default.
  5. **Chips go through the model** like a typed question (one code path, capped and audited). See open question 3.
  6. **`unsupported_question`**, a tenth tool with no arguments, gives an off-topic question a legal route, since a forced tool choice is not available.
  7. **The source line** adds the period and the branch to the design's "source · branch".
  8. The SDK's own transport is `httpx2` in `anthropic` 2.x; the error-mapping tests build its exceptions with it.
- **Independent review (PR #333):** no cross-entity or cross-user leak, permission bypass, injection path or XSS found; each tool's permissions checked against its source view. Two findings, both fixed: (1) someone who may use none of the tools was still sent to the model on every question (a paid call ending in a refusal, counted against the shared company cap): `ai.ask.can_ask` now stops it before any call or row, the page answers 403 with no box, and the dashboard hides the button; (2) a huge raw question (megabytes) was cleaned twice and wrote an uncapped refusal row: over `RAW_MAX_CHARS` (4 × 300) it is refused before cleaning with no call and no row. Also, the SDK's own retry is off (`max_retries=0`), so one reserved call is one request.
- **Risks remaining:**
  - Only the question leaves the server (no figures). A question can still carry whatever the user types (a customer's name, say). It goes to Anthropic under the company's key and retention terms.
  - A wrong route gives a correct figure for the wrong question; the card's title, period and source say what was answered.
  - The live client is tested only with the SDK's `create` mocked and the request bound to the installed SDK's signature. The first real call (model availability, the beta, strict tools with `effort: low`) is unverified until a key is set.
  - DNS and C-level sockets: as HG-078.
  - SQLite's «database is locked» under contention: as HG-078.
  - Not done in this PR (the roadmap's closing conditions, as A1 left them): demo data and the «جرّب» steps, and the independent review focused on injection and branch-to-branch leakage.
  - `anthropic` is a new dependency (2.6.0 here; its transport is `httpx2`). Any environment (CI, the Codex Cloud venv) needs `pip install -r requirements.txt` before `ai.tests_ask` runs: a few of its tests import the SDK. The mock path itself never imports it.
- **Tests** (`ai/tests_ask.py`, 75, all on the no-network bases, mock mode, a pinned Cairo day, explicit figures):
  - each tool equals its report: sales for all five periods against the sales report view's totals (999.00, 250.50, 1,369.50, 1,449.50, 500.00); the invoice count; collections against the dashboard's receipts day by day (75.25, 85.25, 0.00); profit against the profit report (1,399.50); top debtors against the aging report (989.00, 670.50, 204.75; 500.00 overdue); low stock against `stock_alert_counts`; cashboxes against the cashbox report (85.25); best sellers against `analytics.top_items`; appointments against the agenda; the period ranges;
  - permissions: a cashier asking profit is refused with the permission's name, the profit selector patched to raise proves nothing was read, and no figure or zero is on the page; cashboxes, aging and best sellers refused; a cashier's own sales (50.50) as the report shows them; chips filtered; each tool's permissions equal its source view's;
  - entity: a branch manager sees 444.44 and never 999.00 or the group's 1,443.44; main, branch and group each their own; the branch's debtors, collections and cash; an injected question from the branch still gets the branch;
  - injection: injected questions cannot reach profit for a cashier; the mock routes on keywords; the request holds the question only as user content with the fixed system and tools; owner and cashier send identical requests; control characters stripped; the 300-character cap, refused and audited before sending;
  - the "model": unknown names (including `None`, `7`, a trailing space), out-of-enum and wrong-type arguments (`True`, `1.0`, `"1"`, `2`, a list, a string, missing, extra keys), refusal, text only (never shown), `max_tokens`, two blocks, blocks before the tool, `unsupported_question`, a provider failure; the outcome row's routing for each;
  - spend: capability off (page and POST closed, no call, `capability_off` row), the kill switch, not connected, the per-person limit (and someone else, and the next Cairo day), the per-feature count, the company cap, the transaction guard;
  - the page: GET never calls and leaves no row; CSRF refused without the token and accepted with it; signed out goes to login; the card in Arabic and English; the question escaped; the dashboard link only while on; every chip routes to its tool;
  - audit: started and outcome rows with tool and period, and a sentinel question found in no field; a refused question leaves one row without it;
  - **live end to end** (`LiveEndToEndTests`): the real `ClaudeRouter` with only the SDK's `create` replaced, returning SDK-shaped objects, driven through `ask.answer` and A0's door: the figure for a good route with the audit saying `live` / `claude_router`; a bad tool name and out-of-enum or extra arguments run nothing (`invalid_tool` / `invalid_arguments`, the selector patched to raise); a live refusal; a live connection error audited as `call_error`;
  - the tools: strict, enum-only, bilingual; the prompt; the live request exactly as documented and bound to the installed SDK's signature; stop reasons; SDK objects; SDK errors mapped without the key or question; the client's key, base URL (an `ANTHROPIC_BASE_URL` in the environment ignored), timeout and retries; lazy client; the registry per mode; the mock never builds or calls the SDK client; `ai.E007`; every test case on the no-network base.
- **Open questions for Ahmed:**
  1. **Model and cost:** the default is `claude-opus-5-5` (`ANTHROPIC_MODEL`), as instructed; not downgraded for cost. Each question is one short routing call (a fixed prompt and ten tool definitions in, one tool call out, `effort: low`). Keep Opus, or approve a cheaper model after measuring the routing accuracy on real questions? The outcome rows' `routing` field gives the daily rates of `truncated`, `refusal` and `no_tool`: a high `truncated` rate on the first live calls is the signal to raise `max_tokens` (kept at 1024 as briefed).
  2. **More tools:** purchases, supplier dues, expenses, returns, a single customer's balance (that would need a customer argument, which free text cannot be; a picker would be needed). Which, if any?
  3. **Chips without a model call:** the chips are fixed questions, so they could map straight to their tool with no paid call and nothing leaving the server. Today they go through the model like any question.
  4. **The per-person limit:** 20 questions a day. Is that right?
  5. **Keys on live:** live mode still requires the ElevenLabs key (`ai.E002`, from A0), so a company that wants only «اسأل حسبة» live must set both. Should each provider's key be checked only when its feature is on?
  6. **The week:** "this week" starts on Saturday. Confirm.

## HG-081 — A3 invoice photo to a draft purchase invoice

- **Status:** IMPLEMENTED on `claude/maestro-ai-a3-invoice-photo`, pending review. T3. Built on A0 (HG-078), beside A1 (HG-079) and A2 (HG-080), and on S1 (HG-072).
- **Protected logic:** none changed. Purchase posting, the purchase models and migrations, `create_purchase_draft`, the permission core and every report calculation are untouched. The draft is made by the same chain the purchase form uses: `create_purchase_draft_with_tax` → `purchases.services.create_purchase_draft`, unchanged. **Draft only:** nothing here posts, so there is no stock, cost, cashbox, supplier-ledger or journal movement until a person posts the draft from the usual purchase screen.
- **Files:**
  - `ai/invoice_photo.py` (new): the fixed prompt and schema, the upload checks, `validate_reading`, the deterministic matching, the waiting photo, the review card, `save_draft`;
  - `ai/models.py` + `ai/migrations/0002_pending_invoice_photo.py` (new model `PendingInvoicePhoto`, in `ai/` only);
  - `ai/services.py`: the `invoice_photo` feature, the `read_invoice_image` door, `IMAGE_MAX_BYTES`, `IMAGE_TYPES`, `image_type`, and a per-feature per-person refusal message (`USER_CAP_MESSAGES`; A2's text is byte-identical);
  - `ai/providers/claude.py`: `_AnthropicTransport` (the lazy client, the one SDK call and the error mapping, now shared with A2's router, unchanged in behaviour), `ClaudeInvoiceReader`, `parse_reading`;
  - `ai/providers/mock.py` (`MockClaudeInvoiceReader`, `SAMPLE_READING`, `reading_response`), `ai/providers/base.py` (`Reading`, `InvoiceReader`, the reading statuses), `ai/providers/__init__.py` (`claude_invoice_reader`);
  - `ai/text.py` (new): `fold`, the text folding used for matching;
  - `ai/management/commands/prune_invoice_photos.py` (new, independent review): deletes waiting photos past `PENDING_HOURS`;
  - `ai/views.py`, `ai/urls.py` (`/ai/invoice-photo/`, `/<token>/`, `/<token>/again/`, `/<token>/photo/`);
  - `templates/ai/invoice_photo.html`, `static/hesba/css/invoice_photo.css` (new, `--hs-*` tokens only);
  - `attachments/services.py`: a `purchases.purchaseinvoice` owner in `OWNERS`, and an optional `entity` rule used by `_store`; `attachments/handlers.py`: an optional lower `limit` for the cap handler and `too_big_for_the_cap` (the defaults are unchanged);
  - `purchases/views.py` + `templates/purchases/list.html` (the «فاتورة من صورة» link), `templates/purchases/detail.html` (a link to the photo kept on the invoice): UI only;
  - `settings_core/capabilities.py` (`ai_invoice_photo`: AI group, icon, "where", needs `purchases`), `settings_core/operational_views.py` (`FEATURE_LANDING`), `settings_core/ui_messages.py` (four Arabic messages);
  - `config/settings.py` (`AI_INVOICE_PHOTO_USER_DAILY_CAP` via `config()`), `.env.example` (the name only);
  - tests: `ai/tests_invoice_photo.py` (new).
- **As built:**
  - **The flow.** Upload page (GET calls nothing) → one CSRF multipart POST → one paid call through A0's door → the checked, matched reading and the photo wait server-side → the review page (GET calls nothing) → the person maps and edits → «احفظ كمسودة» (CSRF POST) creates the draft, attaches the photo and deletes the waiting row in one transaction → redirect to the draft's own screen (`/purchases/<pk>/`), where the person posts it. «صوّر تاني» forgets the waiting photo.
  - **Before any paid call**, in order: the capability `ai_invoice_photo` is on (404 otherwise; its path is closed by the capability gate too, and it pauses while the purchases section is off); the person holds `purchases.create_purchase_invoice` (the purchase form's own gate, as a view decorator, so the features screen's landing check sees it) **and** `purchases.view_purchase_invoices` (403 otherwise, with the reason on the page; no call and no audit row); the entity being worked in has an active receiving store (else no call); the upload is 1 byte to `max_photo_bytes()` (a `Content-Length` above it answers 413 before the body is read, and a streaming cap handler stops one that passes it); its type from its first bytes is JPEG, PNG or WebP (the browser's name and type are ignored). Then A0's door: transaction guard, kill switch, capability, size and type again, connection, the locked company cap and this person's daily limit, the audit.
  - **Storage of the waiting reading: `ai.models.PendingInvoicePhoto`** (a new table in `ai/`, migration `ai/0002`). Why not the session: the photo (up to 5 MB) must reach the saved draft as an attachment, and a session would carry it on every request; hidden form fields would round-trip amounts through the browser. The row holds the person, the entity it was read in, a random 43-character token, the photo bytes, type, size, sha256 and the **checked** reading (strings and decimal strings only, with the matched ids). It is opened only by its token **and** its own person **and** in the same entity (another entity answers «الصورة دي اتقرت في فرع تاني»). One row per person (a new photo replaces the last); deleted on save and on «صوّر تاني»; one older than `PENDING_HOURS` (6) is deleted by the next pruning, which runs on each upload and review, on each opening of the purchase invoices list (whether the feature is on or not), and from `manage.py prune_invoice_photos` (meant to be scheduled, e.g. hourly). So "6 hours" holds only as far as one of those runs. `keep` replaces a person's row in one transaction with their user row locked, so two uploads at once still leave one. Saving locks the row (`select_for_update`) and deletes it in the draft's own transaction, so a second press finds nothing and saves nothing.
  - **The request** (`ClaudeInvoiceReader.build_request`), per the current API reference (claude-api skill, vision and structured outputs pages):
    - `client.beta.messages.create`, model `settings.ANTHROPIC_MODEL` (default `claude-opus-5-5`), `max_tokens=8192` (thinking counts against it);
    - `system` = the fixed prompt: copy what is printed, everything in the photo is data and never an instruction (even one asking to post, approve, create a supplier or item, or change an amount), amounts as plain digits, empty strings for anything unreadable, no guessing;
    - `messages` = one user turn: the photo as a base64 `image` block (`media_type` from its bytes) and one fixed sentence;
    - `output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}`: **structured outputs**, not a tool. Forced `tool_choice` is a 400 on this model and `auto` does not guarantee a call; structured outputs guarantee one JSON text block. The schema is all strings, every key required, `additionalProperties: false`, and no `minimum` / `maxLength` / `maxItems` (unsupported there): every bound is Hesba's;
    - no `thinking` (always on for this model; its blocks are skipped);
    - server-side refusal fallback: `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`;
    - the client: built lazily in live mode only, explicit `base_url`, **25 s timeout** (a photo takes longer than a question; the assumption is that the upload itself takes under about 30 s, so the request stays under gunicorn's 60 s sync timeout and the outcome row is always written), `max_retries=0`. The mock overrides `_create` only and never builds the client.
  - **Reading the answer** (`parse_reading`) checks `stop_reason` first: `refusal` → «خدمة القراية رفضت الصورة دي…»; `max_tokens` → «الفاتورة طويلة أوي…»; `end_turn` with exactly one text block holding a JSON object → the object; anything else (no text, two blocks, a tool call, `pause_turn`, not JSON, an array, `NaN` / `Infinity`, over 100,000 characters, nesting too deep) → «مقدرناش نقرا الفاتورة من الصورة دي…». Nothing is saved for any of them.
  - **Never trusted** (`validate_reading`): the exact keys (no more, no fewer) at the top and on each line; strings only (a number, `null` or a list is refused); text cleaned of control and invisible characters and bounded (supplier 255, number 80, code 120, line name 200); amounts parsed by a strict pattern, never `Decimal()` alone (Arabic-Indic digits and grouped thousands read; `NaN`, exponents, signs, padding refused): quantity > 0 with at most 11 digits and 3 decimals, price ≥ 0 with at most 12 digits and 2 decimals; each line's total and the sum under 10^12 so they fit the tables; the date `YYYY-MM-DD` and a real date, or empty; 1 to 50 lines. One broken field refuses the whole reading.
  - **No item or supplier is ever created.** Matching (`match_reading`) is server code: the supplier by its exact code (any case), else by its exact folded name; a line's item by its exact code (any case) or barcode, else by its exact folded name (`ai.text.fold`: case, hamza forms, ى/ي, ة/ه, harakat, digits and spaces; letters are never dropped, so «تيل فرامل أمامي K-22» does not match «تيل فرامل أمامي»). Only a single hit counts; two hits leave the line to the person. Unmatched lines show «محتاج ربط» with the select «اختار الصنف من أصنافك» and «مش هنعمل صنف جديد من نفسنا.»; an unmatched supplier the same.
  - **Saving re-checks everything posted back:** an id is read only as 1 to 18 ASCII digits (`record_id`: `"²"`, Arabic digits or a 5,000-digit string are refused, never passed to `int()` or the database); amounts' patterns are ASCII-only and matched whole. Each included line needs an item id from the purchase form's own item set (active), else «السطر N محتاج تختار له صنف…» or «الصنف المختار في السطر N مش من أصنافك المتاحة.»; quantities and prices by the same strict parser; the supplier, store, number and date through the purchase form itself (`PurchaseDraftForm`, bound with server-built data: active suppliers, the receiving stores of the entity being worked in, a used number refused, an empty one taking the next `PI-` number). Then, as `purchases.views.invoice_create` does: `convert_lines`, `prepare_purchase_serials` (a serial-tracked line without its serials is refused with the serial module's own message, e.g. «عدد السيريالات (0) لازم يساوي الكمية (12).»), `create_purchase_draft_with_tax` inside `create_with_fresh_number`, `attach_purchase_batches`, `attach_purchase_serials`; then `attachments.services.add_attachment` (kind photo) and the waiting row's delete, all in one transaction: if any step fails, nothing is kept.
  - **Totals:** the service computes the draft's totals from the lines. The review shows «الإجمالي محسوب من السطور» (Hesba's sum of quantity × price), «الإجمالي المكتوب في الصورة», and «فيه فرق 100.00. راجع السطور قبل ما تحفظ.» when they differ. The written total is never used.
  - **Text from the photo never reaches a document:** the draft lines' description stays empty (printing shows a description in place of the item's name), and the notes are a fixed sentence («اتعملت من صورة فاتورة المورد (حسبة).»). Read text is shown, escaped, on the review page only, to its own person.
  - **The photo is kept on the draft** through S1: a `purchases.purchaseinvoice` owner (seen with `purchases.view_purchase_invoices` in the entity scope; added to with `purchases.create_purchase_invoice`; a cancelled invoice takes no more; deleted, audited, only while the invoice is a draft and by a holder of the create permission, with its own refusal «صورة فاتورة الشراء بتتمسح بس طول ما الفاتورة مسودة…»). Its attachment `entity` is the receiving store's (a new optional `entity` rule; before it, `_store` read `owner.entity`, which a purchase invoice does not have, and a branch user could not have opened their own photo). The draft's screen links to it.
  - **Spend (A0's door):** feature `invoice_photo`, capability `ai_invoice_photo` (available, off by default, every activity, AI group, needs the purchases section, landing `/ai/invoice-photo/`). Kill switch, company cap, not connected and the transaction guard are A0's. **Per person:** 10 photos a Cairo day (`AI_INVOICE_PHOTO_USER_DAILY_CAP`), under the same lock (refusal `user_daily_cap`, «وصلت لحد صور الفواتير النهارده (10 صور للشخص). جرّب تاني بكرة.»).
  - **Audit:** A0's rows. `request_bytes` is the photo's size (`request_chars` 0). The `call_ok` row adds `reading` (`read`, `refusal`, `truncated`, `no_output`, `invalid_output`) and `line_count`. Never the photo, a supplier's name, a line's text, a number or an amount. (The draft itself leaves the purchases module's own `create_purchase_draft` row, as any draft does, and S1 its `add_attachment` row.)
  - **The page**, after the artboard: the navy header «فاتورة شراء من صورة» / «بتتحفظ مسودة بس. الترحيل منك أنت.», the supplier card with the photo, the read name and its chip, the line cards with «متطابق» / «محتاج ربط», the totals card with the difference warning, «احفظ كمسودة» (teal) and «صوّر تاني». Arabic and English (`?lang=en`), phone width, no JavaScript, `private, no-store`. The upload uses `capture="environment"` so a phone opens its camera. The waiting photo is served to its own person only, with `nosniff`, `Content-Security-Policy: default-src 'none'; sandbox` and `no-store`.
  - **Entry point:** a «فاتورة من صورة» button on the purchase invoices list, shown only while the capability is on and only to someone who may use it.
- **Deviations:**
  1. **Two permissions, not one:** `purchases.create_purchase_invoice` (the draft) **and** `purchases.view_purchase_invoices`, because the flow ends on the draft's own screen and the photo is kept on a record the person must be able to see. The seeded owner, manager and stock keeper hold both; the accountant (view only) and the cashier get 403 before any call.
  2. **"Another branch's items and suppliers"** cannot be expressed: suppliers and items are group-wide master data (the purchase form offers every active one). The entity boundary is enforced where it exists: the receiving store (only the entity's own; another entity's store id is refused), the waiting photo (read in one entity, saved only from it), the draft (lands in the branch's store), and the photo's attachment (the branch's entity). Inactive or unknown supplier and item ids are refused.
  3. **The review lets the person edit** each line's quantity and price, the number and the date, leave a line out, and pick the store: a purchase draft cannot be edited after it is saved (only posted), so a misread figure must be corrected before saving.
  4. **The entry point is the purchase invoices list**, not the dashboard (the artboard does not place it; purchases is where a supplier's invoice is entered).
  5. **Up to 50 lines** (the brief's figure), where the hand-typed purchase form takes 20. The service has no line limit.
  6. **The image's pixel size is not checked** (that would need an image library; Pillow is not a dependency). A photo above the API's 8000 × 8000 limit is refused by the API as a 400, which the person sees as «خدمة الذكاء الاصطناعي مردّتش», after a reserved (counted, unbilled) call.
  7. **The review's computed total is the lines' total before tax.** With VAT on, the draft's total adds input VAT per item rate, as the purchase form does.
  8. **The totals on the review do not update while typing** (no JavaScript); they are recomputed on any refused save, and the draft's screen shows the service's figures.
- **Risks remaining:**
  - **The photo leaves the server.** In live mode the whole photo (supplier's name, prices, anything else in frame) is sent to Anthropic under the company's key and retention terms. This is a new kind of data leaving the server compared with A2 (a question only). Live use is Ahmed's decision (open question 1).
  - **Misreadings.** A wrong digit gives a wrong draft line; the person sees every figure, the computed and written totals side by side, and posts it themselves.
  - **Storage.** Photos are bytes in the database (as S1 decided, HG-072); a waiting photo goes after 6 hours only when a pruning runs (the scheduled `prune_invoice_photos`, the purchase invoices list, or the next use). Saved photos count toward S1's caps.
  - The live client is tested only with the SDK's `create` mocked and the request bound to the installed SDK's signature. The first real call (structured outputs with an image on this model, `effort: low`, the 25 s timeout, `max_tokens` 8192) is unverified until a key is set.
  - Postgres-only guarantees: the waiting row's lock is real on PostgreSQL; SQLite serializes writers instead (as HG-078). The suites were run on SQLite only here; Postgres CI is still to run.
  - **Page weight:** every line renders the whole active-item select (as the hand-typed purchase form's rows do). With thousands of items and tens of lines the review page grows to megabytes on a phone. A search-as-you-type picker would fix it later.
  - Not done here (the roadmap's closing conditions, as A1 and A2 left them): demo data and the «جرّب» steps, and the independent review focused on injection and branch-to-branch leakage.
- **Tests** (`ai/tests_invoice_photo.py`, 71, all on the no-network bases, mock mode, explicit figures):
  - the draft equals the mapped reading: INV-5531 of 8 November 2026 from «مؤسسة الأمل للتوريدات», 12 × 385.00 = 4,620.00, 20 × 95.00 = 1,900.00, 8 × 240.00 = 1,920.00 (the third mapped by hand), subtotal and total 8,440.00 (never the written 8,540.00), remaining due 8,440.00, status draft, empty descriptions, fixed notes; the photo attached with its bytes, type and entity; the waiting row gone; the draft's screen links the photo and offers «ترحيل الفاتورة»;
  - nothing posted: stock movements, cashbox movements, supplier ledger, journal entries and customer ledger unchanged; no post audit; item and supplier counts unchanged;
  - unmatched: a line left unmapped refused with «السطر 3 محتاج…» (Arabic and English), nothing created, the waiting row kept; a line left out (6,520.00); mapping ids outside the offered items (inactive, unknown, text, negative, injected SQL) refused; an unmatched supplier shown «محتاج ربط», not created, refused until chosen (empty, inactive, unknown); matching by supplier code, item code and barcode; two items with the same folded name leave the line unmatched; folding never drops letters;
  - the person's edits parsed strictly (`0`, `-1`, `1e3`, `NaN`, `Infinity`, too many decimals, blanks refused; Arabic digits read); a used number refused and an empty one taking `PI-`; a failing attachment rolls back the draft; a second press saves nothing more;
  - injection: a supplier name and line names carrying instructions and a `<script>` tag: one call, escaped on the page, the draft stays a draft, no supplier or item created, nothing posted, no read text in a description, the notes or the audit; the prompt says the photo is data;
  - malformed answers: 24 broken shapes (missing or extra keys, numbers or nulls for strings, no lines, negative, zero, huge, exponent, `NaN` string, a line total over 10^12, too precise, a bad written total, bad and impossible dates, over-long text, a nameless line, a string line, an array), 51 lines, refusal, text only, `max_tokens`, `pause_turn`, a tool call, two text blocks, a `NaN` literal, empty content, a provider failure (`call_error`): each a safe error card with nothing kept, and each outcome row's `reading`;
  - permissions: the cashier and the accountant get 403 and never reach the model (no call, no row); create-without-view refused before any call; the stock keeper may; the purchases-list link only while on and only for those who may use it; the capability registered, off by default, AI group, needs purchases; the landing check;
  - entity: a branch manager's photo is read in the branch (audit entity), only the branch's store is offered, the main store refused, the draft lands in the branch, the photo's attachment is the branch's and opens for them and not for another branch's manager; a photo read in one entity is refused from another; a branch with no receiving store never reaches the model;
  - spend: capability off (pages closed, no call, `capability_off`), the kill switch, the company cap, the per-person limit (Arabic and English, someone else still may), A2's limit text unchanged, the transaction guard, CSRF on the upload and the save, GET never calls, signed out goes to login;
  - upload: GIF, HTML named `.jpg`, a PDF and text refused by their bytes before the door; PNG and WebP read with their own media types; empty and missing; oversize (413) and a `Content-Length` above the cap refused without reading; the cap never above the door's; the door re-checks type and size (`invalid_request`);
  - live client: the request exactly as documented, bound to the installed SDK's signature and its `output_config` keys; the schema strict with no unsupported constraint; stop reasons and content shapes; SDK errors mapped without the key or the photo; the lazy client with its 25 s timeout, explicit base URL and no retry; the mock never builds the client; the registry per mode; **live end to end** through the page and the door (`live` / `claude_invoice_reader` in the audit), a live refusal and a live connection error;
  - the S1 owner rules: seen with the view permission only, not deleted or added to by a view-only user, a cancelled invoice takes no more.
- **Independent review (PR #335)** (Codex found nothing; no high-severity finding). Fixed on the same branch:
  1. MEDIUM, serials and batches: a line mapped to a serial-tracked item could never be saved, and batch / expiry data was always lost. The review now offers, per line, exactly the serial / batch / expiry fields the purchase line form (`PurchaseLineInputForm`) has while those features are on, parsed by that form's own fields and passed through the same `prepare_purchase_serials` / `attach_purchase_serials` / `attach_purchase_batches` as `invoice_create`; a line whose chosen item is serial-tracked is flagged «محتاج سيريال».
  2. LOW, pruning while off: `manage.py prune_invoice_photos` (new) and a pruning on each opening of the purchase invoices list, whatever the switch; the "6 hours" wording above made accurate.
  3. LOW, timeout: `READ_TIMEOUT` 45 s → 25 s, so a slow upload plus the call stays under gunicorn's 60 s.
  4. LOW, deleting the photo: allowed only while the invoice is a draft, with the purchase invoice's own refusal (Arabic and English), still audited.
  5. LOW, a total past 10^12: its own message («إجمالي السطور كبير أوي على فاتورة واحدة…», and per line «إجمالي السطر N كبير أوي.») instead of «لازم يفضل سطر واحد».
  6. LOW, the same typed number saved at the same moment: the `IntegrityError` is caught (everything rolled back) and answered «فيه فاتورة شراء بالرقم ده قبل كده».
  7. LOW, `keep`: delete-then-create in one transaction with the user row locked (the paid call stays outside any transaction).
  8. LOW, privacy: a JPEG's APP1 (EXIF, XMP: camera, time, GPS) and APP13 (IPTC) segments are dropped by walking its markers (no new dependency) before the photo is sent or kept; everything else is copied byte for byte. A JPEG whose header segments cannot be walked is refused before the door. PNG and WebP are sent and kept as they are (their text / EXIF chunks are not stripped).
- **Open questions for Ahmed:**
  1. **Sending photos to an outside model.** In live mode each photo goes to Anthropic. Approve live use (and tell clients), or keep A3 mock-only?
  2. **Model and cost.** `claude-opus-5-5` at `effort: low`, one call per photo (an image of a few thousand tokens in, up to a few thousand out). Keep Opus, or approve a cheaper model after measuring accuracy on real invoices? Raise the effort if misreadings are common?
  3. **The per-person limit:** 10 photos a day. Right?
  4. **Image retention.** A saved photo stays on the invoice for good once it is posted (it can be deleted only while a draft); a waiting one goes after 6 hours at the next pruning. Keep both? Should a saved photo be deleted after some months, or moved to object storage (as HG-072 recommends)?
  5. **Keys on live** (as HG-080 open question 5): live still needs the ElevenLabs key for A0's start-up check.
