# 104_HESBA_UI_DESIGN_MEMORY

Status: NEEDS_CORRECTION_AFTER_STATUS_UI_FEEDBACK

Reason for correction:

Ahmed was not happy with the first premium `/status/` direction. The issue is not the business logic. The issue is that the visual direction became too generic web-dashboard and did not clearly inherit the old Hesba AppSheet feeling.

Core design direction now:

* Arabic RTL first.
* Mobile-first before desktop.
* Light white/off-white background as the main feeling.
* Teal as the main brand color.
* Navy for Arabic titles and important labels.
* Soft cyan accents only, not heavy blocks.
* Clean rounded cards with thin borders.
* Soft shadows, not dark/glass effects.
* Clear logo presence, but not oversized.
* Calm premium look close to the old Hesba screens.
* The app should feel like Hesba, not like a generic SaaS admin panel.

Important visual reference:

Use the organized Drive folder `Hesba_New` as reference only:

* `01_Brand_Logo_Icon` for the Hesba icon/logo direction.
* `02_Backgrounds_Launch` for soft background/launch feeling.
* `03_Old_AppSheet_Reference_Screens` for the real visual feeling Ahmed liked.

Do not copy AppSheet logic.
Do not depend on AppSheet or Google Sheets as foundation.
Hesba remains PostgreSQL-first.

What to keep from the old AppSheet screens:

* Large white cards.
* Teal/navy identity.
* Image-based action cards.
* KPI cards with clean white card background.
* Thin gray borders.
* Soft cyan decorative waves/patterns.
* Big readable Arabic labels.
* Bottom/mobile-friendly navigation feeling.
* Calm spacing and simple hierarchy.

What to avoid:

* Dark hero sections as the main identity.
* Heavy gradients.
* Overly glassy panels.
* Too many badges and technical labels on user-facing screens.
* Dense desktop dashboard look.
* Making `/status/` the main visual benchmark.
* Copying the same CSS block into every page.

Main UI patterns:

* Dashboard: welcome banner first, quick action cards, KPI cards, charts.
* Management: image cards for customers, suppliers, items/services, cashboxes.
* Reports: image cards, read-only.
* Lists: name, phone, balance wording, call, WhatsApp, edit, print where allowed.
* Forms: clean Arabic labels, clear fields, save/cancel, branded soft background.
* Status: technical safe counts only, no money, no balances, no cost, no profit. It should be clean, but it is not the main design sample.

Protected business cycle:

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

Sensitive finance:

Cost, profit, supplier finance, and balances must be protected by backend permissions, not only UI hiding.

Corrected design priority:

1. Rebuild shared visual base using the old Hesba visual reference.
2. Dashboard first because it carries the real product identity.
3. Reports hub.
4. Management cards.
5. Status page revision after the shared base is ready.
6. Customer and supplier lists.
7. Invoice forms.
8. Payment forms.
9. PDFs.

Next implementation recommendation:

Do not continue polishing `/status/` in isolation.
Create a shared premium UI base from the Dashboard/AppSheet reference, then re-apply it to Status and Reports. This avoids repeating the same mistake and keeps all pages consistent.
