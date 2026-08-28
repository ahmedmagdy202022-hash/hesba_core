# Hesba Master Execution Control

Status: ACTIVE CONTROL BASELINE
Current baseline: `develop`
Owner: Ahmed / Main Control
Execution: Agent/Codex
Operating mode: LOW-INTERRUPTION AUTONOMOUS TRACKS

## 1. Purpose
Future Hesba work must continue from the latest accepted engineering baseline without losing code quality, business safety, or Hesba identity.

Ahmed should not be asked to approve routine technical micro-steps.

Work is organized into complete Tracks. Each Track is planned as one package, visually approved as a package where practical, then implemented/tested end-to-end by the Agent before Ahmed is asked for a merge decision.

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
- `docs/DEVELOP_BASELINE_AUDIT_2026-08-28.md`
- active Hesba operating rules
- current Main Control Track Card
- approved Screen Packs for the active Track

## 4. Low-interruption execution model
For an approved Track:

Main Control prepares:
1. Functional Contracts for all screens in the Track.
2. Screen Packs for all screens/devices in the Track.
3. One Track Task Card.
4. Allowed/protected file boundaries.

Ahmed reviews the Track package once.

After approval, the Agent continues without stopping through:
- implementation;
- focused bug fixing;
- tests;
- Arabic/English;
- Web/Tablet/Mobile responsiveness;
- route/action wiring;
- documentation/checkpoints;
- PR preparation.

The Agent stops only at a Hard Gate defined in `AGENTS.md`.

## 5. Current first operating Track
The first operating Track is not Purchase Invoice.

It is:

`MASTER_DATA_FOUNDATION_TRACK`

Reason:
The operating cycle needs real foundational records before invoices and payments can work naturally.

Track family:
1. Cashboxes / الخزن
2. Locations / المخازن أو نقاط التخزين
3. Suppliers / الموردون
4. Customers / العملاء
5. Categories / التصنيفات
6. Items & Services / الأصناف والخدمات

These screens should share one coherent Hesba master-data visual system so Ahmed can approve them as one family instead of reviewing six unrelated designs.

Detailed control:
`docs/TRACK_MASTER_DATA_FOUNDATION.md`

## 6. Operating cycle after Master Data
After Master Data is complete and merged:

1. Purchases
   - Purchase Invoice
   - supplier due behavior
   - immediate payment behavior
   - purchase reversal/return UX as required

2. Inventory operational views
   - stock by location
   - stock visibility needed by purchase/sales
   - controlled stock actions

3. Sales
   - Sales Invoice
   - customer due behavior
   - immediate receipt behavior
   - sales reversal/return UX

4. Collections and supplier payments

5. Cashbox operational flows

6. Reports UX

7. Dashboard reconciliation/finalization

8. Security/permission hardening and end-to-end testing

9. PostgreSQL/deployment/delivery readiness

## 7. Dashboard note
`develop` already contains real dashboard engineering work.

Preserve it.

Do not automatically treat its current visual/product state as final.

Return to Dashboard after the operating flows are clear, then reconcile existing engineering work with the approved Hesba Screen Pack.

## 8. Known sales-cost risk
Before Sales is declared release-ready, review the documented stale `average_cost` risk from the develop baseline audit.

Do not fix it incidentally inside UI work.

## 9. Setup Gate note
Do not reopen Setup Gate Web unless Ahmed explicitly approves it.

Completed Setup/identity work must not be modified as collateral cleanup.

## 10. Approval rhythm
Target rhythm for Ahmed:

- one approval for a full Track Screen Pack;
- one merge/release decision after the Agent finishes and Main Control reviews the Track.

Avoid asking Ahmed to approve:
- ordinary refactors inside scope;
- routine responsive corrections;
- obvious regression fixes;
- test additions;
- implementation details already dictated by contracts/architecture.

## 11. Merge rule
No final merge into `develop` or `main` without the required Ahmed approval unless Ahmed explicitly pre-authorizes that exact merge scope.

The Agent should finish as much as possible before presenting the merge decision.
