# 117B Commercial Sub-Activity Visual Approval

Status: VISUAL_APPROVED

Approved by: Ahmed

Branch:

```text
feature/117b-commercial-subactivity-selection
```

Base main commit:

```text
c49c11bd60a9f8347575528f567e2e77be3d3f96
```

Approved route:

```text
/setup/activity/commercial/
```

Approved preview checks:

```text
/setup/activity/commercial/?lang=ar
/setup/activity/commercial/?lang=en
```

## Approval meaning

Ahmed approved the 117B Commercial Sub-Activity Selection screen visually.

This is not a main lock yet. The screen remains on the feature branch until review and merge are explicitly approved.

## Required visual dependency

117B must continue to follow the approved 117A setup flow visual lock:

```text
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
```

The setup background, shell, large panel, header, logo/language/logout area, footer, and action style must not be redesigned.

## Approved content

Arabic title:

```text
اختر نوع النشاط التجاري
```

English title:

```text
Choose commercial activity type
```

Commercial sub-activity cards:

```text
retail       | محل تجزئة              | Retail store
grocery      | سوبر ماركت / بقالة      | Supermarket / Grocery
fashion      | ملابس وأحذية            | Clothing & Shoes
electronics  | موبايلات وإلكترونيات    | Mobiles & Electronics
pharmacy     | صيدلية                  | Pharmacy
wholesale    | جملة / مخزن             | Wholesale / Warehouse
online       | بيع أونلاين             | Online selling
other        | نشاط تجاري آخر          | Other commercial
```

## Functional approval still needed

Before merge, review must still confirm:

```text
1. Next starts disabled.
2. Selecting a card enables Next.
3. Selected card state is clear.
4. Back preserves current language and returns to /setup/activity/.
5. Next preserves lang, activity=commercial, and sub_activity=<slug>.
6. /setup/modules/ placeholder is safe and clearly out of scope.
7. Existing 117A route remains unchanged.
8. Tests pass locally.
```

## Changed files in current feature branch at approval time

Compared with main at approval time, the feature branch changes:

```text
config/urls.py
docs/preview_links.md
reports/tests_activity_selection.py
static/hesba/css/activity_selection_final_overrides.css
templates/setup/activity_commercial_subactivity.html
templates/setup/modules_placeholder.html
```

After this approval document, this file is also part of the feature branch:

```text
docs/117B_COMMERCIAL_SUB_ACTIVITY_VISUAL_APPROVAL.md
```

## Merge rule

Do not merge to main until Ahmed explicitly approves merge/PR after functional review.
