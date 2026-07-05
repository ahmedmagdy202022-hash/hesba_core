# 120B Dashboard Assets Pack V2 Spec

Status: SOURCE_OF_TRUTH_FOR_ASSETS_PACK_V2
Decision by: Ahmed / Main Control Chat

This is the clean source-of-truth document for preparing the Dashboard Assets Pack V2.
It replaces the earlier fragmented assets requirements/correction notes as the working reference for the next pack.

## 0. Scope

Screen:

```text
Core Dashboard / لوحة القيادة
```

Route later:

```text
/dashboard/
```

Current phase:

```text
Assets Pack preparation only.
No implementation.
No PR.
No merge.
No code changes.
No database logic.
```

## 1. Non-negotiable workflow

The dashboard must follow the approved Hesba screen-pack workflow:

```text
1. Visual base is approved by Ahmed.
2. Required visual corrections are documented.
3. Assets Pack is prepared from atomic reusable assets only.
4. Layout Contract is written.
5. Functional/static visual contract is updated.
6. Only then Sub Chat receives an implementation task.
```

No implementation task is allowed before Assets Pack + Layout Contract are ready.

## 2. Approved visual base

Ahmed approved the uploaded dashboard visual direction as the current base.

The approved base means:

```text
Use the same layout direction.
Use the same white/light Hesba identity.
Use the same clean card style.
Use the same Arabic-first hierarchy.
Use the same teal/navy/gold feeling.
Use the same dashboard density level.
Use the same right-side desktop navigation concept.
```

The approved base does not mean:

```text
Crop elements from the screenshot.
Slice icons from the screenshot.
Slice cards from the screenshot.
Use the screenshot as a production background.
Use the screenshot as an implementation asset.
Generate a new dashboard design.
```

## 3. Exact corrections requested by Ahmed

Only these corrections are approved:

```text
1. Keep the selected dashboard visual base.
2. Replace only the top hero/banner illustration with a cleaner, more premium direction.
3. Add a 3-line menu button to desktop in a suitable header position.
4. Add a 3-line menu button to tablet landscape in a suitable header position.
5. Tablet must be landscape, not portrait.
6. Do not generate new dashboard screens.
7. Do not redesign the whole dashboard.
8. Prepare a proper clean assets pack.
```

## 4. Rejected outputs

The previous file is rejected:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED.zip
```

Reason:

```text
It was created using crop/slice behavior from the visual screenshot.
```

Any generated screen after Ahmed objected to redesigning is rejected as a source for implementation.

## 5. Screenshot usage rule

This is mandatory:

```text
Approved visual screenshots are references only.
Never crop production assets from approved screenshots.
Never slice UI screenshots into implementation assets.
Never treat a screenshot as a source asset pack.
```

Approved screenshot may be included only in:

```text
reference/
```

With this note:

```text
This file is a visual reference only. Do not crop or use as a production asset.
```

## 6. Correct asset source rule

Assets Pack V2 must contain atomic reusable source assets.

Allowed sources:

```text
Existing approved Hesba logos.
Existing approved Hesba identity/app assets.
Original standalone SVG icons prepared for dashboard use.
Original standalone hero/banner asset.
Original standalone empty-state assets.
CSS/SVG chart components.
Token/manifest/usage docs.
```

Forbidden sources:

```text
Screenshot crops.
Screenshot-derived hero crops.
Screenshot-derived cards.
Screenshot-derived icons.
Screenshot-derived charts.
Invented logos.
Random icon packs.
Generic SaaS visuals.
Rejected generated dashboard screens.
```

## 7. Output zip name

The next valid pack must be named:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED_V2.zip
```

Do not reuse:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED.zip
```

## 8. Required folder structure

The pack must use this exact folder structure:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED_V2/
  README.md
  brand/
  header/
  nav/
  hero/
  health/
  kpi/
  alerts/
  actions/
  analytics/
  empty/
  tokens/
  manifest/
  reference/
```

Every folder must contain either real assets or a README explaining why the asset is CSS/SVG-generated during implementation.

## 9. README requirements

The pack root README must include:

```text
Pack name
Status
Date
Screen
Approved visual base note
No screenshot crop rule
Folder structure
How to use the pack
What not to use
Implementation guardrails
```

It must include this warning at the top:

```text
WARNING: Visual screenshots are references only. Do not crop, slice, or use screenshots as production assets.
```

## 10. Brand assets

Folder:

```text
brand/
```

Required files:

```text
hesba_logo_full.png
hesba_logo_mark.png
hesba_logo_wordmark.png
```

Rules:

```text
Use approved Hesba logo only.
Do not redraw the logo.
Do not invent a logo.
Do not use a screenshot crop of the logo.
Do not change brand colors.
```

Source options:

```text
Approved project logo files.
Existing approved Hesba identity files already used in the project.
```

## 11. Header assets

Folder:

```text
header/
```

Required files:

```text
menu_hamburger.svg
language_globe.svg
calendar.svg
clock.svg
notification_bell.svg
user_avatar_placeholder.svg or user_avatar_placeholder.png
```

Desktop rule:

```text
The 3-line menu button must appear in desktop header in a suitable location.
It must not be hidden inside data cards.
It must not remove the existing desktop right navigation by itself.
```

Tablet rule:

```text
The 3-line menu button must appear in tablet landscape header.
```

Mobile rule:

```text
Mobile already uses hamburger behavior and may reuse menu_hamburger.svg.
```

## 12. Navigation assets

Folder:

```text
nav/
```

Required files:

```text
dashboard.svg
operations.svg
customers.svg
suppliers.svg
items_services.svg
cashboxes.svg
reports.svg
settings.svg
```

Arabic labels:

```text
لوحة القيادة
العمليات
العملاء
الموردون
الأصناف والخدمات
الخزائن
التقارير
الإعدادات
```

Rules:

```text
Desktop keeps a right-side navigation/menu area.
Tablet may use the same icons in a drawer or collapsed menu.
Mobile does not show desktop side menu.
```

## 13. Hero/banner assets

Folder:

```text
hero/
```

Required files:

```text
dashboard_hero_premium_banner_desktop.png
dashboard_hero_premium_banner_tablet_landscape.png
dashboard_hero_premium_banner_mobile_safe.png
```

The hero must be original standalone artwork, not cropped from a dashboard screenshot.

Visual direction:

```text
Light premium Hesba banner.
Soft teal waves.
Business growth movement.
Analytics/laptop/checklist/accounting feeling.
Teal bars or upward line.
Small gold accent only if needed.
No people illustration.
No dark dashboard panel.
No generic SaaS scene.
No logo inside the hero unless explicitly needed.
```

Recommended dimensions:

```text
Desktop: 1600 x 360 px
Tablet landscape: 1200 x 300 px
Mobile safe: 720 x 360 px
```

If one responsive hero is used, it must be safe-cropped for all devices.

## 14. Business Health Score assets

Folder:

```text
health/
```

Required files:

```text
health_score_ring_base.svg
health_score_good.svg
health_score_warning.svg
health_score_danger.svg
```

The ring may alternatively be implemented with CSS/SVG using tokens.

State colors:

```text
Good: teal/green
Warning: gold/orange
Danger: red
```

## 15. KPI assets

Folder:

```text
kpi/
```

Required files:

```text
today_sales.svg
net_profit.svg
cashbox_balance.svg
customer_debts.svg
supplier_dues.svg
today_expenses.svg
```

Style:

```text
Consistent dashboard icon family.
Teal/navy primary.
Soft 3D or line style is acceptable if consistent.
Risk color only when a card state requires it.
No random mixed icon packs.
```

## 16. Smart alerts assets

Folder:

```text
alerts/
```

Required files:

```text
cheque_overdue.svg
customer_payment_due.svg
negative_stock.svg
cheque_due_soon.svg
item_below_minimum.svg
low_cashbox.svg
high_expenses.svg
```

Severity may be CSS tokens rather than images.

Severity tokens:

```text
urgent_red
soon_orange
warning_yellow
followup_blue
safe_green
```

Alert examples:

```text
شيك متأخر عن موعده
دفعة عميل مستحقة اليوم
رصيد مخزون سالب
شيك مستحق خلال 3 أيام
صنف تحت الحد الأدنى
خزنة رصيدها منخفض
مصروفات اليوم أعلى من المعتاد
```

Each alert should support:

```text
Severity state
Clear Arabic title
Short explanation
Amount/date when relevant
Safe action button
```

## 17. Quick action assets

Folder:

```text
actions/
```

Required files:

```text
record_transaction.svg
new_customer.svg
new_supplier.svg
new_item_service.svg
new_expense.svg
collect_from_customer.svg
pay_supplier.svg
print_reports.svg
day_close.svg
```

Preferred Arabic labels:

```text
تسجيل عملية
عميل جديد
مورد جديد
صنف / خدمة جديدة
مصروف جديد
تحصيل من عميل
سداد لمورد
طباعة التقارير
إقفال اليوم
```

Important decision:

```text
Use إقفال اليوم as the primary dashboard action.
Do not use جرد خزنة as the primary dashboard action.
Cashbox reconciliation / مطابقة الخزن belongs inside the day close flow.
```

## 18. Analytics assets

Folder:

```text
analytics/
```

Required files/components:

```text
bar_chart_week.svg
donut_cash_credit.svg
donut_cashbox_distribution.svg
list_top_items.svg
list_top_customers.svg
analytics_carousel_dots.svg or CSS token notes
```

Charts may be built with HTML/CSS/SVG during implementation if the pack provides clear tokens and visual rules.

Dynamic analytics rule:

```text
Only analytics cards may rotate.
Critical KPI cards must stay stable.
Urgent alerts must stay stable.
Rotation interval: 8-12 seconds.
Pause on hover/focus.
Manual next/previous or dots/tabs must be available.
```

## 19. Empty/onboarding assets

Folder:

```text
empty/
```

Required files:

```text
rocket_start.svg
step_business_data.svg
step_customers_suppliers.svg
step_items_services.svg
step_first_transaction.svg
```

Arabic onboarding flow:

```text
ابدأ حِسبة في 4 خطوات
بيانات المنشأة
أضف العملاء والموردين
أضف الأصناف والخدمات
سجل أول عملية اليوم
```

## 20. Tokens

Folder:

```text
tokens/
```

Required file:

```text
dashboard_visual_tokens.md
```

Must include:

```text
Colors
Typography direction
Spacing
Radius
Shadows
Device breakpoints
Card states
Alert severities
Dynamic analytics behavior
```

Recommended tokens:

```text
navy_primary
teal_primary
teal_soft
gold_accent
red_risk
orange_due_soon
yellow_warning
blue_followup
green_safe
white_surface
offwhite_background
border_soft
shadow_soft
```

## 21. Manifest

Folder:

```text
manifest/
```

Required file:

```text
dashboard_assets_manifest.md
```

Required columns/fields:

```text
Path
Purpose
Device usage
Required/optional
Source
Notes
```

The manifest must explicitly say for every file whether it is:

```text
Approved existing asset
Original standalone dashboard asset
CSS/SVG component
Reference only
```

## 22. Reference folder

Folder:

```text
reference/
```

Allowed files:

```text
approved_dashboard_visual_base_desktop.png
approved_dashboard_visual_base_tablet_landscape.png if available
approved_dashboard_visual_base_mobile.png if available
```

Each reference file must include a nearby note:

```text
REFERENCE ONLY — DO NOT CROP OR USE AS PRODUCTION ASSET.
```

## 23. What the pack must not contain

Do not include:

```text
Rejected generated dashboard images.
Screenshot crops.
Card crops.
Hero crops from screenshot.
Icon crops from screenshot.
Chart crops from screenshot.
Invented logo files.
Production implementation files.
Django templates.
CSS implementation files.
Database files.
```

## 24. Implementation handoff requirements

Before Sub Chat receives implementation instructions, Main Control must provide:

```text
1. This Assets Pack V2 Spec.
2. Correct Assets Pack V2 zip.
3. Layout Contract.
4. Functional/static visual contract update if needed.
5. Approved visual base reference.
```

The implementation message must state:

```text
Do not redesign.
Do not crop screenshots.
Use assets from the approved V2 pack only.
Follow the layout contract.
No real data wiring.
No production calculations.
No database save.
No PR.
No merge.
```

## 25. Current next step

Prepare:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED_V2.zip
```

It must be built from atomic source assets and documentation only.

No new dashboard screen generation is allowed unless Ahmed explicitly requests it.
