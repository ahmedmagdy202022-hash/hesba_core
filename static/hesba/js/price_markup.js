/* FB2-PRICE: the selling price from the purchase price plus a margin, rounded the way the shop prices.
   Wherever an item's purchase and selling price sit together (the item form, "+ new item" inside an invoice),
   a margin % and a rounding choice appear under them. Typing the purchase price or the margin fills the selling
   price; typing the selling price yourself shows the margin it makes. Rounding goes up, never below the margin:
   to the next pound, the next 5, the next 10, or ending in .99. The last margin and rounding are remembered on
   this device for the next item. Nothing is stored on the server: the item keeps its two prices as before. */
(function () {
  'use strict';
  var en = document.body.dataset.lang === 'en';
  var KEY = 'hesba.priceMarkup';
  var ROUNDING = [
    ['', en ? 'No rounding' : 'من غير تقريب'],
    ['1', en ? 'Up to the pound' : 'لأقرب جنيه (لفوق)'],
    ['5', en ? 'Up to the next 5' : 'لأقرب ٥ (لفوق)'],
    ['10', en ? 'Up to the next 10' : 'لأقرب ١٠ (لفوق)'],
    ['99', en ? 'Ending in .99' : 'ينتهي بـ ‎.99'],
  ];

  function number(text) {
    var digits = String(text || '').trim()
      .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); })
      .replace(/[۰-۹]/g, function (d) { return String(d.charCodeAt(0) - 0x06F0); })
      .replace(/[٬,]/g, '').replace('٫', '.');
    var value = parseFloat(digits);
    return isFinite(value) ? value : null;
  }
  // Exposed for the check in the browser console and for reuse.
  function price(buy, margin, rounding) {
    var raw = buy * (1 + margin / 100);
    var cents = Math.round(raw * 100) / 100;
    var EPS = 1e-9;
    if (rounding === '1') return Math.ceil(cents - EPS);
    if (rounding === '5') return Math.ceil(cents / 5 - EPS) * 5;
    if (rounding === '10') return Math.ceil(cents / 10 - EPS) * 10;
    if (rounding === '99') return Math.max(Math.ceil(cents - EPS) - 0.01, 0);
    return cents;
  }
  window.hesbaPriceFromMargin = price;

  function remembered() {
    try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function remember(margin, rounding) {
    try { localStorage.setItem(KEY, JSON.stringify({ margin: margin, rounding: rounding })); } catch (e) { /* private window */ }
  }

  document.querySelectorAll('input[name="default_sale_price"]').forEach(function (sale, index) {
    var scope = sale.closest('dialog, form') || document;
    var buy = scope.querySelector('input[name="default_purchase_price"]');
    if (!buy || sale.dataset.markupReady) return;  // no cost on screen (no permission): nothing to work from
    sale.dataset.markupReady = '1';
    var detached = buy.getAttribute('form');
    var holder = buy.closest('.md-field, .op-field') || buy.parentNode;
    var box = document.createElement('div');
    box.className = (holder.classList.contains('op-field') ? 'op-field' : 'md-field') + ' hs-markup';
    var id = 'hs-markup-' + index;
    box.innerHTML = '<label for="' + id + '">' + (en ? 'Margin on purchase %' : 'نسبة الربح على الشراء %') + '</label>' +
      '<div class="hs-markup__row"><input id="' + id + '" inputmode="decimal" dir="ltr" autocomplete="off" placeholder="' + (en ? 'e.g. 30' : 'مثلًا 30') + '">' +
      '<select aria-label="' + (en ? 'Rounding' : 'التقريب') + '">' +
      ROUNDING.map(function (r) { return '<option value="' + r[0] + '">' + r[1] + '</option>'; }).join('') + '</select></div>' +
      '<small>' + (en ? 'Fills the selling price from the purchase price. Type a selling price yourself and the margin follows it.'
        : 'سعر البيع بيتحسب لوحده من سعر الشراء. لو كتبت سعر البيع بإيدك، النسبة بتتعدل عليه.') + '</small>';
    holder.insertAdjacentElement('afterend', box);
    var margin = box.querySelector('input');
    var rounding = box.querySelector('select');
    // The fields inside "+ new item" belong to a detached form; these helpers must never be sent with the invoice.
    if (detached) { margin.setAttribute('form', detached); rounding.setAttribute('form', detached); }

    function fillSale() {
      var cost = number(buy.value);
      var pct = number(margin.value);
      if (cost === null || cost <= 0 || pct === null) return;
      sale.value = price(cost, pct, rounding.value).toFixed(2);
      sale.dispatchEvent(new Event('input', { bubbles: true }));
    }
    function showMargin() {
      var cost = number(buy.value);
      var value = number(sale.value);
      if (cost === null || cost <= 0 || value === null || value <= 0) return;
      margin.value = String(Math.round((value / cost - 1) * 1000) / 10);
    }

    // Who leads: the margin (it fills the price) or a price typed by hand (the margin follows it).
    var lead = 'margin';
    var saved = remembered();
    if (saved.rounding !== undefined && ROUNDING.some(function (r) { return r[0] === saved.rounding; })) rounding.value = saved.rounding;
    if ((number(sale.value) || 0) > 0) {
      lead = 'sale';  // an item that already has its price keeps it
      showMargin();
    } else if (saved.margin !== undefined && saved.margin !== '') {
      margin.value = saved.margin;
    }

    buy.addEventListener('input', function () { if (lead === 'margin') fillSale(); else showMargin(); });
    margin.addEventListener('input', function () { lead = 'margin'; fillSale(); remember(margin.value, rounding.value); });
    rounding.addEventListener('change', function () { lead = 'margin'; fillSale(); remember(margin.value, rounding.value); });
    sale.addEventListener('input', function (event) {
      if (!event.isTrusted) return;  // our own fill
      lead = 'sale';
      showMargin();
    });
  });
})();
