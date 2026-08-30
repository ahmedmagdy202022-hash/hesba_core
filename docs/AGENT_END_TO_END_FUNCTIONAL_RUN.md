# AGENT END-TO-END FUNCTIONAL RUN

Status: EXECUTE NOW
Owner: Main Control
Execution branch: `agent/end-to-end-functional-cycle`

## Goal
Continue Hesba from the current repository state until the whole application is functionally usable end-to-end.

This is one continuous execution run.

Do not pause between Tracks for cosmetic approval.
Do not perform final Visual Polish yet.

## Starting state
This branch starts from the existing Master Data implementation developed from `develop`.

First inspect the current branch, PR #52 work, current tests, routes, services, permissions and known defects. Preserve useful work; do not restart it.

---

## Track 1 — Master Data Foundation

Finish and harden:
- Master Data hub;
- Locations list/create/edit;
- Suppliers list/create/edit;
- Customers list/create/edit;
- Categories list/create/edit;
- Items & Services list/create/edit;
- Cashboxes list/read behavior;
- search/filter/pagination;
- dashboard links/quick actions;
- Arabic/English;
- responsive functional UI;
- auth/permission tests.

Rules:
- no delete by default;
- average_cost is not editable;
- purchase price only with cost permission;
- opening balance locked after creation until approved correction logic exists;
- Cashbox create/edit remains a recorded Hard Gate if no safe existing permission can express it.

Then continue immediately.

---

## Track 2 — Purchases

Build the user-facing Purchase flow on top of existing purchase services/models.

Required functional UX where supported by existing backend:
- purchase invoice list;
- new draft invoice;
- supplier selection;
- receiving location;
- line items;
- quantity;
- unit purchase price;
- discount/tax fields supported by current model;
- paid-now amount;
- cashbox selection when required;
- totals/remaining due using existing logic;
- detail screen;
- post action through existing service;
- cancellation/reversal through existing service where available;
- purchase return flow where existing backend supports it;
- clear statuses;
- supplier payment entry if current services already support it.

Do not duplicate posting/accounting calculations in views/templates/JavaScript.

Tests must verify:
- permissions;
- validation;
- posting side effects;
- supplier due;
- cashbox movement;
- stock movement;
- repeated posting protection;
- cancellation/reversal behavior.

Then continue immediately.

---

## Track 3 — Inventory

Build functional inventory screens using existing movement/report logic:
- stock by item/location;
- item stock detail;
- movement history;
- low/out-of-stock visibility;
- location filters;
- stock transfer where existing service supports it;
- controlled adjustment where existing approved permission/service supports it;
- receiving/selling location behavior needed by Purchases/Sales.

Do not directly mutate stock totals outside existing inventory service/movement architecture.

Tests:
- permission boundaries;
- direction/quantity;
- source/destination transfer effects;
- no hidden cost exposure;
- responsive/AR/EN routes.

Then continue immediately.

---

## Track 4 — Sales

Build Sales flow on current service layer:
- sales invoice list;
- new draft;
- customer;
- selling location;
- item/service lines;
- quantity;
- price;
- discounts/tax supported by model;
- paid-now;
- cashbox when needed;
- detail;
- post;
- cancel/reversal;
- sales return where backend supports it;
- customer collection where existing service supports it.

Known stale average-cost defect:
Do not silently modify cost logic.
Before declaring Sales release-ready, characterize the existing behavior and record the Hard Gate if fixing it requires protected business-logic approval.

Tests:
- permissions;
- stock availability;
- customer due;
- receipt/cashbox effects;
- profit/cost behavior characterization;
- repeated posting;
- reversal/return.

Then continue immediately.

---

## Track 5 — Payments & Collections

Expose existing supported flows:
- supplier payment;
- customer collection;
- payment history;
- linked party/invoice context where architecture supports it;
- cashbox selection;
- posted/cancelled state;
- validation.

Use existing services only.

Then continue immediately.

---

## Track 6 — Cashbox Operations

Build what the existing permissions/services safely allow:
- cashbox list;
- financial balance visibility only with permission;
- movement history;
- direct cash in/out where existing service supports it;
- cashbox transfer where existing service supports it;
- links from related payments/receipts.

Do NOT invent Cashbox master-data edit permission.
Keep Cashbox create/edit as a Hard Gate until permission semantics are approved.

Then continue immediately.

---

## Track 7 — Reports

Turn the existing report selectors/services into usable read-only screens:
- Sales report;
- Purchases report;
- Inventory report;
- Customer statement/report;
- Supplier statement/report;
- Cashbox report;
- Profit report for permitted users;
- date/filter controls;
- empty states;
- Arabic/English;
- responsive layout.

Reports remain read-only.
Do not recalculate business truth separately in templates/views.

Then continue immediately.

---

## Track 8 — Closing / Period Operations

Inspect existing closing services/admin/tests.

Where backend support already exists, expose a safe user-facing flow for:
- period state;
- close period;
- close run history;
- reopen where permission allows;
- repeated closing-run history;
- summary viewing.

Do not change accounting/closing rules to fit the UI.
Record any missing protected behavior as a Hard Gate and continue.

Then continue immediately.

---

## Track 9 — Users, Roles, Settings functional completion

Inspect existing setup/settings/accounts/permissions capabilities.

Expose only safe existing functionality needed for a usable application:
- current user/profile context;
- role/permission visibility/manage flow where existing permissions support it;
- operational settings already modeled;
- no speculative new configuration.

Do not reopen Setup Gate Web.
Do not redesign Login/Setup.

Then continue immediately.

---

## Track 10 — Dashboard Functional Reconciliation

Return to the existing Dashboard only after operational screens exist.

Do:
- replace remaining safe placeholder navigation with real completed routes;
- make quick actions point to completed flows;
- preserve report-derived KPI architecture;
- preserve permission-driven cards;
- remove dead links/placeholders caused by now-completed flows;
- validate onboarding completion against real Master Data/operations.

Do NOT perform final visual redesign yet.

Then continue immediately.

---

## Track 11 — End-to-End Quality Pass

Run:
- `python manage.py check`
- focused suites for every new Track
- full Django test suite
- `python manage.py makemigrations --check --dry-run`
- auth/permission route review
- Arabic/English review
- Web/Tablet Landscape/Mobile functional review
- broken links/buttons review
- sensitive finance/cost exposure review
- duplicate posting/reversal safety review

Fix all in-scope defects found by these checks.

Do not hide unrelated existing defects; document them.

---

## Track 12 — Delivery Readiness Audit

Audit, but do not recklessly change:
- SQLite development configuration;
- PostgreSQL target readiness;
- DEBUG/SECRET_KEY environment setup;
- static files;
- deployment settings;
- migration status;
- test repeatability.

If PostgreSQL/environment work can be safely completed without changing product/business semantics, prepare it.
If infrastructure credentials/environment details are unavailable, record the exact delivery gap and continue all other work.

---

## Final completion condition

The run is complete when:
- every non-blocked functional Track above is implemented;
- all relevant tests pass;
- all routes/actions are connected;
- Arabic/English is working;
- responsive functional behavior is present;
- no unapproved protected business logic was changed;
- remaining Hard Gates are consolidated into `docs/AGENT_HARD_GATES.md`;
- a final summary exists at `docs/AGENT_END_TO_END_COMPLETION.md`;
- one final PR from `agent/end-to-end-functional-cycle` to `develop` is prepared.

Do NOT merge the final PR.

Final Visual Polish is a separate later phase after Main Control reviews the completed functional application.
