# 120D Dashboard Live Visual Preview

Status: REVIEW ONLY — not approved for merge.

Branch:
`feature/120d-dashboard-live-visual-preview`

Route:
`/dashboard/?lang=ar`
`/dashboard/?lang=en`

Purpose:
This branch creates a live browser preview so Ahmed can approve the Dashboard visual direction on the running server before the final Screen Pack is locked.

Scope:
- Visual preview only.
- No migrations.
- No models.
- No production accounting calculations.
- No database writes.
- No PR and no merge before Ahmed approval.

Implemented preview behavior:
- Responsive Web / Tablet Landscape / Mobile layout.
- Light Hesba background surface.
- Wave decoration as an inline SVG layer, not baked into a production background.
- Cards and shells are real HTML/CSS, not image placeholders.
- SVG icon partial used for dashboard cards and actions.
- Drawer menu opens from the three-line menu on web, tablet, and mobile.
- Date and time update live in the browser.
- Charts are dynamic SVG placeholders that refresh every 5 seconds.
- Currency is shown as activity currency wording, not a fixed SAR/EGP assumption.

Review sizes:
- Web: 1366x768, 1440x900, 1536x864, 1920x1080
- Tablet landscape: 1024x768, 1180x820
- Mobile: 360x740, 390x844, 414x896, 430x932

Run locally / Codespace:
```bash
python manage.py runserver 0.0.0.0:8010
```

Arabic preview:
`/dashboard/?lang=ar`

English preview:
`/dashboard/?lang=en`

Known note:
This is still a review preview. Final implementation must be rebuilt/cleaned into the approved Screen Pack structure after visual approval.
