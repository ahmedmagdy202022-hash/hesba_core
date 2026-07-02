# 118 Modules Selection Visual Approval

Status: VISUAL_APPROVED
Approved by: Ahmed

Branch:

```text
feature/118-modules-selection
```

Approved route:

```text
/setup/modules/
```

Approved preview checks:

```text
/setup/modules/?lang=ar&activity=commercial&sub_activity=retail
/setup/modules/?lang=en&activity=commercial&sub_activity=retail
/setup/modules/?lang=ar&activity=services&sub_activity=general
/setup/modules/?lang=en&activity=services&sub_activity=general
```

Source of truth:

```text
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
docs/118_MODULES_SELECTION_PLAN.md
```

## Approval meaning

Ahmed approved the 118 Modules Selection screen visually.

This is not a main lock yet. The screen remains on the feature branch until PR and merge are explicitly approved.

## Locked visual requirement

118 must continue to reuse the approved 117A setup shell. No new background, shell redesign, large panel geometry change, header/footer redesign, or approved visual direction change is allowed.

## Approved behavior

118 uses smart default modules with three states:

```text
Required  = ON and locked
Suggested = ON by default and editable
Optional  = OFF by default and editable
```

Commercial and services presets must stay activity-aware.

Back behavior:

```text
activity=commercial -> /setup/activity/commercial/?lang=<lang>
activity=services   -> /setup/activity/services/?lang=<lang>
missing/unknown     -> /setup/activity/?lang=<lang>
```

Next behavior:

```text
/setup/review/?lang=<lang>&activity=<activity>&sub_activity=<slug>&modules=<selected_modules>
```

## Advanced features note

Barcode, expiry dates, batch numbers, serial or IMEI, sizes and colors, minimum stock alerts, stocktaking, WhatsApp invoices, QR invoices, smart alerts, and AI assistant ideas are advanced features inside modules, not additional cards in 118.

They should be planned later under a separate Module Features and Advanced Options plan.

## Functional checks still required before merge

```text
python manage.py check
python manage.py test reports.tests_activity_selection
```

## Merge rule

Do not merge to main until Ahmed explicitly approves PR or merge after final review.
