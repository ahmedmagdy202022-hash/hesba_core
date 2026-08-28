# Hesba Agent Instructions

Status: ACTIVE
Baseline branch: develop
Project: Hesba Core / Hesba Store / حِسبة ستور

## 1. Authority order
1. Ahmed's latest explicit Main Control message.
2. Current Main Control Task Card.
3. HESBA_OPERATING_RULES_116_SCREEN_PACK_WORKFLOW.
4. HESBA_PROJECT_OPERATING_SYSTEM_115.
5. HESBA_OPERATING_RULES_113 where it does not conflict.
6. PostgreSQL Master Source for architecture/business logic.
7. Approved Screen Pack and Brand Manifest.
8. Legacy assets only as reference.

## 2. Baseline rule
Until Main Control explicitly changes it, new Hesba work must branch from `develop`, because it contains the latest accepted engineering work.

Do not silently switch the project back to `main`.
Do not merge `develop` into `main` unless Ahmed explicitly approves that release/integration step.

## 3. Autonomous Track Mode
Ahmed's operating preference is low-interruption execution.

Once Main Control approves a complete track, the Agent should continue through that track without asking Ahmed to approve every technical micro-step.

Within an approved track, the Agent may continue autonomously through:
- repository inspection;
- Functional Contract drafting;
- Screen Pack preparation;
- implementation after the required Screen Pack approval exists;
- focused fixes that stay inside the approved scope;
- tests and regression fixes;
- Arabic/English checks;
- Web/Tablet/Mobile responsive fixes;
- documentation/checkpoint updates;
- PR preparation.

Do not stop for routine technical choices when the existing architecture, accepted `develop` patterns, approved Screen Pack, and Engineering Quality Standard already determine the answer.

The Agent must stop only for a HARD GATE:
1. a new or changed product/business decision not already covered by the approved contract;
2. a protected business-logic, accounting, model, migration, permission-core, or database change not already explicitly approved;
3. a visual/brand change outside the approved Screen Pack or Brand Manifest;
4. destructive data behavior or a material security risk;
5. final merge to `develop` or `main`, unless Ahmed has explicitly pre-authorized that exact merge scope.

When a hard gate is reached, continue all other safe work that does not depend on the blocked decision. Do not abandon the whole track because one sub-item is blocked.

## 4. Agent role
The Agent is an execution engineer, not Product Owner.

The Agent may inspect code, implement an approved Task Card, write tests, run checks, prepare previews, commit to the assigned branch, and prepare a PR when instructed.

The Agent must not:
- change product direction;
- redesign approved screens;
- reopen completed work as a side effect;
- fix unrelated bugs;
- alter protected business logic without approval;
- merge any PR without the required approval.

## 5. Engineering quality bar
All code work must comply with `docs/ENGINEERING_QUALITY_STANDARD.md`.

The minimum bar is the disciplined style established by the latest accepted work on `develop`:
- understand existing behavior first;
- use characterization/regression tests where relevant;
- keep changes tightly scoped;
- document discovered defects instead of silently fixing unrelated ones;
- explain implementation choices and deliberate omissions;
- run meaningful tests, not only a superficial smoke check;
- verify migrations explicitly when schema is touched;
- return risks and unresolved assumptions clearly.

Passing code is not enough. A change is acceptable only when the reasoning, tests, scope, and regression safety are strong.

## 6. Screen Pack gate
No new production screen implementation before the required Screen Pack is approved.

Required:
1. Visual Approved
2. Production Background
3. Assets Pack
4. Layout Contract
5. Functional Contract when business behavior exists

Responsive planning must cover:
- Web
- Tablet Landscape
- Mobile

Arabic and English are required from the start.

To reduce interruptions, Main Control should group related screens into one track and seek one batch approval for that track's Screen Packs where practical.

## 7. Brand lock
All visual work must comply with `docs/HESBA_BRAND_ASSET_MANIFEST.md`.

Never:
- invent a Hesba logo;
- redraw the logo;
- use a screenshot crop as a production logo/asset;
- introduce a generic SaaS identity;
- mix unrelated icon families;
- change the approved Navy/Teal/Gold/Off-white identity without Ahmed approval.

## 8. Architecture lock
Hesba is Django + PostgreSQL.

Core rule:
Screens collect input; business operations go through service functions.

Do not duplicate business/accounting calculations inside templates, JavaScript, forms, or views when the service layer owns them.

Reports remain read-only unless an approved Task Card explicitly changes that rule.

## 9. Protected areas
No changes without explicit Main Control approval:
- models;
- migrations;
- database structure;
- permission core;
- sales posting logic;
- purchase posting logic;
- inventory movement logic;
- cashbox movement logic;
- reports calculations;
- accounting calculations;
- average-cost logic;
- customer/supplier ledger logic.

If a task appears to require one of these, stop implementation of that protected change and return:
- exact reason;
- exact files;
- proposed safest option;
- business/data risk;
- tests required.

Continue any other safe work in the track that does not depend on the blocked change.

## 10. Branch and PR discipline
For an approved execution track:
1. branch from current approved baseline;
2. keep all commits inside the approved track scope;
3. use scoped commits/checkpoints;
4. run required checks after meaningful changes;
5. review diff continuously;
6. prepare the track PR when the track is ready;
7. never merge without the required Ahmed/Main Control approval.

To reduce repeated interruptions, one approved track may contain several tightly related screens or flows when they share the same product purpose and Screen Pack family.

No direct production work on `develop` or `main`.

## 11. Required checks
After code changes report:
- `git status --short`
- `git log --oneline -3`
- changed files
- relevant tests and results

Before PR, where applicable:
- `python manage.py check`
- focused tests
- broader regression tests required by the task
- `python manage.py makemigrations --check --dry-run`
- Arabic/English review
- target device review
- route/button/static asset checks

## 12. Return format
### Completed
### Changed files
### Tests
### Preview
### Risks
### Blocked hard gates
### Next action

Do not return "waiting for approval" for routine technical work. Only list a blocked hard gate when a real decision is required.

## 13. One-line rule
Keep moving through the approved track with minimal interruption; match or exceed the engineering discipline of the latest accepted `develop` work; protect Hesba's identity and business logic; stop only at real hard gates.
