# Hesba Global Asset Pack Specification

Status: ACTIVE ASSET LIBRARY PLAN
Scope: Entire Hesba product
Baseline: develop
Target root: static/hesba/global/

## 1. Purpose
Create one controlled, reusable visual asset library for Hesba so every future screen uses the same identity and icon language.

The Global Asset Pack is not a screen mockup.
It is the approved source library from which Screen Packs may select assets.

Agent rule:
Search the Global Asset Manifest first. If a required asset exists and is approved, reuse it. If it does not exist, report an ASSET GAP. Do not invent, redraw, download, or mix a random replacement.

## 2. File-type strategy
Default:
- SVG for navigation, actions, statuses, business objects, alerts, reports, and system icons.
- PNG/WebP only for brand imagery, launch/splash, hero illustrations, empty-state illustrations, and production backgrounds.

Target ratio:
- approximately 80-90% SVG/components
- approximately 10-20% raster illustrations/backgrounds

Never bake translatable text, dynamic values, buttons, or fields into raster artwork.

## 3. Target folder structure

static/hesba/global/
  brand/
  navigation/
  master_data/
  purchases/
  inventory/
  sales/
  cashboxes/
  reports/
  actions/
  communication/
  status/
  alerts/
  system/
  onboarding/
  empty_states/
  backgrounds/
  manifest/

## 4. Brand assets — P0
Required canonical assets:
- brand/hesba_logo_full.svg or approved raster equivalent
- brand/hesba_logo_mark.svg
- brand/hesba_app_icon.png
- brand/hesba_splash_mark.png
- brand/hesba_watermark.svg
- brand/hesba_pattern.svg

Source rule:
Canonical versions must derive from Ahmed-approved Hesba source assets, not AI redrawing.

Existing repository assets may be screen-approved but are not automatically global masters.

## 5. Navigation icons — P0
Required:
- navigation/dashboard.svg
- navigation/master_data.svg
- navigation/operations.svg
- navigation/purchases.svg
- navigation/inventory.svg
- navigation/sales.svg
- navigation/customers.svg
- navigation/suppliers.svg
- navigation/items_services.svg
- navigation/cashboxes.svg
- navigation/reports.svg
- navigation/settings.svg
- navigation/users_permissions.svg
- navigation/audit.svg

Style:
One family, consistent geometry and stroke/weight.
Primary navy/teal with optional restrained gold accent.

## 6. Master Data icons — P0
Required:
- master_data/cashbox.svg
- master_data/location.svg
- master_data/warehouse.svg
- master_data/supplier.svg
- master_data/customer.svg
- master_data/category.svg
- master_data/item.svg
- master_data/service.svg
- master_data/barcode.svg
- master_data/unit.svg
- master_data/price_tag.svg
- master_data/contact.svg

These assets serve the first Master Data Foundation Track.

## 7. Purchases — P0/P1
Required:
- purchases/purchase_invoice.svg
- purchases/receive_stock.svg
- purchases/supplier_due.svg
- purchases/supplier_payment.svg
- purchases/purchase_return.svg
- purchases/purchase_cancel_reverse.svg
- purchases/credit_purchase.svg
- purchases/cash_purchase.svg

## 8. Inventory — P0/P1
Required:
- inventory/stock.svg
- inventory/stock_in.svg
- inventory/stock_out.svg
- inventory/stock_transfer.svg
- inventory/stock_adjustment.svg
- inventory/stock_count.svg
- inventory/low_stock.svg
- inventory/out_of_stock.svg
- inventory/negative_stock.svg
- inventory/min_stock.svg
- inventory/location_stock.svg

## 9. Sales — P0/P1
Required:
- sales/sales_invoice.svg
- sales/cash_sale.svg
- sales/credit_sale.svg
- sales/customer_due.svg
- sales/customer_collection.svg
- sales/sales_return.svg
- sales/sales_cancel_reverse.svg
- sales/receipt.svg

## 10. Cashboxes and money — P0/P1
Required:
- cashboxes/cashbox.svg
- cashboxes/cash_in.svg
- cashboxes/cash_out.svg
- cashboxes/cash_transfer.svg
- cashboxes/opening_balance.svg
- cashboxes/current_balance.svg
- cashboxes/payment.svg
- cashboxes/receipt.svg
- cashboxes/expense.svg
- cashboxes/egp.svg

Financial-state icons must remain professional and not playful.

## 11. Reports — P1
Required:
- reports/customer_statement.svg
- reports/supplier_statement.svg
- reports/sales_report.svg
- reports/purchase_report.svg
- reports/inventory_report.svg
- reports/cashbox_report.svg
- reports/profit_report.svg
- reports/daily_report.svg
- reports/period_report.svg
- reports/pdf.svg
- reports/print.svg
- reports/export_excel.svg
- reports/export_data.svg

## 12. Common actions — P0
Required:
- actions/add.svg
- actions/edit.svg
- actions/view.svg
- actions/search.svg
- actions/filter.svg
- actions/sort.svg
- actions/save.svg
- actions/cancel.svg
- actions/back.svg
- actions/forward.svg
- actions/refresh.svg
- actions/more.svg
- actions/activate.svg
- actions/deactivate.svg
- actions/copy.svg
- actions/delete.svg

Restriction:
delete.svg may exist in the library but is RESTRICTED. Presence of an icon does not authorize destructive product behavior.

## 13. Communication — P1
Required:
- communication/whatsapp.svg
- communication/phone.svg
- communication/email.svg
- communication/share.svg
- communication/send.svg
- communication/send_receipt.svg

## 14. Status — P0
Required:
- status/success.svg
- status/info.svg
- status/warning.svg
- status/error.svg
- status/pending.svg
- status/draft.svg
- status/posted.svg
- status/cancelled.svg
- status/paid.svg
- status/partial.svg
- status/credit.svg
- status/active.svg
- status/inactive.svg
- status/locked.svg

Status color is mainly controlled by CSS tokens. Icons must still work monochrome.

## 15. Alerts — P1
Required:
- alerts/overdue.svg
- alerts/due_soon.svg
- alerts/low_stock.svg
- alerts/out_of_stock.svg
- alerts/negative_stock.svg
- alerts/low_cashbox.svg
- alerts/high_expense.svg
- alerts/customer_over_limit.svg
- alerts/action_required.svg

## 16. System/header — P0
Required:
- system/menu.svg
- system/close.svg
- system/notification.svg
- system/language.svg
- system/user.svg
- system/calendar.svg
- system/clock.svg
- system/help.svg
- system/logout.svg
- system/security.svg
- system/permission.svg
- system/chevron_down.svg
- system/chevron_up.svg
- system/chevron_left.svg
- system/chevron_right.svg

Directional icons must work correctly in RTL/LTR.

## 17. Onboarding / empty-state illustrations — P2
These may be lightweight premium illustrations, not mandatory for first implementation.

Recommended:
- onboarding/get_started.webp
- onboarding/setup_complete.webp
- onboarding/first_transaction.webp

- empty_states/no_customers.webp
- empty_states/no_suppliers.webp
- empty_states/no_items.webp
- empty_states/no_cashboxes.webp
- empty_states/no_invoices.webp
- empty_states/no_inventory_activity.webp
- empty_states/no_reports_data.webp
- empty_states/no_search_results.webp
- empty_states/generic_empty.webp

Rules:
- no embedded Arabic/English text;
- light Hesba visual language;
- white/off-white/teal/navy;
- small restrained gold accent;
- no generic SaaS people unless explicitly approved.

## 18. Backgrounds — per-screen, not universal by default
Global library may contain only reusable brand textures/patterns.

Screen-specific production backgrounds stay inside each approved Screen Pack unless Main Control explicitly promotes them to global.

Do not automatically reuse:
- login backgrounds;
- Setup Gate background;
- Dashboard visual reference;
as generic backgrounds for unrelated screens.

## 19. Priority build order

P0 — required before Master Data implementation:
1. canonical brand set
2. navigation family
3. Master Data family
4. common actions
5. system/header
6. shared status family

P1 — prepare before operating cycle implementation:
7. purchases
8. inventory
9. sales
10. cashboxes/money
11. reports
12. communication
13. alerts

P2 — polish layer:
14. onboarding illustrations
15. empty-state illustrations
16. reusable decorative patterns

## 20. Approval states
Every manifest entry must use one:
- OFFICIAL_SOURCE
- APPROVED_GLOBAL
- APPROVED_SCREEN_ONLY
- LEGACY_REFERENCE
- RESTRICTED
- GAP_REQUIRED
- REJECTED

Only APPROVED_GLOBAL may be freely reused across product screens.

## 21. Agent rule
Before using an asset, the Agent must verify:
- manifest ID;
- approval state;
- canonical path;
- permitted usage;
- RTL/LTR rule where relevant.

If not found:
Return ASSET GAP with proposed category/name. Do not invent a replacement.
