# Hesba Master Execution Control

Status: ACTIVE CONTROL BASELINE
Current baseline: `develop`
Owner: Ahmed / Main Control
Execution: Agent/Codex

## 1. Purpose
This file controls how future Hesba work starts from the latest accepted engineering baseline without losing the visual/operating rules.

It does not authorize a new product screen by itself.

## 2. Current baseline decision
The latest accepted engineering work is on `develop`, not `main`.

Therefore, until Ahmed explicitly changes the integration strategy:
- new task branches start from `develop`;
- `main` remains behind this working baseline;
- do not discard or bypass the engineering work already on `develop`.

## 3. Control documents
Future Agent work must follow:
- `AGENTS.md`
- `docs/ENGINEERING_QUALITY_STANDARD.md`
- `docs/HESBA_BRAND_ASSET_MANIFEST.md`
- active Hesba operating rules
- current Main Control Task Card
- approved Screen Pack for the target screen

## 4. Current product implementation status
This control pack intentionally does not start a new production screen.

Before the next product implementation, Main Control must:
1. review the exact `develop` baseline;
2. confirm the next screen/flow;
3. create or confirm its Functional Contract;
4. complete its Screen Pack;
5. get Ahmed approval;
6. send a scoped Agent Task Card.

## 5. Dashboard note
`develop` already contains additional dashboard-related engineering work beyond `main`.

Do not delete, rewrite, or assume it is the final approved product UI merely because code exists.

Any continuation must be reconciled with the latest Main Control product decision and Screen Pack gate.

## 6. Setup Gate note
Do not reopen Setup Gate Web unless Ahmed explicitly approves it.

Completed Setup/identity work must not be modified as collateral cleanup.

## 7. Next checkpoint
After this control pack is approved:
- perform a structured `develop` baseline audit;
- identify what is complete, incomplete, and pending approval;
- produce the next Main Control Task Card;
- only then start product implementation.

## 8. Merge rule
This documentation branch may be merged into `develop` only after Ahmed approves the PR.

No automatic merge.
