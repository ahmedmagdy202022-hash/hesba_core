# 120 Dashboard Core Plan

Status: PLANNING_APPROVED
Approved by: Ahmed

Screen:

```text
Core Dashboard / لوحة القيادة الرئيسية
```

Proposed route:

```text
/dashboard/
```

Current product direction:

```text
Stop Module Settings for now.
Build the real product core and operating cycle first.
Dashboard is the first real product screen after Setup Flow.
```

Source of truth:

```text
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
docs/118_MODULES_SELECTION_PLAN.md
docs/119_REVIEW_SETUP_PLAN.md
```

## Purpose

The dashboard must be a daily command center for the business owner.

It must help the owner understand quickly:

```text
Am I selling well?
Am I profitable?
Where is the money?
Who owes me money?
Who do I owe money to?
What needs action today?
Are there risky cheques, payments, stock, or cashbox issues?
What should I do next?
```

The dashboard must feel rich, analytical, creative, and easy. It should impress the user without making the product feel complicated.

## Core decision

The dashboard has one shared foundation that adapts by activity, data, and enabled capabilities.

```text
One Dashboard Core
+
Activity-specific content
+
Data/module availability logic
```

Do not build a completely separate dashboard per activity.

Do not show a module status section inside the dashboard.

Module settings and module status belong to Settings later, not the daily dashboard.

## Dashboard layers

```text
1. Core Dashboard Layer
2. Activity-specific Layer
3. Data availability Layer
```

Examples:

```text
Commercial activity + inventory data -> show stock alerts, low stock, negative stock, top items.
Services activity + appointments data -> show service visits, appointments, due service payments.
Cheques data exists -> show cheque due-date alerts.
No cheques data -> do not show empty cheque noise.
No data yet -> show smart onboarding empty state.
```

## Required main sections

### 1. Hero / Today status

A top summary that gives the owner confidence and orientation.

Possible Arabic direction:

```text
صباح الخير
نشاطك اليوم مستقر
في 3 تنبيهات تحتاج متابعة
```

Include a future Business Health Score concept:

```text
82% — نشاطك مستقر
```

This score is not just decorative. It should eventually reflect sales, profit, overdue payments, cashbox risk, stock risk, and unusual expenses.

### 2. Owner key numbers

Initial key cards:

```text
مبيعات اليوم
صافي الربح
رصيد الخزن
المطلوب من العملاء
المطلوب للموردين
مصروفات اليوم
```

Possible adaptive cards later:

```text
عدد العمليات
قيمة الشيكات القريبة
دفعات مستحقة
قيمة المخزون
إيراد الخدمات
مشاريع نشطة لاحقًا
```

### 3. Needs your attention / Smart alerts

This is a core feature, not decoration.

Arabic section title:

```text
محتاج انتباهك
```

Alerts must be actionable and priority-based.

Alert severity colors:

```text
Red    = overdue / danger / immediate action / negative balance
Orange = due within 1-3 days
Yellow = due within 4-7 days
Blue   = important information
Green  = safe / resolved / normal
```

Required alert types to plan for:

```text
Due customer payment today
Overdue customer payment
Supplier payment due today
Supplier payment overdue
Cheque overdue
Cheque due today
Cheque due within 1-3 days
Cheque due within 4-7 days
Negative stock balance
Low stock below minimum
Cashbox low balance
Customer debt above threshold
Supplier payable above threshold
Unusual daily expenses
Sales lower than usual
```

Cheque color escalation rule:

```text
Overdue cheque -> Red
Cheque due today -> Red
Cheque due in 1-3 days -> Orange
Cheque due in 4-7 days -> Yellow
Later cheque -> Blue or hidden depending on priority
```

Every alert should eventually support one or more actions:

```text
View details
Record collection
Record payment
Open customer
Open supplier
Open item
Open cashbox
Print report
```

### 4. Start from here / Quick actions

Arabic section title:

```text
ابدأ من هنا
```

Initial action shortcuts:

```text
تسجيل عملية
عميل جديد
مورد جديد
صنف / خدمة جديدة
مصروف جديد
تحصيل من عميل
سداد لمورد
جرد خزنة
```

The goal is to make the owner feel the product is easy and not menu-heavy.

### 5. Visual analytics

Charts must be useful and simple.

Initial analytical blocks:

```text
مبيعات آخر 7 أيام
مبيعات نقدي vs آجل
أفضل الأصناف / الخدمات
أعلى العملاء مديونية
أعلى الموردين مستحقات
توزيع رصيد الخزن
```

Do not overload the user with charts. The dashboard should be rich but still readable.

### 6. Smart empty state

When there is no data yet, do not show a dead empty dashboard.

Arabic empty state direction:

```text
ابدأ تشغيل حِسْبَة في 4 خطوات:
1. أضف خزنة
2. أضف عميل أو مورد
3. أضف صنف أو خدمة
4. سجل أول عملية
```

Each step should eventually have a direct action.

## Explicitly excluded from dashboard

Do not include:

```text
Module status section
Settings section
Advanced customization controls
Module enable/disable controls
```

These belong to a later Settings stage.

## Product rule

The dashboard answers:

```text
What is happening?
What is risky?
What needs action?
What should I do next?
```

It should not behave like a settings screen.

## Responsive planning

Dashboard planning must consider:

```text
Desktop
Tablet landscape
Mobile
```

However, no implementation may start before a dashboard Screen Pack is approved.

## Required next workflow

Before implementation, create and approve a full Dashboard Screen Pack:

```text
120A Dashboard Vision & Functional Contract
120B Dashboard Visual Direction
120C Dashboard Layout Contract
120D Dashboard Static/Mock Data Screen Approval
120E Dashboard Implementation Task
```

No coding before:

```text
Visual Approved
Production Background / visual shell decision
Assets Pack if needed
Layout Contract
Functional Contract
```

## Implementation status

```text
Planning approved only.
No code implementation approved yet.
No PR required for this plan beyond the planning commit.
```
