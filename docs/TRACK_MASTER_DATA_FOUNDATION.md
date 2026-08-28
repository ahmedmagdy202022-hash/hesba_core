# Track — Master Data Foundation

Status: PLANNING / BATCH SCREEN PACK REQUIRED
Baseline: `develop`
Operating mode: LOW-INTERRUPTION AUTONOMOUS TRACK

## 1. Track goal
Prepare and implement the foundational records Hesba needs before the commercial operating cycle begins.

The Track must give the user working screens to create and manage:
1. Cashboxes
2. Locations
3. Suppliers
4. Customers
5. Categories
6. Items & Services

The Track should feel like one product family, not six separate mini-projects.

## 2. Why this Track comes first
Purchase invoices require supplier + receiving location + items, and paid-now behavior may require a cashbox.

Sales require customer + selling location + items, and immediate receipt may require a cashbox.

Therefore the operating cycle should not be the first UI work after setup; Master Data must exist first.

## 3. Existing backend foundation
Reuse existing models. Do not create replacement models just for UI.

### Cashbox
Current fields include:
- cashbox_code
- name_ar
- name_en
- opening_balance
- currency
- is_default
- active
- notes

### Location
Current fields include:
- location_code
- name_ar
- name_en
- description
- is_default
- is_receiving_location
- is_selling_location
- active

### Supplier
Current fields include:
- supplier_code
- name
- phone
- whatsapp
- email
- address
- opening_balance
- notes
- active

### Customer
Current fields include:
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

### Category
Current fields include:
- category_code
- name_ar
- name_en
- parent
- active

### Item
Current fields include:
- item_code
- barcode
- item_name
- category
- size
- color
- unit
- default_sale_price
- default_purchase_price
- average_cost
- min_stock
- is_stock_tracked
- active

Important:
- `average_cost` is controlled system data and must not be a casual editable field.
- cost/purchase-price visibility must respect permissions.
- opening balances are financially meaningful and require explicit functional/permission treatment.

## 4. Shared UX system
All six areas should reuse one Master Data design system.

Shared screen pattern should support:
- list/search screen;
- create action;
- edit/detail action where allowed;
- active/inactive state;
- clear empty state;
- filters where useful;
- Arabic and English;
- RTL/LTR;
- responsive Web / Tablet Landscape / Mobile;
- consistent Hesba header/navigation;
- consistent form layout;
- consistent save/cancel patterns;
- validation/error feedback;
- no generic Django Admin look in the user-facing application.

## 5. Screen family

### MD-01 Cashboxes
Purpose:
Create/manage money holders used later by invoice/payment flows.

Functional questions/contracts must define:
- who may create/edit;
- opening balance visibility/editability;
- default cashbox rule;
- whether an in-use cashbox may be deactivated;
- currency behavior;
- what happens if multiple rows try to become default;
- list balance visibility by permission;
- no direct arbitrary movement creation through this master-data screen.

### MD-02 Locations
Purpose:
Create/manage physical/logical stock locations.

Must define:
- default location;
- receiving/selling flags;
- deactivation when referenced;
- naming/code validation;
- list and form behavior.

### MD-03 Suppliers
Purpose:
Create/manage suppliers used by purchases and supplier payments.

Must define:
- opening balance meaning;
- contact fields;
- active/inactive behavior;
- duplicate/code handling;
- future statement navigation placeholder/route contract;
- supplier balance is derived from proper ledger/report logic, not manually overwritten.

### MD-04 Customers
Purpose:
Create/manage customers used by sales and collections.

Must define:
- opening balance;
- credit limit;
- contact fields;
- active/inactive behavior;
- duplicate/code handling;
- future statement navigation placeholder/route contract;
- customer balance is derived from proper ledger/report logic.

### MD-05 Categories
Purpose:
Organize items/services.

Must define:
- optional parent hierarchy;
- deactivation behavior;
- category selection in Item form;
- code/name validation.

### MD-06 Items & Services
Purpose:
Create both stock-tracked items and non-stock-tracked services in one shared master.

Must define:
- item vs service behavior using `is_stock_tracked`;
- barcode;
- category;
- unit;
- size/color optionality;
- sale price;
- purchase price visibility;
- average cost read-only/protected;
- minimum stock only when stock-tracked;
- active/inactive behavior;
- field changes when toggling stock tracking.

## 6. Permissions contract
Before implementation, map each action to existing permission codes where possible.

Minimum considerations:
- Owner
- Manager
- Cashier
- Stock Keeper
- Accountant

Sensitive fields:
- opening balances
- purchase price
- average cost
- derived balances
- finance-sensitive status

Rule:
Do not hardcode a role name into a view when existing permission infrastructure can express the rule.

If a required permission does not exist, that is a Hard Gate because permission-core changes are protected.

## 7. Opening-balance warning
Opening balances are not ordinary cosmetic fields.

Before implementation of editing behavior, Functional Contracts must decide:
- whether opening balance is editable only before operational movements exist;
- whether later corrections require a controlled adjustment flow;
- audit requirements;
- permission requirements.

The Agent must not invent accounting semantics.

## 8. Functional-first screen contract for this phase

Current Ahmed decision: finish the operating cycle before final aesthetic design.

For this Track, prepare only what is necessary to make the screens real and reviewable:
- Functional Contract
- content hierarchy
- fields, tables/cards, filters, actions, states
- Web / Tablet Landscape / Mobile layout behavior
- Arabic / English behavior
- simple Hesba-aligned shell using existing approved colors and typography direction

Deferred until the later Visual Polish Track:
- custom illustrations
- decorative backgrounds
- final icon family
- advanced visual styling
- empty-state artwork
- aesthetic micro-tuning

Do not block functional implementation because a decorative asset is missing. Prefer text labels and simple CSS/components during this phase.

Do not invent a new logo, palette, or generic SaaS identity.

## 9. Implementation strategy after batch approval
Use one track branch from `develop`, for example:

`feature/master-data-foundation-track`

Implement in this internal order:
1. shared Master Data UI shell/components
2. Locations
3. Cashboxes
4. Categories
5. Suppliers
6. Customers
7. Items & Services
8. cross-screen navigation
9. Arabic/English QA
10. responsive QA
11. regression suite
12. track PR preparation

The Agent should not stop after each screen.

## 10. Testing expectations
At minimum, relevant tests should cover:
- authentication;
- permission gates;
- create;
- edit;
- invalid input;
- uniqueness/code collisions;
- inactive behavior;
- protected/sensitive fields;
- Arabic/English route/render behavior where applicable;
- responsive UI via preview/manual evidence;
- no accidental model/business-logic change.

For opening balances and sensitive values, tests must prove the intended rules rather than only checking page rendering.

## 11. Protected areas
Without explicit approval, do not change:
- model definitions;
- migrations;
- accounting calculations;
- customer/supplier ledger logic;
- cashbox movement logic;
- inventory movement logic;
- permission core.

If current models cannot support an approved UX rule, mark the exact conflict as a Hard Gate and continue all unaffected screens/work.

## 12. Definition of Track Ready for Ahmed
Do not bring the Track back to Ahmed until:
- all six Functional Contracts are drafted;
- the shared design system is coherent;
- all Web/Tablet/Mobile visuals are prepared;
- asset classifications are clear;
- layout contracts are prepared;
- genuine business/permission hard gates are isolated.

Ahmed should receive one compact batch review.

## 13. Definition of Track Done after approval
After Ahmed approves the batch:
- all six screens implemented;
- navigation connected;
- Arabic/English checked;
- Web/Tablet/Mobile checked;
- relevant tests pass;
- diff reviewed;
- no unrelated files changed;
- PR prepared;
- remaining Hard Gates clearly listed;
- one merge decision requested.
