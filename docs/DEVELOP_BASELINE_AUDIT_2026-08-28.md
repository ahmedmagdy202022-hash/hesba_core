# Develop Baseline Audit — 2026-08-28

Status: MAIN CONTROL AUDIT
Baseline: `develop`
Compared with: `main`
Purpose: Establish the real Hesba starting point before any new Agent implementation.

## 1. Executive result

`develop` is the current working engineering baseline.

At audit time:
- `develop` is 16 commits ahead of `main`.
- There are no open product PRs.
- The latest accepted work on `develop` includes authentication hardening, broad service-layer tests, setup persistence, permission scoping, dashboard live-data work, period-closing corrections, and admin clarification.
- New work should not bypass or discard this baseline.

This audit does not approve any new product implementation by itself.

## 2. Baseline classification

### A. Authentication / route protection
Status: ENGINEERING_READY

Implemented:
- Django LoginRequiredMiddleware protects application routes by default.
- Login remains public.
- Post-login routing goes through `/start/`.
- Authenticated root requests route through the post-login gate.

Quality:
- Route protection has dedicated tests.
- The design fails safe by default for newly added routes.

Main Control note:
Keep this behavior as the security baseline.

### B. Setup persistence and redirect gate
Status: ENGINEERING_READY

Implemented:
- `ClientProfile.activity_slug`
- `ClientProfile.sub_activity_slug`
- `ClientProfile.setup_completed_at`
- setup completion service
- module FeatureFlag persistence
- setup audit logging
- `/start/` routing:
  - incomplete installation -> `/setup/`
  - completed installation -> `/dashboard/`

Quality:
- tests cover valid setup;
- tampered/unknown activity rejection;
- no-profile behavior;
- rerun/idempotent behavior;
- required-module restoration;
- module read-back;
- audit entry creation.

Scope note:
The implemented setup catalog currently validates Commercial and Services. Other future Hesba activities remain product roadmap work, not silently implemented scope.

### C. Permissions foundation and dashboard scoping
Status: ENGINEERING_READY WITH PRODUCT REVIEW LATER

Implemented:
- view-level permission helpers;
- backend permission checks remain centralized in permissions services;
- permission to distinguish own sales from all-user sales;
- cashier can be scoped to own figures while manager/accountant/owner can receive wider scope according to permission matrix.

Quality:
- permission tests exist;
- new permission is seeded through a reversible data migration.

Main Control note:
Preserve permission-driven behavior. Do not hardcode role names into future financial screens when a permission can express the rule.

### D. Period closing / re-closing correction
Status: ENGINEERING_READY

Implemented:
- PeriodSummary uniqueness now includes closing run:
  `(period, closing_run, summary_code)`
- previous run summaries remain available for audit history;
- admin list shows run number so repeated closing summaries are distinguishable.

Quality:
- migration is explicit and reversible;
- repeated close/reopen cycles are tested;
- summary independence across runs is tested;
- duplicate summary inside one run remains rejected;
- admin display/query behavior is tested.

### E. Service-layer test coverage
Status: STRONG BASELINE

Accepted engineering work substantially expanded tests around:
- purchases;
- sales;
- inventory;
- closing;
- reports;
- imports;
- permissions;
- setup persistence;
- dashboard;
- route authentication.

Historical accepted PR reports:
- PR #48: 505 passing tests.
- follow-on closing fix: 509 passing tests.
- PR #50: 665 passing tests after its addition.

Important:
The current merge commit has no separate GitHub status/check record attached. The workflow exists and runs on pull requests or manual dispatch, so future Agent PRs must rely on actual PR CI plus local/Agent-reported commands, not historical numbers alone.

### F. Dashboard live-data implementation
Status: ENGINEERING_IMPLEMENTED / PRODUCT_NOT_FINAL

`develop` contains a real dashboard implementation:
- real report-derived figures;
- permission-aware KPI cards;
- health score;
- real alerts;
- module-aware navigation/actions;
- onboarding progress;
- Arabic/English text;
- responsive Web/Tablet/Mobile CSS;
- demo-business seeding and extensive tests.

Architecture strength:
- dashboard data reads through the reports/selectors layer where designed;
- sensitive cards are permission-aware;
- repeated shared reads are cached per request;
- demo data command refuses to write transactions outside DEBUG.

Product/visual conflict:
Historical Dashboard 120 documents said production implementation must not start before final Screen Pack / approved assets / layout/functional contracts.

Therefore:
- do not delete this engineering work;
- do not treat it as final approved UI merely because code exists;
- do not expand it automatically;
- reconcile it later with Ahmed's latest product decision and the approved Screen Pack workflow.

Current functional limitation:
Most dashboard navigation/quick-action targets are still safe placeholders routing to `home` rather than finished operating screens.

### G. Database/environment configuration
Status: DEVELOPMENT_ONLY

Current settings use:
- SQLite
- DEBUG=True
- development-only SECRET_KEY

This is acceptable for local development only.

Project direction remains PostgreSQL-first. Before delivery/deployment, environment configuration and PostgreSQL runtime must be completed and tested.

Do not opportunistically change environment/database configuration during a UI task.

## 3. Known defect intentionally documented by the latest engineering work

### Sales cost can use stale stored average cost
Status: KNOWN BUSINESS-LOGIC RISK — NOT FIXED

Current sales posting uses:
`item.average_cost`

A characterization test explicitly documents:
If stock movements contain cost but `item.average_cost` was not refreshed, a sale can book unit cost as zero and overstate line profit.

The latest engineer deliberately documented this rather than silently changing financial logic.

Main Control rule:
- Do not let an Agent fix this incidentally.
- Before Sales operating UI is considered release-ready, create a dedicated business-logic review/task.
- Confirm the intended source-of-truth rule for sales cost with PostgreSQL Master Source and accounting/business requirements.
- Then implement a tested fix only with explicit approval.

## 4. Current design/brand baseline

Preserve:
- Hesba official logo sources;
- existing accepted screen assets;
- Navy / Teal / Gold / Off-white identity;
- Arabic-first hierarchy;
- responsive Web / Tablet Landscape / Mobile behavior;
- real HTML/CSS for text and actions.

Follow:
- `docs/HESBA_BRAND_ASSET_MANIFEST.md`
- active Screen Pack workflow.

Do not:
- invent a new logo;
- use screenshot crops as production assets;
- turn screens into generic SaaS templates;
- reuse legacy action art as current production without approval.

## 5. What is ready to build on

Engineering foundation suitable for new controlled tasks:
- authentication;
- setup completion persistence;
- roles/permissions foundation;
- purchase posting foundation;
- sales posting foundation with the known cost risk above;
- inventory movements;
- supplier/customer ledgers;
- cashbox movements;
- report selectors/services;
- strong test infrastructure;
- audit logging.

## 6. What must not be assumed complete

Do not assume these are final product screens merely because code exists:
- Dashboard visual/product implementation;
- Operations screen;
- Customer screen;
- Supplier screen;
- Item/service screen;
- Cashbox screen;
- finished Purchase Invoice UI;
- finished Sales Invoice UI;
- finished payment/collection UI;
- final reports UX.

## 7. Recommended next Main Control step

Do not code the next operating screen immediately.

First:
1. approve this baseline and Agent control documentation;
2. decide the first operating track;
3. prepare its Functional Contract;
4. prepare Visual Approved + Production Background + Assets Pack + Layout Contract;
5. Ahmed approves the Screen Pack;
6. Agent receives a scoped Task Card from `develop`.

Recommended first operating track remains the Purchase Invoice flow because the backend foundation already exists and it starts the commercial operating cycle.

## 8. Checkpoint

Completed:
- confirmed `develop` as real working baseline;
- audited the main engineering additions;
- identified dashboard implementation as engineering work pending product/visual reconciliation;
- confirmed strong tests/security/setup persistence;
- recorded the unresolved sales-cost risk;
- recorded development-only database/environment state.

Next approval:
Ahmed/Main Control approves the baseline/control documents before opening or merging their documentation PR.
