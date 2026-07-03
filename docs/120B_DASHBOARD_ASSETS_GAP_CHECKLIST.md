# 120B Dashboard Assets Gap Checklist

Status: ASSETS_GAP_CHECKLIST
Decision by: Ahmed / Main Control Chat

Related documents:

```text
docs/120_DASHBOARD_CORE_PLAN.md
docs/120A_DASHBOARD_FUNCTIONAL_CONTRACT.md
docs/120B_DASHBOARD_VISUAL_REJECTION_AND_IDENTITY_FIX.md
```

Related current asset package:

```text
120B_DASHBOARD_HESBA_ASSETS_PACK_v1.zip
```

## Purpose

Define what dashboard visual assets exist and what is still missing before continuing dashboard visual approval.

The goal is to prevent another generic SaaS dashboard and keep the dashboard tied to real Hesba identity.

## Current visual status

```text
First 120B visual mock = rejected.
120B identity fix mock = closer and acceptable as direction, but still needs a dedicated dashboard assets pack before final visual approval.
```

The dashboard idea is valid:

```text
Owner key numbers
Business Health Score
Smart alerts
Quick actions
Quick analytics
Smart empty state
```

The dashboard must still be completed with Hesba-specific assets.

## Existing assets in current pack

### Brand assets

```text
hesba_logo_primary.png
hesba_logo_square.png
hesba_logo_icon.png
hesba_app_icon_2026.png
hesba_launch_identity.png
```

### Legacy quick action references

```text
01_register_transaction.png
02_print_reports.png
03_new_customer.png
04_new_supplier.png
05_new_service.png
06_new_cashbox.png
```

### Supporting references

```text
hesba_assets_contact_sheet.jpg
dashboard_identity_tokens.css
asset_manifest.md
asset_manifest.json
README.md
```

## Missing or incomplete assets

### 1. KPI icons

Need Hesba-style small icons for:

```text
مبيعات اليوم
صافي الربح
رصيد الخزن
المطلوب من العملاء
المطلوب للموردين
مصروفات اليوم
```

Required style:

```text
Navy/teal/gold only.
Soft rounded icon container.
Consistent line weight.
No generic SaaS icon feel.
```

### 2. Missing quick action cards/icons

Existing legacy references cover:

```text
تسجيل عملية
طباعة التقارير
عميل جديد
مورد جديد
خدمة جديدة
خزنة جديدة
```

Need additional Hesba-style quick action assets for:

```text
مصروف جديد
تحصيل من عميل
سداد لمورد
جرد خزنة
```

Optional, if visually needed:

```text
بيع سريع
مرتجع
تحويل خزنة
كشف حساب
```

### 3. Smart alert visual assets

Need severity badge/strip system for:

```text
Red urgent
Orange soon
Yellow near
Blue follow-up/info
Green safe
```

Need alert-type icons for:

```text
شيك
دفعة
مخزون سالب
حد أدنى للمخزون
خزنة منخفضة
مصروفات عالية
مبيعات منخفضة
عميل عالي المديونية
مورد مستحق
```

### 4. Dashboard hero illustration

Need a Hesba-specific hero illustration/shell.

Should include visual direction like:

```text
Hesba identity
business paper/report
cashbox or money stack
growth/check element
soft teal movement
no fake logo
no generic laptop SaaS illustration
```

### 5. Business Health Score component

Need a clear gauge/ring style with states:

```text
Green/teal = stable
Yellow/gold = needs follow-up
Red/orange = risk
```

It must support:

```text
percentage value
Arabic status text
small explanation
```

### 6. Analytics components

Need consistent visual language for:

```text
7-day sales bars
cash vs credit donut
top items/services list
highest customer debts list
cashbox distribution bars/donut
```

These can be CSS components, but their visual style must be defined before final implementation.

### 7. Smart empty state

Need a friendly visual state for:

```text
ابدأ تشغيل حِسْبَة في 4 خطوات
1. أضف خزنة
2. أضف عميل أو مورد
3. أضف صنف أو خدمة
4. سجل أول عملية
```

This should not be plain text only. It should feel like Hesba onboarding.

### 8. Responsive variants

Need visual direction for:

```text
Desktop dashboard
Tablet landscape dashboard
Mobile dashboard
```

Desktop can be approved first, but Mobile/Tablet must not be guessed from desktop only.

## Recommended next asset package

Create:

```text
120B_DASHBOARD_HESBA_ASSETS_PACK_v2.zip
```

It should contain:

```text
brand/
quick_actions_existing/
quick_actions_missing/
kpi_icons/
alert_icons/
alert_badges/
hero/
health_score/
analytics_components/
empty_state/
tokens/
manifest/
```

## Implementation rule

Do not proceed to production dashboard implementation before:

```text
Dashboard Visual Approved
Dashboard Assets Pack approved
Dashboard Layout Contract approved
Dashboard Functional Contract approved
```

No full dashboard PR or merge before Ahmed explicitly approves the visual.

## Current recommendation

Next step should be asset completion, not data wiring.

Suggested task:

```text
120B_DASHBOARD_ASSETS_PACK_V2
```

Goal:

```text
Complete missing dashboard visual assets using Hesba identity, then update the visual mock to use those assets.
```

## Acceptance criteria for assets v2

```text
Uses real Hesba logo/brand.
Does not invent a new logo.
No generic SaaS icon style.
Quick actions feel connected to old Hesba action cards.
KPI icons are consistent.
Alert severity system is clear.
Hero feels like Hesba.
Health Score is reusable.
Analytics components match Hesba visual language.
Empty state is friendly and useful.
```
