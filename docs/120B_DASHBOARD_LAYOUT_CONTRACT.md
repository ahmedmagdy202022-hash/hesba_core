# 120B Dashboard Layout Contract

Status: LAYOUT_CONTRACT_V1
Decision by: Ahmed / Main Control Chat

This contract locks the layout rules for the Core Dashboard / لوحة القيادة.
It is planning only. It does not approve implementation.

Use with:

```text
docs/120_DASHBOARD_CORE_PLAN.md
docs/120A_DASHBOARD_FUNCTIONAL_CONTRACT.md
docs/120B_DASHBOARD_VISUAL_SCREEN_PACK.md
docs/120B_DASHBOARD_ASSETS_PACK_V2_SPEC.md
```

## 1. Scope

```text
Screen: Core Dashboard / لوحة القيادة
Route later: /dashboard/
Phase: Layout contract only
No code, no real data, no database logic, no PR, no merge.
```

## 2. Approved visual base

The implementation must follow Ahmed's approved dashboard visual base:

```text
Light Hesba identity
Arabic-first layout
White/off-white premium surface
Teal/navy/gold palette
Rounded cards
Soft shadows
Dense but readable dashboard
Right-side desktop navigation concept
Business-owner-friendly view
```

Do not redesign from scratch.
Do not use generic SaaS direction.
Do not crop assets from screenshots.

## 3. Global shell

```text
Desktop content max width: about 1680px
Outer padding: 16px to 24px
Card radius: 18px to 28px
Gap system: 12px / 16px / 20px / 24px
No fixed screenshot background
No one-piece dashboard image
No absolute positioning for major sections
```

Arabic mode uses RTL. English mode may mirror where appropriate while preserving the same structure.

## 4. Desktop contract

Breakpoint direction:

```text
>= 1200px
```

Desktop zones:

```text
A. Top header
B. Right-side navigation/menu
C. Hero/status row
D. KPI row
E. Alerts + quick actions row
F. Analytics row
G. Empty/onboarding row
```

Desktop layout:

```text
Use a right-nav + main-content grid.
Right nav width: 220px to 260px.
Main content uses the remaining width.
Do not remove the right-side nav unless Ahmed explicitly approves.
```

Desktop header must include:

```text
Hesba logo
3-line hamburger/menu button
Screen title: لوحة القيادة
Language button
Date + current time
User/account pill
Notification bell
```

Desktop menu button:

```text
Must be visible.
Must be placed in a suitable top header position.
Must not be hidden inside cards.
Must not replace the right-side nav by default.
```

Right-side nav labels:

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

## 5. Hero/status row

Hero must include:

```text
Time-aware greeting
Business status line
Date/time relationship
Premium hero illustration/banner
Business Health Score area/card
```

Greeting must change by time:

```text
صباح الخير، أحمد
نهارك سعيد، أحمد
مساء الخير، أحمد
أهلًا أحمد
```

Hero correction rule:

```text
Only replace the top hero/banner illustration with the approved premium hero asset.
Do not redesign the whole dashboard because of the hero.
```

Recommended desktop hero height:

```text
180px to 240px
```

## 6. KPI row

Desktop should show six core KPI cards in one row where width allows:

```text
مبيعات اليوم
صافي الربح
رصيد الخزن
مديونيات العملاء / مستحقات العملاء
مديونيات الموردين / مستحقات الموردين
مصروفات اليوم
```

KPI cards are stable and must not auto-rotate.
Each card includes icon, label, value, currency/unit, and trend/change.

## 7. Alerts + quick actions

Desktop layout:

```text
Two-column row
Alerts: about 55% to 60%
Quick actions: about 40% to 45%
```

Alert grouping:

```text
عاجل
قريبًا / متوسطة
للمتابعة
```

Quick actions:

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

Important:

```text
Use إقفال اليوم as the primary dashboard action.
Do not use جرد خزنة as the main dashboard label.
مطابقة الخزن belongs inside the إقفال اليوم flow.
```

## 8. Analytics row

Analytics should be dense but readable.

Cards may include:

```text
المبيعات الأسبوعية
نقدي مقابل آجل
أعلى الأصناف / الخدمات مبيعًا
أعلى مديونيات العملاء
توزيع أرصدة الخزن
```

Only analytics cards may rotate.
Rotation rules:

```text
8 to 12 seconds
Pause on hover/focus
Manual dots/tabs/next controls available
No fast animation
No movement in critical numbers
```

## 9. Tablet landscape contract

Breakpoint direction:

```text
768px to 1199px landscape
```

Tablet must be landscape, not portrait.

Tablet header must include:

```text
Hesba logo
3-line hamburger/menu button
Language
Date + current time
User/account
Notification
```

Tablet content order:

```text
1. Header
2. Hero/status row
3. KPI grid
4. Alerts
5. Quick actions
6. Analytics
7. Onboarding/empty state
```

Tablet rules:

```text
Use collapsed/drawer menu if needed.
KPI grid: 3 columns x 2 rows preferred.
Alerts and quick actions may be side-by-side if width allows.
Analytics use 2 or 3 columns.
No horizontal overflow.
```

## 10. Mobile contract

Breakpoint direction:

```text
< 768px
```

Mobile must be stacked and focused, not a squeezed desktop.

Mobile top bar:

```text
Hamburger/menu button
Hesba logo
Language control
Notification bell
User/account avatar or compact pill
Date + current time row
```

Mobile content order:

```text
1. Header/top bar
2. Date + time
3. Greeting hero
4. Business Health Score
5. KPI cards
6. Smart alerts
7. Quick actions
8. Quick analytics
9. Onboarding/empty state
```

Mobile rules:

```text
No desktop right sidebar.
KPI cards: 2 columns if readable, otherwise 1 column.
Alerts: compact priority list with urgent first.
Quick actions: 2-column grid preferred.
Analytics: stacked cards or carousel/tabs.
```

## 11. Activity-aware rule

The dashboard is one shared core dashboard.
It adapts by activity/profile/module, but the shell remains unified.

```text
One Core Dashboard
+
Activity Profiles
+
Module-aware Widgets
```

Only widget priority/content changes. Do not build a separate dashboard design per activity.

## 12. Visual constraints

Use:

```text
White/off-white surfaces
Soft teal gradients/waves
Navy headings
Teal icons and active states
Gold accents for premium/soon states
Red/orange/yellow only for risk/severity
Rounded cards
Soft shadows
Clear Arabic typography
```

Do not use:

```text
Dark command center dashboard
Generic SaaS layout
Random icon families
Screenshot crops
Heavy sidebar
Module status/settings controls
```

## 13. Implementation guardrails

When implementation starts later, Sub Chat must follow:

```text
Do not redesign.
Do not crop screenshots.
Use approved Assets Pack V2 only.
Follow this layout contract.
No real data wiring.
No production calculations.
No database save.
No module settings/status section.
No PR.
No merge.
```

## 14. Readiness checklist before Sub Chat

Before implementation task is sent, Main Control must have:

```text
1. Approved visual base reference.
2. Dashboard Assets Pack V2.
3. This Layout Contract.
4. Functional/static visual contract update if needed.
5. Implementation task message.
```

## 15. Current next step

Prepare:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED_V2.zip
```

It must be built from atomic source assets only.
No screenshots may be cropped into assets.
