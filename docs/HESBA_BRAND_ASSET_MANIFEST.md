# Hesba Brand & Asset Manifest

Status: ACTIVE BRAND CONTROL
Purpose: Define exactly what the Agent may treat as Hesba identity, approved screen assets, global assets, legacy references, or rejected material.

## 1. Brand rule
Hesba must remain visually recognizable across every screen.

Core direction:
- Navy
- Teal
- Gold
- White / Off-white / Cream
- Premium Clean ERP
- Arabic-first hierarchy
- Rounded surfaces
- Soft shadows
- Clear spacing
- Calm, business-owner-friendly visual language

Do not turn Hesba into a generic SaaS dashboard.

## 2. Locked production color sources

### 2.1 Primary Brand Core — from accepted Login identity
These are the current exact primary brand tokens found in the accepted Login implementation:

- `brand_navy = #05243F`
- `brand_teal = #16BDC4`
- `brand_gold = #D9AD50`
- `brand_gold_light = #F5DC91`
- `brand_ink = #13243A`
- `brand_muted = #667085`
- `brand_offwhite = #F6FBFB`

Source:
`static/hesba/css/login.css`

Rule:
These are the default identity colors for new global assets unless a screen-specific approved contract uses an existing Hesba UI token intentionally.

### 2.2 Approved Product UI Support Family — from accepted Setup Gate
These exact UI tokens are already used by accepted Setup Gate screens:

- `ui_bg = #EDF9FB`
- `ui_surface = #FFFFFF`
- `ui_navy = #071F46`
- `ui_text = #102F5B`
- `ui_muted = #60718A`
- `ui_teal = #02AAB6`
- `ui_teal_deep = #038A9A`
- `ui_border = #D8EDF2`
- `ui_border_strong = #CFE7EE`

Source:
`static/hesba/css/setup_gate_web.css`

Rule:
These are approved UI-support colors. They may be used in screen shells/components when the Screen Pack follows the same Hesba family.

### 2.3 Palette prohibition
Do not invent approximate substitutions such as:
- random dark blue;
- random turquoise;
- unrelated mustard/gold;
- gradients not derived from the approved tokens.

Any new color outside the two approved families above requires an explicit Screen Pack decision.

## 3. OFFICIAL SOURCE ASSETS
These are Ahmed-supplied primary Hesba identity sources.

| Source name | Classification | Intended use | Rule |
|---|---|---|---|
| `Hespa_logo1` | OFFICIAL_SOURCE | primary square/logo source | Do not redraw or stylize automatically |
| `Hespa_logo_icon1` | OFFICIAL_SOURCE | logo/icon source | Use for icon/mark derivation only through approved asset task |
| `Hespa_logo2` | OFFICIAL_SOURCE | wide/full logo source | Preferred source where horizontal wordmark is needed |
| `hesba_app_icon_2026.png` | OFFICIAL_SOURCE | application icon | Preserve composition and identity |
| `Hesba_App_Launch` | OFFICIAL_SOURCE | launch/splash identity | Reference for launch identity and brand mood |

Important:
Do not claim these are canonical repository production files until the original sources are actually present in canonical global paths.

If the original source file is unavailable in the active repo/library, report:
`BRAND SOURCE GAP`

Do not fabricate a substitute.

## 4. EXISTING REPOSITORY BRAND ASSETS
Existing project assets already used by approved/accepted UI may continue to be used when their screen contract allows them.

Known examples:
- `static/hesba/icons/hesba-icon.svg`
- `static/hesba/brand/login_web.final.png`
- `static/hesba/brand/login_tablet.png`
- `static/hesba/brand/login_mobile.final.png`
- `static/hesba/setup_gate/assets/setup_gate_logo_approved.png`
- approved Setup Gate assets under `static/hesba/setup_gate/`

Classification:
`APPROVED_EXISTING_SCREEN_ASSET`

Rule:
Screen assets are not automatically global brand masters. Do not crop or repurpose them into a new logo.

## 5. Global asset-generation source rule
No P0 Global Asset Pack artwork may be approved unless all of the following are true:

1. Logo/mark usage is based on an official or already-approved Hesba source.
2. Colors use the locked tokens in Section 2.
3. Icon geometry is consistent across the family.
4. No generic SaaS visual language replaces Hesba identity.
5. The resulting files are separate production assets, not a poster/screenshot.
6. The manifest classifies each file explicitly.

A preview sheet may be created only after the individual assets exist.
The preview sheet is never itself a production asset.

## 6. LEGACY REFERENCE ASSETS
These Ahmed-supplied assets preserve older Hesba visual/business language but are not automatic production assets:

- `01_register_transaction.png`
- `02_print_reports.png`
- `03_new_customer.png`
- `04_new_supplier.png`
- `05_new_service.png`
- `06_new_cashbox.png`
- `Hesba_reports_watermark3`
- `Hesba_Reports_watermark2`
- `Hesba_Reports_watermarks1`

Classification:
`LEGACY_REFERENCE_ONLY`

Allowed:
- understand icon/card language;
- inspire a new approved assets pack;
- preserve continuity of concepts.

Not allowed:
- drop directly into a new production screen without approval;
- treat as current UI specification;
- stretch/crop as a shortcut.

## 7. APPROVED SCREEN ASSETS
Every production screen must use only assets referenced by its approved Screen Pack.

Required classifications inside a Screen Pack:
- `VISUAL_REFERENCE_APPROVED`
- `PRODUCTION_BACKGROUND_APPROVED`
- `PRODUCTION_ASSET_APPROVED`
- `REFERENCE_ONLY`

The manifest for that screen must state path, purpose, device usage, and source.

## 8. REJECTED material
Classification:
`REJECTED_DO_NOT_USE`

Includes:
- rejected AI mockups;
- wrong logo variants;
- stretched screenshots;
- screenshot crops used as UI;
- generic SaaS redesigns;
- any visual Ahmed rejected;
- unnamed files such as `image.png` with no approval record.

Explicitly rejected on 2026-08-28:
- `Hesba Master Data Design Board.png` — rejected; infographic, not Screen Pack/asset source.
- `HESBA Global Asset Pack Overview.png` — rejected; poster/overview with invented logo/palette treatment, not production assets.

The Agent must not resurrect rejected material.

## 9. Logo rules
Never:
- invent a new Hesba logo;
- redraw the logo with AI;
- approximate the Arabic wordmark;
- alter proportions;
- change core colors casually;
- add effects/glows/3D treatment to the master logo without approval;
- crop a logo from a screenshot;
- bake translatable text into a logo asset.

If the required canonical logo file is missing from the repository, report the asset gap. Do not fabricate a substitute.

## 10. Image/background rules
Production backgrounds may contain only fixed, non-clickable, non-translatable visual elements.

Do not bake into images:
- Arabic/English labels;
- buttons;
- input fields;
- dynamic values;
- user data;
- status values;
- anything interactive.

Real UI must be HTML/CSS/components above the approved visual shell.

## 11. Icon rules
Icons across one screen/flow must feel like one family.

Default P0 icon direction:
- clean professional line/duotone;
- Navy as structural color;
- Teal as primary accent;
- Gold only as restrained highlight;
- no rainbow icon packs;
- no cartoon/consumer style;
- must work at 16/20/24/32 px;
- SVG preferred.

Avoid:
- random mixed libraries;
- unrelated stroke weights;
- arbitrary colors;
- playful consumer-style icons in serious financial areas.

New icon families require Screen Pack/Global Pack approval.

## 12. Responsive asset rules
Do not stretch one bitmap across Web/Tablet/Mobile if composition breaks.

The Screen Pack must state whether an asset:
- scales safely;
- uses safe crop;
- has device variants;
- disappears/reflows on smaller screens.

Tablet target is Landscape where specified by current workflow.

## 13. Asset naming
Preferred production naming:

`<screen-or-global-family>_<purpose>_<status>.<ext>`

Examples:
- `global_customer_approved.svg`
- `purchase_invoice_web_background_approved.png`
- `hesba_logo_full_approved.svg`

Avoid generic names:
- `image.png`
- `final2.png`
- `newnew.png`

## 14. Agent asset decision rule
Before using any visual asset, the Agent must be able to answer:
1. What is its classification?
2. What is its approved source?
3. Which screen/device may use it?
4. Is it production-safe or reference-only?
5. Does it contain translatable/clickable content?
6. Does it use only approved Hesba color tokens?
7. Is the logo/mark from an approved source rather than recreated?

If any answer is unclear, do not use the asset. Return the gap to Main Control.
