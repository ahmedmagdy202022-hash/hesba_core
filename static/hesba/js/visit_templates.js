/* M1 (HG-089): visit templates on the visit form.
   «اختار قالب» opens a sheet (the R1 sheet's look, drug_picker.css) with the templates the doctor may use, fetched once from
   medical:visit_templates_data (GET data-url?lang=&patient=&animal=; their own first, then the shared ones). Choosing one is done
   here, in the browser, before anything is saved: it fills ONLY the boxes that are still empty, appends its medicine lines after
   the lines already on the form (through window.HesbaRx from rx_picker.js, as ordinary editable lines) and says in one line what
   was filled and what was left alone. Nothing the doctor typed is overwritten and nothing is sent to the server.
   «احفظ ده كقالب» sends the four texts and the medicine rows to medical:visit_template_from_visit (POST, with the form's CSRF
   token), never the date, vitals, doctor or appointment; the patient's id only lets the server blank the patient's own name,
   file number and phone if they were typed into a text. The sheets live outside the visit's <form>; nothing here has a name
   attribute inside it, so the visit posts the same fields with or without a template applied. */
(function () {
  'use strict';
  var bar = document.querySelector('[data-vt-bar]');
  var form = document.querySelector('[data-visit-form]');
  var sheet = document.querySelector('[data-vt-sheet]');
  var saveSheet = document.querySelector('[data-vt-save]');
  if (!bar || !form || !sheet || !saveSheet || typeof sheet.showModal !== 'function' || !window.fetch) { return; }
  var words = JSON.parse((document.getElementById('vt-words') || {}).textContent || '{}');
  var lang = bar.getAttribute('data-lang') === 'en' ? 'en' : 'ar';
  var url = bar.getAttribute('data-url');
  var saveUrl = bar.getAttribute('data-save-url');
  var patient = bar.getAttribute('data-patient') || '';
  var FIELDS = ['complaint', 'examination', 'diagnosis', 'treatment'];
  var note = bar.querySelector('[data-vt-note]');
  var openButton = bar.querySelector('[data-vt-open]');
  var saveButton = document.querySelector('[data-vt-save-open]');
  var q = sheet.querySelector('[data-vt-q]');
  var results = sheet.querySelector('[data-vt-results]');
  var status = sheet.querySelector('[data-vt-status]');
  var chipsBox = sheet.querySelector('[data-vt-chips]');
  var states = {};
  sheet.querySelectorAll('[data-vt-state]').forEach(function (node) { states[node.getAttribute('data-vt-state')] = node; });
  var data = null, specialty = bar.getAttribute('data-default-specialty') || '', allLangs = false, query = '', loading = null, returnFocus = null;

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
  function join(list) { return list.join(lang === 'en' ? ', ' : '، '); }
  // Arabic and Latin text reduced the same way on both sides of a search (case, alef and ya forms, ta marbuta, harakat, Arabic digits)
  function fold(text) {
    return String(text || '').toLowerCase().replace(/[ً-ٰٟـ]/g, '').replace(/[آأإ]/g, 'ا')
      .replace(/ى/g, 'ي').replace(/ة/g, 'ه').replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); })
      .replace(/\s+/g, ' ').trim();
  }
  function box(name) { return document.getElementById(name); }
  function animal() { var select = document.getElementById('animal'); return select ? select.value : ''; }

  // ---- the sheet ----
  function show(state) {
    Object.keys(states).forEach(function (key) { states[key].hidden = key !== state; });
    results.hidden = state !== 'results';
    if (state === 'loading') { status.textContent = say('loading'); }
    else if (state === 'empty') { status.textContent = say('empty_title'); }
    else if (state === 'error') { status.textContent = say('error_title'); }
  }
  function open(event) {
    returnFocus = event && event.currentTarget ? event.currentTarget : openButton;
    sheet.showModal();
    document.documentElement.classList.add('hs-drug-locked');
    if (!data) { load(); } else { render(); }
    q.focus();
    q.select();
  }
  function close() { if (sheet.open) { sheet.close(); } }
  sheet.addEventListener('close', function () {
    document.documentElement.classList.remove('hs-drug-locked');
    (returnFocus || openButton).focus();
  });
  sheet.querySelector('[data-vt-close]').addEventListener('click', close);
  sheet.addEventListener('click', function (event) { if (event.target === sheet) { close(); } });  // the backdrop
  sheet.addEventListener('keydown', function (event) { if (event.key === 'Escape') { event.preventDefault(); close(); } });
  sheet.querySelector('[data-vt-retry]').addEventListener('click', function () { data = null; load(); q.focus(); });
  openButton.addEventListener('click', open);
  q.addEventListener('input', function () { query = fold(q.value); if (data) { render(); } });
  q.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {  // never saves the visit; with rows on screen it moves onto the first one
      event.preventDefault();
      var first = results.querySelector('button');
      if (first && !results.hidden) { first.focus(); }
    }
    if (event.key === 'ArrowDown') { var next = results.querySelector('button'); if (next && !results.hidden) { event.preventDefault(); next.focus(); } }
  });
  results.addEventListener('keydown', function (event) {
    var buttons = Array.prototype.slice.call(results.querySelectorAll('button'));
    var at = buttons.indexOf(document.activeElement);
    if (at < 0) { return; }
    if (event.key === 'ArrowDown' && at < buttons.length - 1) { event.preventDefault(); buttons[at + 1].focus(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); (at > 0 ? buttons[at - 1] : q).focus(); }
    if (event.key === 'Home') { event.preventDefault(); buttons[0].focus(); }
    if (event.key === 'End') { event.preventDefault(); buttons[buttons.length - 1].focus(); }
  });

  function load() {
    show('loading');
    var address = url + '?lang=' + lang + '&patient=' + encodeURIComponent(patient) + '&animal=' + encodeURIComponent(animal());
    var mine = loading = {};
    fetch(address, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (response) { if (!response.ok) { throw new Error(String(response.status)); } return response.json(); })
      .then(function (answer) {
        if (loading !== mine) { return; }
        data = answer;
        if (!specialty) { specialty = answer.default_specialty || ''; }
        drawChips();
        render();
      })
      .catch(function () { if (loading === mine) { show('error'); } });
  }
  function chip(label, pressed, onTap) {
    var button = el('button', 'vt-chip', label);
    button.type = 'button';
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    button.addEventListener('click', onTap);
    return button;
  }
  function drawChips() {
    var present = {}, otherLang = false;
    data.templates.forEach(function (t) { present[t.specialty] = true; if (t.lang !== lang) { otherLang = true; } });
    var options = (data.specialties || []).filter(function (s) { return present[s.code]; });
    if (specialty && !present[specialty]) { specialty = ''; }
    chipsBox.textContent = '';
    if (options.length > 1) {
      chipsBox.appendChild(chip(say('all_specialties'), !specialty, function () { specialty = ''; syncChips(); render(); }));
      options.forEach(function (s) {
        var button = chip(s.label, specialty === s.code, function () { specialty = s.code; syncChips(); render(); });
        button.setAttribute('data-specialty', s.code);
        chipsBox.appendChild(button);
      });
    }
    if (otherLang) {
      var other = chip(say('all_langs'), allLangs, function () { allLangs = !allLangs; other.setAttribute('aria-pressed', allLangs ? 'true' : 'false'); render(); });
      other.classList.add('vt-chip--lang');
      chipsBox.appendChild(other);
    }
    chipsBox.hidden = !chipsBox.childNodes.length;
  }
  function syncChips() {
    chipsBox.querySelectorAll('[data-specialty]').forEach(function (button) { button.setAttribute('aria-pressed', button.getAttribute('data-specialty') === specialty ? 'true' : 'false'); });
    var all = chipsBox.querySelector('.vt-chip:not([data-specialty]):not(.vt-chip--lang)');
    if (all) { all.setAttribute('aria-pressed', specialty ? 'false' : 'true'); }
  }
  function countText(n) {
    if (n === 1) { return say('count_one'); }
    if (n === 2) { return say('count_two'); }
    return say(n <= 10 ? 'count' : 'count_many', { n: n });
  }
  function preview(t) {
    var text = (t.complaint || t.examination || t.treatment || t.diagnosis || '').replace(/\s+/g, ' ').trim();
    return text.length > 110 ? text.slice(0, 107) + '…' : text;
  }
  function render() {
    var rows = data.templates.filter(function (t) {
      if (specialty && t.specialty !== specialty) { return false; }
      if (!allLangs && t.lang !== lang) { return false; }
      return !query || fold(t.name + ' ' + t.specialty_label).indexOf(query) >= 0;
    });
    results.textContent = '';
    if (!rows.length) { show('empty'); return; }
    rows.forEach(function (t) {
      var li = el('li');
      var button = el('button', 'hs-drug-row vt-row');
      button.type = 'button';
      var main = el('span', 'hs-drug-row__main');
      var line1 = el('span', 'hs-drug-row__l1');
      line1.appendChild(el('strong', '', t.name));
      var tags = el('span', 'vt-row__tags');
      tags.appendChild(el('span', 'hs-drug-chip vt-tag', t.specialty_label));
      tags.appendChild(el('span', 'hs-drug-chip ' + (t.mine ? 'vt-tag--mine' : 'vt-tag--shared'), t.mine ? say('mine') : (t.starter ? say('starter') : say('shared'))));
      if (t.lines.length) { tags.appendChild(el('span', 'hs-drug-chip vt-tag--meds', say('lines_n', { n: t.lines.length }))); }
      line1.appendChild(tags);
      main.appendChild(line1);
      var text = preview(t);
      if (text) { var p = el('span', 'hs-drug-row__l2 vt-row__preview', text); p.setAttribute('dir', 'auto'); main.appendChild(p); }
      button.appendChild(main);
      button.addEventListener('click', function () { apply(t); close(); });
      li.appendChild(button);
      results.appendChild(li);
    });
    show('results');
    status.textContent = countText(rows.length);
  }

  // ---- applying one: only what is empty, never what the doctor typed ----
  function apply(t) {
    var filled = [], kept = [];
    FIELDS.forEach(function (name) {
      var text = String(t[name] || '').trim();
      var field = box(name);
      if (!text || !field) { return; }
      if (field.value.trim() === '') {
        field.value = text;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        filled.push(say('f_' + name));
      } else {
        kept.push(say('f_' + name));
      }
    });
    var added = 0, cut = 0;
    var rx = window.HesbaRx;
    (t.lines || []).forEach(function (spec) {
      if (rx && rx.room() > 0 && rx.add(spec)) { added += 1; } else { cut += 1; }
    });
    var parts = [say('applied', { name: t.name })];
    if (filled.length) { parts.push(say('filled', { fields: join(filled) })); }
    if (added) { parts.push(cut ? say('added_cut', { n: added, m: cut, max: rx ? rx.max : '' }) : say('added', { n: added })); }
    else if (cut) { parts.push(say('added_cut', { n: 0, m: cut, max: rx ? rx.max : '' })); }
    if (kept.length) { parts.push(say('kept', { fields: join(kept) })); }
    if (!filled.length && !added && !cut) { parts.push(say('nothing_new')); }
    showNote(parts.join(' '));
  }
  function showNote(text, link) {
    note.textContent = text;
    if (link) {
      note.appendChild(document.createTextNode(' '));
      var a = el('a', '', say('review'));
      a.href = link;
      note.appendChild(a);
    }
    note.hidden = false;
    if (note.scrollIntoView) { note.scrollIntoView({ block: 'nearest' }); }
  }

  // ---- «احفظ ده كقالب» ----
  var saveForm = saveSheet.querySelector('[data-vt-save-form]');
  var saveName = saveSheet.querySelector('#vt-save-name');
  var saveError = saveSheet.querySelector('[data-vt-save-error]');
  var saveGo = saveSheet.querySelector('[data-vt-save-go]');
  function hasContent() {
    return FIELDS.some(function (name) { var field = box(name); return field && field.value.trim() !== ''; }) ||
      Array.prototype.some.call(form.querySelectorAll('[name="rx_name"], [name="rx_reference"]'), function (field) { return field.value.trim() !== ''; });
  }
  function openSave(event) {
    if (!hasContent()) { showNote(say('save_empty')); return; }
    returnFocus = event.currentTarget;
    saveError.hidden = true;
    saveSheet.showModal();
    document.documentElement.classList.add('hs-drug-locked');
    saveName.focus();
  }
  function closeSave() { if (saveSheet.open) { saveSheet.close(); } }
  if (saveButton) { saveButton.hidden = false; saveButton.addEventListener('click', openSave); }
  saveSheet.addEventListener('close', function () {
    document.documentElement.classList.remove('hs-drug-locked');
    if (returnFocus) { returnFocus.focus(); }
  });
  saveSheet.addEventListener('keydown', function (event) { if (event.key === 'Escape') { event.preventDefault(); closeSave(); } });
  saveSheet.addEventListener('click', function (event) { if (event.target === saveSheet) { closeSave(); } });
  saveSheet.querySelector('[data-vt-save-close]').addEventListener('click', closeSave);
  saveSheet.querySelector('[data-vt-save-cancel]').addEventListener('click', closeSave);
  saveForm.addEventListener('submit', function (event) {
    event.preventDefault();  // Enter in the name box saves the template, never the visit
    var name = saveName.value.trim();
    if (!name) { saveError.textContent = words.e_name; saveError.hidden = false; saveName.focus(); return; }
    var body = new FormData();
    body.append('name', name);
    body.append('specialty', saveSheet.querySelector('#vt-save-specialty').value);
    body.append('text_lang', lang);
    body.append('lang', lang);
    body.append('patient', patient);
    body.append('animal', animal());
    FIELDS.forEach(function (field) { body.append(field, (box(field) || {}).value || ''); });
    form.querySelectorAll('[name^="rx_"]').forEach(function (field) { body.append(field.name, field.value); });
    var token = form.querySelector('[name="csrfmiddlewaretoken"]');
    saveGo.disabled = true;
    fetch(saveUrl + '?lang=' + lang, { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-CSRFToken': token ? token.value : '', Accept: 'application/json' } })
      .then(function (response) { return response.json().then(function (answer) { return { ok: response.ok, answer: answer }; }); })
      .then(function (result) {
        saveGo.disabled = false;
        if (!result.ok || !result.answer.ok) { saveError.textContent = result.answer.error || say('save_failed'); saveError.hidden = false; return; }
        data = null;  // the sheet loads again, so the new template is in it
        saveName.value = '';
        closeSave();
        showNote(result.answer.message, result.answer.url);
      })
      .catch(function () { saveGo.disabled = false; saveError.textContent = say('save_failed'); saveError.hidden = false; });
  });

  bar.hidden = false;
})();
