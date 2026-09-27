/* POS-001: the cashier screen's cart. The server recalculates and posts. */
(function () {
  'use strict';
  var form = document.querySelector('[data-pos]');
  if (!form) { return; }
  var words = JSON.parse(form.getAttribute('data-words') || '{}');
  var catalog = JSON.parse(document.getElementById('hs-item-catalog').textContent);
  var restored = JSON.parse((document.getElementById('pos-cart-back') || { textContent: '[]' }).textContent || '[]');
  var byId = {}, byCode = {}, byLabel = {};
  catalog.forEach(function (item) {
    byId[String(item.id)] = item;
    byLabel[item.label] = item;
    if (item.barcode) { byCode[item.barcode.toLowerCase()] = item; }
    if (item.code && !byCode[item.code.toLowerCase()]) { byCode[item.code.toLowerCase()] = item; }
  });

  var cart = [];
  var body = form.querySelector('[data-pos-lines]');
  var scan = form.querySelector('[data-pos-scan]');
  var search = form.querySelector('[data-pos-search]');
  var status = form.querySelector('[data-pos-status]');
  var discount = form.querySelector('[data-pos-discount]');
  var tendered = form.querySelector('[data-pos-tendered]');
  var customer = form.querySelector('[data-pos-customer]');
  var tenderedTouched = false;
  // PRICE-001: price from the chosen customer's list, when the shop uses them.
  var bookNode = document.getElementById('hs-price-book');
  var book = bookNode ? JSON.parse(bookNode.textContent) : null;
  function priceFor(item) {
    var listId = book && book.customers[customer.value];
    var own = listId && book.lists[listId].prices[String(item.id)];
    return num(own !== undefined && own !== null && own !== '' ? own : item.price);
  }

  function num(value) { var n = parseFloat(String(value || '').replace(',', '.')); return isNaN(n) ? 0 : n; }
  function money(n) { return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function say(text, bad) { status.textContent = text; status.classList.toggle('is-error', !!bad); }
  function el(tag, attrs, text) { var node = document.createElement(tag); Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); }); if (text !== undefined) { node.textContent = text; } return node; }

  // UNITS-001: a carton's barcode adds a carton line at the carton price.
  var unitsNode = document.getElementById('hs-units');
  var units = unitsNode ? JSON.parse(unitsNode.textContent) : {};
  var unitByCode = {}, unitById = {};
  Object.keys(units || {}).forEach(function (itemId) {
    units[itemId].forEach(function (unit) {
      unit.itemId = itemId;
      unitById[String(unit.id)] = unit;
      if (unit.barcode) { unitByCode[unit.barcode.toLowerCase()] = unit; }
    });
  });
  var isEn = document.documentElement.lang === 'en';
  // SERIAL-001: scanning a serial / IMEI adds that exact unit; tracked items need one.
  var serialsNode = document.getElementById('hs-serials');
  var serialData = serialsNode ? JSON.parse(serialsNode.textContent) : { tracked: [], serials: {} };
  var tracked = {}, bySerial = {};
  serialData.tracked.forEach(function (id) { tracked[String(id)] = true; });
  Object.keys(serialData.serials).forEach(function (serial) { bySerial[serial.toLowerCase()] = { serial: serial, itemId: String(serialData.serials[serial]) }; });
  function addSerial(hit) {
    var item = byId[hit.itemId];
    if (!item) { return false; }
    if (cart.some(function (row) { return row.serial === hit.serial; })) { say((words.serial_twice || '') + hit.serial, true); return true; }
    cart.push({ id: item.id, unit: null, serial: hit.serial, label: item.label + ' — S/N ' + hit.serial, qty: 1, price: priceFor(item), auto: null });
    say((words.added || '') + item.label + ' — ' + hit.serial, false);
    render();
    return true;
  }
  function unitLabel(item, unit) { return unit ? item.label + ' — ' + (isEn ? unit.name_en : unit.name_ar) : item.label; }

  function add(item, qty, price, unit) {
    if (tracked[String(item.id)]) { say((words.scan_serial || '') + item.label, true); return; }
    var unitId = unit ? unit.id : null;
    var line = cart.filter(function (row) { return row.id === item.id && (row.unit || null) === unitId; })[0];
    if (line) { line.qty = num(line.qty) + (qty || 1); }
    else if (unit) { cart.push({ id: item.id, unit: unit.id, label: unitLabel(item, unit), qty: qty || 1, price: price !== undefined ? price : num(unit.price), auto: null }); }
    else { var auto = priceFor(item); cart.push({ id: item.id, unit: null, label: item.label, qty: qty || 1, price: price !== undefined ? price : auto, auto: price !== undefined ? null : auto }); }
    say((words.added || '') + unitLabel(item, unit), false);
    render();
  }

  function render() {
    body.textContent = '';
    if (!cart.length) {
      var empty = el('tr', { 'class': 'pos-empty' });
      empty.appendChild(el('td', { colspan: '5', 'class': 'op-empty' }, words.empty_cart || ''));
      body.appendChild(empty);
    }
    cart.forEach(function (row, index) {
      var tr = el('tr', { 'data-line': String(index) });
      tr.appendChild(el('td', { 'class': 'op-cell-text' }, row.label));
      var qtyTd = el('td');
      var qtyCell = el('div', { 'class': 'pos-qty' });
      qtyTd.appendChild(qtyCell);
      var minus = el('button', { type: 'button', 'class': 'pos-step', 'data-step': '-1', 'aria-label': '−' }, '−');
      var qty = el('input', { inputmode: 'decimal', 'data-field': 'qty', value: String(row.qty), 'aria-label': 'qty' });
      var plus = el('button', { type: 'button', 'class': 'pos-step', 'data-step': '1', 'aria-label': '+' }, '+');
      if (row.serial) { qty.readOnly = true; minus.disabled = true; plus.disabled = true; }
      qtyCell.appendChild(minus); qtyCell.appendChild(qty); qtyCell.appendChild(plus);
      tr.appendChild(qtyTd);
      var priceCell = el('td');
      priceCell.appendChild(el('input', { inputmode: 'decimal', 'data-field': 'price', value: String(row.price), 'aria-label': 'price' }));
      tr.appendChild(priceCell);
      tr.appendChild(el('td', { 'class': 'pos-line-total' }, money(num(row.qty) * num(row.price))));
      var removeCell = el('td');
      removeCell.appendChild(el('button', { type: 'button', 'class': 'pos-remove', 'data-remove': '1', 'aria-label': words.remove || 'x' }, '×'));
      tr.appendChild(removeCell);
      body.appendChild(tr);
    });
    totals();
  }

  // TAX-001: VAT per line at the item's rate, rounded per line like the server.
  var ratesNode = document.getElementById('hs-tax-rates');
  var rates = ratesNode ? JSON.parse(ratesNode.textContent) : null;
  function lineTax(row) {
    if (!rates) { return 0; }
    var net = Math.round(num(row.qty) * num(row.price) * 100) / 100;
    return Math.round(net * num(rates[String(row.id)]) + 1e-9) / 100;
  }

  function totals() {
    var subtotal = cart.reduce(function (sum, row) { return sum + num(row.qty) * num(row.price); }, 0);
    var tax = cart.reduce(function (sum, row) { return sum + lineTax(row); }, 0);
    var taxCell = form.querySelector('[data-pos-tax]');
    if (taxCell) { taxCell.textContent = money(tax); }
    var total = Math.max(0, subtotal - num(discount.value) + tax);
    if (!tenderedTouched) { tendered.value = total ? total.toFixed(2) : ''; }
    var paid = num(tendered.value);
    form.querySelector('[data-pos-subtotal]').textContent = money(subtotal);
    form.querySelector('[data-pos-total]').textContent = money(total);
    var change = paid - total, credit = total - paid;
    form.querySelector('[data-pos-change-row]').hidden = !(change > 0.004);
    form.querySelector('[data-pos-change]').textContent = money(Math.max(0, change));
    form.querySelector('[data-pos-credit-row]').hidden = !(credit > 0.004);
    form.querySelector('[data-pos-credit]').textContent = money(Math.max(0, credit));
    body.querySelectorAll('tr[data-line]').forEach(function (tr) {
      var row = cart[parseInt(tr.getAttribute('data-line'), 10)];
      tr.querySelector('.pos-line-total').textContent = money(num(row.qty) * num(row.price));
    });
  }

  body.addEventListener('click', function (event) {
    var tr = event.target.closest('tr[data-line]');
    if (!tr) { return; }
    var index = parseInt(tr.getAttribute('data-line'), 10);
    if (event.target.hasAttribute('data-remove')) { cart.splice(index, 1); render(); scan.focus(); return; }
    var step = event.target.getAttribute('data-step');
    if (step && !cart[index].serial) {
      cart[index].qty = Math.max(0, num(cart[index].qty) + parseInt(step, 10));
      if (!cart[index].qty) { cart.splice(index, 1); }
      render();
    }
  });
  body.addEventListener('input', function (event) {
    var tr = event.target.closest('tr[data-line]');
    var field = event.target.getAttribute('data-field');
    if (!tr || !field) { return; }
    cart[parseInt(tr.getAttribute('data-line'), 10)][field] = event.target.value;
    totals();
  });
  customer.addEventListener('change', function () {
    // Re-price lines still on an automatic price; typed prices stay.
    cart.forEach(function (row) {
      var item = byId[String(row.id)];
      if (item && row.auto !== null && row.auto !== undefined && num(row.price) === num(row.auto)) { row.price = priceFor(item); row.auto = row.price; }
    });
    render();
  });
  discount.addEventListener('input', totals);
  tendered.addEventListener('input', function () { tenderedTouched = tendered.value !== ''; totals(); });

  scan.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter') { return; }
    event.preventDefault();
    var code = scan.value.trim().toLowerCase();
    scan.value = '';
    if (!code) { return; }
    if (bySerial[code] && addSerial(bySerial[code])) { return; }
    var unitHit = unitByCode[code];
    var item = unitHit ? byId[unitHit.itemId] : byCode[code];
    if (item) { add(item, undefined, undefined, unitHit || null); } else { say((words.not_found || '') + code, true); }
  });
  search.addEventListener('change', function () {
    var item = byLabel[search.value];
    if (item) { add(item); search.value = ''; scan.focus(); }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'F9') { event.preventDefault(); var pay = form.querySelector('[data-pos-pay]'); if (pay) { pay.click(); } }
    if (event.key === 'F2') { event.preventDefault(); scan.focus(); }
  });

  form.querySelector('[data-pos-clear]').addEventListener('click', function () {
    cart = []; discount.value = '0'; tenderedTouched = false; customer.value = customer.getAttribute('data-walk-in'); render(); scan.focus();
  });

  form.addEventListener('submit', function (event) {
    var submitter = event.submitter;
    form.querySelector('[data-print-flag]').value = submitter && submitter.getAttribute('data-pos-pay') === 'print' ? '1' : '0';
    form.querySelectorAll('input[data-cart-field]').forEach(function (node) { node.remove(); });
    cart.forEach(function (row, index) {
      [['item_' + index, row.id], ['qty_' + index, row.qty], ['price_' + index, row.price], ['unit_' + index, row.unit || ''], ['serial_' + index, row.serial || '']].forEach(function (pair) {
        var input = el('input', { type: 'hidden', name: pair[0], value: String(pair[1]), 'data-cart-field': '1' });
        form.appendChild(input);
      });
    });
    form.querySelector('[data-line-count]').value = String(cart.length);
    form.querySelector('[data-cart-json]').value = JSON.stringify(cart.map(function (row) { return { id: row.id, qty: row.qty, price: row.price, unit: row.unit || null, serial: row.serial || null }; }));
  });

  restored.forEach(function (row) {
    var item = byId[String(row.id)];
    var unit = row.unit ? unitById[String(row.unit)] : null;
    if (item && row.serial) { cart.push({ id: item.id, unit: null, serial: row.serial, label: item.label + ' — S/N ' + row.serial, qty: 1, price: row.price }); }
    else if (item) { cart.push({ id: item.id, unit: unit ? unit.id : null, label: unitLabel(item, unit), qty: row.qty, price: row.price }); }
  });
  render();
  scan.focus();
})();
