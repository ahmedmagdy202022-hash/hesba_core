/* S10: the week calendar. Reads appointments/week.json, moves with POST appointments/<id>/move/.
   Desktop and tablet: drag a block (pointer events, so a finger on its grip works too) to another slot or employee.
   Phone: a day list with a "Move" button that opens a small sheet. The server decides every move; a refused one snaps back. */
(function () {
  'use strict';
  var root = document.querySelector('[data-week-calendar]');
  if (!root) { return; }

  var W = JSON.parse(document.getElementById('cal-words').textContent);
  var STAFF = JSON.parse(document.getElementById('cal-staff').textContent);
  var lang = root.getAttribute('data-lang') === 'en' ? 'en' : 'ar';
  var PX = 1.2;            // pixels per minute: an hour is 72px
  var SNAP = 15;           // minutes
  var DEFAULT_FROM = 8 * 60;
  var DEFAULT_TO = 21 * 60;
  var NS = 'http://www.w3.org/2000/svg';
  var DAY_NAMES = {
    ar: ['أحد', 'اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت'],
    en: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  };
  var CLOSED = { done: 1, cancelled: 1, no_show: 1 };
  var OFF = { cancelled: 1, no_show: 1 };

  var state = {
    canMove: root.getAttribute('data-can-move') === '1',
    weekStart: root.getAttribute('data-week-start'),
    day: root.getAttribute('data-day'),
    today: root.getAttribute('data-today'),
    items: [], columns: [], from: DEFAULT_FROM, to: DEFAULT_TO,
    filter: 'all', busy: false, loaded: false, token: 0
  };
  var nowAtLoad = Number(root.getAttribute('data-now')) || 0;
  var loadedAt = Date.now();

  var $ = function (sel) { return root.querySelector(sel); };
  var el = {
    sub: $('[data-cal-sub]'), range: $('[data-cal-range]'), strip: $('[data-cal-strip]'), chips: $('[data-cal-chips]'), grid: $('[data-cal-grid]'),
    list: $('[data-cal-list]'), state: $('[data-cal-state]'), stateText: $('[data-cal-state-text]'), err: $('[data-cal-error]'), ok: $('[data-cal-ok]'),
    scrim: $('[data-cal-scrim]'), sheet: $('[data-cal-sheet]'), sheetSub: $('[data-cal-sheet-sub]'), sheetNow: $('[data-cal-sheet-now]'),
    sheetDur: $('[data-cal-sheet-dur]'), sheetErr: $('[data-cal-sheet-error]'), mvStaff: $('[data-cal-mv-staff]'), mvDay: $('[data-cal-mv-day]'),
    mvTime: $('[data-cal-mv-time]')
  };

  /* ---------- small helpers ---------- */
  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }
  function icon(name) {
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'cal-ico');
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS(NS, 'use');
    use.setAttribute('href', '#cal-i-' + name);
    svg.appendChild(use);
    return svg;
  }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } }
  function pad(n) { return n < 10 ? '0' + n : String(n); }
  function fmt(min) { return pad(Math.floor(min / 60)) + ':' + pad(min % 60); }
  function utc(day) { var p = day.split('-'); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  function addDays(day, n) {
    var d = new Date(utc(day) + n * 86400000);
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }
  function weekdayOf(day) { return new Date(utc(day)).getUTCDay(); }
  function dayNumber(day) { return String(+day.split('-')[2]); }
  function longDate(day, withWeekday) {
    var opts = { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' };
    if (withWeekday) { opts.weekday = 'long'; }
    try { return new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : 'ar-EG-u-nu-latn', opts).format(new Date(utc(day))); } catch (e) { return day; }
  }
  function shortDate(day) {
    try { return new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : 'ar-EG-u-nu-latn', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(utc(day))); } catch (e) { return day; }
  }
  function countText(n) { return n + ' ' + (n === 1 ? W.count_one : W.count_many); }
  function fill(text, vars) { return text.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] !== undefined ? vars[k] : m; }); }
  function csrf() { var i = root.querySelector('input[name=csrfmiddlewaretoken]'); return i ? i.value : ''; }
  function urlFor(template, id) { return template.replace(/0\/(?=move\/|$)/, id + '/'); }
  function detailUrl(id) { return urlFor(root.getAttribute('data-detail-url'), id) + '?lang=' + lang; }
  function moveUrl(id) { return urlFor(root.getAttribute('data-move-url'), id); }

  function prepare(a) {
    // The ISO strings carry the server's own offset, so the wall-clock part is read from the text itself.
    var m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(a.start);
    a.date = m[1];
    a.startMin = (+m[2]) * 60 + (+m[3]);
    a.dur = Math.max(5, Math.round((Date.parse(a.end) - Date.parse(a.start)) / 60000));
    return a;
  }
  function itemById(id) { for (var i = 0; i < state.items.length; i++) { if (state.items[i].id === id) { return state.items[i]; } } return null; }
  function staffName(id) {
    if (id === null || id === undefined) { return W.unassigned; }
    for (var i = 0; i < state.columns.length; i++) { if (state.columns[i].id === id) { return state.columns[i].name; } }
    return '';
  }
  function dayItems(day) { return state.items.filter(function (a) { return a.date === day; }); }

  /* ---------- messages ---------- */
  function showError(message, extra) {
    el.ok.hidden = true;
    clear(el.err);
    el.err.appendChild(icon('alert'));
    var box = node('div');
    box.appendChild(node('b', null, message));
    if (extra) { box.appendChild(node('span', null, extra)); }
    el.err.appendChild(box);
    el.err.hidden = false;
    window.clearTimeout(showError.timer);
    showError.timer = window.setTimeout(function () { el.err.hidden = true; }, 9000);
  }
  function showOk(message) {
    el.err.hidden = true;
    clear(el.ok);
    el.ok.appendChild(icon('check'));
    el.ok.appendChild(node('b', null, message));
    el.ok.hidden = false;
    window.clearTimeout(showOk.timer);
    showOk.timer = window.setTimeout(function () { el.ok.hidden = true; }, 4000);
  }
  function hideMessages() { el.err.hidden = true; el.ok.hidden = true; }

  /* ---------- loading ---------- */
  function load() {
    var token = ++state.token;
    el.grid.setAttribute('aria-busy', 'true');
    el.state.hidden = true;
    var url = root.getAttribute('data-week-url') + '?start=' + encodeURIComponent(state.weekStart) + '&lang=' + lang;
    return fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } }).then(function (response) {
      return response.json().then(function (body) { return { ok: response.ok, body: body }; });
    }).then(function (result) {
      if (token !== state.token) { return; }
      if (!result.ok) { throw new Error(result.body && result.body.error || W.load_failed); }
      var items = [];
      var seen = {};
      var extra = [];
      result.body.staff.forEach(function (group) {
        if (group.staff !== null && !seen[group.staff]) {
          seen[group.staff] = true;
          if (!STAFF.some(function (s) { return s.id === group.staff; })) { extra.push({ id: group.staff, name: group.name }); }
        }
        group.appointments.forEach(function (a) { items.push(prepare(a)); });
      });
      state.items = items;
      var columns = STAFF.concat(extra);
      if (items.some(function (a) { return a.staff === null; })) { columns.push({ id: null, name: W.unassigned }); }
      state.columns = columns;
      state.loaded = true;
      var min = DEFAULT_FROM, max = DEFAULT_TO;
      items.forEach(function (a) {
        min = Math.min(min, Math.floor(a.startMin / 60) * 60);
        max = Math.max(max, Math.min(24 * 60, Math.ceil((a.startMin + a.dur) / 60) * 60));
      });
      state.from = min; state.to = max;
      el.grid.setAttribute('aria-busy', 'false');
      render();
    }).catch(function (error) {
      if (token !== state.token) { return; }
      el.grid.setAttribute('aria-busy', 'false');
      el.stateText.textContent = (error && error.message && error.message !== 'Failed to fetch') ? error.message : W.load_failed;
      el.state.hidden = false;
    });
  }

  /* ---------- rendering ---------- */
  function render() {
    renderHeader();
    renderStrip();
    renderChips();
    renderGrid();
    renderList();
  }

  function renderHeader() {
    var end = addDays(state.weekStart, 6);
    el.range.textContent = fill(W.week_of, { a: shortDate(state.weekStart), b: shortDate(end) });
    var count = state.loaded ? ' · ' + countText(dayItems(state.day).length) : '';
    el.sub.textContent = longDate(state.day, true) + count;
  }

  function renderStrip() {
    clear(el.strip);
    for (var i = 0; i < 7; i++) {
      (function (day) {
        var n = dayItems(day).filter(function (a) { return !OFF[a.status]; }).length;
        var countLabel = state.loaded ? (n ? countText(n) : W.off_count) : '';
        var b = node('button', 'cal-day' + (day === state.day ? ' is-sel' : '') + (day === state.today ? ' is-today' : ''));
        b.type = 'button';
        b.setAttribute('role', 'tab');
        b.setAttribute('aria-selected', day === state.day ? 'true' : 'false');
        b.setAttribute('aria-label', longDate(day, true) + (countLabel ? ', ' + countLabel : ''));
        var strong = node('b', null, DAY_NAMES[lang][weekdayOf(day)] + ' ');
        strong.appendChild(node('bdi', null, dayNumber(day)));
        b.appendChild(strong);
        b.appendChild(node('small', null, countLabel));
        b.addEventListener('click', function () { state.day = day; hideMessages(); render(); });
        el.strip.appendChild(b);
      })(addDays(state.weekStart, i));
    }
  }

  function renderChips() {
    clear(el.chips);
    var options = [{ id: 'all', name: W.all }].concat(state.columns.map(function (c) { return { id: c.id === null ? 'none' : String(c.id), name: c.name }; }));
    options.forEach(function (o) {
      var b = node('button', 'cal-chip' + (String(state.filter) === String(o.id) ? ' is-sel' : ''), o.name);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(state.filter) === String(o.id) ? 'true' : 'false');
      b.addEventListener('click', function () { state.filter = o.id; renderChips(); renderList(); });
      el.chips.appendChild(b);
    });
  }

  function movable(a) { return state.canMove && !CLOSED[a.status]; }
  function kind(a) { return OFF[a.status] ? 'k-off' : a.status === 'done' ? 'k-done' : 'k-' + a.status; }
  function stamp(a) { return fmt(a.startMin) + '–' + fmt(a.startMin + a.dur); }

  function renderGrid() {
    clear(el.grid);
    var cols = state.columns;
    var height = (state.to - state.from) * PX;
    var inner = node('div', 'cal-inner');
    inner.style.setProperty('--cal-n', String(Math.max(1, cols.length)));
    var head = node('div', 'cal-head');
    head.appendChild(node('div'));
    var items = dayItems(state.day);
    cols.forEach(function (c) {
      var mine = items.filter(function (a) { return a.staff === c.id && !OFF[a.status]; }).length;
      var cell = node('div', 'cal-staff');
      cell.appendChild(node('span', 'cal-av', (c.name || '?').charAt(0)));
      var t = node('span', 'cal-staff__t');
      t.appendChild(node('b', null, c.name));
      t.appendChild(node('small', null, countText(mine)));
      cell.appendChild(t);
      head.appendChild(cell);
    });
    inner.appendChild(head);

    var body = node('div', 'cal-body');
    var times = node('div', 'cal-times');
    times.style.height = height + 'px';
    for (var h = state.from; h < state.to; h += 60) {
      var label = node('span', 'cal-time');
      label.style.top = ((h - state.from) * PX + 4) + 'px';
      label.appendChild(node('bdi', null, fmt(h)));
      times.appendChild(label);
    }
    body.appendChild(times);
    cols.forEach(function (c, index) {
      var col = node('div', 'cal-col');
      col.style.height = height + 'px';
      col.setAttribute('data-col', String(index));
      items.filter(function (a) { return a.staff === c.id; }).forEach(function (a) { col.appendChild(block(a)); });
      body.appendChild(col);
    });
    var nowMin = nowAtLoad + Math.floor((Date.now() - loadedAt) / 60000);
    if (state.day === state.today && nowMin >= state.from && nowMin <= state.to) {
      var line = node('div', 'cal-now');
      line.style.top = ((nowMin - state.from) * PX) + 'px';
      line.appendChild(node('span', null, W.current + ' ' + fmt(nowMin)));
      var wrap = node('div');
      wrap.style.cssText = 'position:absolute;inset:0;pointer-events:none';
      wrap.appendChild(line);
      body.appendChild(wrap);
    }
    inner.appendChild(body);
    el.grid.appendChild(inner);
  }

  function block(a) {
    var can = movable(a);
    var blk = node('div', 'cal-blk ' + kind(a) + (can ? ' is-movable' : '') + (a.dur * PX < 50 ? ' is-short' : ''));
    blk.setAttribute('data-id', String(a.id));
    blk.style.top = ((a.startMin - state.from) * PX) + 'px';
    blk.style.height = Math.max(20, a.dur * PX - 3) + 'px';
    blk.title = [a.customer, a.service, stamp(a), a.status_label].filter(Boolean).join(' · ');
    var l1 = node('div', 'cal-blk__l1');
    var link = node('a', 'cal-blk__name', a.customer);
    link.href = detailUrl(a.id);
    link.draggable = false;
    l1.appendChild(link);
    l1.appendChild(node('span', 'cal-st s-' + a.status, a.status_label));
    if (CLOSED[a.status]) {
      var lock = icon('lock');
      lock.setAttribute('role', 'img');
      lock.removeAttribute('aria-hidden');
      lock.setAttribute('aria-label', W.closed);
      l1.appendChild(lock);
    } else if (can) {
      var grip = node('button', 'cal-grip');
      grip.type = 'button';
      grip.setAttribute('aria-label', W.drag_handle + ': ' + a.customer);
      grip.appendChild(icon('grip'));
      grip.addEventListener('click', function (ev) {
        if (blk.getAttribute('data-dragged')) { ev.preventDefault(); return; }
        openSheet(a, grip);
      });
      l1.appendChild(grip);
    }
    blk.appendChild(l1);
    var l2 = node('div', 'cal-blk__l2');
    var time = node('bdi', null, stamp(a));
    time.setAttribute('dir', 'ltr');
    l2.appendChild(time);
    if (a.service) { l2.appendChild(node('span', null, a.service)); }
    blk.appendChild(l2);
    if (can) { blk.addEventListener('pointerdown', function (ev) { dragStart(ev, blk, a); }); }
    blk.addEventListener('click', function (ev) {
      if (blk.getAttribute('data-dragged')) { ev.preventDefault(); ev.stopPropagation(); }
    }, true);
    return blk;
  }

  function renderList() {
    clear(el.list);
    var items = dayItems(state.day).filter(function (a) {
      if (state.filter === 'all') { return true; }
      return state.filter === 'none' ? a.staff === null : String(a.staff) === String(state.filter);
    }).sort(function (x, y) { return x.startMin - y.startMin; });
    if (!items.length) {
      var empty = node('li', 'cal-empty', state.loaded ? W.empty_day : W.loading);
      el.list.appendChild(empty);
      return;
    }
    items.forEach(function (a) {
      var li = node('li', 'cal-item ' + kind(a));
      li.setAttribute('data-id', String(a.id));
      var t = node('div', 'cal-item__time');
      var b = node('b'); var bdi = node('bdi', null, fmt(a.startMin)); b.appendChild(bdi);
      var s = node('small'); s.appendChild(node('bdi', null, fmt(a.startMin + a.dur)));
      t.appendChild(b); t.appendChild(s);
      var main = node('div', 'cal-item__main');
      var l1 = node('div', 'cal-item__l1');
      var link = node('a', null, a.customer);
      link.href = detailUrl(a.id);
      l1.appendChild(link);
      l1.appendChild(node('span', 'cal-st s-' + a.status, a.status_label));
      main.appendChild(l1);
      main.appendChild(node('div', 'cal-item__l2', [a.service, staffName(a.staff)].filter(Boolean).join(' · ')));
      if (movable(a)) {
        var btn = node('button', 'op-secondary cal-mv');
        btn.type = 'button';
        btn.appendChild(icon('move'));
        btn.appendChild(document.createTextNode(W.move));
        btn.addEventListener('click', function () { openSheet(a, btn); });
        main.appendChild(btn);
      } else if (CLOSED[a.status]) {
        var lock = node('div', 'cal-lock');
        lock.appendChild(icon('lock'));
        lock.appendChild(node('span', null, W.closed));
        main.appendChild(lock);
      }
      li.appendChild(t); li.appendChild(main);
      el.list.appendChild(li);
    });
  }

  /* ---------- moving (shared by drag and the sheet) ---------- */
  function iso(date, min) { return date + 'T' + fmt(min) + ':00'; }

  /* Resolves {ok:true,body} or {ok:false,message,status}. Never rejects. */
  function postMove(a, target) {
    var form = new URLSearchParams();
    form.set('start', iso(target.date, target.min));
    form.set('lang', lang);
    if (target.staff !== undefined && target.staff !== a.staff && target.staff !== null) { form.set('staff', String(target.staff)); }
    return fetch(moveUrl(a.id), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf(), 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString()
    }).then(function (response) {
      return response.json().then(function (body) { return { response: response, body: body }; }, function () { return { response: response, body: {} }; });
    }).then(function (r) {
      if (r.response.ok && r.body.ok) { return { ok: true, body: r.body }; }
      return { ok: false, status: r.response.status, message: r.body && r.body.error || W.network };
    }, function () { return { ok: false, status: 0, message: W.network }; });
  }

  function applyMoved(a, body) {
    a.staff = body.staff === undefined ? a.staff : body.staff;
    a.start = body.start; a.end = body.end;
    prepare(a);
  }

  function afterMove(a, result) {
    if (result.ok) {
      applyMoved(a, result.body);
      render();
      markLanded(a.id);
      showOk(W.moved);
      return;
    }
    if (result.status === 403) { state.canMove = false; }
    render();
    markBack(a.id);
    showError(result.message, result.status === 0 ? '' : W.back);
  }
  function markLanded(id) {
    root.querySelectorAll('[data-id="' + id + '"]').forEach(function (n) { n.classList.add('is-landed'); });
    window.setTimeout(function () { root.querySelectorAll('[data-id="' + id + '"]').forEach(function (n) { n.classList.remove('is-landed'); }); }, 1800);
  }
  function markBack(id) {
    root.querySelectorAll('[data-id="' + id + '"]').forEach(function (n) { n.classList.add('is-back'); });
    window.setTimeout(function () { root.querySelectorAll('[data-id="' + id + '"]').forEach(function (n) { n.classList.remove('is-back'); }); }, 2500);
  }

  /* ---------- drag ---------- */
  var drag = null;
  function colRects() {
    return Array.prototype.map.call(el.grid.querySelectorAll('.cal-col'), function (c) { return c.getBoundingClientRect(); });
  }
  function dragStart(ev, blk, a) {
    if (state.busy || drag) { return; }
    if (ev.pointerType === 'mouse' && ev.button !== 0) { return; }
    // A finger drags from the grip only, so the rest of the block still scrolls the page.
    if (ev.pointerType === 'touch' && !ev.target.closest('.cal-grip')) { return; }
    var col = blk.parentNode;
    drag = {
      a: a, blk: blk, id: ev.pointerId, x0: ev.clientX, y0: ev.clientY, scroll0: window.pageYOffset, active: false,
      col0: Number(col.getAttribute('data-col')), colEl0: col, rects: null, target: null, ghost: null, zone: null, label: null,
      top0: (a.startMin - state.from) * PX
    };
    try { blk.setPointerCapture(ev.pointerId); } catch (e) { /* not supported: the document listeners below still work */ }
    document.addEventListener('pointermove', dragMove);
    document.addEventListener('pointerup', dragEnd);
    document.addEventListener('pointercancel', dragCancel);
  }
  function dragActivate() {
    var d = drag;
    d.active = true;
    d.rects = colRects();
    d.ghost = d.blk.cloneNode(true);
    d.ghost.className = d.blk.className.replace('is-movable', '') + ' is-ghost';
    d.ghost.removeAttribute('data-id');
    d.colEl0.insertBefore(d.ghost, d.blk);
    d.blk.classList.add('is-float');
    d.label = node('span', 'cal-float');
    d.blk.appendChild(d.label);
    d.blk.setAttribute('data-dragged', '1');
    document.body.classList.add('cal-dragging');
    d.zone = node('div', 'cal-zone');
  }
  function pickColumn(x) {
    var rects = drag.rects, best = drag.col0, dist = Infinity;
    for (var i = 0; i < rects.length; i++) {
      var r = rects[i];
      var d = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
      if (d < dist) { dist = d; best = i; }
    }
    // Nobody can be moved into the "unassigned" lane: the server keeps the employee when none is sent.
    if (state.columns[best].id === null && best !== drag.col0) { best = drag.col0; }
    return best;
  }
  function dragMove(ev) {
    var d = drag;
    if (!d || ev.pointerId !== d.id) { return; }
    var dx = ev.clientX - d.x0, dy = ev.clientY - d.y0 + (window.pageYOffset - d.scroll0);
    if (!d.active) {
      if (Math.abs(dx) < 5 && Math.abs(dy) < 5) { return; }
      dragActivate();
    }
    ev.preventDefault();
    if (ev.clientY > window.innerHeight - 50) { window.scrollBy(0, 14); } else if (ev.clientY < 50) { window.scrollBy(0, -14); }
    var raw = (d.top0 + dy) / PX + state.from;
    var min = Math.round(raw / SNAP) * SNAP;
    min = Math.max(state.from, Math.min(min, Math.min(24 * 60, state.to) - d.a.dur));
    var index = pickColumn(ev.clientX);
    var target = state.columns[index];
    d.target = { col: index, staff: target.id, min: min, date: d.a.date };
    var shiftY = (min - d.a.startMin) * PX;
    var shiftX = d.rects[index].left - d.rects[d.col0].left;
    d.blk.style.transform = 'translate(' + shiftX + 'px,' + shiftY + 'px)';
    var topPx = (min - state.from) * PX;
    d.label.textContent = fill(W.moved_to, { staff: target.name, time: fmt(min) });
    d.label.classList.toggle('is-below', topPx < 40);
    d.zone.style.top = topPx + 'px';
    d.zone.style.height = (d.a.dur * PX) + 'px';
    var targetCol = el.grid.querySelectorAll('.cal-col')[index];
    if (d.zone.parentNode !== targetCol) { targetCol.appendChild(d.zone); }
  }
  function dragCleanup(keepBlock) {
    var d = drag;
    document.removeEventListener('pointermove', dragMove);
    document.removeEventListener('pointerup', dragEnd);
    document.removeEventListener('pointercancel', dragCancel);
    document.body.classList.remove('cal-dragging');
    drag = null;
    if (d && d.active && !keepBlock) {
      d.blk.classList.remove('is-float');
      d.blk.style.transform = '';
      if (d.label && d.label.parentNode) { d.label.parentNode.removeChild(d.label); }
    }
    if (d && d.active) {
      if (d.ghost && d.ghost.parentNode) { d.ghost.parentNode.removeChild(d.ghost); }
      if (d.zone && d.zone.parentNode) { d.zone.parentNode.removeChild(d.zone); }
      window.setTimeout(function () { d.blk.removeAttribute('data-dragged'); }, 60);
    }
    return d;
  }
  function dragCancel() { dragCleanup(false); }
  function dragEnd(ev) {
    var d = drag;
    if (!d || ev.pointerId !== d.id) { return; }
    if (!d.active || !d.target) { dragCleanup(false); return; }
    var target = d.target, a = d.a;
    var unchanged = target.min === a.startMin && target.staff === a.staff;
    if (unchanged) { dragCleanup(false); return; }
    // Keep the block where it was dropped, dimmed, while the server decides.
    d.blk.classList.add('is-pending');
    d.blk.classList.remove('is-float');
    if (d.label && d.label.parentNode) { d.label.parentNode.removeChild(d.label); }
    dragCleanup(true);
    state.busy = true;
    postMove(a, { date: a.date, min: target.min, staff: target.staff }).then(function (result) {
      state.busy = false;
      afterMove(a, result);
    });
  }

  /* ---------- the move-to sheet ---------- */
  var sheet = { a: null, opener: null };
  function openSheet(a, opener) {
    if (state.busy) { return; }
    sheet.a = a; sheet.opener = opener;
    el.sheetSub.textContent = [a.customer, a.service].filter(Boolean).join(' · ');
    el.sheetNow.textContent = staffName(a.staff) + ' · ' + longDate(a.date, true) + ' · \u2066' + stamp(a) + '\u2069';  // isolate the range so RTL does not reverse it
    el.sheetDur.textContent = fill(W.duration_fixed, { n: a.dur });
    clear(el.mvStaff);
    state.columns.forEach(function (c) {
      if (c.id === null && a.staff !== null) { return; }
      var o = node('option', null, c.name);
      o.value = c.id === null ? '' : String(c.id);
      if (c.id === a.staff) { o.selected = true; }
      el.mvStaff.appendChild(o);
    });
    clear(el.mvDay);
    for (var i = 0; i < 7; i++) {
      var day = addDays(state.weekStart, i);
      var opt = node('option', null, longDate(day, true));
      opt.value = day;
      if (day === a.date) { opt.selected = true; }
      el.mvDay.appendChild(opt);
    }
    el.mvTime.value = fmt(a.startMin);
    el.sheetErr.hidden = true;
    el.scrim.hidden = false;
    el.sheet.hidden = false;
    el.mvStaff.focus();
  }
  function closeSheet() {
    el.sheet.hidden = true; el.scrim.hidden = true;
    var opener = sheet.opener;
    sheet.a = null; sheet.opener = null;
    if (opener && document.body.contains(opener)) { opener.focus(); }
  }
  function sheetGo() {
    var a = sheet.a;
    if (!a || state.busy) { return; }
    var m = /^(\d{1,2}):(\d{2})/.exec(el.mvTime.value || '');
    if (!m || +m[1] > 23 || +m[2] > 59) {
      el.sheetErr.textContent = W.bad_time; el.sheetErr.hidden = false; el.mvTime.focus(); return;
    }
    var min = (+m[1]) * 60 + (+m[2]);
    var staff = el.mvStaff.value === '' ? null : Number(el.mvStaff.value);
    var date = el.mvDay.value;
    if (min === a.startMin && staff === a.staff && date === a.date) { closeSheet(); return; }
    state.busy = true;
    el.sheetErr.hidden = true;
    postMove(a, { date: date, min: min, staff: staff }).then(function (result) {
      state.busy = false;
      if (!result.ok) {
        if (result.status === 403) { closeSheet(); afterMove(a, result); return; }
        el.sheetErr.textContent = result.message; el.sheetErr.hidden = false; return;
      }
      sheet.opener = null;
      closeSheet();
      afterMove(a, result);
      var again = root.querySelector('[data-id="' + a.id + '"] .cal-mv, [data-id="' + a.id + '"] .cal-grip');
      if (again) { again.focus({ preventScroll: true }); }
    });
  }
  root.querySelector('[data-cal-mv-go]').addEventListener('click', sheetGo);
  root.querySelector('[data-cal-mv-cancel]').addEventListener('click', closeSheet);
  el.scrim.addEventListener('click', closeSheet);
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !el.sheet.hidden) { closeSheet(); }
    if (ev.key === 'Enter' && !el.sheet.hidden && ev.target && ev.target.tagName === 'INPUT') { ev.preventDefault(); sheetGo(); }
  });

  /* ---------- week navigation ---------- */
  function go(weekStart, day) {
    if (!el.sheet.hidden) { closeSheet(); }
    state.weekStart = weekStart; state.day = day;
    state.loaded = false;
    hideMessages();
    try {
      var url = new URL(window.location.href);
      url.searchParams.set('day', day);
      window.history.replaceState(null, '', url.toString());
    } catch (e) { /* the address bar is a convenience only */ }
    renderHeader(); renderStrip();
    load();
  }
  root.querySelector('[data-cal-prev]').addEventListener('click', function () { go(addDays(state.weekStart, -7), addDays(state.day, -7)); });
  root.querySelector('[data-cal-next]').addEventListener('click', function () { go(addDays(state.weekStart, 7), addDays(state.day, 7)); });
  root.querySelector('[data-cal-today]').addEventListener('click', function () {
    var offset = (weekdayOf(state.today) - 6 + 7) % 7;   // the week starts on Saturday
    go(addDays(state.today, -offset), state.today);
  });
  root.querySelector('[data-cal-retry]').addEventListener('click', function () { load(); });

  render();
  load();
})();
