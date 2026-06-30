# 117A Setup Flow Visual Lock

Status: APPROVED_MAIN_LOCK

Approved by: Ahmed

Approved main commit at lock time:

```text
3640ca0 Restore approved 117A activity template on main
```

Approved route:

```text
/setup/activity/
```

## Purpose

This document locks the approved setup flow visual shell after 117A Activity Selection was visually accepted on `main`.

The approved shell must be reused for all upcoming Setup screens. Future Setup screens must not redesign, regenerate, or replace the background/shell.

## Locked visual shell

The following visual elements are locked:

- Page background and soft teal bottom glow.
- Top light wave/background feeling.
- Header area, logo placement, language control, and logout control.
- Large white rounded setup panel.
- Panel radius, spacing, shadow, and calm gradient feel.
- Stepper position and structure.
- Footer action area.
- Back and Next button placement and style.
- Activity card visual style as the baseline card contract.

## Allowed changes for future Setup screens

Future setup screens may change only the inner content:

- Page title.
- Subtitle/helper text.
- Active step number/text.
- Inner cards/forms/content inside the locked panel.
- Next button text and target route.
- Back button target route.

## Forbidden changes

Do not do any of the following unless Ahmed explicitly opens a new visual approval cycle:

- Do not generate a new background.
- Do not replace the setup shell.
- Do not change the page background direction.
- Do not change the large white panel geometry.
- Do not change the top wave feeling.
- Do not compact/minify the template in a way that makes visual recovery hard.
- Do not introduce a new CSS shell for every setup screen.
- Do not move to another setup screen implementation before the screen pack is approved.

## Required implementation rule

All future Setup screens must reuse the 117A setup shell classes and structure:

```text
activity-stage
activity-bg-frame
activity-ui-layer
activity-panel
activity-stepper
activity-footer
activity-action
```

If shared CSS is extracted later, it must preserve this visual lock and should be named clearly, for example:

```text
static/hesba/css/setup_flow_shell.css
```

## Approval rule going forward

No future visual approval may be recorded using only a screenshot or only a commit.

Every approval must include:

```text
1. Screenshot approved
2. Commit SHA approved
3. File list approved
4. Route approved
```

## 117A locked source files

The 117A lock depends on these files:

```text
templates/setup/activity_selection.html
static/hesba/css/activity_selection.css
static/hesba/css/activity_selection_final_overrides.css
templates/setup/setup_gate.html
```

## Current next scope

After this lock, the next work should continue only as screen-pack planning/preparation for Setup Gate Mobile + Tablet Landscape, unless Ahmed explicitly changes the scope.
