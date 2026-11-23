# Design tokens — direction B+

Status: APPROVED by Ahmed on 2026-09-25 (direction "B+" on the design canvas).
Source file: `static/hesba/css/tokens.css`. Roadmap item: 1.6 `TOKENS-001`.

## Direction
- **Navy is the structure.** Sidebar, headers and the brand block.
- **Teal is the action.** Primary buttons, the "new invoice" action and the active mobile tab. It sits on `--hs-teal`, with navy text on top for contrast.
- **Gold is a touch, never a fill.** The active navigation item, the owner avatar, and amounts owed to the business.
- The ground is a quiet grey-white. Content sits on white cards with a hairline border. *(Superseded inside the app by the calm interior below, 10 Nov 2026; public pages keep it.)*

## Type
- Body, numbers and tables: **IBM Plex Sans Arabic** (400/500/600/700).
- Headings: **Noto Kufi Arabic** (600/700).
- Both are self-hosted under `static/hesba/fonts/`, under the SIL Open Font License 1.1 (licence beside each family). The app makes no request to a font CDN and renders the same offline.
- Base size 15px, line height 1.65. Arabic body text needs the extra leading. Key figures are 30px with tabular numerals.

## Scales
| Group | Tokens |
|---|---|
| Colour | `--hs-navy`, `--hs-teal`, `--hs-on-teal`, `--hs-teal-deep`, `--hs-gold*`, neutrals, and status pairs (`--hs-success` / `-wash` / `-line` and so on). Alerts use three severities: `--hs-danger` (urgent), `--hs-caution` (due soon) and `--hs-warning` (watch) |
| Text | `--hs-text-xs` 12 → `--hs-text-num` 30 |
| Space | `--hs-space-1` 4px … `--hs-space-12` 48px (4px grid) |
| Radius | `sm` 8, `md` 12, `lg` 14, `xl` 20, `pill` |
| Controls | `--hs-control-height` 44px (minimum touch target), `-lg` 48px |
| Elevation | `--hs-shadow-sm`, `-md`, `-lg`, `-fab`, `--hs-scrim` (behind drawers), `--hs-focus-ring` |
| Calm interior (UX Wave 4) | `--hs-page`, `--hs-card-*`, `--hs-input-radius`, `--hs-chip-*`, `--hs-gutter`, `--hs-band-radius`, `--hs-on-navy*`, `--hs-focus-ring-on-navy`, `--hs-tab-*`, `--hs-tabbar-*` (see below) |

## The calm interior (UX Wave 4, 10 Nov 2026)
Ahmed found the all-white interior glaring («الأبيض الكتير بيزغلل العين») and, on 10 Nov 2026, delegated the redesign.
The approved palette values do not change; what changes is which token is the page ground and how cards are drawn.

- **Page ground moved from `#F6F8FA` to the neutral wash `#EEF2F6`** inside the signed-in app. The new token is
  `--hs-page` (= `--hs-neutral-wash`). `--hs-ground` keeps `#F6F8FA`: the public pages (login, `/demo/`, customer
  links, public booking, print, error pages) still stand on it, and inside the app it remains the soft fill inside a
  white card (table heads, disabled inputs).
- **Cards are white with no border**: `--hs-card-bg`, `--hs-card-border` (`1px solid transparent`, so box sizes and
  `border-color` state modifiers keep working and a border still shows in forced-colours mode), `--hs-card-radius`
  (18px, 22px on a phone), `--hs-card-radius-sm` (14px / 16px), `--hs-card-shadow` (barely there) and
  `--hs-card-shadow-hover`. Borders stay only where they carry meaning: inputs, table grids, dividers inside a card,
  status edges.
- **Inputs** are white with a 1px border and `--hs-input-radius` (14px); the focus ring is teal.
  **Secondary buttons are quiet**: no outline, white on the page ground and the page wash inside a white card.
- **Tinted icon chips** (`.hs-ichip`, `--hs-chip-size` 44px, `--hs-chip-radius` 14px): a squircle in a wash with the
  icon in the matching deep colour. Hub tiles, report headings and (part 2) the dashboard's metric icons, shortcuts
  and attention rows share it.
- **Phones (≤700px):** a navy top bar and a **navy header band**: the page's own head (its title, back arrow and
  actions) continues the navy with `--hs-band-radius` (28px) bottom corners and bleeds by `--hs-gutter` (16px).
  Text on navy uses `--hs-on-navy` / `--hs-on-navy-muted`; quiet controls on navy `--hs-on-navy-wash`; focus on navy
  `--hs-focus-ring-on-navy` (solid teal). A **floating navy tab bar** (`--hs-tabbar-height` 68px,
  `--hs-tabbar-radius` 24px, `--hs-tabbar-gap` 12px above the edge) with the selling tab raised as a teal circle
  (`--hs-tab-rise` 18px) and the current tab tinted (`--hs-tab-active-wash`). A bar docked to the bottom edge clears
  `--hs-tabbar-space`, or `--hs-tabbar-clear` to clear the raised circle too.
- **The dashboard on a phone (part 2, 11 Nov 2026):** the band carries a calm greeting line (date · business, then the
  greeting) and the bell; a **money card** sits inside the band on `--hs-on-navy-wash` with a `--hs-on-navy-line` edge
  (today's sales, their invoice count, today as a share of the average day, the period's trend line: figures the view
  already works out); the shortcuts are a row of 60px tinted chips with the label under each (the first one solid teal);
  what needs attention is a short list of rows with a small chip and a chevron. No new tokens: it reads the ones above.
- **Desktop and tablet landscape** keep the navy sidebar and a light page head; only the calmer ground and the
  borderless cards apply there.
- A night theme swaps these tokens' values; it does not need new rules.

## Palette reconciliation
The roadmap palette (Teal `#02AEB7`, Navy `#092851`) is the source. The older values in `AGENTS.md` and the current stylesheets (`#05243F`, `#16BDC4`, `#D9AD50`) are superseded as each stylesheet moves onto tokens.

## Adoption
| Stylesheet | Task |
|---|---|
| `operations.css`, `master_data.css` | TOKENS-002 |
| `shell.css`, `table_cards.css` | SHELL-001, MOBILE-002 |
| `dashboard.css`, `login.css` | TOKENS-003 |
| Setup screens (`setup_gate_*`, `activity_*`) | Not yet: they carry their own approved visuals (roadmap D3.2) |
| Calm interior: `tokens.css`, `shell.css`, `buttons.css`, `lists.css`, `form_controls.css`, `table_cards.css`, `pager.css`, `report_tools.css`, `operations.css`, `master_data.css`, `dashboard.css`, `settings.css` | UX Wave 4 (part 1) |

## Logo usage (until the SVG source arrives)
- On light backgrounds: the full logo with its outer white removed.
- On navy: the reversed-colour logo. The wordmark is white, the teal rules and dots are kept, and the mark is unchanged.
- In compact spots (mobile headers, favicon): the mark alone.
- None of these redraws the logo. They are colour or background treatments of the approved artwork. Produce them from the SVG once it is available.
