# Hesba — the actual state of the project (DOC-002)

**Last updated:** 28 September 2026 · `develop` after PR #112

> `docs/HESBA_ROADMAP.md` is Main Control's task board and has fallen behind: many
> items it lists as ⏳ are done. This file records **what actually exists today on
> `develop`**, so the roadmap can be updated from it. It doesn't replace the roadmap.

---

## 1. What's built and merged

### Basic operations
| Area | What exists | PR |
|---|---|---|
| Sales and purchases | Drafts → posting → append-only reversal, returns, collections and payments, closed periods | up to #83 |
| Inventory | Movements, transfers, adjustments, stock count, average cost from movements | #94 |
| Cash | Cashboxes, cash in/out, transfers, opening balances with audited adjustments | — |
| Expenses | Categories, expenses from a cashbox, net profit | #81 |
| Printing | A4 invoice + 80mm receipt, vouchers, returns, **the client's own logo** | #84, #107 |
| Barcode | Internal EAN-13 generation, scanning, labels | #85 |
| POS | Quick cashier, change, 80mm receipt | #86 |
| Users | Roles and permissions, user management, Django Admin locked | #87 |
| Import | Items, customers and suppliers from Excel | #88 |
| Receivables | Due dates + ageing + WhatsApp reminders | #89 |
| Dashboard | Analytic v2 + alerts | #90 |
| Deployment | Production settings, backups and verification, healthz | #92 |

### Capabilities (switched on or off per activity, from Settings → Capabilities)
| Capability | PR |
|---|---|
| Price lists (retail / wholesale / per customer) | #96 |
| VAT per item (sales + purchases, recoverable) | #97, #98 |
| E-invoice (data + ETA v1.0 file; **no signing or submission**) | #99 |
| Units of measure (carton / box / piece) | #100 |
| Batches and expiry dates (FEFO) | #101 |
| Sizes and colours | #102 |
| Serial / IMEI and warranty | #103 |
| Instalment sales | #104 |
| Fixed assets and depreciation | #105 |

### The night of 28 September
| Feature | PR |
|---|---|
| Sign-in lock after wrong passwords + idle sign-out | #106 |
| Client logo on invoices and receipts, and hiding the Hesba mark | #107 |
| Customer / supplier card + account statement (screen and print) | #108 |
| Cashier shift: float, expected cash, difference, posting the difference | #109 |
| Search across the whole app (`/` or Ctrl+K) | #110 |
| Sending invoices and statements on WhatsApp via a secure link | #111 |
| Purchase suggestions ("buy soon") → pre-filled purchase draft | #112 |

Every protected-logic decision is recorded in `docs/AGENT_HARD_GATES.md` (HG-001 to HG-024).

---

## 2. Still missing, in order of importance

1. **Scheduled off-site backups.** The commands exist; running them on a schedule to an external store is an operations decision (where, and at what cost).
2. **Two-step sign-in (2FA) for the owner.**
3. **Snapshot of company details on each posted invoice**, so reprinting an old invoice shows the details from its own date. Needs a new table.
4. **Server-generated PDF**, for sending by email. Today PDF means "Save as PDF" from the browser.
5. **E-invoice phase 3** (signing and submission) + credit notes for returns + the e-receipt for the POS. Waiting for the first real client with an account and a signature.
6. **Choosing a batch or serial number on the sales return form itself.** Today it's done from the batch and serial screens.
7. **Label templates by size** (38×25, 50×30, A4 at 21/40 labels) with a choice of fields.
8. **Design track D:** fonts, components, empty states (only partly done).
9. **Cleanup:** about 80 old branches on GitHub.

---

## 3. Parked ideas (from Ahmed; no work on them now)

### 🍽️ Restaurants and cafés (Ahmed's note, 28 Sep 2026)
Needs a different experience and a different look from the shop POS, and must not slow down the current work. Expected items when we start:
- a table screen (open / occupied / bill requested) and taking orders per table;
- menu items with **modifiers** (sizes, add-ons, "no onion"), and recipes that deduct ingredients from stock (recipe / BOM);
- order printing to the kitchen and bar (KOT) separately from the customer invoice;
- splitting and merging bills, service charge, and delivery or takeaway;
- a large touch-first UI for the tablet.
The right start: a separate activity `restaurant` in the setup wizard with its own capabilities, on top of the same sales and inventory engine, without duplicating the accounting.

### Other ideas from the report
- A morning summary on WhatsApp for the owner (yesterday's sales, the cash, late payers, what expires soon).
- A customer self-service page (statement and instalments) + an InstaPay or wallet payment reference.
- A POS that keeps working when the internet drops (a queue that syncs when it comes back).
- Simple loyalty points.
- AI: supplier invoice photo → purchase draft, and "Ask Hesba" (read-only). AI only suggests; it never posts.
