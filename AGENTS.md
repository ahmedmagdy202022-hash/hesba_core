# Hesba Agent — End-to-End Operating Rules

Status: The end-to-end run on `agent/end-to-end-functional-cycle` is COMPLETE and merged to `develop` via PR #54 (`c77c7e5`).
Current task board and work rules: `docs/HESBA_ROADMAP.md`. Where this file and the roadmap disagree on branching, the roadmap wins.
The engineering benchmark and protected-logic rules below remain in force.

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

Use the existing Hesba identity only. The palette is the one approved in
`docs/HESBA_ROADMAP.md` ("قرارات معتمدة — مرجع ثابت"), implemented as the
`--hs-*` tokens in `static/hesba/css/tokens.css` (see `docs/DESIGN_TOKENS.md`):
- Navy #092851 (the real logo's navy): structure, sidebar, headings
- Teal #02AEB7: primary actions, with navy text on top
- Gold as an accent only: #8A6A1F for text on light, #F2D58E on navy
- Ground #F6F8FA, surfaces white
Take colours from the tokens; do not type new colour literals into stylesheets.
The earlier values (#05243F, #16BDC4, #D9AD50, #F6FBFB) are superseded; the
last two survive only inside the approved login artwork until roadmap D3.1.
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
All seven Hard Gates from the end-to-end run are RESOLVED; see `docs/AGENT_HARD_GATES.md`. In particular:
1. `cashboxes.manage_cashboxes` now exists (HG-001). Cashbox master-data editing uses it, not `cashboxes.move_cash`.
2. Opening-balance correction after operational use is an auditable dated adjustment with append-only reversal (HG-002).
3. Posted sales cost comes from authoritative inventory movement cost; `Item.average_cost` is a display cache (HG-003).
Record any new protected-logic question in `docs/AGENT_HARD_GATES.md` as before.

## Branch discipline
Historical (end-to-end run): all work went on `agent/end-to-end-functional-cycle` as one PR (#54, merged).

Current rule, from `docs/HESBA_ROADMAP.md`:
- one branch per task, cut from an up-to-date `develop`;
- never commit to `develop` or `main`;
- never merge without Ahmed's explicit approval;
- one PR per task to `develop` for Main Control review.

## Continuous execution rule
After completing one Track:
- run focused tests;
- fix in-scope failures;
- commit;
- immediately continue to the next Track.

Do not return "waiting for approval" unless every remaining task is blocked by a true Hard Gate.
