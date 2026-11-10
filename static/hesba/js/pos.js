/* POS-001: the cashier screen's cart. The server recalculates and posts. */
(function () {
  'use strict';
  var form = document.querySelector('[data-pos]');
  if (!form) { return; }
  var words = JSON.parse(form.getAttribute('data-words') || '{}');
  // UX Wave 4: the line's button names and the pay sheet's note, in the page's language (sales/pos.html).
  var labels = JSON.parse((document.getElementById('hs-pos-labels') || { textContent: '{}' }).textContent || '{}');
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
  // T6: points spent come off the total; the customer's points are shown when a customer is chosen.
  var pointsBox = form.querySelector('[data-pos-points]');
  var pointsRow = form.querySelector('[data-pos-points-row]');
  var pointsSeen = form.querySelector('[data-pos-points-seen]');
  function pointsOff() {
    var off = pointsBox ? Math.round(num(pointsBox.value) * num(pointsBox.getAttribute('data-point-value')) * 100) / 100 : 0;
    if (pointsSeen) { pointsSeen.value = off ? off.toFixed(2) : ''; }  // Codex #274: what the payment was taken for
    return off;
  }
  function showPoints() {
    var hint = pointsRow && pointsRow.querySelector('[data-points-available]');
    if (!hint || !customer || !window.fetch) { return; }
    hint.hidden = true;
    if (!customer.value || customer.value === customer.getAttribute('data-walk-in')) { return; }
    var url = pointsRow.getAttribute('data-url').replace(/\/0\/balance\/$/, '/' + encodeURIComponent(customer.value) + '/balance/');
    fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) { if (data && data.points !== undefined) { hint.textContent = hint.getAttribute('data-word').replace('{n}', data.points); hint.hidden = false; } })
      .catch(function () {});
  }
  if (pointsBox) { pointsBox.addEventListener('input', function () { totals(); }); }
  if (customer && pointsRow) { customer.addEventListener('change', showPoints); }
  var tenderedTouched = false;
  // UX Wave 1: the pay bar (fixed at the bottom on phones and tablets) and the panels that stay open on wide screens.
  var paybar = form.querySelector('[data-pos-paybar]');
  var barTotal = form.querySelector('[data-pos-bar-total]');
  var wide = window.matchMedia ? window.matchMedia('(min-width: 1024px)') : null;
  function isWide() { return !!(wide && wide.matches); }
  // UX Wave 4: «تفاصيل» is always unfolded. On wide screens it stands in the payment column; on a phone it lives in the
  // «تفاصيل الفاتورة» sheet (opened from the cart's customer button or the «⋯» menu), so folding it there would only hide it.
  function syncWide() {
    form.querySelectorAll('[data-wide-open]').forEach(function (node) { node.open = true; });
    if (isWide() && layer === sheet) { closeLayer(true); }  // the sheet is a column on wide screens, never a layer
  }

  // UX Wave 4: two layers on a phone, the «تفاصيل الفاتورة» sheet and the «⋯» menu. One at a time, over a scrim; Esc, the
  // scrim or a close button shuts it, and the focus goes back to the scan field, as after any cart action (a scanner's
  // Enter must never land on a menu button). On wide screens the sheet is the payment column and only focuses its field.
  var sheet = form.querySelector('[data-pos-sheet]');
  var menu = form.querySelector('[data-pos-menu]');
  var scrim = form.querySelector('[data-pos-scrim]');
  var layer = null;
  function focusables(node) { return Array.prototype.filter.call(node.querySelectorAll('a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),select,textarea,summary'), function (el) { return el.offsetParent !== null; }); }
  function openLayer(node, target) {
    closeLayer(true);
    layer = node;
    if (node === menu) { menu.hidden = false; } else { node.classList.add('is-open'); }
    if (scrim) { scrim.hidden = false; }
    document.body.classList.add('pos-layer-open');
    var first = target && target.offsetParent !== null ? target : focusables(node)[0];
    if (first) { first.focus(); }
  }
  function closeLayer(quiet) {
    if (!layer) { return; }
    if (layer === menu) { menu.hidden = true; } else { layer.classList.remove('is-open'); }
    layer = null;
    if (scrim) { scrim.hidden = true; }
    document.body.classList.remove('pos-layer-open');
    if (!quiet && scan) { scan.focus(); }
  }
  function detailsTarget(which) {
    if (which === 'discount') {
      var amount = form.querySelector('[data-pos-discount]');
      return amount && amount.hidden ? form.querySelector('[data-discount-percent]') || amount : amount;  // the % box when % is chosen
    }
    if (which === 'place') { var place = form.querySelector('.pos-more'); if (place) { place.open = true; } return form.querySelector('#pos-location'); }
    return form.querySelector('[data-pos-customer-find]') || form.querySelector('[data-pos-customer]');
  }
  function openDetails(which) {
    form.querySelectorAll('[data-wide-open]').forEach(function (node) { node.open = true; });
    var target = detailsTarget(which);
    if (isWide()) { closeLayer(true); if (target) { target.scrollIntoView({ block: 'center' }); target.focus(); } }
    else { openLayer(sheet, target); }
    if (target && which === 'discount' && target.select) { target.select(); }  // typing replaces the 0
  }
  form.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-pos-menu-open]');
    if (opener) { if (layer === menu) { closeLayer(); } else { openLayer(menu); } return; }
    var details = event.target.closest('[data-pos-open-details]');
    if (details) { openDetails(details.getAttribute('data-pos-open-details')); return; }
    if (event.target.closest('[data-pos-layer-close]') || event.target === scrim) { closeLayer(); return; }
    // A choice in the menu (a link, «سلة جديدة», the drawer, the language) closes the menu first.
    if (layer === menu && event.target.closest('.pos-menu__item') && !event.target.closest('[type=submit]')) { closeLayer(true); }
  });
  document.addEventListener('keydown', function (event) {
    if (!layer) { return; }
    if (event.key === 'Escape') { event.preventDefault(); closeLayer(); return; }
    if (event.key === 'Tab') {  // the focus stays inside the open layer
      var items = focusables(layer);
      if (!items.length) { return; }
      var first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  // PRICE-001: price from the chosen customer's list, when the shop uses them.
  var bookNode = document.getElementById('hs-price-book');
  var book = bookNode ? JSON.parse(bookNode.textContent) : null;
  function priceFor(item) {
    var listId = book && book.customers[customer.value];
    var own = listId && book.lists[listId].prices[String(item.id)];
    return num(own !== undefined && own !== null && own !== '' ? own : item.price);
  }

  // POS-002: find the customer by phone or name; add a new one without leaving the till.
  var directoryNode = document.getElementById('hs-customers');
  var directory = directoryNode ? JSON.parse(directoryNode.textContent) : [];
  var finder = form.querySelector('[data-pos-customer-find]');
  var finderList = document.getElementById('pos-customer-list');
  var finderHint = form.querySelector('[data-pos-customer-hint]');
  var adder = form.querySelector('[data-pos-customer-add]');
  function digitsOf(text) { return String(text || '').replace(/[^0-9٠-٩]/g, '').replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 1632); }); }
  function pickCustomer(id, name) {
    if (!customer.querySelector('option[value="' + id + '"]')) { var option = document.createElement('option'); option.value = String(id); option.textContent = name; customer.appendChild(option); }
    customer.value = String(id);
    customer.dispatchEvent(new Event('change'));
    if (finderHint) { finderHint.hidden = false; finderHint.textContent = (isEnLang() ? 'Customer: ' : 'العميل: ') + name; }
    if (adder) { adder.hidden = true; }
  }
  function isEnLang() { return document.documentElement.lang === 'en'; }
  if (finder) {
    finder.addEventListener('input', function () {
      var query = finder.value.trim(), phone = digitsOf(query), lower = query.toLowerCase();
      finderList.textContent = '';
      if (query.length < 2) { if (adder) { adder.hidden = true; } if (finderHint) { finderHint.hidden = true; } return; }
      var hits = directory.filter(function (row) { return (phone.length >= 3 && row.phone.indexOf(phone) !== -1) || row.name.toLowerCase().indexOf(lower) !== -1; }).slice(0, 8);
      hits.forEach(function (row) { var option = document.createElement('option'); option.value = row.name + (row.phone ? ' — ' + row.phone : ''); finderList.appendChild(option); });
      var exact = hits.filter(function (row) { return (phone.length >= 7 && row.phone === phone) || finder.value === row.name + (row.phone ? ' — ' + row.phone : ''); })[0];
      if (exact) { pickCustomer(exact.id, exact.name); return; }
      if (finderHint) { finderHint.hidden = false; finderHint.textContent = hits.length ? '' : (isEnLang() ? 'No customer found.' : 'مفيش عميل بالبيانات دي.'); }
      if (adder) { adder.hidden = hits.length > 0; }
    });
  }
  if (adder) {
    adder.querySelector('[data-new-customer-save]').addEventListener('click', function (event) {
      var button = event.currentTarget, nameInput = adder.querySelector('[data-new-customer-name]');
      var query = finder.value.trim(), phone = digitsOf(query);
      var payload = { name: nameInput.value.trim() || (phone.length >= 7 ? '' : query), phone: phone.length >= 7 ? phone : '' };
      if (!payload.name) { nameInput.focus(); return; }
      button.disabled = true;
      fetch(button.getAttribute('data-url'), { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value }, body: JSON.stringify(payload), credentials: 'same-origin' })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          button.disabled = false;
          if (!data.ok) { finderHint.hidden = false; finderHint.textContent = isEnLang() ? 'Could not add the customer.' : 'مقدرناش نضيف العميل؛ راجع الاسم والتليفون.'; return; }
          directory.push({ id: data.id, name: data.name, phone: data.phone });
          nameInput.value = ''; finder.value = '';
          pickCustomer(data.id, data.name);
        })
        .catch(function () { button.disabled = false; });
    });
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

  // UX Wave 1: a cart line reads as the item's name; its code ("name · CODE") is kept for the tooltip.
  function shortLabel(row) {
    var item = byId[String(row.id)];
    return item && item.code ? row.label.replace(' · ' + item.code, '') : row.label;
  }

  function render() {
    requote();
    body.textContent = '';
    if (!cart.length) {
      var empty = el('tr', { 'class': 'pos-empty' });
      empty.appendChild(el('td', { colspan: '5', 'class': 'op-empty' }, words.empty_cart || ''));
      body.appendChild(empty);
    }
    cart.forEach(function (row, index) {
      var tr = el('tr', { 'data-line': String(index), 'data-item': String(row.id), 'data-unit': row.unit ? String(row.unit) : '' });  // R1: the line's item, for the «بدائل» sheet
      // UX Wave 4: one compact row per line: the name (two lines at most), the unit price small under it (still a box
      // that takes a typed price), a − qty + stepper, the line total and ×. The buttons hold text only: the clicks below
      // read event.target, so an icon inside a button would swallow the tap.
      var name = shortLabel(row);
      var nameCell = el('td', { 'class': 'pos-line__name op-cell-text', title: row.label });
      nameCell.appendChild(el('span', { 'class': 'pos-line__label' }, name));
      tr.appendChild(nameCell);
      var qtyTd = el('td', { 'class': 'pos-line__qty' });
      var qtyCell = el('div', { 'class': 'pos-qty' });
      qtyTd.appendChild(qtyCell);
      var minus = el('button', { type: 'button', 'class': 'pos-step', 'data-step': '-1', 'aria-label': (labels.dec || '−') + ': ' + name }, '−');
      var qty = el('input', { inputmode: 'decimal', autocomplete: 'off', 'data-field': 'qty', value: String(row.qty), 'aria-label': (labels.qty || 'qty') + ': ' + name });
      var plus = el('button', { type: 'button', 'class': 'pos-step', 'data-step': '1', 'aria-label': (labels.inc || '+') + ': ' + name }, '+');
      if (row.serial) { qty.readOnly = true; minus.disabled = true; plus.disabled = true; }
      qtyCell.appendChild(minus); qtyCell.appendChild(qty); qtyCell.appendChild(plus);
      tr.appendChild(qtyTd);
      var priceCell = el('td', { 'class': 'pos-line__price' });
      var priceLabel = el('label', { 'class': 'pos-line__each' });
      priceLabel.appendChild(el('input', { inputmode: 'decimal', autocomplete: 'off', 'data-field': 'price', value: String(row.price), 'aria-label': (labels.price || 'price') + ': ' + name }));
      priceLabel.appendChild(el('span', { 'aria-hidden': 'true' }, labels.each || ''));
      priceCell.appendChild(priceLabel);
      tr.appendChild(priceCell);
      tr.appendChild(el('td', { 'class': 'pos-line-total' }, money(num(row.qty) * num(row.price))));
      var removeCell = el('td', { 'class': 'pos-line__rm' });
      removeCell.appendChild(el('button', { type: 'button', 'class': 'pos-remove', 'data-remove': '1', 'aria-label': (words.remove || 'x') + ': ' + name, title: words.remove || '' }, '×'));
      tr.appendChild(removeCell);
      body.appendChild(tr);
    });
    totals();
    form.dispatchEvent(new CustomEvent('pos:rendered'));  // R1: pos_alternatives.js marks the drug lines again
  }

  // TAX-001: VAT per line at the item's rate, rounded per line like the server.
  var ratesNode = document.getElementById('hs-tax-rates');
  var rates = ratesNode ? JSON.parse(ratesNode.textContent) : null;
  function lineTax(row, index) {
    if (!rates) { return 0; }
    var net = Math.round(num(row.qty) * num(row.price) * 100) / 100 - num(promo.lines[index]);
    return Math.round(net * num(rates[String(row.id)]) + 1e-9) / 100;
  }

  // T5 (Codex #268): the promotion the server would give this cart, shown before the payment is taken.
  var promoRow = form.querySelector('[data-pos-promo-row]');
  var promoSeen = form.querySelector('[data-pos-promo-seen]');
  var coupon = form.querySelector('[data-pos-coupon]');
  // Codex #268: while a quote is pending the total on screen may be stale, so payment waits for it (promoPending).
  var promo = { off: 0, lines: [], key: '' }, promoTimer = null, promoPending = false, promoSeq = 0;
  function noPromo() { promo = { off: 0, lines: [], key: '' }; promoSeen.value = ''; promoRow.hidden = true; totals(); }
  function quote() {
    if (!promoRow || !window.fetch) { promoPending = false; return; }
    var body = new FormData();
    body.append('lang', document.body.dataset.lang === 'en' ? 'en' : 'ar');
    body.append('line_count', String(cart.length));
    body.append('coupon', coupon ? coupon.value : '');
    body.append('customer', customer ? customer.value : '');  // S6 (HG-071): a members-only promotion depends on the customer
    cart.forEach(function (row, index) {
      body.append('item_' + index, row.id); body.append('qty_' + index, row.qty); body.append('price_' + index, row.price); body.append('unit_' + index, row.unit || '');
    });
    var key = JSON.stringify([coupon ? coupon.value : '', customer ? customer.value : '', cart.map(function (row) { return [row.id, row.qty, row.price, row.unit || '']; })]);
    if (key === promo.key) { promoPending = false; return; }
    var seq = promoSeq;
    var token = form.querySelector('input[name="csrfmiddlewaretoken"]');
    // Codex #268: a stalled connection gives up after 4 seconds, so the till falls back to no promotion (and offline saving).
    var controller = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (controller) { controller.abort(); } else if (seq === promoSeq) { promoPending = false; noPromo(); } }, 4000);
    fetch(promoRow.getAttribute('data-url'), { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-CSRFToken': token ? token.value : '' },
                                               signal: controller ? controller.signal : undefined })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (answer) {
        clearTimeout(timer);
        if (seq !== promoSeq) { return; }  // the cart changed again; a newer quote is on its way
        promoPending = false;
        if (!answer || !answer.ok) { noPromo(); return; }
        promo = { off: num(answer.discount), lines: answer.lines || [], key: key };
        promoSeen.value = answer.discount;
        promoRow.hidden = !promo.off;
        promoRow.querySelector('[data-pos-promo]').textContent = '-' + money(promo.off);
        promoRow.querySelector('[data-pos-promo-name]').textContent = answer.name || '';
        totals();
      })
      .catch(function () { clearTimeout(timer); if (seq === promoSeq) { promoPending = false; noPromo(); } });  // offline or timed out: no promotion shown, none applied on sync
  }
  function requote() { if (promoRow) { promoSeq += 1; promoPending = true; promo.key = ''; promoSeen.value = ''; clearTimeout(promoTimer); promoTimer = setTimeout(quote, 250); } }
  if (coupon) { coupon.addEventListener('input', requote); }

  function totals() {
    var subtotal = cart.reduce(function (sum, row) { return sum + num(row.qty) * num(row.price); }, 0);
    var tax = cart.reduce(function (sum, row, index) { return sum + lineTax(row, index); }, 0);
    var taxCell = form.querySelector('[data-pos-tax]');
    if (taxCell) { taxCell.textContent = money(tax); }
    var total = Math.max(0, subtotal - num(discount.value) - promo.off - pointsOff() + tax);
    var cashDue = Math.max(0, total - (giftAmount ? num(giftAmount.value) : 0));  // S6 (HG-071): the card pays its part
    if (!tenderedTouched) { tendered.value = cashDue ? cashDue.toFixed(2) : ''; }
    var paid = num(tendered.value);
    form.querySelector('[data-pos-subtotal]').textContent = money(subtotal);
    form.querySelector('[data-pos-total]').textContent = money(total);
    if (barTotal) { barTotal.textContent = money(total); }  // UX Wave 1: the pay bar repeats what is due
    if (paybar) { paybar.classList.toggle('is-empty', !cart.length); }
    // UX Wave 4: the cart's count, and under the amount due one quiet line of what changed it (the full sums are in
    // «تفاصيل الفاتورة»). Display only: the numbers are the ones worked out above.
    form.classList.toggle('has-lines', cart.length > 0);
    var count = form.querySelector('[data-pos-count]');
    if (count) { count.textContent = String(cart.length); }
    var note = form.querySelector('[data-pos-bar-note]');
    if (note) {
      var parts = [];
      if (num(discount.value)) { parts.push((labels.discount || '') + ' ' + money(num(discount.value))); }
      if (promo.off) { parts.push((labels.promo || '') + ' ' + money(promo.off)); }
      var pointsMoney = pointsBox ? Math.round(num(pointsBox.value) * num(pointsBox.getAttribute('data-point-value')) * 100) / 100 : 0;
      if (pointsMoney) { parts.push((labels.points || '') + ' ' + money(pointsMoney)); }
      if (tax) { parts.push((labels.tax || '') + ' ' + money(tax)); }
      note.textContent = parts.join(' · ');
      note.hidden = !parts.length;
    }
    var change = paid - cashDue, credit = cashDue - paid;
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
    requote();  // Codex #268: a typed quantity or price changes the promotion too
    totals();
  });
  function showCustomer() {  // UX Wave 4: the chosen customer's name on the cart's button and the pay sheet
    var option = customer.options[customer.selectedIndex];
    var text = option ? option.textContent.trim() : '';
    form.querySelectorAll('[data-pos-customer-name],[data-pos-bar-customer]').forEach(function (node) { node.textContent = text; });
  }
  customer.addEventListener('change', showCustomer);
  showCustomer();
  customer.addEventListener('change', function () {
    // Re-price lines still on an automatic price; typed prices stay.
    cart.forEach(function (row) {
      var item = byId[String(row.id)];
      if (item && row.auto !== null && row.auto !== undefined && num(row.price) === num(row.auto)) { row.price = priceFor(item); row.auto = row.price; }
    });
    render();
  });
  discount.addEventListener('input', totals);
  var giftAmount = form.querySelector('[data-pos-gift-amount]');
  if (giftAmount) { giftAmount.addEventListener('input', totals); }
  tendered.addEventListener('input', function () { tenderedTouched = tendered.value !== ''; totals(); });

  // UX Wave 4: one field for the scanner and for names. A scanner (USB, Bluetooth or the camera) types a code and Enter;
  // a person types a few letters of a name and the till lists the items under the field. What Enter does:
  //   1. the text is exactly a serial, a unit's barcode, a barcode or an item code: that item, as before, always first;
  //   2. otherwise, a row of the list, only when the list was drawn for exactly this text and the text was typed by hand
  //      (keys at a person's pace, or a row chosen with ↑/↓), never on a synthetic Enter (the camera's);
  //   3. otherwise «مفيش صنف بالكود ده», as before: a mis-scan never adds a look-alike.
  // A row taken goes through the name-search path (data-pos-search), the one the quick buttons and «بدائل» use.
  var results = form.querySelector('[data-pos-results]');
  var found = [], active = -1, shownFor = null, navigated = false, findTimer = null, stamps = [], pasted = false;
  var BURST_MS = 35;     // a scanner sends a key every few milliseconds; a person needs far more than this between keys
  var FIND_DELAY = 150;  // the list waits for a pause, so a scanner's burst never draws it
  var MIN_CHARS = 2;
  function fold(text) {  // Arabic letters that people type either way, Arabic-Indic digits, case
    return String(text || '').toLowerCase().replace(/[\u064B-\u0652\u0670\u0640]/g, '').replace(/[أإآٱ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
      .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 1632); });
  }
  var folded = catalog.map(function (item) { return { item: item, text: fold(item.label + ' ' + (item.barcode || '')), name: fold(item.label) }; });
  function matches(query) {
    var parts = fold(query).trim().split(/\s+/).filter(Boolean);
    if (!parts.length) { return []; }
    var hits = folded.filter(function (row) { return parts.every(function (part) { return row.text.indexOf(part) !== -1; }); });
    function rank(row) { return row.name.indexOf(parts[0]) === 0 ? 0 : (row.name.indexOf(' ' + parts[0]) !== -1 ? 1 : 2); }
    hits.sort(function (a, b) { return rank(a) - rank(b) || a.name.localeCompare(b.name); });
    return hits.slice(0, 8).map(function (row) { return row.item; });
  }
  function byHand() {
    if (navigated) { return true; }
    if (pasted || stamps.length < 2) { return false; }
    return (stamps[stamps.length - 1] - stamps[0]) / (stamps.length - 1) >= BURST_MS;
  }
  function closeList() {
    clearTimeout(findTimer);
    if (!results) { return; }
    results.hidden = true; results.textContent = '';
    found = []; active = -1; shownFor = null; navigated = false;
    scan.setAttribute('aria-expanded', 'false'); scan.removeAttribute('aria-activedescendant');
  }
  function highlight(index) {
    active = index;
    Array.prototype.forEach.call(results.children, function (li, i) { li.setAttribute('aria-selected', i === index ? 'true' : 'false'); li.classList.toggle('is-active', i === index); });
    var li = results.children[index];
    if (li) { scan.setAttribute('aria-activedescendant', li.id); li.scrollIntoView({ block: 'nearest' }); } else { scan.removeAttribute('aria-activedescendant'); }
  }
  function showList() {
    if (!results) { return; }
    var query = scan.value;
    if (query.trim().length < MIN_CHARS) { closeList(); return; }
    found = matches(query);
    results.textContent = '';
    if (!found.length) { closeList(); return; }
    found.forEach(function (item, index) {
      var li = el('li', { id: 'pos-find-' + index, role: 'option', 'aria-selected': 'false', 'data-index': String(index), 'class': 'pos-find__row' });
      var main = el('span', { 'class': 'pos-find__main' });
      main.appendChild(el('span', { 'class': 'pos-find__name' }, item.code ? item.label.replace(' · ' + item.code, '') : item.label));
      if (item.code) { main.appendChild(el('bdi', { 'class': 'pos-find__code', dir: 'ltr' }, item.code)); }
      li.appendChild(main);
      li.appendChild(el('bdi', { 'class': 'pos-find__price' }, money(priceFor(item))));
      results.appendChild(li);
    });
    results.hidden = false;
    shownFor = query;
    scan.setAttribute('aria-expanded', 'true');
    highlight(0);  // shown, never taken by itself: Enter takes it only under rule 2 above
  }
  function take(item) {
    scan.value = ''; stamps = []; pasted = false;
    closeList();
    search.value = item.label;
    search.dispatchEvent(new Event('change', { bubbles: true }));
  }
  scan.addEventListener('input', function (event) {
    if (!scan.value) { stamps = []; pasted = false; closeList(); return; }
    if (event.inputType && /^insertFromPaste|^insertReplacementText|^insertFromDrop/.test(event.inputType) || (event.data && event.data.length > 1)) { pasted = true; }
    stamps.push(Date.now());
    if (stamps.length === 1 && scan.value.length > 1) { pasted = true; }  // the whole text arrived at once
    navigated = false;
    clearTimeout(findTimer);
    if (scan.value.trim().length < MIN_CHARS) { closeList(); return; }
    findTimer = setTimeout(showList, FIND_DELAY);
  });
  scan.addEventListener('blur', function () { setTimeout(function () { if (document.activeElement !== scan) { closeList(); } }, 0); });
  scan.addEventListener('focus', function () { if (scan.value.trim().length >= MIN_CHARS && byHand()) { showList(); } });
  if (results) {
    results.addEventListener('mousedown', function (event) { event.preventDefault(); });  // the field keeps the focus
    results.addEventListener('mousemove', function (event) { var li = event.target.closest('[data-index]'); if (li && +li.getAttribute('data-index') !== active) { highlight(+li.getAttribute('data-index')); } });
    results.addEventListener('click', function (event) {
      var li = event.target.closest('[data-index]');
      var item = li && found[+li.getAttribute('data-index')];
      if (item) { take(item); scan.focus(); }
    });
  }
  scan.addEventListener('keydown', function (event) {
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && results) {
      if (results.hidden) { if (scan.value.trim().length >= MIN_CHARS) { event.preventDefault(); showList(); navigated = true; } return; }
      event.preventDefault();
      navigated = true;
      highlight((active + (event.key === 'ArrowDown' ? 1 : found.length - 1)) % found.length);
      return;
    }
    if (event.key === 'Escape' && results && !results.hidden) { event.preventDefault(); event.stopPropagation(); closeList(); return; }
    if (event.key !== 'Enter') { return; }
    event.preventDefault();
    var pick = event.isTrusted && results && !results.hidden && shownFor === scan.value && found[active] && byHand() ? found[active] : null;
    var code = scan.value.trim().toLowerCase();
    scan.value = ''; stamps = []; pasted = false;
    closeList();
    if (!code) { return; }
    if (bySerial[code] && addSerial(bySerial[code])) { return; }
    var unitHit = unitByCode[code];
    var item = unitHit ? byId[unitHit.itemId] : byCode[code];
    if (item) { add(item, undefined, undefined, unitHit || null); }
    else if (pick) { search.value = pick.label; search.dispatchEvent(new Event('change', { bubbles: true })); }
    else { say((words.not_found || '') + code, true); }
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
    cart = []; discount.value = '0'; if (pointsBox) { pointsBox.value = ''; } tenderedTouched = false;
    var giftCode = form.querySelector('[data-pos-gift-code]');  // S6: never carry one holder's card into the next sale
    if (giftCode) { giftCode.value = ''; } if (giftAmount) { giftAmount.value = ''; }
    if (coupon) { coupon.value = ''; }  // nor the last sale's coupon: render() re-quotes the promotion
    customer.value = customer.getAttribute('data-walk-in'); render(); scan.focus();
  });

  form.addEventListener('submit', function (event) {
    if (promoPending && cart.length) {  // the total is not confirmed yet: no payment, online or saved offline
      event.preventDefault(); event.stopImmediatePropagation();
      clearTimeout(promoTimer); quote(); say(words.promo_wait || '', true);
      return;
    }
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

  syncWide();
  if (wide && wide.addEventListener) { wide.addEventListener('change', syncWide); }
  // UX Wave 4: the page leaves room under the cart for the pay sheet fixed at the bottom of a phone, whatever its height.
  if (paybar && window.ResizeObserver) {
    new ResizeObserver(function () {
      var fixed = getComputedStyle(paybar).position === 'fixed' && !paybar.classList.contains('is-empty');
      document.documentElement.style.setProperty('--pos-paybar-h', fixed ? paybar.offsetHeight + 'px' : '0px');
    }).observe(paybar);
  }

  restored.forEach(function (row) {
    var item = byId[String(row.id)];
    var unit = row.unit ? unitById[String(row.unit)] : null;
    if (item && row.serial) { cart.push({ id: item.id, unit: null, serial: row.serial, label: item.label + ' — S/N ' + row.serial, qty: 1, price: row.price }); }
    else if (item) { cart.push({ id: item.id, unit: unit ? unit.id : null, label: unitLabel(item, unit), qty: row.qty, price: row.price }); }
  });
  render();
  scan.focus();
  // camera_scan.js (deferred, after this file) moves the field into its wrapper, and a moved field loses the focus:
  // once every deferred script has run, the field takes it back, so the next customer's first scan lands in it.
  document.addEventListener('DOMContentLoaded', function () {
    if (!layer && (!document.activeElement || document.activeElement === document.body)) { scan.focus(); }
  });
})();
