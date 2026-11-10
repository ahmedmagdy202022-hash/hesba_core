/* M2 (HG-086): «الأدوية» on the visit form.
   «ضيف دوا» opens the R1 sheet (drug_picker.css: a bottom sheet on a phone, a centred dialog from 720px) that searches the
   reference drug list through medical:drug_search (GET data-url?q=&patient=&animal=, at most 20 rows, no prices). A picked
   row becomes a line with the dose filled from its form; «كام مرة» and «لمدة» are one tap on a quick pick. «اكتب دوا بإيدك»
   adds a line with a name box. Every line posts its own inputs in order with the visit; the server checks and writes
   them (services.save_visit) and keeps a saved line's copy of the list as it was. The allergy alert on a picked row comes
   from the server's one rule (prescribing.allergy_terms); it is advice and never stops the save.
   The sheet's behaviour follows drug_picker.js (stale answers dropped, abort on typing, Esc, arrows), which stays as is. */
(function () {
  'use strict';
  var root = document.querySelector('[data-rx]');
  if (!root) { return; }
  var words = JSON.parse((document.getElementById('rx-words') || {}).textContent || '{}');
  var chips = JSON.parse((document.getElementById('rx-chips') || {}).textContent || '{}');
  var list = root.querySelector('[data-rx-list]');
  var empty = root.querySelector('[data-rx-empty]');
  var count = root.querySelector('[data-rx-count]');
  var template = root.querySelector('[data-rx-template]');
  var addButton = root.querySelector('[data-rx-add]');
  var handButton = root.querySelector('[data-rx-hand]');
  var max = parseInt(root.getAttribute('data-max'), 10) || 20;
  var lang = root.getAttribute('data-lang') === 'en' ? 'en' : 'ar';
  var FOOD = chips.food || [];

  function say(key, values) {
    var text = words[key] || '';
    Object.keys(values || {}).forEach(function (k) { text = text.split('{' + k + '}').join(values[k]); });
    return text;
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text != null) { node.textContent = text; }
    return node;
  }
  function ltr(text) { var b = el('bdi', '', text); b.setAttribute('dir', 'ltr'); return b; }
  function lines() { return Array.prototype.slice.call(list.querySelectorAll('[data-rx-line]')); }
  function input(line, name) { return line.querySelector('[data-rx-input="' + name + '"]'); }
  function nameOf(line) { var field = line.querySelector('[data-rx-name]'); return field ? field.value.trim() : ''; }

  // ---- the lines ----
  function renumber() {
    var all = lines();
    all.forEach(function (line, index) {
      line.querySelector('[data-rx-no]').textContent = String(index + 1);
      var label = nameOf(line) || say('medicine_n', { n: index + 1 });
      line.querySelector('[data-rx-edit]').setAttribute('aria-label', say('edit_label', { name: label }));
      line.querySelector('[data-rx-remove]').setAttribute('aria-label', say('remove_label', { name: label }));
      line.querySelector('[data-rx-up]').disabled = index === 0;
      line.querySelector('[data-rx-down]').disabled = index === all.length - 1;
    });
    empty.hidden = all.length > 0;
    count.hidden = all.length === 0;
    count.textContent = String(all.length);
    var full = all.length >= max;
    if (addButton) { addButton.disabled = full; }
    handButton.disabled = full;
  }
  function summary(line) {
    var parts = ['dose', 'frequency', 'duration'].map(function (name) { return input(line, name).value.trim(); }).filter(Boolean);
    line.querySelector('[data-rx-summary]').textContent = parts.join(' · ');
  }
  function setOpen(line, open) {
    line.classList.toggle('is-collapsed', !open);
    line.querySelector('[data-rx-edit]').setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) { drawChips(line); }
  }
  function collapseOthers(keep) { lines().forEach(function (line) { if (line !== keep) { setOpen(line, false); } }); }

  // quick picks: they fill the text; the doctor can still type anything
  function chip(label, pressed, onTap) {
    var button = el('button', 'rx-chip', label);
    button.type = 'button';
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    button.addEventListener('click', onTap);
    return button;
  }
  function splitFood(value) {
    var text = value.trim();
    for (var i = 0; i < FOOD.length; i++) {
      if (text === FOOD[i]) { return ['', FOOD[i]]; }
      if (text.slice(-FOOD[i].length - 1) === ' ' + FOOD[i]) { return [text.slice(0, -FOOD[i].length - 1).trim(), FOOD[i]]; }
    }
    return [text, ''];
  }
  function drawChips(line) {
    var form = line.getAttribute('data-form') || '';
    ['dose', 'frequency', 'duration'].forEach(function (name) {
      var box = line.querySelector('[data-rx-chips="' + name + '"]');
      var field = input(line, name);
      box.textContent = '';
      if (name === 'frequency') {
        var parts = splitFood(field.value);
        (chips.frequency || []).forEach(function (label) {
          box.appendChild(chip(label, parts[0] === label, function () {
            var now = splitFood(field.value);
            field.value = now[1] ? label + ' ' + now[1] : label;
            changed(line);
          }));
        });
        FOOD.forEach(function (label) {
          box.appendChild(chip(label, parts[1] === label, function () {
            var now = splitFood(field.value);
            var food = now[1] === label ? '' : label;  // a second tap takes it off again
            field.value = [now[0], food].filter(Boolean).join(' ');
            changed(line);
          }));
        });
        return;
      }
      var options = name === 'dose' ? ((chips.dose || {})[form] || (chips.dose || {})[''] || []) : (chips.duration || []);
      options.forEach(function (label) {
        box.appendChild(chip(label, field.value.trim() === label, function () { field.value = label; changed(line); }));
      });
    });
  }
  // Only the pressed state changes: the chips are never rebuilt under a tap (a box's change event fires on the same
  // tap that leaves it, and a rebuilt chip would swallow that tap).
  function sync(line) {
    ['dose', 'frequency', 'duration'].forEach(function (name) {
      var value = input(line, name).value.trim();
      var parts = name === 'frequency' ? splitFood(value) : null;
      line.querySelectorAll('[data-rx-chips="' + name + '"] .rx-chip').forEach(function (button) {
        var label = button.textContent;
        var on = parts ? (FOOD.indexOf(label) >= 0 ? parts[1] === label : parts[0] === label) : value === label;
        button.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    });
  }
  function changed(line) {
    summary(line);
    sync(line);
  }

  function wire(line) {
    line.querySelector('[data-rx-edit]').addEventListener('click', function () {
      var open = line.classList.contains('is-collapsed');
      if (open) { collapseOthers(line); }
      setOpen(line, open);
      if (open) { (input(line, 'name') || input(line, 'dose')).focus(); }
    });
    line.querySelector('[data-rx-remove]').addEventListener('click', function () {
      var next = line.nextElementSibling || line.previousElementSibling;
      line.remove();
      renumber();
      var target = next ? next.querySelector('[data-rx-edit]') : (addButton && !addButton.hidden ? addButton : handButton);
      target.focus();
    });
    line.querySelector('[data-rx-up]').addEventListener('click', function (event) {
      if (line.previousElementSibling) { list.insertBefore(line, line.previousElementSibling); renumber(); event.currentTarget.focus(); }
    });
    line.querySelector('[data-rx-down]').addEventListener('click', function (event) {
      if (line.nextElementSibling) { list.insertBefore(line.nextElementSibling, line); renumber(); event.currentTarget.focus(); }
    });
    line.querySelector('[data-rx-done]').addEventListener('click', function () {
      setOpen(line, false);
      line.querySelector('[data-rx-edit]').focus();
    });
    line.addEventListener('input', function (event) {
      var name = event.target.getAttribute('data-rx-input');
      if (name === 'dose' || name === 'frequency' || name === 'duration') { changed(line); }
      if (name === 'name') { renumber(); }
    });
    // Enter inside a line moves to the next box instead of saving the whole visit half-written.
    line.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' || event.target.tagName !== 'INPUT') { return; }
      event.preventDefault();
      var order = ['name', 'dose', 'frequency', 'duration'];
      var at = order.indexOf(event.target.getAttribute('data-rx-input'));
      var next = at >= 0 && at < order.length - 1 ? input(line, order[at + 1]) : null;
      if (next) { next.focus(); return; }
      setOpen(line, false);
      (addButton && !addButton.hidden && !addButton.disabled ? addButton : line.querySelector('[data-rx-edit]')).focus();
    });
  }

  function newLine() {
    var line = template.content.firstElementChild.cloneNode(true);
    list.appendChild(line);
    wire(line);
    return line;
  }
  function alertFor(line, terms) {
    var box = line.querySelector('[data-rx-alert]');
    if (terms && terms.length) {
      box.querySelector('[data-rx-alert-text]').textContent = say('allergy', { terms: terms.join(lang === 'en' ? ', ' : '، ') });
      box.hidden = false;
    } else {
      box.hidden = true;
    }
  }
  function fromRow(row) {
    var line = newLine();
    var name = lang === 'en' ? (row.name_en || row.name_ar) : (row.name_ar || row.name_en);
    var field = line.querySelector('[data-rx-name]');
    field.type = 'hidden';
    field.removeAttribute('data-rx-input');
    field.value = name;
    var holder = field.closest('label');
    var title = el('strong', 'rx-line__name', name);
    title.setAttribute('data-rx-title', '');
    holder.parentNode.insertBefore(field, holder);
    holder.parentNode.insertBefore(title, holder);
    holder.remove();
    line.querySelector('[data-rx-ref]').value = row.id;
    var form = (row.form && row.form.code) || '';
    line.setAttribute('data-form', form);
    var sub = line.querySelector('[data-rx-sub]');
    var bits = [];
    if (row.strength && !inName(row.strength, name)) { bits.push(ltr(row.strength)); }  // «بنادول 500 مجم» already says 500
    if (row.form && row.form.label) { bits.push(document.createTextNode(row.form.label)); }
    bits.forEach(function (bit, index) { if (index) { sub.appendChild(document.createTextNode(' · ')); } sub.appendChild(bit); });
    line.querySelector('[data-rx-ing]').textContent = row.ingredient || '';
    var dose = ((chips.dose || {})[form] || [])[0];
    if (dose) { input(line, 'dose').value = dose; }  // one unit of its form; a tap or a word changes it
    alertFor(line, row.allergy);
    finish(line);
    return line;
  }
  // as prescribing._strength_in_name: the strength's first number is already a number of the trade name
  function digits(text) { return String(text || '').replace(/[\u0660-\u0669]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); }).replace(/\u066b/g, '.'); }
  function inName(strength, name) {
    var first = digits(strength).match(/\d+(?:\.\d+)?/);
    return !!first && (digits(name).match(/\d+(?:\.\d+)?/g) || []).indexOf(first[0]) >= 0;
  }
  function byHand(name) {
    var line = newLine();
    if (name) { line.querySelector('[data-rx-name]').value = name; }
    finish(line);
    return line;
  }
  function finish(line) {
    summary(line);
    collapseOthers(line);
    setOpen(line, true);
    renumber();
  }

  // the lines the server drew (a saved visit, or a refused save): complete ones fold up, the rest stay open
  lines().forEach(function (line) {
    wire(line);
    summary(line);
    var complete = nameOf(line) && input(line, 'dose').value.trim() && input(line, 'frequency').value.trim();
    setOpen(line, !complete);
  });
  root.classList.add('rx--ready');
  renumber();
  handButton.hidden = false;
  handButton.addEventListener('click', function () { var line = byHand(''); input(line, 'name').focus(); });

  // ---- the sheet ----
  var dialog = root.querySelector('[data-rx-picker]');
  if (!addButton || !dialog || typeof dialog.showModal !== 'function' || !window.fetch) { return; }
  var url = root.getAttribute('data-url');
  var patient = root.getAttribute('data-patient');
  var q = dialog.querySelector('[data-rx-q]');
  var results = dialog.querySelector('[data-rx-results]');
  var status = dialog.querySelector('[data-rx-status]');
  var cap = dialog.querySelector('[data-rx-cap]');
  var states = {};
  dialog.querySelectorAll('[data-rx-state]').forEach(function (node) { states[node.getAttribute('data-rx-state')] = node; });
  var timer = null, controller = null, lastQuery = null, returnFocus = null, hiddenForTyping = false;
  addButton.hidden = false;
  addButton.addEventListener('click', open);

  function show(state) {
    Object.keys(states).forEach(function (key) { states[key].hidden = key !== state; });
    results.hidden = state !== 'results';
    cap.hidden = true;
    if (state === 'loading') { status.textContent = say('loading'); }
    else if (state === 'short') { status.textContent = ''; }
    else if (state === 'empty') { status.textContent = say('empty_title'); }
    else if (state === 'nolist') { status.textContent = say('nolist_title'); }
    else if (state === 'error') { status.textContent = say('error_title'); }
  }
  function open(event) {
    returnFocus = event && event.currentTarget ? event.currentTarget : addButton;
    dialog.showModal();
    document.documentElement.classList.add('hs-drug-locked');
    run(true);
    q.focus();
    q.select();
  }
  function close() { if (dialog.open) { dialog.close(); } }
  dialog.addEventListener('close', function () {
    document.documentElement.classList.remove('hs-drug-locked');
    if (controller) { controller.abort(); }
    (returnFocus || addButton).focus();
  });
  dialog.querySelector('[data-rx-close]').addEventListener('click', close);
  dialog.querySelector('[data-rx-manual]').addEventListener('click', function () {
    var line = byHand(folded(q.value));
    returnFocus = input(line, 'name');
    q.value = '';
    close();
  });
  dialog.querySelector('[data-rx-retry]').addEventListener('click', function () { lastQuery = null; run(true); q.focus(); });
  dialog.addEventListener('click', function (event) { if (event.target === dialog) { close(); } });  // the backdrop
  dialog.addEventListener('keydown', function (event) { if (event.key === 'Escape') { event.preventDefault(); close(); } });
  q.addEventListener('input', function () {
    clearTimeout(timer);
    var query = folded(q.value);
    // an answer still on its way is for the old text: stop it now so its rows never land in between
    if (query !== lastQuery && controller) { controller.abort(); controller = null; lastQuery = null; }
    // the rows on screen answer the old text: never let them be picked while the new search waits
    if (query.length >= 2 && query !== lastQuery) { show('loading'); hiddenForTyping = true; }
    timer = setTimeout(function () { run(false); }, 250);
  });
  q.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { event.preventDefault(); clearTimeout(timer); run(true); }
    if (event.key === 'ArrowDown') { var first = results.querySelector('button'); if (first && !results.hidden) { event.preventDefault(); first.focus(); } }
  });
  results.addEventListener('keydown', function (event) {
    var buttons = Array.prototype.slice.call(results.querySelectorAll('button'));
    var at = buttons.indexOf(document.activeElement);
    if (at < 0) { return; }
    if (event.key === 'ArrowDown' && at < buttons.length - 1) { event.preventDefault(); buttons[at + 1].focus(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); (at > 0 ? buttons[at - 1] : q).focus(); }
  });

  function folded(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  function animal() { var select = document.getElementById('animal'); return select ? select.value : ''; }
  function run(force) {
    var query = folded(q.value);
    if (query.length < 2) { lastQuery = null; if (controller) { controller.abort(); controller = null; } show('short'); return; }
    // the same text as the last search: nothing to do, unless typing hid its rows meanwhile
    if (!force && query === lastQuery && !hiddenForTyping) { return; }
    hiddenForTyping = false;
    lastQuery = query;
    if (controller) { controller.abort(); }
    var mine = controller = new AbortController();
    function settled() { if (controller === mine) { controller = null; } }
    function current() { return folded(q.value) === query; }  // an answer for text no longer in the box is dropped
    show('loading');
    var address = url + '?lang=' + lang + '&q=' + encodeURIComponent(query) + '&patient=' + encodeURIComponent(patient) + '&animal=' + encodeURIComponent(animal());
    fetch(address, { headers: { Accept: 'application/json' }, credentials: 'same-origin', signal: mine.signal })
      .then(function (response) { if (!response.ok) { throw new Error(String(response.status)); } return response.json(); })
      .then(function (data) {
        settled();
        if (!current()) { return; }
        if (data.too_short) { show('short'); return; }
        if (data.list_empty) { show('nolist'); return; }
        draw(data.results || [], data.limit || 20);
      })
      .catch(function (error) {
        settled();
        if ((error && error.name === 'AbortError') || !current()) { return; }
        lastQuery = null;
        show('error');
      });
  }
  // Arabic: نتيجة واحدة، نتيجتين، 3–10 نتايج، 11 نتيجة وأكتر (English: 1 result, n results)
  function countText(n) {
    if (n === 1) { return say('count_one'); }
    if (n === 2) { return say('count_two'); }
    return say(n <= 10 ? 'count' : 'count_many', { n: n });
  }
  function draw(rows, limit) {
    results.textContent = '';
    if (!rows.length) { show('empty'); return; }
    rows.forEach(function (item) {
      var li = el('li');
      var button = el('button', 'hs-drug-row');
      button.type = 'button';
      var main = el('span', 'hs-drug-row__main');
      var line1 = el('span', 'hs-drug-row__l1');
      var first = lang === 'en' ? (item.name_en || item.name_ar) : (item.name_ar || item.name_en);  // the reader's language first
      var second = lang === 'en' ? item.name_ar : item.name_en;
      line1.appendChild(el('strong', '', first));
      if (second && second !== first) { var other = el('bdi', 'hs-drug-row__en', second); line1.appendChild(other); }
      var line2 = el('span', 'hs-drug-row__l2');
      var parts = [];
      if (item.ingredient || item.strength) { parts.push(ltr([item.ingredient, item.strength].filter(Boolean).join(' '))); }
      if (item.form && item.form.label) { parts.push(document.createTextNode(item.form.label)); }
      if (item.pack) { parts.push(ltr(item.pack)); }
      parts.forEach(function (part, index) { if (index) { line2.appendChild(document.createTextNode(' · ')); } line2.appendChild(part); });
      main.appendChild(line1);
      main.appendChild(line2);
      var side = el('span', 'hs-drug-row__side');
      if (item.allergy && item.allergy.length) { side.appendChild(el('span', 'hs-drug-chip rx-chip--allergy', '⚠ ' + say('allergy_chip'))); }
      if (item.prescription_only === true) { side.appendChild(el('span', 'hs-drug-chip hs-drug-chip--rx', say('rx_chip'))); }
      button.appendChild(main);
      if (side.childNodes.length) { button.appendChild(side); }
      button.addEventListener('click', function () { pick(item); });
      li.appendChild(button);
      results.appendChild(li);
    });
    show('results');
    status.textContent = countText(rows.length);
    if (rows.length >= limit) { cap.textContent = say('cap', { n: limit }); cap.hidden = false; }
  }
  function pick(item) {
    var line = fromRow(item);
    returnFocus = line;
    q.value = '';
    close();
    line.scrollIntoView({ block: 'nearest' });
  }
})();
