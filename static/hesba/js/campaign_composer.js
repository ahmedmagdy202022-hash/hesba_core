/* S8: the campaign composer's live audience count and message preview.
 *
 * The page already shows the numbers for the saved form; this asks the server again (counts and one preview only,
 * never the customers) whenever the audience, the number, the category, the day or the text changes. Everything the
 * server sends is written with textContent. Without JavaScript the form still saves and queues.
 */
(function () {
  'use strict';
  var form = document.querySelector('[data-composer]');
  if (!form) { return; }
  var url = form.getAttribute('data-audience-url');
  var lang = form.getAttribute('data-lang') || 'ar';
  var campaign = form.getAttribute('data-campaign') || '';
  var timer = null, serial = 0;

  function all(sel) { return Array.prototype.slice.call(form.querySelectorAll(sel)); }
  function one(sel) { return form.querySelector(sel); }
  function say(sel, value) { all(sel).forEach(function (node) { node.textContent = String(value); }); }
  function field(name) { return form.elements[name]; }

  function chosen() { return one('input[name="audience"]:checked'); }

  function showParam() {
    var radio = chosen();
    var box = one('[data-param-box]');
    if (!radio || !box) { return; }
    box.hidden = !radio.getAttribute('data-param');
    one('[data-category-box]').hidden = !radio.getAttribute('data-category');
    one('[data-param-label]').textContent = radio.getAttribute('data-label') || '';
    var holder = one('[data-chip-list]');
    while (holder.firstChild) { holder.removeChild(holder.firstChild); }
    (radio.getAttribute('data-chips') || '').split(',').filter(Boolean).forEach(function (value) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'cp-chip';
      chip.setAttribute('data-chip', value);
      chip.textContent = value;
      holder.appendChild(chip);
    });
    all('.cp-seg').forEach(function (label) { label.classList.toggle('is-sel', label.contains(radio)); });
    markChips();
  }

  function markChips() {
    var value = field('threshold').value.trim();
    all('[data-chip]').forEach(function (chip) { chip.classList.toggle('is-sel', chip.getAttribute('data-chip') === value); });
  }

  function refresh() {
    var query = new URLSearchParams();
    ['name', 'text', 'audience', 'threshold', 'category', 'scheduled_day'].forEach(function (name) {
      var node = field(name);
      var value = node && (node.value !== undefined ? node.value : '');
      if (name === 'audience') { var radio = chosen(); value = radio ? radio.value : ''; }
      query.set(name, value || '');
    });
    query.set('lang', lang);
    if (campaign) { query.set('campaign', campaign); }
    var mine = ++serial;
    fetch(url + '?' + query.toString(), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (mine !== serial) { return; }
        var error = one('[data-live-error]');
        if (!data.ok) { error.textContent = data.error || ''; error.hidden = !data.error; return; }
        error.hidden = true;
        say('[data-count]', data.count);
        say('[data-match]', data.match);
        say('[data-no-phone]', data.no_phone);
        say('[data-dups]', data.duplicates);
        say('[data-preview]', data.preview);
        var cap = one('[data-cap-note]');
        cap.textContent = data.cap_note;
        cap.classList.toggle('op-message-success', !data.cap_over);
        var queue = one('[data-queue]');
        queue.textContent = data.cta;
        queue.setAttribute('data-confirm', data.confirm);
      })
      .catch(function () { /* the numbers on the page stay as they were */ });
  }

  function later() { window.clearTimeout(timer); timer = window.setTimeout(refresh, 350); }

  form.addEventListener('change', function (event) {
    if (event.target.name === 'audience') { field('threshold').value = ''; showParam(); }
    markChips();
    later();
  });
  form.addEventListener('input', function (event) {
    if (event.target.name === 'text') { say('[data-len]', event.target.value.length); }
    if (event.target.name === 'threshold') { markChips(); }
    if (event.target.name !== 'name') { later(); }
  });
  form.addEventListener('click', function (event) {
    var chip = event.target.closest('[data-chip]');
    var insert = event.target.closest('[data-insert]');
    if (chip) {
      field('threshold').value = chip.getAttribute('data-chip');
      markChips();
      later();
    } else if (insert) {
      var area = field('text');
      var token = insert.getAttribute('data-insert');
      var start = area.selectionStart === undefined ? area.value.length : area.selectionStart;
      var end = area.selectionEnd === undefined ? start : area.selectionEnd;
      area.value = area.value.slice(0, start) + token + area.value.slice(end);
      area.focus();
      area.setSelectionRange(start + token.length, start + token.length);
      say('[data-len]', area.value.length);
      later();
    }
  });
  markChips();
})();
