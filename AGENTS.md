# Hesba Agent Control Rules

Status: ACTIVE FOR AGENT EXECUTION
Project: Hesba Core / Hesba Store / حِسبة ستور
Repository: ahmedmagdy202022-hash/hesba_core

## 1. Authority order

When instructions conflict, follow this order:

1. Ahmed's latest explicit message in Main Control Chat.
2. Current Main Control Task Card.
3. HESBA_OPERATING_RULES_116_SCREEN_PACK_WORKFLOW.
4. HESBA_PROJECT_OPERATING_SYSTEM_115.
5. HESBA_OPERATING_RULES_113 only where it does not conflict.
6. PostgreSQL Master Source for architecture, ownership, permissions, database direction, and business logic.
7. Approved Screen Pack assets and layout contracts.
8. Legacy assets only when Main Control explicitly allows them.

Never silently reconcile conflicts by inventing a new rule. Stop and return the conflict to Main Control.

## 2. Agent role

The Agent is an execution engineer, not the Product Owner.

The Agent may:
- Inspect repository code and documentation.
- Implement an explicitly approved Task Card.
- Write or update scoped tests.
- Run checks and tests.
- Prepare live preview instructions.
- Commit to the assigned branch.
- Prepare a PR only when Main Control explicitly requests it.

The Agent must not:
- Change product direction.
- Redesign approved screens.
- Merge to main.
- Open unrelated PRs.
- Add unrelated fixes.
- Rewrite architecture because another design seems cleaner.
- Treat legacy screenshots as active design authority.
- Continue when required Screen Pack artifacts are missing.

## 3. Current repository state

Treat main as the source branch.

Known completed product setup work includes:
- Approved Login on main.
- Setup Gate Web merged and closed.
- Setup Gate Mobile/Tablet pack merged.
- Activity selection flow merged.
- Commercial and Services sub-activity selection merged.
- Modules Selection merged.
- Review Setup merged.

Do not reopen completed Setup Gate work unless Ahmed explicitly approves it.

Dashboard 120 contains planning/functional/layout/visual work. It is not an authorization to implement production dashboard code.

Current Main Control direction is to continue the real operating cycle before returning to the Dashboard.

## 4. Current next track

Current implementation candidate:

121 — Purchase Invoice UI / Purchase Operating Track

Important:
- Planning may start.
- Production implementation may NOT start until its Screen Pack is approved.
- Existing purchase models and posting services are the business-logic foundation.
- UI must call existing service-layer operations rather than duplicating posting logic.

## 5. Mandatory Screen Pack gate

No new production screen implementation before all required artifacts exist and are approved:

1. Visual Approved
2. Production Background
3. Assets Pack
4. Layout Contract
5. Functional Contract where the screen has business behavior

A screen is not implementation-ready when only a mockup exists.

For responsive screens, the Screen Pack must cover:
- Web
- Tablet Landscape
- Mobile

Arabic and English must be planned from the start.

## 6. Architecture lock

Project architecture is Django + PostgreSQL.

Core rule:
Screens collect input. Business operations go through services.

Do not put posting/accounting/business logic directly in templates, JavaScript, forms, or views when the service layer owns it.

Reports remain read-only unless an explicitly approved task changes that rule.

## 7. Protected areas

Do not change these without explicit Ahmed/Main Control approval:
- Models
- Migrations
- Database structure
- Permission core
- Sales posting logic
- Purchase posting logic
- Inventory movement logic
- Cashbox movement logic
- Reports calculations
- Accounting calculations
- Average cost logic
- Supplier/customer ledger logic

If a UI task appears to require one of these changes, stop and return:
- why the change appears necessary
- exact files affected
- safest alternative
- risk if not changed

Do not implement the protected change automatically.

## 8. Purchase business rules that UI must respect

The existing purchase foundation is authoritative.

Purchase Invoice includes:
- invoice number
- invoice date
- supplier
- receiving location
- optional cashbox
- multiple purchase lines
- subtotal
- discount
- tax
- total
- paid now
- remaining due
- notes
- draft / posted / cancelled status
- credit / partial / paid payment status

Required behavior:
- A purchase invoice affects suppliers, not customers.
- Supplier due is based on remaining due.
- Cashbox is affected only by the amount paid now.
- If paid_now > 0, a cashbox is required.
- Inventory increases through stock movements into receiving_location for stock-tracked items.
- Average cost is recalculated through the existing inventory service.
- Posting is service-driven.
- Cancellation/reversal is service-driven.
- A posted/cancelled invoice must not be casually edited as if it were a draft.

Do not duplicate these calculations in UI code.

## 9. Visual implementation rules

Production UI must use:
- Approved production backgrounds
- Real HTML/CSS/components
- Real translated text
- Real buttons/inputs/actions
- Layout Contract responsive rules
- Approved reusable assets

Never use:
- A full screenshot as the production UI
- Screenshot crops as interactive controls
- Text baked into production background images
- One stretched image for all devices
- CSS masks to fake a full screenshot UI
- A new unapproved design generated during implementation
- Random icon families that break Hesba identity

## 10. Responsive and language rules

Required target classes:
- Web
- Tablet Landscape
- Mobile

Implementation must be responsive beyond only the exact approval viewport sizes.

Arabic:
- RTL
- Arabic-first hierarchy
- Correct arrow/order behavior

English:
- LTR where appropriate
- Same information architecture
- No layout break from longer labels

No translatable text may be baked into production assets.

## 11. Permissions

Core roles:
- Owner
- Manager
- Cashier
- Stock Keeper
- Accountant

Sensitive information includes:
- cost
- profit
- reports
- cashbox balances
- sensitive finance data
- user/system settings

UI must not expose sensitive values/actions merely because data exists in the database.

Permission behavior must come from the approved Functional Contract and project permission system.

## 12. Branch policy

Every execution task starts from updated main.

Use one branch per screen/flow track.

Required lifecycle:

main -> task branch -> scoped commits -> checks/tests -> Main Control review -> PR when requested -> Ahmed approval -> merge

Never commit production work directly to main.

Never merge without explicit approval.

## 13. Required Agent checks

After every Agent/Codex code change, report:

- git status --short
- git log --oneline -3
- git diff --name-only HEAD~1 HEAD

Before PR:
- confirm changed files are in allowed scope
- confirm no protected files changed without approval
- run python manage.py check
- run relevant focused tests
- run broader tests when required by Task Card
- verify Arabic and English
- verify target device classes
- verify routes/buttons
- verify static assets return correctly

## 14. Live preview

Every implementation Task Card must define a preview route.

Default local server:
- 8000
- use 8010 when 8000/forwarding is unstable

Preview review must include:
- Arabic
- English
- target device widths
- RTL/LTR behavior

## 15. Task Card compliance

The Agent executes only the current Task Card.

If the Task Card does not specify:
- screen/goal
- branch
- allowed files
- forbidden files
- required Screen Pack references
- acceptance criteria

then the Agent must not start production implementation.

## 16. Return format

Every execution return must contain exactly these sections:

### Completed
What was implemented.

### Changed files
Exact changed paths.

### Tests
Commands and results.

### Preview
Route, language variants, target sizes, and known preview notes.

### Risks
Any unresolved issue, assumption, protected-area request, or mismatch with the approved Screen Pack.

### Next approval needed
What Ahmed/Main Control must approve before the next action.

## 17. One-line operating rule

Approve the Screen Pack first; then implement only the approved task on a scoped branch; protect existing business logic; test; review; and never merge without Ahmed's explicit approval.
