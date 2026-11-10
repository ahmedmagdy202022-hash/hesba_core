/* R1: alternatives at the till (a pharmacy with the drug database on).
   A drug line in the cart that is short at the till's location says so and offers «بدائل»; any drug line with
   alternatives offers it too. The sheet lists the items with the same active ingredient on the shelf here, and one
   line naming the reference-list drugs the pharmacy does not have. Tapping an alternative adds it through the till's
   own name search, exactly as a quick tile does, so pricing, units, serials and every check of the sale stay pos.js's
   and the server's. Nothing is removed by itself: the short line then offers «شيله؟», the till's own remove button. */
(function () {
  'use strict';
  var form = document.querySelector('[data-pos]');
  var sheet = document.querySelector('[data-pos-alt]');
  if (!form || !sheet || !window.fetch) { return; }
  var words = JSON.parse(sheet.getAttribute('data-words') || '{}');
  var url = sheet.getAttribute('data-url');
  var catalog = JSON.parse((document.getElementById('hs-item-catalog') || {}).textContent || '[]');
  var byId = {};
  catalog.forEach(function (item) { byId[String(item.id)] = item; });
  var body = form.querySelector('[data-pos-lines]');
  var search = form.querySelector('[data-pos-search]');
  var status = form.querySelector('[data-pos-status]');
  var place = form.querySelector('select[name="location"]');
  var customer = form.querySelector('[data-pos-customer]');
  var unitBox = form.querySelector('[data-pos-discount]');
  var unit = unitBox ? unitBox.getAttribute('data-unit') || '' : '';
  var title = sheet.querySelector('[data-pos-alt-title]');
  var content = sheet.querySelector('[data-pos-alt-body]');
  var foot = sheet.querySelector('[data-pos-alt-foot]');
  var lang = document.documentElement.lang === 'en' ? 'en' : 'ar';
  var answers = {};  // item|location|customer -> the answer
  var failed = {};   // the same key -> when asking last failed (offline): asked again after a pause, or once back online
  var RETRY_MS = 15000;
  var asking = {};
  // UNITS-001: a line sold in a bigger unit (a box of 12) takes factor x quantity from the shelf; the units catalog
  // the till already carries (#hs-units) gives each unit's factor.
  var factorOf = {};
  (function () {
    var node = document.getElementById('hs-units');
    var units = node ? JSON.parse(node.textContent || '{}') || {} : {};
    Object.keys(units).forEach(function (itemId) { units[itemId].forEach(function (u) { factorOf[String(u.id)] = num(u.factor) || 1; }); });
  })();
  var offered = {};  // item id -> an alternative was added for its short line, which now offers «شيله؟»
  var current = null;  // the line the sheet is open for

  // Each value is bidi-isolated (FSI … PDI), so an Arabic drug name reads right inside an English sentence and the other way round.
  function fill(text, values) { return String(text || '').replace(/\{(\w+)\}/g, function (all, name) { return values[name] !== undefined ? '\u2068' + values[name] + '\u2069' : all; }); }
  function num(value) { var n = parseFloat(String(value || '').replace(',', '.')); return isNaN(n) ? 0 : n; }
  function money(value) { return num(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function el(tag, cls, text) { var node = document.createElement(tag); if (cls) { node.className = cls; } if (text !== undefined) { node.textContent = text; } return node; }
  function keyFor(id) { return [id, place ? place.value : '', customer ? customer.value : ''].join('|'); }

  function ask(id) {
    var key = keyFor(id);
    if (key in answers || asking[key]) { return; }
    if (failed[key] && Date.now() - failed[key] < RETRY_MS) { return; }
    asking[key] = true;
    var query = '?item=' + encodeURIComponent(id) + '&location=' + encodeURIComponent(place ? place.value : '') +
                '&customer=' + encodeURIComponent(customer ? customer.value : '') + '&lang=' + lang;
    fetch(url + query, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (answer) {
        // A refusal the server gave (no permission, not a drug shop) is final for this page; a network failure is not.
        if (answer && answer.ok) { answers[key] = answer; delete failed[key]; } else { answers[key] = false; }
      })
      .catch(function () { failed[key] = Date.now(); })
      .then(function () { delete asking[key]; if (key in answers) { mark(); } });
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
      var answer = answers[keyFor(id)];
      if (answer === undefined) { ask(id); return; }
      if (!answer || !answer.item.drug) { return; }
      var short = shortText(tr, answer);
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

  function focusLine(id) {
    var target = body.querySelector('tr[data-item="' + id + '"] .pos-alt-remove') || body.querySelector('tr[data-item="' + id + '"] [data-pos-alt-open]');
    if (target) { target.focus(); return; }
    var scan = form.querySelector('[data-pos-scan]');
    if (scan) { scan.focus(); }
  }

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

  function open(id, opener) {
    var answer = answers[keyFor(id)];
    if (!answer) { return; }
    current = { id: id, name: answer.item.name, short: !!opener.closest('tr.is-short'), adding: false };
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
      fold.appendChild(el('span', '', answer.unstocked.names.join(words.sep) + more + '. ' + words.order));
      content.appendChild(fold);
    }
    foot.textContent = words.foot;
    if (sheet.showModal) { sheet.showModal(); } else { sheet.setAttribute('open', ''); }
    content.scrollTop = 0;
    (content.querySelector('[data-pos-alt-add]:not([disabled])') || sheet.querySelector('[data-pos-alt-close]')).focus();
  }

  function close() {
    if (!sheet.hasAttribute('open')) { return; }
    if (sheet.close) { sheet.close(); } else { sheet.removeAttribute('open'); sheet.dispatchEvent(new Event('close')); }
  }

  // Closed by Esc, the close button or a tap outside: focus goes back to the line's button (render() made it anew).
  sheet.addEventListener('close', function () {
    if (current && !current.adding) { focusLine(current.id); }
    current = null;
  });
  sheet.querySelector('[data-pos-alt-close]').addEventListener('click', close);
  sheet.addEventListener('click', function (event) {
    if (event.target !== sheet) { return; }
    var box = sheet.getBoundingClientRect();
    if (event.clientY < box.top || event.clientY > box.bottom || event.clientX < box.left || event.clientX > box.right) { close(); }
  });

  content.addEventListener('click', function (event) {
    var button = event.target.closest('[data-pos-alt-add]');
    var item = button && byId[button.getAttribute('data-pos-alt-add')];
    if (!item || button.disabled || !current) { return; }
    var source = current;
    source.adding = true;
    close();  // the page outside a modal sheet is inert until it closes
    search.value = item.label;
    search.dispatchEvent(new Event('change', { bubbles: true }));  // the till's own add path, the one a quick tile uses
    if (source.short) {
      offered[source.id] = true;
      mark();
      if (status) {
        status.textContent = fill(words.added, { alt: button.querySelector('.pos-alt__name').textContent, name: source.name });
        status.classList.remove('is-error');
      }
    }
    focusLine(source.id);
  });

  body.addEventListener('click', function (event) {
    var button = event.target.closest('[data-pos-alt-open]');
    if (button) { open(button.getAttribute('data-pos-alt-open'), button); }
  });
  body.addEventListener('change', function (event) { if (event.target.getAttribute('data-field') === 'qty') { mark(); } });
  form.addEventListener('pos:rendered', mark);
  if (place) { place.addEventListener('change', mark); }
  mark();
})();
