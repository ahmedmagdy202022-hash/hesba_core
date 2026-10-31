# Hesba Agent — End-to-End Operating Rules

Status: The end-to-end run on `agent/end-to-end-functional-cycle` is COMPLETE and merged to `develop` via PR #54 (`c77c7e5`).
Current task board and work rules: `docs/HESBA_ROADMAP.md`. Where this file and the roadmap disagree on branching, the roadmap wins.
What is actually built today, and the parked ideas (restaurants/cafés and others): `docs/PROJECT_STATE.md`.
Round 2 (Ahmed's demo feedback, approved 9 Oct 2026): `docs/R2_PRODUCT_PLAN.md` sets the order of the R2 work; the roadmap's work rules still apply.
The engineering benchmark and protected-logic rules below remain in force.

## Mission
Complete Hesba functionally end-to-end with minimal interruption.

Finish the user's explicitly requested task end-to-end; do not autonomously start unrelated Tracks or backlog tasks.
Do not ask Ahmed routine technical questions.
Do not merge to `develop` or `main` without Ahmed's explicit approval.

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

## Continuous execution within a requested task
For each task Ahmed explicitly requests:
- understand the narrow scope, implement the requested change and run focused tests;
- fix in-scope failures, commit on its own branch, and open a PR to `develop`;
- report the evidence, PR, and next suggestion; then stop rather than starting an unrequested Track;
- only pause for a genuine Hard Gate or necessary business decision.

Never auto-merge, even when automated checks pass.

## Codex Cloud and Claude Code cross-agent handoff
- This root `AGENTS.md` is read by Codex. Claude Code's `CLAUDE.md` includes it; the engineering and protected-logic rules apply to **both** tools.
- Keep work mobile-friendly: concise Egyptian Arabic updates with one actionable checkpoint. Prefer scoped files and direct dependencies over repository-wide dumps. Read related project documents only as needed; `docs/HESBA_ROADMAP.md` may lag behind current code and merged PRs.
- Use the smallest relevant validation first. Escalate to broader testing for sensitive accounting, inventory, authorization or cross-app changes.
- Before handing off to the other agent, push work to a named branch with a PR, and provide exact scope, files, tests, risks and next action; neither tool automatically sees the other's conversations or uncommitted changes.
- Codex Cloud onboarding and the reusable handoff template are in `docs/CODEX_CLOUD_SETUP.md`. This is optional reference material; **do not read it for unrelated coding tasks**.
- Never use production credentials or deploy to real services merely to complete a development task.

## Quota-aware AI team: Claude supervisor, Codex fallback
- For tasks involving cross-agent work, or when the user reports either tool near its usage limit, follow `docs/AI_TEAM_PROTOCOL.md`. Do not load that file for unrelated tasks.
- Use a pushed branch + task issue or PR as the only durable handoff. Never assume subscription quota values, that the other provider was invoked, or that a cloud session can start another provider's cloud task.
- Checkpoint implementation before quota exhaustion; a session that has hit its limit cannot make a last-minute handoff. Never run both agents on the same branch concurrently.
- If Claude is unavailable, Codex can continue a previously approved in-scope task and leave it awaiting review; no automatic merging, spending, or deployment.

## Select model and effort for the task
- For cross-agent routing and non-trivial model recommendations, use `docs/AI_TEAM_MODEL_POLICY.md` (load it only when model selection is in scope). Classify task risk T0–T3, optimize quality after validation per unit of available subscription usage, and suggest a higher-capability model only when risk or evidence warrants it.
- This instruction file cannot itself change a Codex Cloud model. Respect available UI model controls and current task model, do not make undocumented session API calls or force API-only model IDs.
