# 107_HESBA_UI_DIRECTION_CORRECTION

Status: DRAFT_REFERENCE

This note records Ahmed's feedback after the first premium status UI attempt.

## Feedback

Ahmed was not happy with the visual result. The problem was visual identity, not data logic.

The status page direction looked too much like a generic web dashboard. It did not feel close enough to the old Hesba AppSheet screens that Ahmed liked.

## Correct visual target

Hesba should feel:

* light
* clean
* Arabic-first
* mobile-first
* white/off-white
* teal and navy
* soft card-based
* close to the old AppSheet Hesba reference
* premium but calm

## Correct implementation approach

Do not keep polishing `/status/` alone.

The next UI work should create a shared visual base inspired by the Dashboard reference, then apply it to:

1. Dashboard
2. Reports hub
3. Management cards
4. Status page

## Safety rules

This is visual direction only.

No changes to:

* PostgreSQL data structure
* sales logic
* purchase logic
* inventory logic
* customer/supplier balances
* cashbox movements
* permissions
* reports calculations

Protected business cycle stays:

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports
