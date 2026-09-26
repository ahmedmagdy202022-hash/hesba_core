# Design tokens — direction B+

Status: APPROVED by Ahmed on 2026-09-25 (direction "B+" on the design canvas).
Source file: `static/hesba/css/tokens.css`. Roadmap item: 1.6 `TOKENS-001`.

## Direction
- **Navy is the structure.** Sidebar, headers and the brand block.
- **Teal is the action.** Primary buttons, the "new invoice" action and the active mobile tab. It sits on `--hs-teal`, with navy text on top for contrast.
- **Gold is a touch, never a fill.** The active navigation item, the owner avatar, and amounts owed to the business.
- The ground is a quiet grey-white. Content sits on white cards with a hairline border.

## Type
- Body, numbers and tables: **IBM Plex Sans Arabic** (400/500/600/700).
- Headings: **Noto Kufi Arabic** (600/700).
- Both are self-hosted under `static/hesba/fonts/`, under the SIL Open Font License 1.1 (licence beside each family). The app makes no request to a font CDN and renders the same offline.
- Base size 15px, line height 1.65. Arabic body text needs the extra leading. Key figures are 30px with tabular numerals.

## Scales
| Group | Tokens |
|---|---|
| Colour | `--hs-navy`, `--hs-teal`, `--hs-on-teal`, `--hs-teal-deep`, `--hs-gold*`, neutrals, and status pairs (`--hs-success` / `-wash` and so on) |
| Text | `--hs-text-xs` 12 → `--hs-text-num` 30 |
| Space | `--hs-space-1` 4px … `--hs-space-12` 48px (4px grid) |
| Radius | `sm` 8, `md` 12, `lg` 14, `xl` 20, `pill` |
| Controls | `--hs-control-height` 44px (minimum touch target), `-lg` 48px |
| Elevation | `--hs-shadow-sm`, `-md`, `-fab`, `--hs-focus-ring` |

## Palette reconciliation
The roadmap palette (Teal `#02AEB7`, Navy `#092851`) is the source. The older values in `AGENTS.md` and the current stylesheets (`#05243F`, `#16BDC4`, `#D9AD50`) are superseded as each stylesheet moves onto tokens.

## Logo usage (until the SVG source arrives)
- On light backgrounds: the full logo with its outer white removed.
- On navy: the reversed-colour logo. The wordmark is white, the teal rules and dots are kept, and the mark is unchanged.
- In compact spots (mobile headers, favicon): the mark alone.
- None of these redraws the logo. They are colour or background treatments of the approved artwork. Produce them from the SVG once it is available.
