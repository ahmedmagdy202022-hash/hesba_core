# 119 Review Setup Visual Approval

Status: VISUAL_APPROVED
Approved by: Ahmed

Branch:

```text
feature/119-review-setup
```

Approved route:

```text
/setup/review/
```

Completion placeholder route:

```text
/setup/complete/
```

Source of truth:

```text
docs/119_REVIEW_SETUP_PLAN.md
docs/118_MODULES_SELECTION_PLAN.md
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
```

## Approval meaning

Ahmed approved the 119 Review Setup screen visually.

This is not a main lock yet. The screen remains on the feature branch until PR and merge are explicitly approved.

## Locked visual requirement

119 must continue to reuse the approved 117A setup shell.

Do not change:

```text
background
setup shell
large panel geometry
header/logo/language/logout positions
footer/action style
approved visual direction
```

Allowed 119 visual scope:

```text
Stepper active step 4: Review / المراجعة
Review title and subtitle
Activity summary section
Selected modules section
Settings note section
Back/Next labels and targets
```

## Approved product message

Initial setup chooses the starting configuration only.

Users can later enable, disable, or adjust modules from Settings.

Disabling a module must never delete existing data.

Required/locked module rules and dependency rules belong to a later Module Settings stage.

## Approved routes and behavior

Review route input:

```text
/setup/review/?lang=<lang>&activity=<activity>&sub_activity=<slug>&modules=<selected_modules>
```

Back behavior:

```text
/setup/modules/?lang=<lang>&activity=<activity>&sub_activity=<slug>&modules=<selected_modules>
```

Next behavior:

```text
/setup/complete/?lang=<lang>
```

Completion route is a safe placeholder only.

No real setup activation.
No database save.
No production settings logic.
No module settings implementation yet.

## Required checks before PR or merge

```text
python manage.py check
python manage.py test reports.tests_activity_selection
```

## Merge rule

Do not merge to main until Ahmed explicitly approves PR or merge after final review.
