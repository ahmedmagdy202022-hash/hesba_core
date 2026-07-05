# 120B Dashboard Visual Screen Pack

Status: VISUAL_SCREEN_PACK_V1
Decision by: Ahmed / Main Control Chat

Related documents:

```text
docs/120_DASHBOARD_CORE_PLAN.md
docs/120A_DASHBOARD_FUNCTIONAL_CONTRACT.md
docs/120B_DASHBOARD_VISUAL_REJECTION_AND_IDENTITY_FIX.md
docs/120B_DASHBOARD_ASSETS_GAP_CHECKLIST.md
```

## Important correction

Previous implementation-first dashboard attempts are stopped.

Do not continue from generic SaaS dashboard attempts.
Do not continue by wiring assets into code before visual approval.
Return to the approved screen-pack workflow:

```text
1. Visual concepts by device.
2. Ahmed approves or rejects the visual.
3. Create assets pack based on the approved visual.
4. Create layout contract and functional contract updates where needed.
5. Send implementation task to Sub Chat.
```

## Approved visual direction

Ahmed confirmed the new visual concept direction is exactly the desired direction.

The dashboard should feel:

```text
Hesba-native
Arabic-first
Premium
Friendly for a business owner
Dense but clear
Analytical but not scary
Creative without confusing the user
```

It must not feel like:

```text
Generic SaaS
Cold enterprise template
Random icon-pack dashboard
Invented logo identity
Separate app per activity
```

## Screen name

Preferred Arabic screen name:

```text
لوحة القيادة
```

Alternative if more explicit:

```text
لوحة قيادة النشاط
```

Avoid using only:

```text
لوحة التحكم
```

Reason:

```text
لوحة القيادة feels more premium and communicates that the business owner is driving the business, not just controlling settings.
```

English label:

```text
Dashboard
```

Optional formal label:

```text
Business Dashboard
```

## Universal dashboard strategy

The dashboard should be one shared core screen, not a separate redesigned dashboard per activity.

Approved rule:

```text
One Core Dashboard
+
Activity Profiles
+
Module-aware Widgets
```

Meaning:

```text
The visual shell stays unified.
The dashboard identity stays Hesba.
The layout logic stays consistent.
Only content priority, widgets, labels, alerts, quick actions, and analytics change based on activity, selected modules, and available data.
```

User feeling goal:

```text
دي نفس حِسبة، بس فاهمة نشاطي.
```

Do not create a completely new dashboard design per activity.
Do not make the dashboard so generic that it becomes useless.

## Shared fixed shell

These parts are shared across activities:

```text
Header
Time-aware greeting / daily status hero
Business Health Score
Owner key numbers / core indicators
Smart alerts
Quick actions
Quick analytics
Smart empty state
```

## Activity-aware content

### Commercial activity priority

Show more emphasis on:

```text
Sales
Inventory
Top items
Customer debts
Supplier payables
Minimum stock
Cheques and due payments
Cashbox balance
```

### Service activity priority

Show more emphasis on:

```text
Executed services
Customer collections
Appointments / visits if enabled
Technicians if enabled
Top requested services
Late customer payments
```

### Inventory-enabled activity priority

Show more emphasis on:

```text
Stock balance
Negative stock
Items below minimum
Slow-moving items
Spare parts cost
Warehouse/cashbox relation if relevant
```

### Contracting / field operation future profile

Show more emphasis on:

```text
Projects
Progress percentage
Due extracts/payments
Project expenses
Site inventory
Cashflow by project
```

## Time-aware greeting

Greeting must not be static.

Arabic examples:

```text
05:00 - 11:59  => صباح الخير، أحمد
12:00 - 16:59  => نهارك سعيد، أحمد
17:00 - 23:59  => مساء الخير، أحمد
00:00 - 04:59  => أهلًا أحمد
```

English examples:

```text
05:00 - 11:59  => Good morning, Ahmed
12:00 - 16:59  => Good afternoon, Ahmed
17:00 - 23:59  => Good evening, Ahmed
00:00 - 04:59  => Welcome, Ahmed
```

The header should include date and current time.

Arabic format direction:

```text
الأربعاء، 03 يوليو 2026 — 09:12 ص
```

English format direction:

```text
Wednesday, 03 Jul 2026 — 09:12 AM
```

## Dynamic analytics cards

Dynamic/rotating cards are approved only for analytics and insight areas.

Critical numbers should stay stable.

Stable sections:

```text
Business Health Score
Core KPIs
Urgent alerts
Quick actions
```

Dynamic sections:

```text
Quick analytics
Secondary insights
Non-critical charts
```

Preferred behavior:

```text
Auto-rotate every 8-12 seconds.
Pause on hover/focus.
Manual next/previous available.
Dots or tabs show current view.
Card title clearly changes with the active view.
No random animation.
No fast movement.
No movement on urgent/critical numbers.
```

Possible rotating analytics groups:

### Sales analytics card

```text
Last 7 days sales
Current week vs previous week
Best sales day
Average transaction value
```

### Inventory analytics card

```text
Inventory distribution
Items below minimum
Slow-moving items
Negative stock
```

### Collection / cashflow analytics card

```text
Cash vs credit
Due customer payments
Highest customer debts
Collection ratio
```

### Profitability analytics card

```text
Top profitable items/services
Margin trend
Expenses vs sales
Profit by activity/service
```

## Cashbox action decision

Do not use `جرد خزنة` as the primary dashboard action without context.

Meaning of cashbox count/reconciliation:

```text
Count actual cash.
Compare actual cash with system balance.
Record shortage/surplus.
Record reason if needed.
Close or review the daily cash position.
```

Preferred primary dashboard action:

```text
إقفال اليوم
```

Supporting action/label inside that flow:

```text
مطابقة الخزن
```

Detailed label if needed:

```text
مطابقة رصيد الخزنة
```

Reason:

```text
The business owner thinks: I want to close the day and make sure everything is correct.
Not only: I want to count a cashbox.
```

Recommended dashboard quick actions:

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

If space is limited, `إقفال اليوم` has higher priority than `جرد خزنة`.

## Smart alerts direction

Smart alerts should show operational matters that need attention, not generic notifications.

Examples:

```text
شيك متأخر عن موعده
دفعة عميل مستحقة اليوم
رصيد مخزون سالب
شيك مستحق خلال 3 أيام
صنف تحت الحد الأدنى
خزنة رصيدها منخفض
مصروفات اليوم أعلى من المعتاد
```

Severity behavior:

```text
Red    = overdue / immediate danger / negative stock
Orange = due soon / needs action soon
Yellow = early warning
Blue   = follow-up / operational note
Green  = safe / normal
```

Alerts should include:

```text
Severity badge or strip
Clear Arabic title
Short business explanation
Amount/date when relevant
One safe placeholder action button
```

## Visual concept structure

### Web concept

Use a full desktop dashboard with:

```text
Top brand header
Time-aware user/date area
Wide daily hero with Business Health Score
Core KPI strip/cards
Smart alerts area
Hesba quick actions area
Quick analytics cards
Smart empty/onboarding state
```

### Tablet concept

Use the same visual identity but reorganized:

```text
Header wraps safely
Hero remains prominent
KPI cards become 2-column/3-column depending width
Alerts become stacked or grouped cards
Quick actions become compact grid
Analytics become 2-column cards
```

### Mobile concept

Use a focused stacked dashboard:

```text
Logo/header first
Date/time and notifications
Greeting hero
Health Score
KPI cards in 2-column or stacked layout
Alerts as compact priority list
Quick actions as 2-column grid
Analytics cards stacked with optional carousel/tabs
Empty state as compact steps
```

## Visual identity rules

Use real Hesba identity only.

Must use:

```text
Real Hesba logo
Real Hesba colors
Soft white/off-white background
Teal movement/waves
Navy primary typography
Gold accents for premium/soon states
Red/orange/yellow for risks only
Rounded cards
Soft shadows
Arabic-first text hierarchy
```

Must not use:

```text
Invented logo
Generic SaaS template
Heavy sidebar dashboard
Cold enterprise layout
Random icon packs
Dark command-center look
Module settings/status cards
Settings controls
Database/production logic
```

## Screen pack output requirements before implementation

Before Sub Chat implementation, prepare:

```text
1. Approved visual references for Web/Tablet/Mobile.
2. Dashboard assets pack based on the approved visual.
3. Layout contract.
4. Functional/static visual contract update.
5. Implementation task message.
```

No production dashboard implementation before visual approval and assets/layout contract are ready.

## Implementation guardrails

Current task remains visual approval work only.

Do not:

```text
Wire real data
Create database calculations
Add save logic
Add module settings
Add module status section
Open PR without Ahmed instruction
Merge without Ahmed instruction
```

## Current next step

Create the next visual concept iteration using this screen pack.

Recommended next task:

```text
120B_DASHBOARD_VISUAL_SCREEN_PACK_V2
```

Goal:

```text
Produce final Web/Tablet/Mobile visual concepts following this document, then ask Ahmed for visual approval before creating the production assets pack.
```
