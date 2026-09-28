# Hesba — the actual state of the project (DOC-002)

**Last updated:** 29 September 2026 · `develop` after PR #133

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
| Label templates by size (A4 at 21/24/40, rolls 38×25 to 50×30) + choosing fields + print offset | #114 |
| Owner's daily summary (`/reports/daily/`) + sending it on WhatsApp | #115 |
| Company details frozen on each posted invoice (a reprint shows the details from its own date) | #116 |
| POS: find a customer by phone, or add one from the till | #117 |
| Two-step sign-in (2FA) with an authenticator app + recovery codes | #118 |

### Client-owned deployment (28–29 September)
| Feature | PR |
|---|---|
| Backups encrypted to the owner's key (only the owner can open them) | #120 |
| Runs on PostgreSQL (client-owned Supabase) + CI on PostgreSQL; fixed row locks that stopped posting on PostgreSQL, overselling of the last unit, and the month-opening race (HG-026, HG-027) | #121 |
| Nightly encrypted backup to the client's own Google Drive (`drive.file` only) | #122 |
| Real database size vs the plan limit + dashboard warning + safe cleanup | #123 |
| The till keeps selling offline and syncs each sale once (HG-028) | #124 |
| Tests stable across midnight | #125 |
| Render blueprint + Arabic go-live guide (`docs/GO_LIVE.md`) | #126 |

### Every activity open in the setup wizard (29 September)
| What | PR |
|---|---|
| Employees and technicians (commission, linked login) | #128 |
| Appointments and visits: agenda, no double booking for an employee, WhatsApp reminder, billing through sales, employee performance | #129 |
| Restaurants and cafés: tables, dine-in/takeaway/delivery orders, kitchen ticket, bill through the POS checkout itself | #130 |
| Medical, education and "other" activities, with their sub-activities and presets | #131 |
| Contracting: projects, progress bills, materials issued to site, linked expenses, project profit (HG-029) | #132 |
| Manufacturing: recipes (BOM) and production runs that take out materials and bring in the product at their cost (HG-030) | #133 |

Every protected-logic decision is recorded in `docs/AGENT_HARD_GATES.md` (HG-001 to HG-030; HG-029 and HG-030 are open proposals that block nothing).

---

## 2. Still missing, in order of importance

1. **The first real client:** follow `docs/GO_LIVE.md` (Supabase owned by the client, Render, Drive, cron-job.org).
2. **Google OAuth app for Hesba:** one-time setup in the Google Cloud console, set to "In production" (`docs/BACKUP_DRIVE_SETUP.md`).
3. **Server-generated PDF**, for sending by email. Today PDF means "Save as PDF" from the browser.
4. **E-invoice phase 3** (signing and submission) + credit notes for returns + the e-receipt for the POS. Waiting for the first real client with an account and a signature.
5. **The offline till opening from scratch without internet** (a service worker). Today the page has to be open before the connection drops.
6. **Choosing a batch or serial number on the sales return form itself.** This touches return posting, so it needs a Hard Gate first.
7. **2FA extras:** a QR image, and a setting that makes 2FA mandatory for the owner role.
8. **Design track D:** fonts, components, empty states.
9. **Cleanup:** about 80 old branches on GitHub.
10. **Restaurants, round two:** item modifiers (sizes and add-ons), deducting ingredients by recipe on sale (needs a Hard Gate), split bill, service charge, kitchen display.
11. **Education:** student groups and monthly fees (subscriptions). Today it runs on customers, appointments and invoices.
12. **Dedicated movement types** for "issued to project" and "production" (HG-029, HG-030), so the inventory report tells them apart from shrinkage.

---

## 3. Parked ideas (from Ahmed; no work on them now)

### 🍽️ Restaurants and cafés (Ahmed's note, 28 Sep 2026) — round one done in #130
Needs a different experience and a different look from the shop POS, and must not slow down the current work. Expected items when we start:
- a table screen (open / occupied / bill requested) and taking orders per table;
- menu items with **modifiers** (sizes, add-ons, "no onion"), and recipes that deduct ingredients from stock (recipe / BOM);
- order printing to the kitchen and bar (KOT) separately from the customer invoice;
- splitting and merging bills, service charge, and delivery or takeaway;
- a large touch-first UI for the tablet.
The right start: a separate activity `restaurant` in the setup wizard with its own capabilities, on top of the same sales and inventory engine, without duplicating the accounting.

### Other ideas from the report
- ~~A morning summary on WhatsApp~~ → done as the daily summary (#115); **sending it automatically** each morning needs a WhatsApp Business API account.
- A customer self-service page (statement and instalments) + an InstaPay or wallet payment reference.
- ~~A POS that keeps working when the internet drops~~ → done (#124).
- Simple loyalty points.
- AI: supplier invoice photo → purchase draft, and "Ask Hesba" (read-only). AI only suggests; it never posts.
