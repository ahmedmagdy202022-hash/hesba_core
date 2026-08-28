# Master Data Foundation — Batch Functional Contract

Status: DRAFT FOR MAIN CONTROL / BATCH APPROVAL
Track: MASTER_DATA_FOUNDATION_TRACK
Baseline: `develop`

## 1. Shared rules

All six areas are authenticated.

Shared read permission:
- `master_data.view_master_data` for master-data lists/details.
- Cashbox list access also respects `cashboxes.view_cashboxes`.

Shared management permissions:
- Items/Categories: `master_data.manage_items`
- Customers/Suppliers: `master_data.manage_parties`
- Locations: `master_data.manage_locations`
- Cashbox finance/movement visibility remains under cashbox permissions.

Do not create new permission codes unless an approved requirement cannot be expressed with the existing matrix.

All create/edit actions must:
- validate uniqueness;
- preserve auditability where financial meaning exists;
- avoid hard deletion of in-use records;
- prefer active/inactive status for operational retirement;
- keep Arabic/English labels and RTL/LTR behavior.

## 2. Cashboxes

### Purpose
Create and maintain money holders used later by sales receipts, purchase payments, collections, supplier payments, and direct cash movements.

### List
Show:
- code
- Arabic/English name according to language
- currency
- active/default state

Financial balance display:
- only when the viewer has `cashboxes.view_finance`.
- balance must come from report/business logic, not a manually editable displayed field.

### Create/Edit
Fields:
- code
- name_ar
- name_en
- opening_balance
- currency
- is_default
- active
- notes

Permissions:
- view: `cashboxes.view_cashboxes`
- opening balance/finance-sensitive treatment requires finance-aware permission review.
- creating/editing cashbox master data is a HARD GATE if the current permission matrix cannot safely express it without using `cashboxes.move_cash` incorrectly.

### Financial rule
The master screen must never create arbitrary `CashboxMovement` rows just to make a displayed balance match.

Opening balance is financially meaningful. Until Main Control approves its correction semantics:
- allow initial creation according to current model;
- do not design a casual "edit balance" action after operational use.

## 3. Locations

### Purpose
Create inventory locations used by receiving and selling flows.

### List
Show:
- code
- name
- default
- receiving enabled
- selling enabled
- active

### Create/Edit
Fields:
- location_code
- name_ar
- name_en
- description
- is_default
- is_receiving_location
- is_selling_location
- active

Permission:
- view: `master_data.view_master_data`
- manage: `master_data.manage_locations`

### Rules
- codes unique;
- inactive locations remain readable when referenced historically;
- do not hard-delete referenced locations;
- default behavior must not be invented if multiple defaults are technically possible today; flag for a protected/business decision only if enforcement is required.

## 4. Suppliers

### Purpose
Create and maintain supplier parties used by purchases and supplier payments.

### List
Show:
- supplier code
- name
- phone
- active
- optional current due only when the viewer has appropriate supplier-report permission.

### Create/Edit
Fields:
- supplier_code
- name
- phone
- whatsapp
- email
- address
- opening_balance
- notes
- active

Permission:
- view: `master_data.view_master_data`
- manage: `master_data.manage_parties`
- supplier financial balance/report visibility: `reports.view_supplier_report`

### Financial rule
Current supplier balance must never be typed directly into the master record after setup. It is derived from opening balance + supplier ledger/report logic.

Opening balance correction after operational movements is a HARD GATE until accounting semantics are explicitly approved.

## 5. Customers

### Purpose
Create and maintain customer parties used by sales and collections.

### List
Show:
- customer code
- name
- phone
- active
- credit-limit indicator where useful
- current due only when the viewer has `reports.view_customer_report`

### Create/Edit
Fields:
- customer_code
- name
- phone
- whatsapp
- email
- address
- opening_balance
- credit_limit
- notes
- active

Permission:
- view: `master_data.view_master_data`
- manage: `master_data.manage_parties`
- balance/report: `reports.view_customer_report`

### Financial rule
Current customer balance is derived from opening balance + customer ledger/report logic.

Opening balance correction after operational movements is a HARD GATE until explicitly approved.

## 6. Categories

### Purpose
Organize items and services.

### List
Show:
- code
- name
- parent
- active

### Create/Edit
Fields:
- category_code
- name_ar
- name_en
- parent
- active

Permission:
- view: `master_data.view_master_data`
- manage: `master_data.manage_items`

### Rules
- code unique;
- parent optional;
- prevent obvious self-parenting/cycle behavior at form/service level if current model validation does not already prevent it; if this requires model-level constraint change, treat as Hard Gate.
- inactive categories remain historically readable.

## 7. Items & Services

### Purpose
Maintain stock-tracked items and non-stock-tracked services in one shared master.

### List
Show:
- item code
- item/service name
- category
- unit
- stock-tracked/service state
- active
- barcode when useful

Cost-sensitive columns:
- purchase price / average cost only when the user has `inventory.view_cost`.

### Create/Edit
Fields:
- item_code
- barcode
- item_name
- category
- size
- color
- unit
- default_sale_price
- default_purchase_price
- min_stock
- is_stock_tracked
- active

Protected/read-only:
- average_cost must not be casually editable in the user-facing form.

Permission:
- view: `master_data.view_master_data`
- manage: `master_data.manage_items`
- cost visibility: `inventory.view_cost`

### Dynamic behavior
If `is_stock_tracked = false`:
- label the record as Service / خدمة;
- stock-specific fields such as min_stock may be hidden/disabled in UI as appropriate;
- do not create stock movements merely from creating/editing the master.

If `is_stock_tracked = true`:
- stock-specific configuration becomes available;
- current stock is still derived from movements by item + location, not stored/edited here.

## 8. Shared list interactions

Every list should support:
- search;
- active/inactive filter;
- create action when permitted;
- edit action when permitted;
- clear empty state;
- safe pagination if data grows;
- mobile-friendly cards/rows instead of squeezing a desktop table.

Do not expose an action the backend permission will refuse.

## 9. Shared form interactions

Forms should:
- preserve entered data on validation failure;
- show field-level errors;
- show server-side validation errors clearly;
- mark required vs optional fields;
- use real HTML controls;
- support Arabic and English labels;
- keep primary save action visible without excessive visual emphasis;
- support cancel/back without accidental data loss where practical.

## 10. Deactivation vs deletion

Default product direction:
- use active/inactive rather than hard delete for operational masters that may be referenced.

Do not add destructive delete buttons by default.

If deletion is later required, it needs a separate contract covering references/audit behavior.

## 11. Routes — proposed family

Proposed route family, subject to implementation review:

- `/master-data/`
- `/master-data/locations/`
- `/master-data/cashboxes/`
- `/master-data/suppliers/`
- `/master-data/customers/`
- `/master-data/categories/`
- `/master-data/items/`

Create/edit routes should follow one consistent pattern.

The Agent may adjust exact URL naming to match Django conventions already used in the repo without asking Ahmed, provided navigation meaning is unchanged.

## 12. Hard Gates already identified

### HG-MD-01 Cashbox management permission
Current permissions contain:
- `cashboxes.view_cashboxes`
- `cashboxes.move_cash`
- `cashboxes.view_finance`

There is no explicit `cashboxes.manage_cashboxes` permission.

Do not misuse `move_cash` as a generic master-data edit permission without Main Control approval.

All non-cashbox screens may continue while this is isolated.

### HG-MD-02 Opening balance correction semantics
Customer, Supplier, and Cashbox opening balances exist on the models.

The repo does not currently encode a complete UX/accounting rule for changing them after operational movements exist.

Initial creation can be planned, but post-operation correction behavior must not be invented.

## 13. Batch acceptance target

For Ahmed's review, present one batch containing:
- six functional screen summaries;
- one shared visual shell;
- six Web mockups;
- six Tablet Landscape mockups;
- six Mobile mockups;
- one shared assets family;
- screen-specific layout notes only where behavior differs;
- the two Hard Gates above highlighted separately.

After batch approval, the Agent should implement the whole Track without stopping screen-by-screen.
