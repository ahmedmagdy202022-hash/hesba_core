/* R1 step 2: «اختار من القايمة» on a pharmacy's new item.
   The button opens a bottom sheet (phone) or a centred dialog (desktop) that searches the reference drug list
   (GET data-url?q=, at most 20 rows). Picking a row only fills the form for the person to review: the tinted fields
   come from the list until edited, the sale price is a dated suggestion, the prescription box stays as it was.
   Nothing is saved until the form's own save button. Without this script the form works exactly as before. */
(function () {
  'use strict';
  var dialog = document.querySelector('[data-drug-picker]');
  if (!dialog || typeof dialog.showModal !== 'function' || !window.fetch) { return; }
  var form = dialog.closest('form');
  var words = JSON.parse((document.getElementById('drug-words') || {}).textContent || '{}');
  var prefill = JSON.parse((document.getElementById('drug-prefill') || {}).textContent || 'null');
  var url = dialog.getAttribute('data-url');
  var lang = dialog.getAttribute('data-lang') === 'en' ? 'en' : 'ar';
  var input = dialog.querySelector('[data-drug-q]');
  var list = dialog.querySelector('[data-drug-list]');
  var status = dialog.querySelector('[data-drug-status]');
  var cap = dialog.querySelector('[data-drug-cap]');
  var source = dialog.querySelector('[data-drug-source]');
  var states = {};
  dialog.querySelectorAll('[data-drug-state]').forEach(function (el) { states[el.getAttribute('data-drug-state')] = el; });
  var banner = document.querySelector('[data-drug-banner]');
  var bannerPrice = document.querySelector('[data-drug-banner-price]');
  function field(id) { return document.getElementById(id); }
  var name = field('id_item_name');
  var price = field('id_default_sale_price');
  var reference = form ? form.querySelector('[data-drug-reference]') : null;
  // form field -> what of the row fills it
  var FILL = [
    ['id_item_name', function (row) { return lang === 'en' ? (row.name_en || row.name_ar) : (row.name_ar || row.name_en); }],
    ['id_barcode', function (row) { return row.barcode; }],
    ['id_drug-active_ingredient', function (row) { return row.ingredient; }],
    ['id_drug-strength', function (row) { return row.strength; }],
    ['id_drug-form', function (row) { return row.form && row.form.code; }],
    ['id_drug-manufacturer', function (row) { return row.manufacturer; }],
    ['id_default_sale_price', function (row) { return row.public_price; }]
  ];
  if (!name) { return; }

  function say(key, values) {
    var text = words[key] || '';
    Object.keys(values || {}).forEach(function (k) { text = text.replace('{' + k + '}', values[k]); });
    return text;
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text != null) { node.textContent = text; }
    return node;
  }
  function ltr(text) { var b = el('bdi', '', text); b.setAttribute('dir', 'ltr'); return b; }

  // ---- the opener beside the name, and the hint under it ----
  var opener = document.querySelector('[data-drug-open-main]');
  var hint = document.querySelector('[data-drug-hint]');
  var row = el('div', 'hs-drug-name-row');
  name.parentNode.insertBefore(row, name);
  row.appendChild(name);
  if (opener) { row.appendChild(opener); opener.hidden = false; }
  if (hint) { row.insertAdjacentElement('afterend', hint); hint.hidden = false; }
  document.querySelectorAll('[data-drug-picker-open]').forEach(function (button) {
    button.hidden = false;
    button.addEventListener('click', open);
  });

  // ---- the sheet ----
  var timer = null, controller = null, lastQuery = null, returnFocus = null, hiddenForTyping = false;
  function show(state) {
    Object.keys(states).forEach(function (key) { states[key].hidden = key !== state; });
    list.hidden = state !== 'results';
    cap.hidden = true;
    if (state === 'loading') { status.textContent = say('loading'); }
    else if (state === 'short') { status.textContent = ''; }
    else if (state === 'empty') { status.textContent = say('empty_title'); }
    else if (state === 'error') { status.textContent = say('error_title'); }
  }
  function open(event) {
    returnFocus = event && event.currentTarget ? event.currentTarget : name;
    dialog.showModal();
    document.documentElement.classList.add('hs-drug-locked');
    if (!input.value && name.value && !name.classList.contains('hs-from-list')) { input.value = name.value; }
    run(true);
    input.focus();
    input.select();
  }
  function close() { if (dialog.open) { dialog.close(); } }
  dialog.addEventListener('close', function () {
    document.documentElement.classList.remove('hs-drug-locked');
    if (controller) { controller.abort(); }
    (returnFocus || name).focus();
  });
  dialog.querySelector('[data-drug-picker-close]').addEventListener('click', close);
  dialog.querySelector('[data-drug-manual]').addEventListener('click', function () { returnFocus = name; close(); });
  dialog.querySelector('[data-drug-retry]').addEventListener('click', function () { lastQuery = null; run(true); input.focus(); });
  dialog.addEventListener('click', function (event) { if (event.target === dialog) { close(); } });  // the backdrop
  // Esc closes from anywhere in the sheet; a search box would otherwise only clear its text on the first press.
  dialog.addEventListener('keydown', function (event) { if (event.key === 'Escape') { event.preventDefault(); close(); } });
  input.addEventListener('input', function () {
    clearTimeout(timer);
    var query = folded(input.value);
    // A search still on its way answers the old text: stop it now, not when the debounced search starts, so its rows
    // can never land in between. Nothing answers lastQuery any more, so the same text typed again searches again.
    if (query !== lastQuery && controller) { controller.abort(); controller = null; lastQuery = null; }
    // the rows on screen answer the old text: never let them be picked while the new search waits
    if (query.length >= 2 && query !== lastQuery) { show('loading'); hiddenForTyping = true; }
    timer = setTimeout(function () { run(false); }, 250);
  });
  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { event.preventDefault(); clearTimeout(timer); run(true); }
    if (event.key === 'ArrowDown') { var first = list.querySelector('button'); if (first && !list.hidden) { event.preventDefault(); first.focus(); } }
  });
  list.addEventListener('keydown', function (event) {
    var buttons = Array.prototype.slice.call(list.querySelectorAll('button'));
    var at = buttons.indexOf(document.activeElement);
    if (at < 0) { return; }
    if (event.key === 'ArrowDown' && at < buttons.length - 1) { event.preventDefault(); buttons[at + 1].focus(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); (at > 0 ? buttons[at - 1] : input).focus(); }
  });

  function folded(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  function run(force) {
    var query = folded(input.value);
    if (query.length < 2) { lastQuery = null; if (controller) { controller.abort(); controller = null; } show('short'); return; }
    // The same text as the last search: nothing to do, unless typing hid its rows meanwhile (a letter typed and
    // deleted inside the debounce window), in which case they are fetched again instead of leaving the skeleton up.
    if (!force && query === lastQuery && !hiddenForTyping) { return; }
    hiddenForTyping = false;
    lastQuery = query;
    if (controller) { controller.abort(); }
    var mine = controller = new AbortController();
    function settled() { if (controller === mine) { controller = null; } }
    // An answer for text that is no longer in the box is dropped, whatever it is (rows, too short or an error).
    function current() { return folded(input.value) === query; }
    show('loading');
    fetch(url + '?lang=' + lang + '&q=' + encodeURIComponent(query), { headers: { Accept: 'application/json' }, credentials: 'same-origin', signal: mine.signal })
      .then(function (response) { if (!response.ok) { throw new Error(String(response.status)); } return response.json(); })
      .then(function (data) {
        settled();
        if (!current()) { return; }
        if (data.too_short) { show('short'); return; }
        draw(data.results || [], data.limit || 20);
      })
      .catch(function (error) {
        settled();
        if ((error && error.name === 'AbortError') || !current()) { return; }
        lastQuery = null;
        show('error');
      });
  }

  function money(value) { return value == null ? '' : value; }
  // Arabic: نتيجة واحدة، نتيجتين، 3–10 نتايج، 11 نتيجة وأكتر (English: 1 result, n results)
  function countText(n) {
    if (n === 1) { return say('count_one'); }
    if (n === 2) { return say('count_two'); }
    return say(n <= 10 ? 'count' : 'count_many', { n: n });
  }
  function draw(rows, limit) {
    list.textContent = '';
    if (!rows.length) { show('empty'); return; }
    rows.forEach(function (item) {
      var li = el('li');
      var button = el('button', 'hs-drug-row');
      button.type = 'button';
      var main = el('span', 'hs-drug-row__main');
      var line1 = el('span', 'hs-drug-row__l1');
      line1.appendChild(el('strong', '', item.name_ar || item.name_en));
      if (item.name_ar && item.name_en) { var en = ltr(item.name_en); en.className = 'hs-drug-row__en'; line1.appendChild(en); }
      var line2 = el('span', 'hs-drug-row__l2');
      var parts = [];
      if (item.ingredient || item.strength) { parts.push(ltr([item.ingredient, item.strength].filter(Boolean).join(' '))); }
      if (item.form && item.form.label) { parts.push(document.createTextNode(item.form.label)); }
      if (item.pack) { parts.push(ltr(item.pack)); }
      parts.forEach(function (part, index) { if (index) { line2.appendChild(document.createTextNode(' · ')); } line2.appendChild(part); });
      main.appendChild(line1);
      main.appendChild(line2);
      var side = el('span', 'hs-drug-row__side');
      var amount = el('span', 'hs-drug-row__price');
      if (item.public_price != null) { amount.appendChild(ltr(money(item.public_price))); amount.appendChild(el('small', '', ' ' + say('currency'))); }
      else { amount.appendChild(el('small', '', say('no_price'))); }
      side.appendChild(amount);
      if (item.prescription_only === true) { side.appendChild(el('span', 'hs-drug-chip hs-drug-chip--rx', say('rx_chip'))); }
      button.appendChild(main);
      button.appendChild(side);
      button.addEventListener('click', function () { pick(item); });
      li.appendChild(button);
      list.appendChild(li);
    });
    show('results');
    status.textContent = countText(rows.length);
    if (rows.length >= limit) { cap.textContent = say('cap', { n: limit }); cap.hidden = false; }
    var first = rows[0];
    source.textContent = say('source') + (first.price_month ? ' · ' + say('price_of', { month: first.price_month }) : '');
  }

  // ---- filling the form ----
  // picked: the row the form now follows (null once the name is cleared by hand). everPicked: a row filled the form
  // before, so a field without the tint and not empty was typed by the person and a later pick leaves it alone.
  var picked = null, everPicked = false;
  function tint(input, on) {
    if (!input) { return; }
    input.classList.toggle('hs-from-list', !!on);
  }
  function setValue(input, value) {
    if (!input) { return; }
    input.value = value == null ? '' : String(value);
    if (input.tagName === 'SELECT' && input.value !== String(value || '')) { input.value = ''; }
  }
  function handTyped(target) {
    return everPicked && target !== name && !target.classList.contains('hs-from-list') && String(target.value).trim() !== '';
  }
  function apply(item, fromServer) {
    FILL.forEach(function (pair) {
      var target = field(pair[0]);
      if (!target) { return; }
      var value = pair[1](item);
      if (fromServer) {  // ?ref= or a refused save: the server drew the values; tint only those still equal to the list's
        tint(target, value && String(target.value) === String(value));
        return;
      }
      if (handTyped(target)) { return; }  // a second pick never overwrites what the person typed (the name excepted)
      if (value) { setValue(target, value); tint(target, true); }
      else if (target.classList.contains('hs-from-list')) { setValue(target, ''); tint(target, false); }
    });
    picked = item;
    everPicked = true;
    if (reference) { reference.value = item.id; }
    if (banner) {
      banner.hidden = false;
      if (bannerPrice) { bannerPrice.textContent = ''; }
    }
    priceLine();
  }
  function edited(event) {
    var target = event.target;
    if (target && target.classList && target.classList.contains('hs-from-list')) { tint(target, false); }
    if (target === name && picked && !name.value.trim()) { forget(); return; }
    if (target === price) { priceLine(); }
  }
  // The name cleared by hand: the form no longer follows that row (no reference id, no suggested price).
  function forget() {
    picked = null;
    if (reference) { reference.value = ''; }
    if (bannerPrice) { bannerPrice.textContent = ''; }
    priceLine();
  }
  form.addEventListener('input', edited);
  form.addEventListener('change', edited);

  var line = null;
  function priceLine() {
    if (!price) { return; }
    if (!line) {
      line = el('p', 'hs-drug-price');
      line.setAttribute('aria-live', 'polite');
      var wrap = price.closest('.md-field') || price.parentNode;
      var after = wrap.querySelector('.hs-help') || price;
      after.insertAdjacentElement('afterend', line);
    }
    line.textContent = '';
    line.className = 'hs-drug-price';
    if (!picked || picked.public_price == null) { line.hidden = true; return; }
    line.hidden = false;
    var month = picked.price_month || say('no_date');
    var same = Number(price.value) === Number(picked.public_price) && price.value !== '';
    var head = el('span');
    if (!same) {
      line.classList.add('hs-drug-price--own');
      head.appendChild(el('b', '', say('own')));
      head.appendChild(document.createTextNode(' · '));
      var listPrice = say('list_price', { price: '\u0000', month: '\u0001' }).split(/\u0000|\u0001/);
      head.appendChild(document.createTextNode(listPrice[0]));
      head.appendChild(ltr(picked.public_price));
      head.appendChild(document.createTextNode(listPrice[1]));
      head.appendChild(ltr(month));
      head.appendChild(document.createTextNode(listPrice[2] || ''));
      line.appendChild(head);
      var restore = el('button', 'hs-drug-link', say('restore'));
      restore.type = 'button';
      restore.addEventListener('click', function () { price.value = picked.public_price; tint(price, true); priceTaken(); priceLine(); price.focus(); });
      line.appendChild(restore);
      return;
    }
    if (picked.stale) { line.classList.add('hs-drug-price--stale'); }
    head.appendChild(el('b', '', picked.stale ? say('stale') : say('suggested')));
    head.appendChild(document.createTextNode(' · ' + say('from_list') + ' · '));
    var of = say('price_of', { month: '\u0000' }).split('\u0000');
    head.appendChild(document.createTextNode(of[0]));
    head.appendChild(ltr(month));
    head.appendChild(document.createTextNode(of[1] || ''));
    line.appendChild(head);
    line.appendChild(el('span', 'hs-drug-price__note', (picked.stale ? say('stale_body') + ' ' : '') + say('disclaimer') + '. ' + (picked.stale ? '' : say('can_change'))));
  }

  // price_markup.js fills the selling price from the cost and a remembered margin until a price is typed by hand.
  // A price accepted from the list counts as typed by hand: tell the helper, or the next cost entry overwrites it.
  function priceTaken() {
    if (price && picked && picked.public_price != null && price.value !== '') { price.dispatchEvent(new CustomEvent('hesba:price-set', { bubbles: true })); }
  }

  function pick(item) {
    apply(item, false);
    priceTaken();
    returnFocus = name;
    close();
  }

  if (prefill) { apply(prefill, true); }
})();
