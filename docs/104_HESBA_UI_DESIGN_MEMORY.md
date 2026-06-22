# 104_HESBA_UI_DESIGN_MEMORY

Status: OK

Hesba UI identity:

* Arabic RTL first.
* White/off-white background.
* Teal as primary brand color.
* Navy for titles and important text.
* Soft cyan accents.
* Rounded cards.
* Soft shadows.
* Clear logo presence.
* Premium calm look.

Use old AppSheet screens and Drive assets as design reference only.
Do not copy AppSheet logic.
Hesba remains PostgreSQL-first.

Main UI patterns:

* Dashboard: welcome banner, quick actions, KPI cards, charts.
* Management: image cards for customers, suppliers, items/services, cashboxes.
* Reports: image cards, read-only.
* Lists: name, phone, balance wording, call, WhatsApp, edit, print where allowed.
* Forms: clean Arabic labels, clear fields, save/cancel, branded background.
* Status: counts only, no money, no balances, no cost, no profit.

Protected cycle:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

Sensitive finance:
Cost, profit, supplier finance, and balances must be protected by backend permissions, not only UI hiding.

Next design priority:

1. Status page
2. Dashboard
3. Reports
4. Management cards
5. Customer and supplier lists
6. Invoice forms
7. Payment forms
8. PDFs
