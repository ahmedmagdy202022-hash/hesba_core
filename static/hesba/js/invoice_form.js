/* BARCODE-001: scan-to-line, auto price and a live total on the invoice forms.
 *
 * A barcode scanner types the code and presses Enter. The scan box finds the
 * item by barcode (or item code), adds 1 to its line if it is already on the
 * invoice, or fills the next empty line, adding a line when all are used.
 * The live total is a convenience only: the server recalculates and validates
 * every figure when the draft is saved.
 *
 * R2-6: each line shows its own total, a line can be cleared, and the summary
 * beside the lines follows the invoice discount, tax and amount paid.
 */
(function () {
  'use strict';
  var form = document.querySelector('[data-invoice-form]');
  if (!form) { return; }
  var catalogNode = document.getElementById('hs-item-catalog');
  var catalog = catalogNode ? JSON.parse(catalogNode.textContent) : [];
  var priceField = form.getAttribute('data-price-field');
  var words = JSON.parse(form.getAttribute('data-words') || '{}');
  var byId = {}, byCode = {};
  catalog.forEach(function (item) {
    byId[String(item.id)] = item;
    if (item.barcode) { byCode[item.barcode.toLowerCase()] = item; }
    if (item.code) { byCode[item.code.toLowerCase()] = byCode[item.code.toLowerCase()] || item; }
  });

  // PRICE-001: the customer's price list, when the shop uses price lists.
  var bookNode = document.getElementById('hs-price-book');
  var book = bookNode ? JSON.parse(bookNode.textContent) : null;
  var customerSelect = form.querySelector('select[name="customer"]');
  var listNote = document.querySelector('[data-price-list-note]');

  function customerList() {
    if (!book || !customerSelect) { return null; }
    var listId = book.customers[customerSelect.value];
    return listId ? book.lists[listId] : null;
  }

  function priceFor(item) {
    var list = customerList();
    var own = list && list.prices[String(item.id)];
    return own !== undefined && own !== null ? own : item.price;
  }

  function showList() {
    if (!listNote) { return; }
    var list = customerList();
    listNote.hidden = !list;
    listNote.textContent = list ? (listNote.getAttribute('data-prefix') || '') + (document.documentElement.lang === 'en' ? list.name_en : list.name_ar) : '';
  }

  var taxNode = document.getElementById('hs-tax-rates');
  var taxRates = taxNode ? JSON.parse(taxNode.textContent) : null;

  // UNITS-001: bigger units per item (carton, box...), with their own price and barcode.
  var unitsNode = document.getElementById('hs-units');
  var units = unitsNode ? JSON.parse(unitsNode.textContent) : null;
  var unitByCode = {};
  if (units) {
    Object.keys(units).forEach(function (itemId) {
      units[itemId].forEach(function (unit) {
        unit.itemId = itemId;
        if (unit.barcode) { unitByCode[unit.barcode.toLowerCase()] = unit; }
      });
    });
  }
  var isEn = document.documentElement.lang === 'en';

  function unitOf(index) {
    var select = field(index, 'unit');
    var item = field(index, 'item');
    if (!units || !select || !select.value || !item) { return null; }
    return (units[item.value] || []).filter(function (unit) { return String(unit.id) === select.value; })[0] || null;
  }

  function fillUnits(index) {
    var select = field(index, 'unit');
    var item = field(index, 'item');
    if (!units || !select || !item) { return; }
    var keep = select.value;
    var baseLabel = select.options.length ? select.options[0].textContent : '';
    select.innerHTML = '';
    var base = document.createElement('option');
    base.value = ''; base.textContent = baseLabel;
    select.appendChild(base);
    (units[item.value] || []).forEach(function (unit) {
      var option = document.createElement('option');
      option.value = String(unit.id);
      option.textContent = (isEn ? unit.name_en : unit.name_ar) + ' (' + parseFloat(unit.factor) + ')';
      select.appendChild(option);
    });
    select.value = keep;
    if (select.value !== keep) { select.value = ''; }
  }

  var total = form.querySelector('input[name="lines-TOTAL_FORMS"]');
  var max = form.querySelector('input[name="lines-MAX_NUM_FORMS"]');
  var linesBox = form.querySelector('.op-lines');
  var scan = form.querySelector('[data-scan-input]');
  var status = form.querySelector('[data-scan-status]');
  var liveTotal = form.querySelector('[data-live-total]');

  function field(index, name) { return form.querySelector('[name="lines-' + index + '-' + name + '"]'); }
  function count() { return parseInt(total.value, 10) || 0; }
  function num(input) { var v = parseFloat((input && input.value || '').replace(',', '.')); return isNaN(v) ? 0 : v; }

  function addLine() {
    var n = count();
    var limit = parseInt(max && max.value, 10) || 20;
    if (n >= limit) { return -1; }
    var last = linesBox.querySelector('.op-line:last-of-type');
    var clone = last.cloneNode(true);
    var from = n - 1;
    clone.querySelectorAll('[name], [id], [for]').forEach(function (el) {
      ['name', 'id', 'for'].forEach(function (attr) {
        var value = el.getAttribute(attr);
        if (value) { el.setAttribute(attr, value.replace('lines-' + from + '-', 'lines-' + n + '-').replace('id_lines-' + from + '-', 'id_lines-' + n + '-')); }
      });
      if (el.tagName === 'SELECT') { el.selectedIndex = 0; }
      else if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') { el.value = el.name && /line_discount_amount$/.test(el.name) ? '0' : ''; }
    });
    clone.querySelectorAll('.op-field-error').forEach(function (el) { el.remove(); });
    clone.classList.remove('has-error');
    clone.querySelectorAll('details').forEach(function (el) { el.open = false; });
    var legend = clone.querySelector('legend, [data-line-no]');
    if (legend) { legend.textContent = String(n + 1); }
    linesBox.appendChild(clone);
    total.value = String(n + 1);
    return n;
  }

  function fillPrice(index, force) {
    var select = field(index, 'item');
    var price = field(index, priceField);
    var item = select && byId[select.value];
    if (item && price && item.price !== undefined && (force || !price.value)) {
      var unit = unitOf(index);
      price.value = unit ? unit.price : priceFor(item);
      price.setAttribute('data-auto', price.value);
    }
  }

  function cents(value) { return (value < 0 ? -1 : 1) * Math.round(Math.abs(value) * 100 + 1e-9) / 100; }
  function money(value) { return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function header(name) { return form.querySelector('[name="' + name + '"]'); }
  var sumTotal = form.querySelector('[data-sum-total]');
  var sumRemaining = form.querySelector('[data-sum-remaining]');

  function lineRow(index) {
    var select = field(index, 'item');
    return select ? select.closest('.op-line') : null;
  }

  function invoiceTotal(lines) {
    return Math.max(lines - num(header('discount_amount')) + num(header('tax_amount')), 0);
  }

  function recalc() {
    if (!liveTotal) { return; }
    var sum = 0;
    for (var i = 0; i < count(); i += 1) {
      var select = field(i, 'item');
      var row = lineRow(i);
      var cell = row && row.querySelector('[data-line-total]');
      if (!select || !select.value) { if (cell) { cell.textContent = '0.00'; } continue; }
      // Each line rounded to cents (half up) before summing, like the server.
      var net = cents(num(field(i, 'quantity')) * num(field(i, priceField)) - num(field(i, 'line_discount_amount')));
      var line = net;
      // TAX-001: VAT at the item's rate, rounded per line like the server.
      if (taxRates) { line += Math.round(net * num({ value: taxRates[select.value] || '0' }) + 1e-9) / 100; }
      sum += line;
      if (cell) { cell.textContent = money(line); }
    }
    liveTotal.textContent = money(sum);
    var totalValue = invoiceTotal(sum);
    if (sumTotal) { sumTotal.textContent = money(totalValue); }
    if (sumRemaining) { sumRemaining.textContent = money(Math.max(totalValue - num(header('paid_now')), 0)); }
    showBalance(totalValue);
  }

  // AN3: the party's balance before this invoice, and where the invoice leaves it.
  var balanceBox = form.querySelector('[data-party-balance]');
  var partySelect = form.querySelector('select[name="customer"], select[name="supplier"]');
  var balanceBefore = null;
  function showBalance(totalValue) {
    if (!balanceBox) { return; }
    if (balanceBefore === null) { balanceBox.hidden = true; return; }
    var paidNow = num(header('paid_now'));
    balanceBox.querySelector('[data-balance-before]').textContent = money(balanceBefore);
    balanceBox.querySelector('[data-balance-invoice]').textContent = money(totalValue);
    balanceBox.querySelector('[data-balance-paid]').textContent = money(paidNow);
    balanceBox.querySelector('[data-balance-after]').textContent = money(cents(balanceBefore + totalValue - paidNow));
    balanceBox.hidden = false;
  }
  function loadBalance() {
    balanceBefore = null;
    if (!balanceBox || !partySelect || !partySelect.value || !window.fetch) { recalc(); return; }
    var url = balanceBox.getAttribute('data-url').replace(/\/0\/balance\/$/, '/' + encodeURIComponent(partySelect.value) + '/balance/');
    fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) { balanceBefore = data ? parseFloat(data.balance) : null; recalc(); })
      .catch(function () { balanceBefore = null; recalc(); });
  }
  if (partySelect) { partySelect.addEventListener('change', loadBalance); }

  form.addEventListener('click', function (event) {
    var clear = event.target.closest('[data-clear-line]');
    if (clear) {
      var row = clear.closest('.op-line');
      row.querySelectorAll('input, select, textarea').forEach(function (el) {
        if (el.tagName === 'SELECT') { el.selectedIndex = 0; }
        else { el.value = /line_discount_amount$/.test(el.name || '') ? '0' : ''; }
      });
      // A date picker is cleared through its own API, so an earlier "not a date" complaint goes too.
      row.querySelectorAll('.hs-date').forEach(function (wrap) { if (wrap.hsPicker) { wrap.hsPicker.set(null); } });
      recalc();
      return;
    }
    if (event.target.closest('[data-pay-all]')) {
      var paid = header('paid_now');
      var lines = 0;
      recalc();
      lines = num({ value: (liveTotal.textContent || '0').replace(/,/g, '') });
      if (paid) { paid.value = invoiceTotal(lines).toFixed(2); paid.dispatchEvent(new Event('input', { bubbles: true })); }
    }
  });

  function say(text, isError) {
    if (!status) { return; }
    status.textContent = text;
    status.classList.toggle('is-error', !!isError);
  }

  function addItem(item, unit) {
    var target = -1;
    var unitId = unit ? String(unit.id) : '';
    for (var i = 0; i < count(); i += 1) {
      var select = field(i, 'item');
      var unitSelect = field(i, 'unit');
      if (select && select.value === String(item.id) && (!unitSelect || unitSelect.value === unitId)) {
        var qty = field(i, 'quantity');
        qty.value = String(num(qty) + 1);
        target = i;
        break;
      }
    }
    if (target < 0) {
      for (var j = 0; j < count(); j += 1) {
        var empty = field(j, 'item');
        if (empty && !empty.value) { target = j; break; }
      }
      if (target < 0) { target = addLine(); }
      if (target < 0) { say(words.full || 'No more lines.', true); return; }
      field(target, 'item').value = String(item.id);
      fillUnits(target);
      if (field(target, 'unit')) { field(target, 'unit').value = unitId; }
      field(target, 'quantity').value = '1';
      fillPrice(target, true);
    }
    say((words.added || 'Added: ') + item.label + ' × ' + field(target, 'quantity').value, false);
    recalc();
  }

  if (scan) {
    scan.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') { return; }
      event.preventDefault();
      var code = scan.value.trim().toLowerCase();
      scan.value = '';
      if (!code) { return; }
      var unitHit = unitByCode[code];
      var item = unitHit ? byId[unitHit.itemId] : byCode[code];
      if (item) { addItem(item, unitHit || null); } else { say((words.not_found || 'Not found: ') + code, true); }
      scan.focus();
    });
  }

  form.addEventListener('change', function (event) {
    if (event.target === customerSelect) {
      // Re-price only lines still on an automatic price; typed prices stay.
      for (var i = 0; i < count(); i += 1) {
        var price = field(i, priceField);
        if (price && (!price.value || price.value === price.getAttribute('data-auto'))) { fillPrice(i, true); }
      }
      showList();
    }
    var match = /^lines-(\d+)-item$/.exec(event.target.name || '');
    if (match) { fillUnits(parseInt(match[1], 10)); fillPrice(parseInt(match[1], 10), false); }
    var unitMatch = /^lines-(\d+)-unit$/.exec(event.target.name || '');
    if (unitMatch) { fillPrice(parseInt(unitMatch[1], 10), true); }
    recalc();
  });
  form.addEventListener('input', recalc);
  // FB-QUICKADD: a new item from inside the invoice. Saved through the items screen's own form (automatic code,
  // same checks), then offered on every line and put on the next empty one.
  var quick = form.querySelector('[data-quick-item]');
  var quickOpen = form.querySelector('[data-quick-item-open]');
  if (quick && quickOpen && quick.showModal) {
    var quickError = quick.querySelector('[data-quick-item-error]');
    var quickSave = quick.querySelector('[data-quick-item-save]');
    var csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');
    var isPurchase = priceField === 'unit_purchase_price';
    quickOpen.addEventListener('click', function () {
      quickError.hidden = true;
      quick.showModal();
      quick.querySelector('[name="item_name"]').focus();
    });
    quick.querySelector('[data-quick-item-close]').addEventListener('click', function () { quick.close(); });
    quick.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && event.target.tagName === 'INPUT') { event.preventDefault(); quickSave.click(); }
    });
    quickSave.addEventListener('click', function () {
      var body = new FormData();
      quick.querySelectorAll('input[name], select[name]').forEach(function (input) {
        if (input.type === 'checkbox') { if (input.checked) { body.append(input.name, 'on'); } } else { body.append(input.name, input.value); }
      });
      body.append('lang', document.body.dataset.lang === 'en' ? 'en' : 'ar');
      quickSave.disabled = true;
      fetch(quick.getAttribute('data-url'), { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf ? csrf.value : '' } })
        .then(function (response) { return response.json(); })
        .then(function (answer) {
          if (!answer.ok) {
            var detail = answer.errors ? Object.keys(answer.errors).map(function (k) { return k + ': ' + answer.errors[k].join(' '); }).join(' · ') : '';
            quickError.textContent = (answer.error || '') + (detail ? ' ' + detail : '');
            quickError.hidden = false;
            return;
          }
          var made = answer.item;
          var entry = { id: made.id, code: made.code, barcode: made.barcode, label: made.label, price: isPurchase ? (made.purchase_price || '') : made.sale_price };
          if (!entry.price) { delete entry.price; }
          catalog.push(entry);
          byId[String(entry.id)] = entry;
          if (entry.barcode) { byCode[entry.barcode.toLowerCase()] = entry; }
          if (entry.code) { byCode[entry.code.toLowerCase()] = entry; }
          form.querySelectorAll('select[name$="-item"]').forEach(function (select) {
            var option = document.createElement('option');
            option.value = String(entry.id);
            option.textContent = entry.label;
            select.appendChild(option);
          });
          quick.querySelectorAll('input[name]').forEach(function (input) {
            if (input.type === 'checkbox') { input.checked = true; } else { input.value = /price/.test(input.name) ? '0' : ''; }
          });
          quick.close();
          addItem(entry, null);
        })
        .catch(function () { quickError.textContent = words.offline || 'Could not save; try again.'; quickError.hidden = false; })
        .finally(function () { quickSave.disabled = false; });
    });
  }

  var addButton = form.querySelector('[data-add-line]');
  if (addButton) { addButton.addEventListener('click', function () { if (addLine() < 0) { say(words.full || 'No more lines.', true); } }); }
  for (var u = 0; u < count(); u += 1) { fillUnits(u); }
  showList();
  loadBalance();
})();
