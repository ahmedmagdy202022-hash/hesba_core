# Audit: every activity, every role, every screen (October 2026)

Asked for by Ahmed after the first demo feedback, before testers see the demo:
"every activity needs a track on every button press, and a precise review".

## How it was checked

- **Crawl.** A throwaway crawler (never committed) set up each of the 51 sub-activities with its own sample data (`seed_demo_business` and `prepare_demo`, the same ones the demo uses). For each role (owner, manager, accountant, cashier, stock keeper) it:
  - opened every page reachable from the menu and the dashboard (about 100–190 pages per role);
  - followed every link on those pages;
  - submitted every form it found with its hidden fields and empty visible fields.

  It recorded:
  - server errors;
  - broken links;
  - links that are shown but lead to "not allowed" or "module not enabled";
  - internal codes or English text on Arabic pages;
  - template leaks.
- **Screens.** For one sub-activity of each of the 8 activities, the real demo database was loaded, and screenshots were taken of:
  - the owner on desktop (1366), tablet (1024) and mobile (390);
  - the cashier on mobile.

  Each run also checked for page overflow and JavaScript errors.

## What the crawl found (before the fixes)

- **No server errors and no exceptions** on any page or any form, in any of the 51 sub-activities and 5 roles.
- **Links that open a closed door** (33 kinds):
  - "Account" cards for parties the role cannot see;
  - "New invoice", "Till" and "New purchase" shown to the accountant;
  - links into switched-off modules, such as purchases in a school or a clinic, from reports, cashboxes, party cards, the master-data hub and quick actions;
  - the tax report shown to the stock keeper.
- **Internal words on Arabic screens:**
  - permission codes on Profile and Roles;
  - English posting descriptions in cashbox movements and the party report;
  - account control keys in the chart of accounts;
  - opening-balance references in the journal;
  - setting keys in the settings table.

After the fixes, a second crawl of 10 representative sub-activities found **no** closed-door links and **no** internal words.

A final crawl of all 51 sub-activities on the fixed code opened 22,118 pages and submitted 7,422 forms. It found:
- no server errors and no internal words;
- one closed-door link: "← Stock" on the batches and serials pages, in a lab, a medical centre, a vet and a repair shop, where those capabilities are suggested but the stock module is off.

That link now goes to Home when stock is closed.

## Roles: what each one sees (after the fixes)

| Role | Sees | Does not see |
|---|---|---|
| Owner | everything | — |
| Manager | daily work: selling, buying, stock, reports, staff, settings | period closing, permissions |
| Accountant | sales and purchases (to read), parties, cash, expenses, reports, ledger, staff | the till, new invoices, new purchases |
| Cashier | selling, the till, customers (patients/students), items, the day's cashbox, own sales report | staff list, tax settings, setup steps |
| Stock keeper | stock, warehouses, purchases (receiving), items, suppliers, manufacturing | customers / patients / students, staff list, setup steps |

**Changes:**
- The customer list (patients in a clinic, students in a school) is for people who sell, collect or report on customers. Its menu item and its page both check this (`CUSTOMER_AUDIENCE`).
- The staff list is for the owner, the manager and the accountant (`STAFF_AUDIENCE`), and the same group sees the tax settings in the menu.
- "Start using Hesba in 4 steps" shows only to whoever can change settings (the owner). A cashier or stock keeper sees their own quick actions instead.

## Activities: what fits each one

**Services can invoice.**
- The services preset left out "Sales operations", so a maintenance company or an office had no way to issue an invoice. Medical and education, which are also services, already required it.
- It is now required for services too.
- This reverses a choice in `docs/118_MODULES_SELECTION_PLAN.md`; the tests that encoded it are updated.

**Capabilities per activity.** Until now only commercial and manufacturing had suggestions; every other activity was shown the 11 trade capabilities as they are. Now:

| Activity | Not offered | Suggested |
|---|---|---|
| Contracting | quick till, barcodes, sizes/colours, serials, batches, instalments, price lists | units (metres, m², m³, tonnes), fixed assets (equipment) |
| Education | quick till, barcodes, sizes/colours, serials, batches, units | instalments (student fees) |
| Medical | sizes/colours, serials | dental: instalments; medical centre/lab: fixed assets, batches; vet: batches, till |
| Restaurants | serials, instalments | units; café: till, drink sizes; fast food / cloud kitchen: till; bakery: till, barcodes, batches |
| Services | sizes/colours, batches | maintenance: serial numbers and warranty; beauty: till |
| Other | — | till, barcodes |

Notes on the presets:
- A capability that is already on still shows in Settings, so it can be switched off.
- VAT and e-invoicing are never switched on by a preset outside wholesale, because they change what a bill adds up to.
- Where the general wording does not fit, the description speaks the activity's language:
  - "metres, m², m³, tonnes, bags" for a builder;
  - "student fees in instalments" for a school;
  - "drink sizes" for a café;
  - "each device by its serial number" for a repair shop.

**Words.**
- The sales list and the new-invoice screen use the activity's own words:
  - restaurant: "Orders & bills" and "New order";
  - clinic: "Visits & bills" and "New visit bill";
  - builder: "Progress bills & invoices";
  - repair shop: "Work orders & invoices".
- The invoice's party field reads "the patient", "the student" or "the project owner".
- The appointments page title follows the menu: "Bookings" in a clinic, "Classes" in a school.
- A restaurant (no customers module) starts a manual invoice on the walk-in customer.

## Screens

On desktop, tablet and mobile:
- no page overflowed;
- no JavaScript error appeared on the sampled screens;
- the only console errors were the 403s of pages deliberately opened outside a role's menu.

## Open decisions for Ahmed (not built)

These need a decision because they touch protected logic (customer/supplier ledgers, posting, report figures). They are recorded here and will go to `docs/AGENT_HARD_GATES.md` once approved:

1. **Contracting:**
   - bill of quantities (BOQ) with progress bills drawn from it;
   - retention withheld from each progress bill and released at handover;
   - advance payments recovered from progress bills;
   - subcontractor progress bills;
   - per-project budget against actual.
2. **Date picker.** Empty date fields show the browser's own placeholder (for example `mm/dd/yyyy`). Fixing this needs the custom picker already discussed (D2.2).
