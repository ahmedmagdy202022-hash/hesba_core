# 120B Dashboard Assets Pack Requirements

Status: ASSETS_PACK_REQUIREMENTS_V1
Decision by: Ahmed / Main Control Chat

This document is for asset planning only.
No implementation is approved by this file.
No new image generation is approved by this file.

## Current approved visual base

Ahmed selected the uploaded dashboard visual as the current visual base reference.

Use the current uploaded base screenshots as the visual source of truth, with only these corrections:

```text
1. Do not redesign the dashboard.
2. Do not create new dashboard concepts.
3. Keep the same approved layout, spacing direction, card style, white/light Hesba identity, teal/navy/gold palette, and Arabic-first hierarchy.
4. Replace only the top hero/banner image with the more premium banner direction Ahmed indicated.
5. Add a 3-line menu button to desktop in a suitable place.
6. Add a 3-line menu button to tablet landscape in a suitable place.
7. Tablet must be landscape, not portrait.
8. Mobile can keep hamburger menu in the top bar.
9. Prepare the assets pack needed for implementation.
```

All image outputs generated after Ahmed objected to redesigning are rejected as implementation references.

## Screen title

Preferred Arabic title:

```text
لوحة القيادة
```

Avoid using only:

```text
لوحة التحكم
```

English:

```text
Dashboard
```

## Device requirements

### Desktop

Required visual properties:

```text
Wide desktop layout.
Right-side vertical navigation/menu area remains part of the desktop concept.
Add a visible 3-line hamburger/menu button in the top header.
The menu button should be near the top-left/user/tool side, not inside data cards.
Do not remove the existing right menu unless Ahmed explicitly approves.
Use the approved banner structure but replace the banner illustration with the better premium direction.
```

### Tablet

Required visual properties:

```text
Tablet must be landscape.
Use a landscape tablet layout derived from desktop, not a tall mobile-like tablet.
Add a 3-line hamburger/menu button in a suitable top bar location.
Keep the dashboard dense but readable.
Keep hero, KPI row, alerts, quick actions, analytics, and onboarding sections visible/reflowed.
```

### Mobile

Required visual properties:

```text
Mobile remains stacked and focused.
Hamburger/menu exists in top bar.
Keep logo, user/notification, time/date, greeting, health score, KPIs, smart alerts, quick actions, quick analytics, and onboarding steps.
No desktop side menu on mobile.
```

## Top hero/banner asset requirement

The hero/banner is not a random illustration.
It should be a dedicated Hesba dashboard hero visual.

Required direction:

```text
Light premium banner.
Soft teal wave background.
Business growth/dashboard illustration.
Laptop or analytics panel.
Checklist/accounting feeling.
Teal bars/line going up.
Small gold accent for premium movement.
No generic SaaS people illustration.
No heavy dark colors.
No new logo.
```

Expected asset:

```text
assets/dashboard/hero/dashboard_hero_premium_banner.png
```

Recommended dimensions:

```text
Desktop banner: 1600 x 360 px
Tablet landscape banner: 1200 x 300 px
Mobile banner crop/safe variant: 720 x 360 px
```

The implementation may use one wide responsive asset with safe cropping if it preserves the visual direction.

## Brand assets

Use existing approved Hesba brand identity only.

Required brand assets:

```text
assets/dashboard/brand/hesba_logo_full.png
assets/dashboard/brand/hesba_logo_mark.png
assets/dashboard/brand/hesba_logo_wordmark.png
```

Source should come from approved Hesba logo files already used in the project, not invented logo marks.

## Header assets

Required assets/icons:

```text
assets/dashboard/header/menu_hamburger.svg
assets/dashboard/header/language_globe.svg
assets/dashboard/header/calendar.svg
assets/dashboard/header/clock.svg
assets/dashboard/header/notification_bell.svg
assets/dashboard/header/user_avatar_placeholder.png
```

Notes:

```text
Desktop and tablet must include menu_hamburger.svg.
Mobile already uses hamburger behavior, but use the same icon family.
Notification badge is UI/CSS, not necessarily image.
```

## Right menu / navigation assets

Desktop approved visual contains right-side navigation.

Required nav icons:

```text
assets/dashboard/nav/dashboard.svg
assets/dashboard/nav/operations.svg
assets/dashboard/nav/customers.svg
assets/dashboard/nav/suppliers.svg
assets/dashboard/nav/items_services.svg
assets/dashboard/nav/cashboxes.svg
assets/dashboard/nav/reports.svg
assets/dashboard/nav/settings.svg
```

Labels in Arabic:

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

If tablet uses collapsed menu, these icons can be reused inside drawer/menu.

## Business Health Score assets

Required:

```text
assets/dashboard/health/health_score_ring.svg
assets/dashboard/health/health_score_good.svg
assets/dashboard/health/health_score_warning.svg
assets/dashboard/health/health_score_danger.svg
```

The ring may be implemented in CSS/SVG instead of a bitmap image.

States:

```text
Good: teal/green
Warning: gold/orange
Danger: red
```

## KPI icon assets

Required KPI icons:

```text
assets/dashboard/kpi/today_sales.svg
assets/dashboard/kpi/net_profit.svg
assets/dashboard/kpi/cashbox_balance.svg
assets/dashboard/kpi/customer_debts.svg
assets/dashboard/kpi/supplier_dues.svg
assets/dashboard/kpi/today_expenses.svg
```

Visual style:

```text
Line/soft 3D hybrid is acceptable if consistent.
Teal/navy primary.
Red/orange only for risk states.
No random colorful icon pack.
```

## Smart alerts assets

Required alert type icons:

```text
assets/dashboard/alerts/cheque_overdue.svg
assets/dashboard/alerts/customer_payment_due.svg
assets/dashboard/alerts/negative_stock.svg
assets/dashboard/alerts/cheque_due_soon.svg
assets/dashboard/alerts/item_below_minimum.svg
assets/dashboard/alerts/low_cashbox.svg
assets/dashboard/alerts/high_expenses.svg
```

Severity badges/strips can be CSS or SVG.

Required severity tokens:

```text
urgent_red
soon_orange
warning_yellow
followup_blue
safe_green
```

Smart alerts examples:

```text
شيك متأخر عن موعده
دفعة عميل مستحقة اليوم
رصيد مخزون سالب
شيك مستحق خلال 3 أيام
صنف تحت الحد الأدنى
خزنة رصيدها منخفض
مصروفات اليوم أعلى من المعتاد
```

## Quick actions assets

Approved dashboard quick actions should use clear icons/assets.

Required actions:

```text
assets/dashboard/actions/record_transaction.svg
assets/dashboard/actions/new_customer.svg
assets/dashboard/actions/new_supplier.svg
assets/dashboard/actions/new_item_service.svg
assets/dashboard/actions/new_expense.svg
assets/dashboard/actions/collect_from_customer.svg
assets/dashboard/actions/pay_supplier.svg
assets/dashboard/actions/print_reports.svg
assets/dashboard/actions/day_close.svg
```

Important naming decision:

```text
Use إقفال اليوم as the primary dashboard quick action.
Do not use جرد خزنة as the primary dashboard label.
Cashbox reconciliation / مطابقة الخزن belongs inside the day close flow.
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

## Analytics assets

Analytics can use CSS/SVG components, not necessarily static images.

Required chart components:

```text
assets/dashboard/analytics/bar_chart_week.svg
assets/dashboard/analytics/donut_cash_credit.svg
assets/dashboard/analytics/donut_cashbox_distribution.svg
assets/dashboard/analytics/list_top_items.svg
assets/dashboard/analytics/list_top_customers.svg
```

Dynamic/rotating analytics cards are allowed only in analytics/insights areas.

Approved rotation behavior:

```text
Auto-rotate every 8-12 seconds.
Pause on hover/focus.
Manual next/previous available.
Dots or tabs show current view.
No rotation for critical KPIs or urgent alerts.
```

## Empty/onboarding assets

Required:

```text
assets/dashboard/empty/rocket_start.svg
assets/dashboard/empty/step_business_data.svg
assets/dashboard/empty/step_customers_suppliers.svg
assets/dashboard/empty/step_items_services.svg
assets/dashboard/empty/step_first_transaction.svg
```

Approved onboarding direction:

```text
ابدأ حِسبة في 4 خطوات
بيانات المنشأة
أضف العملاء والموردين
أضف الأصناف والخدمات
سجل أول عملية اليوم
```

## Tokens file

Assets pack must include a simple token manifest:

```text
assets/dashboard/tokens/dashboard_visual_tokens.md
```

Required token sections:

```text
Colors
Typography direction
Spacing/radius/shadow notes
Device breakpoints
Card states
Alert severities
```

Recommended color tokens:

```text
navy_primary
teal_primary
teal_soft
gold_accent
red_risk
orange_due_soon
yellow_warning
blue_followup
white_surface
offwhite_background
border_soft
shadow_soft
```

## Manifest file

Assets pack must include:

```text
assets/dashboard/manifest/dashboard_assets_manifest.md
```

Manifest should list:

```text
File path
Purpose
Device usage
Required/optional
Notes
```

## Assets pack folder structure

Target structure:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED/
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
```

## What must not be included

Do not include:

```text
Rejected generated dashboard images.
Generic SaaS visuals.
Invented logos.
Unapproved sidebar redesign.
Database screenshots.
Production data.
Implementation code.
```

## Implementation readiness checklist

Before Sub Chat implementation, these must exist:

```text
1. Visual base reference approved by Ahmed.
2. Corrections documented: desktop/tablet hamburger + hero banner replacement + tablet landscape.
3. Assets pack prepared with the structure above.
4. Layout contract written.
5. Functional/static visual contract updated.
6. Sub Chat implementation message prepared.
```

## Current status

This file only defines what the assets pack must contain.

Next step:

```text
Prepare the actual Dashboard Assets Pack from approved sources only.
No new images unless Ahmed explicitly asks.
```
