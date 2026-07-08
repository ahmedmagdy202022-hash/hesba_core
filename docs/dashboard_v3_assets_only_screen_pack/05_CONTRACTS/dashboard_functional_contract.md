# Dashboard Functional Contract

Screen: Core Dashboard
Route: /dashboard/

Purpose:
A premium operational and analytical dashboard for Hesba business owners.

Blocks:
1. Header: menu, language, time, date, notifications, brand, profile.
2. Hero: user greeting, business message, health score, illustration.
3. KPI Cards: daily sales, net profit, cashbox balance, customer receivables, supplier payables, daily expenses.
4. Smart Alerts: urgent/medium/info alerts.
5. Quick Actions: new invoice, new customer, new supplier, add product/service, journal entry, cash movement, print report, register transaction.
6. Analytics: sales trend, cash vs credit, top products/services, receivable/payable aging.
7. Onboarding: setup/start progress.

Permission rules:
- Profit/cost and sensitive financial values must respect permissions.
- Dashboard is read-only except quick action navigation.
- Hidden values should preserve card layout, replacing value with permission-safe placeholder.

Language rules:
- Arabic and English supported.
- RTL/LTR layout must be tested.
- No text is baked into images.
