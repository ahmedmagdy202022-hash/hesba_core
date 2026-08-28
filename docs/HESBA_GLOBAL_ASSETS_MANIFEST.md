# Hesba Global Assets Manifest

Status: INITIAL INVENTORY / GAP MAP
Baseline audited: develop
Target production root: static/hesba/global/

## Classification key
- APPROVED_GLOBAL: reusable anywhere within stated usage.
- APPROVED_SCREEN_ONLY: approved only for its existing screen.
- OFFICIAL_SOURCE: Ahmed-approved source asset; canonical production conversion may still be needed.
- LEGACY_REFERENCE: reference only.
- GAP_REQUIRED: asset does not yet exist in the approved global library.
- RESTRICTED: may exist, but use requires explicit functional authorization.

## A. Existing repository inventory

| ID | Current path | Classification | Usage |
|---|---|---|---|
| EXIST-001 | static/hesba/icons/hesba-icon.svg | APPROVED_SCREEN_ONLY | Existing Login/Dashboard icon usage; do not assume global master |
| EXIST-002 | static/hesba/brand/login_web.final.png | APPROVED_SCREEN_ONLY | Login Web background only |
| EXIST-003 | static/hesba/brand/login_tablet.png | APPROVED_SCREEN_ONLY | Login Tablet background only |
| EXIST-004 | static/hesba/brand/login_mobile.final.png | APPROVED_SCREEN_ONLY | Login Mobile background only |
| EXIST-005 | static/hesba/setup_gate/setup_gate_web_background_approved.png | APPROVED_SCREEN_ONLY | Setup Gate Web background only |
| EXIST-006 | static/hesba/setup_gate/setup_gate_web_visual_approved.png | APPROVED_SCREEN_ONLY | Setup Gate Web visual reference only |
| EXIST-007 | static/hesba/setup_gate/assets/setup_gate_logo_approved.png | APPROVED_SCREEN_ONLY | Setup Gate logo instance |
| EXIST-008 | static/hesba/setup_gate/assets/setup_gate_hero_illustration_approved.png | APPROVED_SCREEN_ONLY | Setup Gate hero only |
| EXIST-009 | static/hesba/setup_gate/assets/setup_gate_step_* | APPROVED_SCREEN_ONLY | Setup step family |
| EXIST-010 | static/hesba/setup_gate/icons/activity_* | APPROVED_SCREEN_ONLY | Setup activity selection family |

Conclusion:
The repository currently has screen-specific assets, but no complete reusable Global Asset Pack.

## B. Official source assets to canonicalize

| ID | Source name | Target | Classification |
|---|---|---|---|
| BRAND-001 | Hespa_logo1 | global/brand/hesba_logo_full.* | OFFICIAL_SOURCE |
| BRAND-002 | Hespa_logo_icon1 | global/brand/hesba_logo_mark.* | OFFICIAL_SOURCE |
| BRAND-003 | Hespa_logo2 | alternate full/wordmark source | OFFICIAL_SOURCE |
| BRAND-004 | hesba_app_icon_2026.png | global/brand/hesba_app_icon.png | OFFICIAL_SOURCE |
| BRAND-005 | Hesba_App_Launch | global/brand/hesba_splash_mark.png | OFFICIAL_SOURCE |

Canonical production assets are GAP_REQUIRED until the correct original source file is copied/converted into the target global path without redesign.

## C. P0 Global gaps — build before Master Data implementation

### Navigation
| ID | Target path | State |
|---|---|---|
| NAV-001 | global/navigation/dashboard.svg | GAP_REQUIRED |
| NAV-002 | global/navigation/master_data.svg | GAP_REQUIRED |
| NAV-003 | global/navigation/operations.svg | GAP_REQUIRED |
| NAV-004 | global/navigation/purchases.svg | GAP_REQUIRED |
| NAV-005 | global/navigation/inventory.svg | GAP_REQUIRED |
| NAV-006 | global/navigation/sales.svg | GAP_REQUIRED |
| NAV-007 | global/navigation/customers.svg | GAP_REQUIRED |
| NAV-008 | global/navigation/suppliers.svg | GAP_REQUIRED |
| NAV-009 | global/navigation/items_services.svg | GAP_REQUIRED |
| NAV-010 | global/navigation/cashboxes.svg | GAP_REQUIRED |
| NAV-011 | global/navigation/reports.svg | GAP_REQUIRED |
| NAV-012 | global/navigation/settings.svg | GAP_REQUIRED |
| NAV-013 | global/navigation/users_permissions.svg | GAP_REQUIRED |

### Master Data
| ID | Target path | State |
|---|---|---|
| MD-ICON-001 | global/master_data/cashbox.svg | GAP_REQUIRED |
| MD-ICON-002 | global/master_data/location.svg | GAP_REQUIRED |
| MD-ICON-003 | global/master_data/warehouse.svg | GAP_REQUIRED |
| MD-ICON-004 | global/master_data/supplier.svg | GAP_REQUIRED |
| MD-ICON-005 | global/master_data/customer.svg | GAP_REQUIRED |
| MD-ICON-006 | global/master_data/category.svg | GAP_REQUIRED |
| MD-ICON-007 | global/master_data/item.svg | GAP_REQUIRED |
| MD-ICON-008 | global/master_data/service.svg | GAP_REQUIRED |
| MD-ICON-009 | global/master_data/barcode.svg | GAP_REQUIRED |
| MD-ICON-010 | global/master_data/unit.svg | GAP_REQUIRED |
| MD-ICON-011 | global/master_data/price_tag.svg | GAP_REQUIRED |

### Actions
| ID | Target path | State |
|---|---|---|
| ACT-001 | global/actions/add.svg | GAP_REQUIRED |
| ACT-002 | global/actions/edit.svg | GAP_REQUIRED |
| ACT-003 | global/actions/view.svg | GAP_REQUIRED |
| ACT-004 | global/actions/search.svg | GAP_REQUIRED |
| ACT-005 | global/actions/filter.svg | GAP_REQUIRED |
| ACT-006 | global/actions/sort.svg | GAP_REQUIRED |
| ACT-007 | global/actions/save.svg | GAP_REQUIRED |
| ACT-008 | global/actions/cancel.svg | GAP_REQUIRED |
| ACT-009 | global/actions/back.svg | GAP_REQUIRED |
| ACT-010 | global/actions/refresh.svg | GAP_REQUIRED |
| ACT-011 | global/actions/more.svg | GAP_REQUIRED |
| ACT-012 | global/actions/activate.svg | GAP_REQUIRED |
| ACT-013 | global/actions/deactivate.svg | GAP_REQUIRED |
| ACT-014 | global/actions/delete.svg | RESTRICTED |

### System/Header
| ID | Target path | State |
|---|---|---|
| SYS-001 | global/system/menu.svg | GAP_REQUIRED |
| SYS-002 | global/system/close.svg | GAP_REQUIRED |
| SYS-003 | global/system/notification.svg | GAP_REQUIRED |
| SYS-004 | global/system/language.svg | GAP_REQUIRED |
| SYS-005 | global/system/user.svg | GAP_REQUIRED |
| SYS-006 | global/system/calendar.svg | GAP_REQUIRED |
| SYS-007 | global/system/clock.svg | GAP_REQUIRED |
| SYS-008 | global/system/help.svg | GAP_REQUIRED |
| SYS-009 | global/system/logout.svg | GAP_REQUIRED |
| SYS-010 | global/system/security.svg | GAP_REQUIRED |
| SYS-011 | global/system/permission.svg | GAP_REQUIRED |
| SYS-012 | global/system/chevron_left.svg | GAP_REQUIRED |
| SYS-013 | global/system/chevron_right.svg | GAP_REQUIRED |

### Status
| ID | Target path | State |
|---|---|---|
| STATUS-001 | global/status/success.svg | GAP_REQUIRED |
| STATUS-002 | global/status/info.svg | GAP_REQUIRED |
| STATUS-003 | global/status/warning.svg | GAP_REQUIRED |
| STATUS-004 | global/status/error.svg | GAP_REQUIRED |
| STATUS-005 | global/status/pending.svg | GAP_REQUIRED |
| STATUS-006 | global/status/draft.svg | GAP_REQUIRED |
| STATUS-007 | global/status/posted.svg | GAP_REQUIRED |
| STATUS-008 | global/status/cancelled.svg | GAP_REQUIRED |
| STATUS-009 | global/status/paid.svg | GAP_REQUIRED |
| STATUS-010 | global/status/partial.svg | GAP_REQUIRED |
| STATUS-011 | global/status/credit.svg | GAP_REQUIRED |
| STATUS-012 | global/status/active.svg | GAP_REQUIRED |
| STATUS-013 | global/status/inactive.svg | GAP_REQUIRED |
| STATUS-014 | global/status/locked.svg | GAP_REQUIRED |

## D. P1 Operating-cycle families
All currently GAP_REQUIRED as global assets:
- purchases/*
- inventory/*
- sales/*
- cashboxes/*
- reports/*
- communication/*
- alerts/*

Exact required filenames are controlled by docs/HESBA_GLOBAL_ASSET_PACK_SPEC.md.

## E. P2 Illustration families
Currently GAP_REQUIRED:
- onboarding/*
- empty_states/*

These do not block the first functional implementation if a high-quality CSS/SVG empty state is acceptable in the approved Screen Pack.

## F. Promotion rule
A screen-specific approved asset does not become APPROVED_GLOBAL automatically.

Promotion requires Main Control to:
1. confirm it is generic enough;
2. assign canonical path;
3. verify no baked translatable/interactive content;
4. update this manifest to APPROVED_GLOBAL.


## G. P0 SVG candidate family — 2026-08-28

Classification: `CANDIDATE_FOR_AHMED_APPROVAL`

Actual separate SVG files now exist on branch `docs/agent-quality-brand-control` under:
- `static/hesba/global/navigation/`
- `static/hesba/global/master_data/`
- `static/hesba/global/actions/`
- `static/hesba/global/system/`
- `static/hesba/global/status/`

Candidate files created:
- navigation: dashboard, master_data, purchases, sales
- master_data: cashbox, location, supplier, customer, category, item, service
- actions: add, edit, search, filter
- system: menu, notification
- status: success, warning, error, info

Identity tokens used:
- Navy `#05243F`
- Teal `#16BDC4`
- Gold `#D9AD50`

These files are real SVG assets, but they are NOT `APPROVED_GLOBAL` yet.

Ahmed approval of the visual family is required before:
- completing the rest of P0 in the same style;
- allowing Agent production use.

Brand binary note:
Ahmed's original logo/app/launch source files are available to Main Control and were used for exact-reference previewing, but the original binary masters have not yet been copied into canonical GitHub paths. Existing approved repository logos remain screen-specific until canonical source transfer is completed.
