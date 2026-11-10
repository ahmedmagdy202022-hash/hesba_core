/* R1: alternatives at the till (a pharmacy with the drug database on).
   A drug line in the cart that is short at the till's location says so and offers «بدائل»; any drug line with
   alternatives offers it too. The sheet lists the items with the same active ingredient on the shelf here, and one
   line naming the reference-list drugs the pharmacy does not have. Tapping an alternative adds it through the till's
   own name search, exactly as a quick tile does, so pricing, units, serials and every check of the sale stay pos.js's
   and the server's. Nothing is removed by itself: the short line then offers «شيله؟», the till's own remove button.
   A USB scanner types a code then Enter into the scan field, so the focus is never left on a row or a line's button:
   the sheet opens on its heading, and an add or a close puts the focus back on the scan field, as pos.js does. */
(function () {
  'use strict';
  var form = document.querySelector('[data-pos]');
  var sheet = document.querySelector('[data-pos-alt]');
  if (!form || !sheet || !window.fetch) { return; }
  function json(id, fallback) {
    var node = document.getElementById(id);
    try { return node ? JSON.parse(node.textContent || 'null') : fallback; } catch (error) { return fallback; }
  }
  var words = JSON.parse(sheet.getAttribute('data-words') || '{}');
  var url = sheet.getAttribute('data-url');
  var catalog = json('hs-item-catalog', []) || [];
  var byId = {};
  catalog.forEach(function (item) { byId[String(item.id)] = item; });
  // Only the items with drug data are asked about (sales/pos.py sends their ids); without the list, every line is.
  var drugs = null;
  (function () {
    var ids = json('hs-alt-drugs', null);
    if (ids) { drugs = {}; ids.forEach(function (id) { drugs[String(id)] = true; }); }
  })();
  // The price lists (PRICE-001): the sheet's prices change with the customer's list only, so the list, not the
  // customer, is part of what an answer is kept under; with no lists on the page, the customer changes nothing.
  var book = json('hs-price-book', null);
  var body = form.querySelector('[data-pos-lines]');
  var search = form.querySelector('[data-pos-search]');
  var scan = form.querySelector('[data-pos-scan]');
  var status = form.querySelector('[data-pos-status]');
  var place = form.querySelector('select[name="location"]');
  var customer = form.querySelector('[data-pos-customer]');
  var unitBox = form.querySelector('[data-pos-discount]');
  var unit = unitBox ? unitBox.getAttribute('data-unit') || '' : '';
  var title = sheet.querySelector('[data-pos-alt-title]');
  var content = sheet.querySelector('[data-pos-alt-body]');
  var foot = sheet.querySelector('[data-pos-alt-foot]');
  var lang = document.documentElement.lang === 'en' ? 'en' : 'ar';
  if (!title.hasAttribute('tabindex')) { title.setAttribute('tabindex', '-1'); }
  var answers = {};  // item|location|price list -> the answer (false: the server refused it)
  var latest = {};   // item|location -> the last answer for any price list: the shelf and the alternatives do not depend on prices
  var failed = {};   // item|location|price list -> when asking last failed (offline, a stall, a login page): asked again after a pause, or once back online
  var asking = {};
  var queue = [];    // item ids waiting to be asked, at most MAX_IN_FLIGHT at a time, a tapped line first
  var inFlight = 0;
  var MAX_IN_FLIGHT = 2;
  var RETRY_MS = 15000;
  var TIMEOUT_MS = 5000;
  var WAIT_MS = 3000;  // a tapped «بدائل» opens when its answer comes within this, never later
  var refused = false;  // a 403: this page asks nothing more
  var pending = null;   // a tapped «بدائل» waiting for its answer
  // UNITS-001: a line sold in a bigger unit (a box of 12) takes factor x quantity from the shelf; the units catalog
  // the till already carries (#hs-units) gives each unit's factor.
  var factorOf = {};
  (function () {
    var units = json('hs-units', {}) || {};
    Object.keys(units).forEach(function (itemId) { units[itemId].forEach(function (u) { factorOf[String(u.id)] = num(u.factor) || 1; }); });
  })();
  var offered = {};  // item id -> an alternative was added for its short line, which now offers «شيله؟»
  var current = null;  // the line the sheet is open for

  // Each value is bidi-isolated (FSI … PDI), so an Arabic drug name reads right inside an English sentence and the other way round.
  function isolate(text) { return '⁨' + text + '⁩'; }
  function fill(text, values) { return String(text || '').replace(/\{(\w+)\}/g, function (all, name) { return values[name] !== undefined ? isolate(values[name]) : all; }); }
  function num(value) { var n = parseFloat(String(value || '').replace(',', '.')); return isNaN(n) ? 0 : n; }
  function money(value) { return num(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function el(tag, cls, text) { var node = document.createElement(tag); if (cls) { node.className = cls; } if (text !== undefined) { node.textContent = text; } return node; }
  function say(text) { if (status) { status.textContent = text; status.classList.add('is-error'); } }
  function focusScan() { if (scan) { scan.focus(); } }
  function priceList() { return book && book.customers && customer ? book.customers[customer.value] || '' : ''; }
  function placeKey(id) { return id + '|' + (place ? place.value : ''); }
  function keyFor(id) { return placeKey(id) + '|' + priceList(); }
  function isDrug(id) { return !drugs || !!drugs[id]; }

  function due(id) {
    var key = keyFor(id);
    if (refused || key in answers || asking[key]) { return false; }
    return !(failed[key] && Date.now() - failed[key] < RETRY_MS);
  }

  function want(id, first) {
    if (!due(id)) { return; }
    var at = queue.indexOf(id);
    if (at !== -1) { if (!first) { return; } queue.splice(at, 1); }
    if (first) { queue.unshift(id); } else { queue.push(id); }
    pump();
  }

  function pump() {
    while (inFlight < MAX_IN_FLIGHT && queue.length) {
      var id = queue.shift();
      if (due(id)) { send(id); }
    }
  }

  function send(id) {
    var key = keyFor(id);
    var where = placeKey(id);
    var query = '?item=' + encodeURIComponent(id) + '&location=' + encodeURIComponent(place ? place.value : '') +
                '&customer=' + encodeURIComponent(customer ? customer.value : '') + '&lang=' + lang;
    var controller = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (controller) { controller.abort(); } }, TIMEOUT_MS);
    asking[key] = true;
    inFlight += 1;
    fetch(url + query, { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, signal: controller ? controller.signal : undefined })
      .then(function (response) {
        if (response.status === 403) { refused = true; }
        return response.ok ? response.json() : null;  // a login page (HTML) makes .json() reject: a failure, asked again later
      })
      .then(function (answer) {
        // A refusal the server gave (no permission, no such item) is final for this page; a failed request is not.
        if (answer && answer.ok) { answers[key] = answer; latest[where] = answer; delete failed[key]; } else { answers[key] = false; }
      })
      .catch(function () { failed[key] = Date.now(); })
      .then(function () {
        clearTimeout(timer);
        delete asking[key];
        inFlight -= 1;
        pump();
        if (pending && pending.key === key) {
          var tap = pending;
          pending = null;
          if (Date.now() - tap.at <= WAIT_MS) { if (answers[key]) { show(tap.id, answers[key], tap.short); } else { say(words.error); } }
        }
        mark();
      });
  }

  function shortText(tr, answer) {
    var onHand = num(answer.item.on_hand);
    if (onHand <= 0) { return words.out; }
    var qty = tr.querySelector('[data-field="qty"]');
    var unitId = tr.getAttribute('data-unit');
    var factor = unitId ? factorOf[unitId] : 1;
    if (!factor) { return ''; }  // a unit this page does not know: say nothing rather than guess
    if (qty && num(qty.value) * factor > onHand) { return fill(words.only, { n: answer.item.on_hand }); }
    return '';
  }

  // Marks every drug line: short at this location, and «بدائل» (or «شيله؟» once an alternative was added for it).
  function mark() {
    var present = {};
    body.querySelectorAll('tr[data-item]').forEach(function (tr) {
      var id = tr.getAttribute('data-item');
      present[id] = true;
      var old = tr.querySelector('.pos-alt-line');
      if (old) { old.remove(); }
      tr.classList.remove('is-short');
      if (!isDrug(id)) { return; }
      var key = keyFor(id);
      var answer = key in answers ? answers[key] : latest[placeKey(id)];
      if (answer === undefined) { want(id); return; }
      if (!answer || !answer.item.drug) { return; }
      var short = shortText(tr, answer);
      if (short && !(key in answers)) { want(id); }  // a short line is the one opened next: its prices for this customer, ahead of time
      if (!short && !answer.stocked.length && !answer.unstocked.count) { return; }
      var line = el('div', 'pos-alt-line');
      if (short) { tr.classList.add('is-short'); line.appendChild(el('span', 'pos-alt-flag', short)); }
      var button;
      if (short && offered[id]) {
        // The till's own remove button (data-remove, read from the tap's target): text only, so a tap always lands on it.
        button = el('button', 'pos-alt-remove', words.remove);
        button.setAttribute('data-remove', '1');
        button.setAttribute('aria-label', fill(words.remove_label, { name: answer.item.name }));
      } else {
        button = el('button', 'pos-alt-open', words.open);
        button.setAttribute('data-pos-alt-open', id);
        button.setAttribute('aria-haspopup', 'dialog');
      }
      button.type = 'button';
      line.appendChild(button);
      tr.querySelector('td').appendChild(line);
    });
    Object.keys(offered).forEach(function (id) { if (!present[id]) { delete offered[id]; } });
  }

  // Back online: lines whose question failed are asked again at once.
  window.addEventListener('online', function () { failed = {}; mark(); });

  function row(alternative) {
    var button = el('button', 'pos-alt__row');
    button.type = 'button';
    button.setAttribute('data-pos-alt-add', alternative.id);
    button.disabled = !byId[String(alternative.id)];
    var main = el('span', 'pos-alt__main');
    main.appendChild(el('span', 'pos-alt__name', alternative.name));
    var meta = [alternative.strength, alternative.form_label].filter(Boolean).join(' · ');
    if (meta) { main.appendChild(el('bdi', 'pos-alt__meta', meta)); }
    var chips = el('span', 'pos-alt__chips');
    chips.appendChild(el('span', 'pos-alt-chip ' + (alternative.same_strength ? 'pos-alt-chip--same' : 'pos-alt-chip--other'), alternative.same_strength ? words.same : words.other));
    if (alternative.prescription_only) { chips.appendChild(el('span', 'pos-alt-chip pos-alt-chip--rx', words.rx)); }
    main.appendChild(chips);
    var side = el('span', 'pos-alt__side');
    side.appendChild(el('bdi', 'pos-alt__price', money(alternative.price) + (unit ? ' ' + unit : '')));
    side.appendChild(el('span', 'pos-alt-chip ' + (alternative.low ? 'pos-alt-chip--low' : 'pos-alt-chip--ok'), fill(alternative.low ? words.low : words.stock, { n: alternative.on_hand })));
    var plus = el('span', 'pos-alt__plus', '+');
    plus.setAttribute('aria-hidden', 'true');
    button.appendChild(main);
    button.appendChild(side);
    button.appendChild(plus);
    return button;
  }

  // A tap on «بدائل»: the answer for this customer's prices, asked first when it is not here yet.
  function open(id, opener) {
    var key = keyFor(id);
    var short = !!opener.closest('tr.is-short');
    if (answers[key]) { show(id, answers[key], short); return; }
    if (answers[key] === false) { say(words.error); return; }
    delete failed[key];  // a tap asks again at once
    pending = { key: key, id: id, short: short, at: Date.now() };
    want(id, true);
    if (!asking[key] && queue.indexOf(id) === -1) { pending = null; say(words.error); }
  }

  function show(id, answer, short) {
    current = { id: id, name: answer.item.name, short: short };
    title.textContent = fill(words.title, { name: answer.item.name });
    content.textContent = '';
    if (answer.item.ingredient) { content.appendChild(el('p', 'pos-alt__intro', fill(words.intro, { ingredient: answer.item.ingredient }))); }
    var group = el('section', 'pos-alt__group');
    var head = el('h3', 'pos-alt__group-h', words.have + ' ');
    head.appendChild(el('span', 'pos-alt__count', String(answer.stocked.length)));
    group.appendChild(head);
    if (answer.stocked.length) {
      var list = el('div', 'pos-alt__list');
      answer.stocked.forEach(function (alternative) { list.appendChild(row(alternative)); });
      group.appendChild(list);
    } else {
      group.appendChild(el('p', 'pos-alt__empty', words.empty));
    }
    content.appendChild(group);
    if (answer.unstocked.count) {
      var fold = el('div', 'pos-alt__fold');
      fold.appendChild(el('b', '', fill(words.missing, { n: answer.unstocked.count })));
      var more = answer.unstocked.count > answer.unstocked.names.length ? words.more : '';
      fold.appendChild(el('span', '', answer.unstocked.names.map(isolate).join(words.sep) + more + '. ' + words.order));
      content.appendChild(fold);
    }
    foot.textContent = words.foot;
    if (sheet.showModal) { sheet.showModal(); } else { sheet.setAttribute('open', ''); }
    content.scrollTop = 0;
    // On the heading, not a row: a scanner's Enter arriving now adds nothing (the rows take Tab, then Enter or a tap).
    title.focus();
  }

  function close() {
    if (!sheet.hasAttribute('open')) { return; }
    if (sheet.close) { sheet.close(); } else { sheet.removeAttribute('open'); sheet.dispatchEvent(new Event('close')); }
  }

  // Closed by Esc, the close button, a tap outside or an add: the focus goes back to the scan field, as after any
  // cart action of pos.js, never to a line's «بدائل» or «شيله؟» (a scan's Enter would open the sheet or remove the line).
  sheet.addEventListener('close', function () {
    current = null;
    focusScan();
  });
  sheet.querySelector('[data-pos-alt-close]').addEventListener('click', close);
  sheet.addEventListener('click', function (event) {
    if (event.target !== sheet) { return; }
    var box = sheet.getBoundingClientRect();
    if (event.clientY < box.top || event.clientY > box.bottom || event.clientX < box.left || event.clientX > box.right) { close(); }
  });

  // While the sheet is open no key reaches the page behind it: pos.js's F9 would take the payment and F2 would focus
  // the scan field under the sheet. The sheet's own keys (Tab, Esc, Enter or Space on a row) are the browser's and still work.
  window.addEventListener('keydown', function (event) {
    if (!sheet.hasAttribute('open')) { pending = null; return; }  // any key after a tap: the cashier has moved on
    event.stopPropagation();
    if (event.key === 'F9' || event.key === 'F2') { event.preventDefault(); }
  }, true);

  // The rows of one item in the cart, and their quantities: more after an add only when pos.js took it.
  function held(id) {
    var total = 0;
    body.querySelectorAll('tr[data-item="' + id + '"]').forEach(function (tr) {
      var qty = tr.querySelector('[data-field="qty"]');
      total += 1 + (qty ? num(qty.value) : 0);
    });
    return total;
  }

  content.addEventListener('click', function (event) {
    var button = event.target.closest('[data-pos-alt-add]');
    var item = button && byId[button.getAttribute('data-pos-alt-add')];
    if (!item || button.disabled || !current) { return; }
    var source = current;
    close();  // the page outside a modal sheet is inert until it closes
    var before = held(item.id);
    search.value = item.label;
    search.dispatchEvent(new Event('change', { bubbles: true }));  // the till's own add path, the one a quick tile uses
    // pos.js may refuse (a serial-tracked item needs its serial scanned): its own message then stays.
    if (source.short && held(item.id) > before) {
      offered[source.id] = true;
      mark();
      if (status) {
        status.textContent = fill(words.added, { alt: button.querySelector('.pos-alt__name').textContent, name: source.name });
        status.classList.remove('is-error');
      }
    }
    focusScan();  // the status line (aria-live) says what happened; the next scan goes to the cart
  });

  body.addEventListener('click', function (event) {
    var button = event.target.closest('[data-pos-alt-open]');
    if (button) { open(button.getAttribute('data-pos-alt-open'), button); }
  });
  body.addEventListener('change', function (event) { if (event.target.getAttribute('data-field') === 'qty') { mark(); } });
  form.addEventListener('pos:rendered', function () { pending = null; mark(); });
  if (place) { place.addEventListener('change', mark); }
  mark();
})();
