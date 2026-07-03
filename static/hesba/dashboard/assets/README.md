# 120B Dashboard Hesba Assets Pack v2

Status: ASSETS_PACK_DRAFT_FOR_DASHBOARD_VISUAL_APPROVAL
Screen: Core Dashboard / لوحة القيادة

## Purpose

Complete the missing dashboard visual assets so the next dashboard mock uses Hesba identity instead of generic SaaS styling.

## Important rules

- Use real Hesba brand assets only.
- Do not redraw or invent a new Hesba logo.
- Dynamic text, numbers, labels and buttons must remain HTML/CSS.
- Legacy quick action images are visual references; production text must not be baked into images.
- This is an assets pack for visual approval work, not production data wiring.

## Main folders

- `brand/` — real Hesba logo/icon/launch identity sources.
- `quick_actions_existing/` — old Hesba quick action references.
- `quick_actions_missing/` — new matching action icons/card shells with no baked labels.
- `kpi_icons/` — KPI icons for owner key numbers.
- `alert_icons/` and `alert_badges/` — smart alert system assets.
- `hero/` — Hesba dashboard hero illustration, no dynamic text.
- `health_score/` — reusable Business Health Score rings, no baked values.
- `analytics_components/` — reusable visual components for mock charts/lists.
- `empty_state/` — visual starter state.
- `tokens/` — CSS identity tokens.
- `manifest/` — asset list and usage notes.

## Not included

- No fonts.
- No database/data wiring.
- No rejected dashboard visuals.
- No AI-redrawn logo.

## Integration note

The uploaded pack was inspected locally in this execution session. Binary PNG/JPG files require a binary-capable repository upload path. This connector can safely write UTF-8 text files, so this metadata and the CSS token file are committed while the binary image placement remains blocked until the assets are uploaded through Codespaces/Codex/git.
