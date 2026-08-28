# Hesba Brand & Asset Manifest

Status: ACTIVE BRAND CONTROL
Purpose: Define what the Agent may treat as Hesba identity, approved screen assets, legacy references, or rejected material.

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

## 2. Current production color anchors
Existing project UI uses closely related approved values, including:

- Navy family: approximately `#05243F` / `#052643`
- Teal family: approximately `#0C8A8F` / `#16BDC4`
- Gold family: approximately `#D6A84F` / `#D9AD50`
- Cream / Off-white family: approximately `#FBF7EF` / `#F6FBFB`

These are identity anchors, not permission to invent new palettes.

A screen-specific Screen Pack may define exact tokens.

## 3. OFFICIAL SOURCE ASSETS
These are Ahmed-supplied primary Hesba identity sources.

| Source name | Classification | Intended use | Rule |
|---|---|---|---|
| `Hespa_logo1` | OFFICIAL_SOURCE | primary square/logo source | Do not redraw or stylize automatically |
| `Hespa_logo_icon1` | OFFICIAL_SOURCE | logo/icon source | Use for icon/mark derivation only through approved asset task |
| `Hespa_logo2` | OFFICIAL_SOURCE | wide/full logo source | Preferred source where horizontal wordmark is needed |
| `hesba_app_icon_2026.png` | OFFICIAL_SOURCE | application icon | Preserve composition and identity |
| `Hesba_App_Launch` | OFFICIAL_SOURCE | launch/splash identity | Reference for launch identity and brand mood |

These source assets must be copied into canonical repository paths only through a controlled asset task. Renaming for production is allowed; visual redesign is not.

## 4. EXISTING REPOSITORY BRAND ASSETS
Existing project assets already used by approved/accepted UI may continue to be used when their screen contract allows them.

Known examples:
- `static/hesba/icons/hesba-icon.svg`
- approved login visual assets under `static/hesba/brand/`
- approved Setup Gate assets under `static/hesba/setup_gate/`

Classification:
`APPROVED_EXISTING_SCREEN_ASSET`

Rule:
Screen assets are not automatically global brand masters. Do not crop or repurpose them into a new logo.

## 5. LEGACY REFERENCE ASSETS
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

## 6. APPROVED SCREEN ASSETS
Every production screen must use only assets referenced by its approved Screen Pack.

Required classifications inside a Screen Pack:
- `VISUAL_REFERENCE_APPROVED`
- `PRODUCTION_BACKGROUND_APPROVED`
- `PRODUCTION_ASSET_APPROVED`
- `REFERENCE_ONLY`

The manifest for that screen must state path, purpose, device usage, and source.

## 7. REJECTED material
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

The Agent must not resurrect rejected material.

## 8. Logo rules
Never:
- invent a new Hesba logo;
- redraw the logo with AI;
- alter proportions;
- change core colors casually;
- add effects/glows/3D treatment to the master logo without approval;
- crop a logo from a screenshot;
- bake translatable text into a logo asset.

If the required canonical logo file is missing from the repository, report the asset gap. Do not fabricate a substitute.

## 9. Image/background rules
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

## 10. Icon rules
Icons across one screen/flow must feel like one family.

Avoid:
- random mixed libraries;
- unrelated stroke weights;
- arbitrary colors;
- playful consumer-style icons in serious financial areas.

New icon families require Screen Pack approval.

## 11. Responsive asset rules
Do not stretch one bitmap across Web/Tablet/Mobile if composition breaks.

The Screen Pack must state whether an asset:
- scales safely;
- uses safe crop;
- has device variants;
- disappears/reflows on smaller screens.

Tablet target is Landscape where specified by current workflow.

## 12. Asset naming
Preferred production naming:

`<screen>_<device>_<purpose>_<status>.<ext>`

Examples:
- `purchase_invoice_web_background_approved.png`
- `purchase_invoice_mobile_visual_approved.png`
- `hesba_logo_full_approved.png`

Avoid generic names:
- `image.png`
- `final2.png`
- `newnew.png`

## 13. Agent asset decision rule
Before using any visual asset, the Agent must be able to answer:
1. What is its classification?
2. What is its approved source?
3. Which screen/device may use it?
4. Is it production-safe or reference-only?
5. Does it contain translatable/clickable content?

If any answer is unclear, do not use the asset. Return the gap to Main Control.
