/* FB-25: every discount takes a percent or an amount.
 *
 * Beside each discount box sits a small "%" box. A percent typed there fills the amount from what it is a percent of
 * (the line's quantity × price, the invoice's lines after their own discounts, the till's subtotal) and follows that
 * base when the quantity, the price or the cart change. Typing an amount by hand, or the page resetting it, clears
 * the percent. Only the amount is sent, so the server and posting stay as they were.
 */
(function () {
  'use strict';
  var en = document.body.dataset.lang === 'en';
  var busy = false;
  var boxes = [];

  // A figure shown by the page ("1,250.00"): commas group thousands.
  function num(text) { var v = parseFloat(String(text || '').replace(/,/g, '')); return isNaN(v) ? 0 : v; }
  // What someone typed (Codex #267): a lone comma is the decimal point, as in the till's own boxes ("2,5" is 2.5).
  function typed(text) {
    var value = String(text || '').trim();
    value = value.indexOf('.') < 0 && (value.match(/,/g) || []).length === 1 ? value.replace(',', '.') : value.replace(/,/g, '');
    var v = parseFloat(value);
    return isNaN(v) ? 0 : v;
  }
  function cents(v) { return Math.round(v * 100 + 1e-9) / 100; }
  function named(form, name) { return form ? form.querySelector('[name="' + name + '"]') : null; }
  function price(form, prefix) { return named(form, prefix + 'unit_sale_price') || named(form, prefix + 'unit_purchase_price'); }

  function lineBase(form, prefix) {
    var item = named(form, prefix + 'item');
    if (item && !item.value) { return 0; }
    return typed((named(form, prefix + 'quantity') || {}).value) * typed((price(form, prefix) || {}).value);
  }

  // What the discount in ``input`` is a percent of, or null when this page has nothing to take it from.
  function baseOf(input) {
    var form = input.form, name = input.name || '';
    var line = name.match(/^(.*-)line_discount_amount$/);
    if (line && named(form, line[1] + 'quantity') && price(form, line[1])) {
      return function () { return lineBase(form, line[1]); };
    }
    if (name === 'discount_amount' && form && form.querySelector('[name$="-quantity"]')) {
      return function () {
        var sum = 0;
        form.querySelectorAll('[name$="-quantity"]').forEach(function (qty) {
          var prefix = qty.name.slice(0, -'quantity'.length);
          if (!price(form, prefix)) { return; }
          sum += cents(lineBase(form, prefix) - typed((named(form, prefix + 'line_discount_amount') || {}).value));
        });
        return sum;
      };
    }
    if (input.hasAttribute('data-pos-discount')) {
      var subtotal = document.querySelector('[data-pos-subtotal]');
      return subtotal ? function () { return num(subtotal.textContent); } : null;
    }
    return null;
  }

  function set(box) {
    var pct = typed(box.pct.value);
    if (!box.pct.value.trim()) { return; }
    if (box.amount.value !== box.lastSet && box.lastSet !== null) {  // changed elsewhere (by hand, or the page reset it)
      box.pct.value = '';
      box.lastSet = null;
      if (box.mode) { box.mode(false); }
      return;
    }
    var value = cents(Math.max(0, box.base()) * Math.min(Math.max(pct, 0), 100) / 100).toFixed(2);
    if (value !== box.amount.value) {
      box.amount.value = value;
      busy = true;
      box.amount.dispatchEvent(new Event('input', { bubbles: true }));
      busy = false;
    }
    box.lastSet = value;
    if (box.show) { box.show(); }
  }

  function refresh() {
    boxes = boxes.filter(function (box) { return box.amount.isConnected; });
    boxes.forEach(function (box) { if (box.line) { set(box); } });  // the lines first: the invoice's own discount is taken after theirs
    boxes.forEach(function (box) { if (!box.line) { set(box); } });
  }

  function attach(amount) {
    var row = amount.parentElement && amount.parentElement.classList.contains('hs-pct-row') ? amount.parentElement : null;
    if (row && row.hesbaBound) { return; }
    if (row) {  // copied with a new invoice line: make its own
      row.querySelectorAll('.hs-pct, .hs-pct-toggle, .hs-pct-amount').forEach(function (el) { el.remove(); });
      amount.hidden = false;
    }
    var base = baseOf(amount);
    if (!base) { return; }
    if (!row) {
      row = document.createElement('span');
      row.className = 'hs-pct-row';
      amount.parentNode.insertBefore(row, amount);
      row.appendChild(amount);
    }
    row.hesbaBound = true;
    // An invoice line has a narrow cell: one box at a time, switched by a small "%" button.
    var compact = !!amount.closest('.inv-row');
    row.classList.toggle('hs-pct-row--compact', compact);
    var wrap = document.createElement('span');
    wrap.className = 'hs-pct';
    wrap.innerHTML = '<input type="text" inputmode="decimal" dir="ltr" autocomplete="off" placeholder="%"><span aria-hidden="true">%</span>';
    var pct = wrap.querySelector('input');
    pct.setAttribute('form', 'pct-detached');  // never sent: only the amount is
    pct.setAttribute('aria-label', en ? 'Discount as a percent' : 'الخصم كنسبة %');
    pct.setAttribute('data-discount-percent', '');
    row.appendChild(wrap);
    var box = { amount: amount, pct: pct, base: base, line: /line_discount_amount$/.test(amount.name || ''), lastSet: null };
    if (compact) {
      var toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'hs-pct-toggle';
      toggle.textContent = '%';
      toggle.setAttribute('aria-label', en ? 'Type the discount as a percent' : 'اكتب الخصم كنسبة');
      var shown = document.createElement('small');
      shown.className = 'hs-pct-amount';
      row.appendChild(toggle);
      row.appendChild(shown);
      box.mode = function (percent) {
        wrap.hidden = !percent;
        amount.hidden = percent;  // still sent with the form
        shown.hidden = !percent;
        toggle.setAttribute('aria-pressed', percent ? 'true' : 'false');
        if (!percent) { pct.value = ''; box.lastSet = null; }
      };
      box.show = function () { shown.textContent = pct.value.trim() ? '= ' + amount.value : ''; };
      toggle.addEventListener('click', function () {
        var percent = wrap.hidden;
        box.mode(percent);
        (percent ? pct : amount).focus();
      });
      box.mode(false);
    }
    boxes.push(box);
    pct.addEventListener('input', function () {
      if (!pct.value.trim()) { return; }
      box.lastSet = amount.value;  // the percent now owns the amount
      set(box);
      refresh();
    });
    amount.addEventListener('input', function () {
      if (!busy && pct.value) { pct.value = ''; box.lastSet = null; }  // an amount typed by hand wins
    });
  }

  function scan(root) {
    (root || document).querySelectorAll('input[name$="line_discount_amount"], input[name="discount_amount"], input[data-pos-discount]').forEach(attach);
  }

  scan();
  // A percent follows its base: a quantity, a price, an item, or the till's cart.
  ['input', 'change'].forEach(function (type) {
    document.addEventListener(type, function (event) {
      if (busy || !boxes.length || (event.target && event.target.hasAttribute && event.target.hasAttribute('data-discount-percent'))) { return; }
      setTimeout(refresh, 0);  // after the page's own handlers set prices and totals
    });
  });
  document.addEventListener('click', function () { if (boxes.length) { setTimeout(refresh, 0); } });
  var subtotal = document.querySelector('[data-pos-subtotal]');
  if (subtotal && window.MutationObserver) {
    new MutationObserver(function () { if (!busy) { refresh(); } }).observe(subtotal, { childList: true, characterData: true, subtree: true });
  }
  if (window.MutationObserver) {  // a new invoice line gets its own percent box
    new MutationObserver(function (changes) {
      changes.forEach(function (change) { change.addedNodes.forEach(function (node) { if (node.querySelectorAll) { scan(node); } }); });
    }).observe(document.body, { childList: true, subtree: true });
  }
})();
