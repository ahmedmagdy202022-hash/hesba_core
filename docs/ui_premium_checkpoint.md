# Premium UI checkpoint

## Goal

Create a premium read-only UI layer for Hesba Core without changing the accounting foundation.

## Scope completed

- Premium shell for Home, Dashboard, and Reports template.
- Premium shell for Status Counts report.
- Core positioning as one reusable ERP Core for stores, services, construction, industrial future modules, and other activities.
- Quick action cards for operational UX.
- Protected rules section remains visible.
- Status remains safe: no money totals, no balances, no cost, no profit.
- Codespaces preview host support through ALLOWED_HOSTS environment fallback.

## Accounting protection

No transaction posting logic was changed.

The protected cycle remains:

Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Activity editions shown in UI

- Store Edition
- Services and Telecom
- Construction
- Industrial

These are UI and roadmap cards only in this checkpoint. Industrial and construction logic should be implemented later through settings, roles, and modules over the same Core, not as a separate accounting engine.

## Next recommended checkpoint

Add safe live counts and non-sensitive widgets to the dashboard. Keep cost and profit hidden until real permissions are enforced.
