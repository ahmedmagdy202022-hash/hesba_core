# Usage Notes

## Implementation destination

Suggested repository destination after approval:

```text
static/hesba/dashboard/assets/
```

## Text rule

Do not bake Arabic/English labels, KPI values, dates, names, or action labels into production image assets.
Use these assets as icons, illustrations, shells, and visual references only.

## Quick action rule

Use old quick action images as legacy visual references. For production cards, prefer the new icon/card-shell assets plus real HTML text.

## Alert rule

Use severity strips/badges consistently:

- red = overdue / urgent / negative stock
- orange = due within 1–3 days
- yellow = due within 4–7 days
- blue = information / follow-up
- green = safe / normal
