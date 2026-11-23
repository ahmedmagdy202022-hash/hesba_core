/* E1: the scan screen. One box takes a hardware scanner (it types the code, then Enter) and the phone camera (camera_scan.js writes the
 * code into the same box and sends a synthetic Enter). A synthetic key press does not submit a form, so Enter is handled here, not by the form.
 * The server decides everything (who, which class, present or late); this file only sends the code and draws the answer. */
(function () {
  'use strict';
  // The nursery's board: the same box, but a scan is an ordinary form post (the page redraws its counts and rows).
  var nursery = document.querySelector('[data-nursery-scan]');
  if (nursery) {
    var nbox = nursery.querySelector('[data-scan-box]');
    nbox.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.keyCode === 13) { event.preventDefault(); if (nbox.value.trim()) { nursery.requestSubmit ? nursery.requestSubmit() : nursery.submit(); } }
    });
    var picking = document.querySelector('[data-pick-focus]');   // a child already inside: go straight to the pick-up choice
    if (picking) { picking.scrollIntoView({ block: 'center' }); picking.focus(); } else { nbox.focus(); }
    document.addEventListener('DOMContentLoaded', function () { if (!document.querySelector('[data-pick-focus]')) { nbox.focus(); } });
    return;
  }
  var root = document.querySelector('[data-edu-scan]');
  if (!root) { return; }
  var form = root.querySelector('[data-scan-form]');
  var box = root.querySelector('[data-scan-box]');
  var result = root.querySelector('[data-scan-result]');
  var ready = result.querySelector('[data-result-ready]');
  var card = result.querySelector('[data-result-card]');
  var recent = root.querySelector('[data-scan-recent]');
  var log = root.querySelector('[data-scan-log]');
  var choices = result.querySelector('[data-result-choices]');
  var T = function (key) { return root.getAttribute('data-t-' + key) || ''; };
  var lang = root.getAttribute('data-lang') || 'ar';
  var busy = false, pending = '', queue = [];
  var waiting = root.querySelector('[data-scan-waiting]');

  function el(id) { return result.querySelector('[data-result-' + id + ']'); }
  function camera() { return document.querySelector('.hs-cam:not([hidden])'); }
  function focusBox() { if (camera()) { return; } try { box.focus({ preventScroll: true }); } catch (e) { box.focus(); } }

  function stateOf(data) {
    if (data.status === 'ok') { return data.presence === 'late' ? 'late' : 'present'; }
    if (data.status === 'again') { return 'again'; }
    if (data.status === 'choose') { return 'choose'; }
    return 'bad';
  }

  function render(data) {
    var state = stateOf(data);
    result.setAttribute('data-state', state);
    result.setAttribute('data-status', data.status || '');
    ready.hidden = true; card.hidden = false;
    el('name').textContent = data.student ? data.student.name : (data.message || '');
    el('group').textContent = data.group ? (data.group.course + ' — ' + data.group.name) : '';
    var mark = el('mark');
    mark.textContent = data.presence_label || '';
    mark.hidden = !data.presence_label;
    el('time').textContent = data.time || '';
    // for a plain refusal the message is the headline; otherwise it is the small note under the mark
    el('note').textContent = data.student ? (data.message || '') : '';
    choices.textContent = '';
    var list = data.status === 'choose' ? data.choices : (data.status === 'no_class' ? data.groups : []);
    (list || []).forEach(function (group) {
      var line = (group.time ? group.time + ' · ' : '') + group.course + ' — ' + group.name;
      if (data.status === 'choose') {
        var button = document.createElement('button');
        button.type = 'button'; button.className = 'es-choice'; button.setAttribute('data-choice', group.id); button.textContent = line;
        button.addEventListener('click', function () { send(pending, group.id); });
        choices.appendChild(button);
      } else {
        var item = document.createElement('p'); item.className = 'es-groupline'; item.textContent = line; choices.appendChild(item);
      }
    });
    if (data.status !== 'choose') { pending = ''; }
    if (data.status === 'ok' || data.status === 'again') { remember(data); }
    var cam = camera();   // the camera covers the screen: say it there too, so the person scanning sees who it was
    if (cam) {
      cam.setAttribute('data-scan-state', state);
      var line = cam.querySelector('.hs-cam__status');
      if (line) { line.textContent = (data.student ? data.student.name : '') + (data.presence_label ? ' — ' + data.presence_label + (data.time ? ' ' + data.time : '') : (data.message ? (data.student ? ' — ' : '') + data.message : '')); line.classList.remove('is-bad'); }
    }
  }

  function remember(data) {
    recent.hidden = false;
    var item = document.createElement('li');
    item.className = 'es-log--' + (data.presence || 'again');
    item.textContent = data.student.name + ' · ' + (data.presence_label || '') + (data.time ? ' ' + data.time : '');
    log.insertBefore(item, log.firstChild);
    while (log.children.length > 6) { log.removeChild(log.lastChild); }
  }

  function fail(text) {
    render({ status: 'error', message: text });
  }

  function showWaiting() {
    if (!waiting) { return; }
    waiting.hidden = !queue.length;
    waiting.textContent = queue.length ? T('waiting').replace('{n}', queue.length) : '';
  }

  // One request at a time, in the order the cards were shown. A card scanned while the last one is still on its way, or while a
  // «which class?» choice is waiting for a tap, is kept in line and sent after it: a scan is never dropped.
  function send(code, group) {
    code = String(code || '').trim();
    if (!code) { return; }
    if (busy || (pending && !group)) { queue.push(code); showWaiting(); return; }
    busy = true; pending = code;
    result.setAttribute('data-state', 'working');
    var body = new FormData(form);
    body.set('code', code); body.set('lang', lang);
    if (group) { body.set('group', group); } else { body.delete('group'); }
    fetch(form.getAttribute('action'), { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } })
      .then(function (response) { return response.json().then(function (data) { return data; }, function () { return { status: 'error', message: T('failed') }; }); })
      .then(function (data) { render(data); }, function () { fail(T('offline')); })
      .then(function () { busy = false; focusBox(); next(); });
  }

  function next() {
    if (busy || pending || !queue.length) { showWaiting(); return; }   // a choice still waiting for its tap goes first
    var code = queue.shift();
    showWaiting();
    send(code);
  }

  // The code leaves the box the moment Enter arrives, so the next card types into an empty box and nothing typed meanwhile is wiped later.
  // ED-02: pressing «سجّل» (or Enter) on an empty box says what to do in the result area instead of doing nothing.
  function take() {
    var code = box.value; box.value = '';
    if (!String(code).trim()) { if (!busy && !pending) { render({ status: 'empty', message: T('empty') }); } return; }  // never over a waiting «which class?» choice
    send(code);
  }

  box.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' || event.keyCode === 13) { event.preventDefault(); take(); }
  });
  form.addEventListener('submit', function (event) { event.preventDefault(); take(); });

  // The box stays focused for a hardware scanner; no on-screen keyboard unless «اكتب الكود» is tapped.
  var typeButton = root.querySelector('[data-scan-type]');
  typeButton.addEventListener('click', function () {
    var typing = box.getAttribute('inputmode') !== 'text';
    box.setAttribute('inputmode', typing ? 'text' : 'none');
    typeButton.setAttribute('aria-pressed', typing ? 'true' : 'false');
    box.blur(); focusBox();
  });
  // camera_scan.js (deferred, after this file) moves the box into its wrapper, and a moved box loses the focus: take it back once every script ran.
  document.addEventListener('DOMContentLoaded', function () { focusBox(); });
  window.addEventListener('load', function () { if (!document.activeElement || document.activeElement === document.body) { focusBox(); } });
  focusBox();
}());
