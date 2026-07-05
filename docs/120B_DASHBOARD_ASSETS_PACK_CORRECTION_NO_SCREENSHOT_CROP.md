# 120B Dashboard Assets Pack Correction — No Screenshot Crop

Status: ACTIVE_CORRECTION
Decision by: Ahmed / Main Control Chat

This correction overrides any previous interpretation of the dashboard assets pack process.

## Reason for this correction

Ahmed rejected the previous attempt because it treated an approved dashboard screenshot as a production asset source and extracted/cropped visual pieces from it.

That is not the approved Hesba workflow.

## Rejected output

The previously generated/downloaded file is rejected and must not be used:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED.zip
```

Reason:

```text
It included crop-and-paste assets extracted from a dashboard screenshot.
```

## Mandatory rule

```text
Approved visual screenshots are references only.
Never crop production assets from approved screenshots.
Never slice UI screenshots into implementation assets.
Never treat a screenshot as a source asset pack.
```

## Correct role of the approved dashboard visual

The approved dashboard visual is only for:

```text
Layout reference
Spacing reference
Visual hierarchy reference
Card style reference
Color/identity direction reference
Device adaptation reference
```

It is not for:

```text
Cutting hero images
Cutting icons
Cutting charts
Cutting cards
Cutting logos
Cutting any bitmap UI pieces for production
```

## Correct assets pack source rules

The actual Dashboard Assets Pack must contain atomic reusable source assets only.

Allowed sources:

```text
Existing approved Hesba logo files
Existing approved Hesba app identity assets
Original standalone SVG icons created specifically for dashboard use
Original standalone hero illustration asset
Original standalone empty-state illustration asset
CSS/SVG chart components
Tokens and manifest files
```

Not allowed:

```text
Cropped screenshot pieces
Screenshot-derived hero crops
Screenshot-derived card crops
Screenshot-derived icons
Screenshot-derived chart images
Invented logos
Generic SaaS icon packs
Rejected generated dashboard images
```

## Hero/banner correction

Ahmed requested one specific visual correction:

```text
Keep the approved dashboard screen.
Change only the top banner/hero illustration direction to the cleaner, more premium direction.
Do not redesign the whole screen.
```

Therefore the hero asset must be an original standalone asset, not a crop from the approved screenshot.

Expected hero output:

```text
hero/dashboard_hero_premium_banner.png
hero/dashboard_hero_premium_banner_tablet_landscape.png
hero/dashboard_hero_premium_banner_mobile_safe.png
```

## Desktop menu correction

Ahmed requested:

```text
Add a 3-line menu button to the desktop screen.
Place it in a suitable header position.
Do not remove the existing desktop navigation/menu area unless Ahmed explicitly approves.
```

The menu button must be a standalone icon asset:

```text
header/menu_hamburger.svg
```

## Tablet correction

Ahmed requested:

```text
Tablet dashboard must be landscape.
Add a 3-line menu button to tablet in a suitable header position.
Do not create a portrait tablet concept.
```

## Assets pack target structure

The next valid assets pack must use this structure:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED_V2/
  brand/
  header/
  nav/
  hero/
  health/
  kpi/
  alerts/
  actions/
  analytics/
  empty/
  tokens/
  manifest/
  reference/
  README.md
```

## Reference folder rule

The approved dashboard screenshot may be included only under:

```text
reference/
```

With a clear note:

```text
This is a visual reference only. Do not crop or use as a production asset.
```

## Required manifest warning

The manifest must include this warning at the top:

```text
WARNING: Visual screenshots are references only. Production assets in this pack must be atomic reusable files, not cropped from screenshots.
```

## Implementation guardrail

Sub Chat must not start implementation until the corrected assets pack exists.

Do not send the rejected pack to Sub Chat.
Do not implement from cropped assets.
Do not implement from screenshot slices.

## Current next step

Prepare a clean assets pack from original/atomic assets only:

```text
120B_DASHBOARD_ASSETS_PACK_APPROVED_V2.zip
```

No new screen generation is allowed unless Ahmed explicitly asks for it.
