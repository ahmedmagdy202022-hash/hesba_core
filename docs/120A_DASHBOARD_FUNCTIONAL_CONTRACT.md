# 120A Dashboard Functional Contract

Status: FUNCTIONAL_CONTRACT_APPROVED
Approved by: Ahmed

Parent plan:

```text
docs/120_DASHBOARD_CORE_PLAN.md
```

Screen:

```text
Core Dashboard / لوحة القيادة الرئيسية
```

Proposed route:

```text
/dashboard/
```

## Contract type

```text
Functional contract only.
No visual approval yet.
No implementation yet.
No data wiring yet.
```

## Purpose

The dashboard must be a daily command center for the business owner.

It must answer four questions quickly:

```text
الدنيا ماشية إزاي؟
فين الخطر؟
مين محتاج متابعة؟
أعمل إيه دلوقتي؟
```

It is not a simple KPI page and it is not a settings screen.

## Core foundation rule

The dashboard has one shared foundation that adapts to activity and data.

```text
One Dashboard Core
+
Activity-specific content
+
Data availability behavior
```

Do not build separate dashboard structures from scratch for each activity.

Examples:

```text
Commercial -> stock, items, sales, suppliers, cheques.
Services -> services, appointments, payments, customers, technicians later.
Contracting later -> projects, payments, extracts, site expenses.
```

The structure remains one, while content changes intelligently.

## Explicit exclusions

Do not include these in the dashboard:

```text
Module status section
Settings section
Module enable/disable controls
Advanced customization controls
```

These belong to later Settings screens, not the daily dashboard.

## Main dashboard sections

### 1. Hero / Today status

The first area gives the owner confidence and orientation.

Arabic direction:

```text
صباح الخير
نشاطك اليوم مستقر
في 3 تنبيهات تحتاج متابعة
```

Include the Business Health Score concept:

```text
82%
نشاطك مستقر
```

First implementation can use mock/static values. Later, the score should be derived from sales, profit, overdue payments, cheque risk, stock risk, cashbox risk, and unusual expenses.

### 2. Owner key numbers

Required first six cards:

```text
مبيعات اليوم
صافي الربح
رصيد الخزن
المطلوب من العملاء
المطلوب للموردين
مصروفات اليوم
```

Rules:

```text
Numbers must be large and clear.
Each card should have a small explanatory label.
Negative values must be clear and not confusing.
No-data state should show 0 or a helpful starting message.
```

### 3. Needs your attention / Smart alerts

Arabic title:

```text
محتاج انتباهك
```

This section is core functionality, not decoration.

Alert groups:

```text
عاجل الآن
قريبًا
متابعة
```

Severity colors:

```text
Red    = danger / overdue / immediate action / negative balance
Orange = due within 1-3 days
Yellow = due within 4-7 days
Blue   = important information
Green  = safe / resolved / normal
```

Required alert examples:

```text
شيك متأخر
شيك مستحق اليوم
شيك مستحق خلال 3 أيام
دفعة عميل مستحقة اليوم
دفعة مورد مستحقة قريبًا
رصيد مخزون سالب
صنف تحت الحد الأدنى
خزنة رصيدها منخفض
عميل مديونيته عالية
مورد مستحق له مبلغ كبير
مصروفات اليوم أعلى من الطبيعي
مبيعات اليوم أقل من المعتاد
```

Cheque escalation rule:

```text
Overdue cheque -> Red
Cheque due today -> Red
Cheque due in 1-3 days -> Orange
Cheque due in 4-7 days -> Yellow
Later cheque -> Blue or hidden depending on priority
```

Every alert should eventually support one or more actions:

```text
عرض التفاصيل
تسجيل تحصيل
تسجيل سداد
فتح العميل
فتح المورد
فتح الصنف
فتح الخزنة
```

First static/mock implementation may use safe placeholder links.

### 4. Start from here / Quick actions

Arabic title:

```text
ابدأ من هنا
```

Required action shortcuts:

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

### 5. Quick analytics

Analytics must be simple and useful.

Required analytical blocks to plan for:

```text
مبيعات آخر 7 أيام
مبيعات نقدي vs آجل
أفضل الأصناف / الخدمات
أعلى العملاء مديونية
أعلى الموردين مستحقات
توزيع رصيد الخزن
```

Rule:

```text
Useful analysis, not chart noise.
Rich but readable.
No crowded dashboard.
```

### 6. Smart empty state

When there is no data, the dashboard must not look dead.

Arabic direction:

```text
ابدأ تشغيل حِسْبَة في 4 خطوات:
1. أضف خزنة
2. أضف عميل أو مورد
3. أضف صنف أو خدمة
4. سجل أول عملية
```

Each step should eventually have a direct action.

## First implementation expectation

The first implementation should be a static/mock dashboard screen, not full real data wiring.

```text
Full visual screen
Mock numbers
Mock alerts
Safe placeholder links
No production data assumptions
```

Purpose of first implementation:

```text
Validate premium feeling.
Validate dashboard density.
Validate ease of use.
Validate alert visibility.
Validate owner decision flow.
```

Data wiring should come gradually after visual approval and after the core operating cycle is clearer.

## Workflow after this contract

Required next steps:

```text
120B Dashboard Visual Direction
120C Dashboard Layout Contract
120D Static Mock Dashboard Approval
120E Dashboard Implementation Task
120F Gradual Data Wiring
```

## Screen Pack requirement

No implementation may start before Dashboard Screen Pack approval:

```text
Visual Approved
Production Background / visual shell decision
Assets Pack if needed
Layout Contract
Functional Contract
```

## Implementation status

```text
Functional contract approved only.
No implementation approved yet.
No PR required beyond the planning commit.
```
