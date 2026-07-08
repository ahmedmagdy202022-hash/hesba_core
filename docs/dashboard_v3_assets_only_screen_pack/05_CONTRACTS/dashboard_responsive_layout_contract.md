# Dashboard Responsive Layout Contract

## Web
- Test: 1366x768, 1440x900, 1536x864, 1920x1080.
- Max content width: 1600px.
- Header fixed height range: 56-72px.
- Hero: 3 areas: illustration / copy / score.
- KPI grid: 3 columns x 2 rows.
- Alerts and quick actions: 2 columns.
- Analytics: 5 cards when width allows; 4 cards + horizontal scroll is acceptable below 1366.

## Tablet Landscape
- Test: 1024x768 and 1180x820 landscape.
- Tablet is NOT portrait in this task.
- Use compressed desktop layout.
- KPI grid remains 3x2.
- Alerts and quick actions remain 2 columns.
- Analytics may reduce from 5 to 4 visible cards.

## Mobile
- Test: 360x740, 390x844, 414x896, 430x932.
- Single scroll page.
- KPI grid: 2 columns x 3 rows when width >= 360px.
- Alerts and quick actions may be side-by-side only if readable; otherwise stack.
- Analytics: cards can scroll horizontally or use carousel dots.

## Implementation rules
- Use real HTML/CSS components, not screenshot UI.
- Production background is optional visual shell; if CSS can reproduce the shell, CSS is preferred.
- Icons must be loaded from the individual PNG assets.
- Use CSS variables for colors/radius/shadow/gaps.
- Use clamp() for font sizes.
- Avoid viewport-specific hacks.
