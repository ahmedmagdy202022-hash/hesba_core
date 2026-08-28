# Hesba Master Execution Plan

Status: ACTIVE CONTROL PLAN
Owner: Ahmed / Main Control Chat
Execution: Agent/Codex under AGENTS.md
Repository: ahmedmagdy202022-hash/hesba_core

## 1. Objective

Finish Hesba as a usable responsive Arabic/English operating system without losing the approved visual identity or corrupting the existing PostgreSQL/Django business foundation.

The Agent carries implementation effort.
Ahmed remains Product Owner and final approval authority.
Main Control defines tasks, reviews output, and controls PR/merge.

## 2. Non-negotiable workflow

For every new screen/flow:

1. Screen Definition
2. Functional Contract
3. Visual Approved — Web / Tablet Landscape / Mobile
4. Production Background
5. Assets Pack
6. Layout Contract
7. Ahmed Screen Pack approval
8. Implementation Task Card
9. Agent implementation on scoped branch
10. Tests + Arabic/English + responsive preview
11. Main Control PR review
12. Ahmed merge approval
13. Merge
14. Checkpoint update
15. Next track

No production implementation may skip the Screen Pack gate.

## 3. Current completed foundation

The repository already contains the core business foundation for:
- Master data
- Purchases
- Sales
- Inventory
- Customer payments
- Supplier payments
- Cashboxes
- Reports
- Closing/period support
- Imports
- Audit
- Roles/permissions foundation
- CI/testing foundation

The operating UI must reuse this foundation instead of rebuilding business logic inside screens.

## 4. Completed UX/setup foundation

Treat these as completed unless Ahmed explicitly reopens them:
- Login
- Setup Gate Web
- Setup Gate Mobile/Tablet
- Activity selection
- Commercial sub-activity selection
- Services sub-activity selection
- Modules selection
- Review Setup

No Agent task may reopen them as a side effect.

## 5. Dashboard 120 status

Dashboard work already has planning artifacts.

Current control decision:
- Preserve 120 work.
- Do not delete or rewrite it.
- Defer production dashboard implementation while the core operating screens are completed.
- Return to 120 later with real operating flows and stronger data context.

This prevents building a decorative dashboard before the operational cycle is usable.

## 6. Current execution track — 121 Purchase Invoice

### Goal

Create the first real operating UI track for Purchase Invoices using the existing purchase business foundation.

### Existing foundation to reuse

Purchase domain already supports:
- Supplier
- Receiving location
- Cashbox for paid-now amount
- Multi-line invoice items
- Draft / Posted / Cancelled
- Credit / Partial / Paid
- Supplier remaining due
- Stock increase on posting
- Cashbox movement for paid_now
- Average cost recalculation
- Cancellation/reversal foundation

### 121 work packages

#### 121A — Purchase Invoice Functional Contract

Define before visual design:
- user roles
- route map
- create flow
- edit draft flow
- invoice lines behavior
- supplier selection
- receiving location selection
- cashbox rules
- paid_now behavior
- totals display
- validation/error states
- save draft action
- post action
- cancel/reversal visibility
- permission visibility
- Arabic/English labels
- empty/loading states
- mobile/tablet behavior
- relation to supplier, inventory, cashbox, and reports

Output:
- Functional Contract only
- No production implementation

#### 121B — Purchase Invoice Screen Pack

Prepare and approve:
- Web Visual Approved
- Tablet Landscape Visual Approved
- Mobile Visual Approved
- Production Backgrounds
- Assets Pack
- Layout Contract
- final Functional Contract

Output:
- Approved Screen Pack
- No production implementation until Ahmed approves the pack

#### 121C — Purchase Invoice Implementation

Only after 121B approval:
- create scoped feature branch from main
- implement real HTML/CSS/forms/views/routes as allowed by Task Card
- call existing services for posting/cancellation
- do not duplicate accounting/business logic
- add focused tests
- provide live preview

#### 121D — Purchase Invoice QA / PR

Required:
- Web review
- Tablet Landscape review
- Mobile review
- Arabic review
- English review
- draft flow
- posting validation
- paid_now/cashbox rule
- route/action checks
- protected file diff review
- tests
- PR review
- Ahmed merge approval

## 7. Operating-cycle sequence after 121

Exact checkpoint numbers may be adjusted by Main Control, but the functional order is:

1. Purchase Invoice operating track
2. Supplier-facing operational screens needed by the purchase cycle
3. Inventory/stock operational visibility needed after purchase posting
4. Sales Invoice operating track
5. Customer collection/payment operating track
6. Supplier payment operating track
7. Cashbox operating screens
8. Returns/reversals and exception flows
9. Reports operational UX
10. Dashboard 120 resume using real product flows
11. Security/permission hardening
12. Delivery/install/support package

Do not start the next track before the current one is merged and checkpointed unless Ahmed explicitly authorizes parallel planning.

## 8. What the Agent may automate

Within an approved Task Card, the Agent should handle:
- repository inspection
- scoped code changes
- form/view/template wiring
- CSS/responsive implementation
- translation-ready UI
- tests
- debugging
- route verification
- preview preparation
- diff review
- PR preparation when requested

The Agent should reduce manual coding work, not remove approval gates.

## 9. What remains human-controlled

Ahmed/Main Control controls:
- screen purpose
- business meaning
- design approval
- Screen Pack approval
- permission decisions
- protected logic changes
- product prioritization
- PR approval
- merge approval

## 10. Immediate next action

Start 121A only:

"Purchase Invoice Functional Contract"

Do not write production UI code yet.

The first deliverable should answer:
- What screens are required for the Purchase Invoice track?
- What fields/actions appear on each screen?
- What is allowed in Draft?
- What is locked after Posted?
- What permissions apply?
- What existing services are called?
- What happens to supplier due, cashbox, inventory, and average cost?
- What are Web/Tablet/Mobile interaction requirements?
- What Arabic/English and validation states are required?

After 121A is reviewed by Main Control and approved by Ahmed, proceed to 121B Screen Pack.
