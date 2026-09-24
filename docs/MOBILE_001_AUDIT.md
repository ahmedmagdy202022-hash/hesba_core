# MOBILE-001 — Mobile & Tablet Verification

Status: AUDIT COMPLETE — findings below, no code changed
Roadmap item: 5.5 `MOBILE-001`
Audited: `develop` @ `7a262a3`, 2026-09-24

> **الخلاصة:** البرنامج **بيشتغل** على الموبايل والتابلت: مفيش صفحة واقعة، مفيش صفحة بتتحرك بالعرض، ومفيش أخطاء JavaScript في 171 حالة فحص.
> لكن فيه **مشكلة استخدام كبيرة**: في 29 شاشة، الجداول عرضها لا يقل عن 760px والموبايل 390px، فأهم عمود (المبلغ / الكمية / الرصيد) **مخفي** ولازم المستخدم يسحب الجدول بالعرض من غير أي إشارة إن فيه أعمدة تانية.
> **التابلت ممتاز.**

## Method

- Headless Chromium through Playwright. Signed in as the seeded `owner` (sees every screen and cost column).
- Database: fresh SQLite with `seed_demo_users` and `seed_demo_business --username owner`, so every list has real rows.
- **57 paths**, listed exactly in `docs/mobile_001/urls.json`: every named, parameterless route, plus a real sales invoice, purchase invoice, item card, cashbox and customer edit form. The ids are the ones `seed_demo_business` creates on a fresh database.
  - 55 of them are screens.
  - `/` and `/start/` are redirect-only routes and land on `/dashboard/` by design.
  - No load ended on the login page.
- **3 modes** give 171 page loads:
  - mobile 390×844 in Arabic
  - mobile 390×844 in English
  - tablet landscape 1024×768 in Arabic
- Each load records:
  - HTTP status
  - JS/console errors
  - viewport meta
  - page-level horizontal overflow and the outermost element causing it
  - tables wider than the viewport, and which header cells fall outside it
  - tap targets under 36 px
  - text under 12 px
- Re-runnable with `scripts/mobile_audit.js`; its header has the usage.

## Results

| Check | mobile AR | mobile EN | tablet AR |
|---|---|---|---|
| Pages loaded (HTTP 200) | 57/57 | 57/57 | 57/57 |
| JS / console errors | 0 | 0 | 0 |
| `<meta name="viewport">` present | 57/57 | 57/57 | 57/57 |
| Page scrolls sideways | **0** | **0** | **0** |
| Tables wider than the screen | **29** | 29 | 0 |

The PWA-era claim that the app works "from any device" is **confirmed for layout integrity**: nothing breaks or overflows the page. It is **not yet true for usability** on phones, for the reasons below.

## Findings (highest impact first)

### M1 — Key columns are hidden on phone lists · **High**
- **Cause:** `.op-table` (`static/hesba/css/operations.css:1`) and `.md-table` (`static/hesba/css/master_data.css:23`) both set `min-width:760px`.
- On a 390 px phone the table scrolls inside its box. Only the first 1–3 columns are visible, and nothing indicates that more exist.
- The hidden column is almost always the one the user came for:

| Screen | Visible | Hidden on first view |
|---|---|---|
| `/sales/` | 2/7 | customer, location, status, **total**, **due** |
| `/purchases/` | 2/7 | supplier, location, status, **total**, **due** |
| `/inventory/` | 2/6 | **quantity**, minimum, state, average cost |
| `/inventory/movements/` | 2/7 | item, location, **quantity**, unit cost, reference |
| `/cashboxes/` | 3/7 | default, in, out, **balance** |
| `/cashboxes/movements/` | 1/6 | cashbox, type, direction, **amount**, description |
| `/master-data/items/` | 2/9 | category, unit, type, **sale price**, purchase price, status |
| `/master-data/customers/` | 2/6 | phone, **credit limit**, status |
| `/reports/sales/`, `/reports/purchases/` | 2/8 | party, status, payment, **total**, paid, due |
| `/reports/profit/` | 2/6 | item, **sales, cost, profit** |
| `/reports/customers/`, `/reports/suppliers/` | 2/6 | opening, increase, decrease, **balance** |
| `/sales/<id>/` lines | 3/8 | unit price, **line total**, cost, profit |
| `/inventory/items/<id>/` stock | 0/3 | location, **quantity**, state |

The same applies to the other 16 list and detail screens (29 in total).

- **Not fixed here, deliberately.** Deleting `min-width` alone would squeeze 7–9 columns into 390 px and make the rows unreadable.
- The right fix is the **mobile list pattern (D2.1)**: each row becomes a card, with the key figure prominent and secondary fields collapsed. Per the roadmap, a visual pattern is built only after its design is approved.

### M2 — Tap targets too small for a finger · **Medium**
- Most common offenders on mobile AR:
  - row links inside tables, about 17 px tall (e.g. invoice numbers, item names)
  - the `← العودة …` back link, 15 px tall
  - the header brand link, 32 px
  - the `تعديل` (edit) links, 37×17 px
  - checkboxes, 20×20 px
- Guidelines call for about 44–48 px.
- This belongs with D0.3 (component library) and D2.1: button, link and row sizing.

### M3 — The sales/purchase invoice form is very long on a phone · **Medium**
- `/sales/new/` renders about 3,500 CSS px on a phone.
- It always shows 5 empty line blocks, each with 5 stacked fields.
- There's no add-line control and no live running total; the note says totals are computed on save.
- The save button sits at the very bottom.
- This belongs with D2.2 (form pattern with multi-line entry and live totals).

### M4 — Date inputs show the browser's own format · **Medium** (already known)
- The invoice date shows `09/24/2026` (US order) under an Arabic screen.
- `<input type="date">` is drawn by the browser in the browser's locale, so no server-side change affects it.
- Already recorded under FIX-002. The fix is a custom picker with D2.2.

### M5 — No navigation menu on operational screens · **Medium** (already known)
- Inner screens (sales, stock, cashboxes, reports…) have only the brand link and a language toggle in the header.
- Moving between modules on a phone means going back to the dashboard.
- The dashboard itself has a mobile menu.
- This is `SHELL-001` / D1.4 (mobile drawer), already on the roadmap.

### M6 — Very small text on the setup screens · **Low**
- Up to 49 text nodes under 12 px on `/setup/modules/`: module card descriptions and badges.
- Also on `/setup/`, `/setup/activity/` and the dashboard badges.
- Readable, but strained on a phone.
- This belongs with D0.1 (type scale) and D3.2 (setup screens).

### Tablet landscape — **Pass**
- At 1024×768, every table fits with all columns visible.
- No overflow; forms and lists read well.
- See the evidence image `04`.

## Evidence

Contact sheets, first screenful of each screen at 390×844, Arabic:

- `docs/mobile_001/01_dashboard_sales_list_detail.webp`: the dashboard stacks well. On the sales list the total, status and customer are cut off; the sales invoice detail is shown alongside.
- `docs/mobile_001/02_invoice_form_items_movements.webp`: the invoice form header, including the `09/24/2026` date. The items list hides the prices, and the stock movements list hides the quantity.
- `docs/mobile_001/03_invoice_form_lines.webp`: the five stacked line blocks of the invoice form.
- `docs/mobile_001/04_tablet_list_and_setup_modules.webp`: on the left, the tablet sales list with every column visible; on the right, the small text on mobile setup modules.

## Recommended follow-up

| Finding | Where it gets fixed | Blocked on |
|---|---|---|
| M1 hidden columns | D2.1 list pattern, mobile card layout | D2.1 design approval |
| M2 tap targets | D0.3 components + D2.1 | D0.3 |
| M3 long invoice form | D2.2 form pattern | D2.2 |
| M4 date format | D2.2 (custom date picker) | D2.2 |
| M5 no inner nav on mobile | SHELL-001 / D1.4 | D1 |
| M6 small setup text | D0.1 type scale / D3.2 | D0.1 |

**Suggested design priority:** do D2.1 (the list pattern) with a mobile variant first. It alone resolves the highest-impact finding across 29 screens.

## Re-running

```bash
# 1. scratch DB with demo data
export SQLITE_PATH=/tmp/audit.sqlite3
python manage.py migrate
python manage.py seed_demo_users --password '<choose one>'
python manage.py seed_demo_business --username owner
python manage.py runserver 127.0.0.1:8020

# 2. copy docs/mobile_001/urls.json to $S/urls.json (the exact audited manifest), then:
npm i playwright-core   # in a scratch folder
NODE_PATH=<scratch>/node_modules S=<outdir> AUDIT_PASS='<password>' \
  CHROME=<path to chromium> node scripts/mobile_audit.js
```

The script stops when the login fails, rather than auditing the login page. It lists every redirected path, records screenshot failures as errors, and exits non-zero when any load errored or returned something other than 200.
