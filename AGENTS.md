# Hesba Agent — End-to-End Operating Rules

Status: ACTIVE FOR THIS BRANCH
Branch: `agent/end-to-end-functional-cycle`
Baseline lineage: `develop` + current Master Data work

## Mission
Complete Hesba functionally end-to-end with minimal interruption.

Do not stop after each screen or Track.
Do not ask Ahmed routine technical questions.
Do not merge to `develop` or `main`.

## Product direction
Current phase is FUNCTIONAL-FIRST.

Build:
- real contents;
- real routes/actions;
- Arabic + English;
- Web + Tablet Landscape + Mobile;
- permissions;
- validation;
- tests;
- safe business-service integration.

Defer until the final Visual Polish phase:
- decorative illustrations;
- custom icon polish;
- hero art;
- production backgrounds;
- cosmetic micro-tuning.

Use the existing Hesba identity only:
- Navy #05243F
- Teal #16BDC4
- Gold #D9AD50
- Off-white #F6FBFB
Do not invent or redraw the Hesba logo.

## Engineering benchmark
Match or exceed the discipline of the latest accepted work on `develop`:
- understand current logic before changing;
- service layer owns business/accounting behavior;
- characterization/regression tests where behavior is unclear;
- focused scope;
- no hidden unrelated fixes;
- meaningful permission/auth tests;
- explicit financial/stock assertions;
- clear risk and defect documentation.

## Protected logic
Do not change protected business logic merely to unblock UI:
- models/schema/migrations;
- permission core;
- purchase posting;
- sales posting;
- inventory movement/accounting;
- cashbox movement/accounting;
- customer/supplier ledgers;
- report calculations;
- average-cost logic;
- closing accounting logic.

If a protected change is genuinely required:
1. record it in `docs/AGENT_HARD_GATES.md`;
2. state exact reason/files/risk/proposed fix/tests;
3. continue every other unaffected task.

A Hard Gate must not stop the whole run.

## Known Hard Gates / risks
1. There is currently no dedicated `cashboxes.manage_cashboxes` permission.
   Do not misuse `cashboxes.move_cash` for Cashbox master-data editing.
2. Customer/Supplier/Cashbox opening-balance correction after operational use has no approved accounting semantics.
   Initial creation may follow existing model behavior; do not invent later correction behavior.
3. Sales cost has a known stale-`average_cost` risk.
   Do not silently fix it while building UI. Record/review before Sales is declared release-ready.

## Branch discipline
Work only on:
`agent/end-to-end-functional-cycle`

Use scoped commits/checkpoints.
Do not create a new branch for every screen.
Do not merge to `develop` or `main`.

At the end, prepare one final PR to `develop` for Main Control review.

## Continuous execution rule
After completing one Track:
- run focused tests;
- fix in-scope failures;
- commit;
- immediately continue to the next Track.

Do not return "waiting for approval" unless every remaining task is blocked by a true Hard Gate.
