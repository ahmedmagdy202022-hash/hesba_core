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
