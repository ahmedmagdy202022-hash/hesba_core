# Retail promo video: verified facts

Research date: 27 Nov 2026, on `develop` at `9722fb1`. Read-only review of the code and docs. A claim goes in the video only if it is listed here as built.

## Sub-activities

The activity is «نشاط تجاري». The try-it page (`/demo/commercial/`) offers five shops: retail, pharmacy, grocery, fashion, electronics. Film «محل تجزئة» (the default demo: an open shift and a month of sales); «سوبر ماركت / بقالة» is the alternative for barcode, carton and expiry shots. The shop tour in `config/demo.py`: «سجّل عملية بيع من الكاشير» → «شوف اللي قرب يخلص من المخزن» → «اقفل اليوم وشوف الربح».

## Built features (candidates)

| Feature | Evidence | Where to film | Do not over-promise |
|---|---|---|---|
| Till: barcode or item name, quick buttons, change, receipt | `templates/sales/pos.html`, `sales/pos.py`, `/sales/pos/`; tests `sales/tests_pos.py`, `tests_pos_buttons.py`, `tests_camera_scan.py` | «الكاشير»: «امسح الباركود أو اكتب اسم الصنف», «أزرار سريعة», «السلة», «المدفوع», «الباقي للعميل», «دفع وطباعة الإيصال» | Camera scan needs HTTPS. Offline selling only if the page was already open. No scale or card-machine link |
| Cashier shift and drawer count | `shifts/`, `/shifts/`; tests `shifts/tests.py` | «وردية الكاشير»: «المفروض يكون في الدرج», «الموجود في الدرج», «الفرق» | The difference is posted to the cashbox only by a manager |
| Stock alerts and reorder suggestions | `/inventory/`, `/inventory/reorder/`; tests `inventory/tests_reorder.py` | «نفد المخزون», «مخزون منخفض», «تحت الحد الأدنى»; «محتاج يتطلب»: «يكفي كام يوم», «المقترح» | Suggestions only; the owner reviews the purchase draft |
| Daily summary and profit | `/reports/daily/` (`reports/digest_views.py`, `templates/reports/digest.html`), `/reports/profit/`; tests `reports/tests_digest.py` | «ملخص النهارده»: «المبيعات», «الأكثر مبيعًا», «مجمل الربح», «الخزن»; «تقرير الأرباح» | The page opens on yesterday by default. «ابعته واتساب» only prepares the message |
| Credit customers | `/reports/aging/`, `/sales/collections/`; tests `reports/tests_aging.py`, `parties/tests.py` | «أعمار الديون», «تذكير واتساب», «كشف الحساب», «تحصيل» | The reminder is a prepared WhatsApp message the owner sends himself |

Tests run during the research: `sales.tests_pos`, `shifts`, `offline_pos`, `reports.tests_digest` (42 passed). The others listed exist but were not run that day.

## Do not claim

- A live link to the tax authority's e-invoice or e-receipt.
- AI features (ask, invoice from a photo, voice summary): off by default and need server keys.
- Card-machine, customer-display or scale integration.
- Automatic WhatsApp sending.
- Selling with no internet from a cold start.
- Online store, Shopify or courier sync as live.
- Any number, percentage or customer count.

## Wording on the screens

«الكاشير», «الخزنة» (plural «الخزن»; «خزينة» is not used anywhere), «المخزن», «فاتورة», «مرتجع», «وردية», «آجل», «ملخص النهارده», «تقرير الأرباح», «مخزون منخفض», «نفد المخزون».

## Second research pass: larger businesses (28 Nov 2026)

Ahmed rejected the first script on 28 Nov: not premium, and it spoke only to small shops. The new script speaks to owners of any size, so these claims were verified on `develop` (code, templates and tests read; no tests run in this pass).

| Claim in the script | Verdict | Evidence | Limits |
|---|---|---|---|
| A till in every branch | Built | `sales/pos.py` scopes the selectable warehouses and cashboxes to the branch selected in the «شغال في» bar; `entities/tests_scope.py` | No test opens `/sales/pos/` itself with a branch selected |
| Roles and permissions per employee | Built | `/settings/roles/` «الأدوار والصلاحيات», `/settings/users/`; custom roles (HG-049); `permissions/tests_role_matrix.py`, `tests_custom_roles.py`, `accounts/tests_users.py` | A role is not a branch assignment |
| Stock of every branch and warehouse | Built | `/entities/stock/` «فين الصنف ده؟» («الإجمالي في الشركة», «الكيان», «المخزن», «الكمية»), `/inventory/warehouses/`; `entities/tests.py::GroupStockTests`, `inventory/tests_warehouses.py` | Figures are computed on page load, no live refresh. `/inventory/` with no location chosen shows the company total |
| Alert before an item runs out, and a suggested quantity | Built | «مخزون منخفض», «نفد المخزون»; `/inventory/reorder/` column «المقترح»; `inventory/tests_reorder.py` | The reorder page is company-wide, not per branch |
| Reports for sales, profit and cashboxes | Built | `/reports/sales/`, `/reports/profit/`, `/reports/cashboxes/`, `/reports/daily/`; scoped in `reports/selectors.py`; `entities/tests_scope.py` (branch + main = group) | One branch at a time or the whole group summed; no side-by-side comparison |
| From the phone or the computer | Partly | Responsive layout; dashboard and daily summary have a phone layout (`reports/tests_ux_wave4_dashboard.py`) | The wide report tables scroll sideways on a phone, so the phone shot uses the dashboard or the daily summary. It is the phone browser, not an app |
| One shop up to a chain of branches | Built | Settings → «الفروع» (`/entities/`, «فرع جديد», kinds «فرع (نفس الشركة)» and «شركة مستقلة»); `entities/tests.py::EntityScreenTests` | |

### Added to the do-not-claim list

- An employee limited to one branch: the rule exists in code (`EntityMembership`) but no screen assigns it.
- A side-by-side comparison or ranking of branches.
- «لحظة بلحظة» or live sync wording (the script says «أول بأول»).
- A mobile app.
- «من أي مكان» as a guarantee (it depends on hosting).

### Demo data used for filming

The seeded demo has one branch and one warehouse. Two more branches, with a warehouse, a cashbox, transferred stock and a few sales each, were added through the product's own screens as the demo owner, inside a throwaway demo box. Nothing in the repository's demo seed was changed.
