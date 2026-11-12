/*
 * Restaurants R4: the kitchen screen. Polls the feed every 5 seconds and draws one card per ticket.
 * Tap a dish = ready (tap again = not ready); tap the card = the ticket is done; «رجّع» brings back the last finished ticket.
 * A void after the kitchen fired stays struck through on the card until the kitchen taps it («تمام، شفته»).
 * Everything the server sends is drawn with textContent, never as markup.
 */
(function () {
  'use strict';
  var root = document.querySelector('[data-kds]');
  if (!root) { return; }
  var feedUrl = root.getAttribute('data-feed');
  var actUrl = root.getAttribute('data-act');
  var station = root.getAttribute('data-station') || '';
  var lang = root.getAttribute('data-lang') || 'ar';
  var csrf = root.getAttribute('data-csrf') || '';
  var words = {};
  try { words = JSON.parse(document.getElementById('kds-words').textContent); } catch (e) { words = {}; }
  var board = root.querySelector('[data-board]');
  var empty = root.querySelector('[data-empty]');
  var status = root.querySelector('[data-live]');
  var recall = root.querySelector('[data-recall]');
  var clock = root.querySelector('[data-clock]');
  var INTERVAL = 5000;
  var known = null;     // ticket ids seen so far (null before the first answer, so the first load does not flash every card)
  var signature = '';
  var data = null;
  var fetchedAt = 0;
  var inflight = false;
  var stopped = false;

  function query() { return '?lang=' + encodeURIComponent(lang) + (station ? '&station=' + encodeURIComponent(station) : ''); }
  function say(text) { status.textContent = text || ''; }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function minutes(card) {
    var seconds = card.age + Math.max(0, Math.round((Date.now() - fetchedAt) / 1000));
    return Math.floor(seconds / 60) + ' ' + (words.min || 'min');
  }

  function levelClass(card) {
    var cls = 'kds-card kds-card--' + card.level;
    if (card.cancelled) { cls += ' kds-card--cancelled'; }
    return cls;
  }

  function lineNode(line) {
    var button = el('button', 'kds-line');
    button.type = 'button';
    button.setAttribute('data-line', line.id);
    var name = el('span', 'kds-line__name');
    name.appendChild(el('span', 'kds-line__qty', line.quantity + ' ×'));
    name.appendChild(document.createTextNode(' ' + line.name));
    button.appendChild(name);
    if (line.note) { button.appendChild(el('span', 'kds-line__note', line.note)); }
    if (line.void) {
      button.className += ' kds-line--void' + (line.seen ? ' kds-line--seen' : '');
      button.appendChild(el('span', 'kds-line__tag', (words.voided || '') + (line.reason ? ': ' + line.reason : '') + (line.seen ? '' : ' · ' + (words.seen || ''))));
    } else if (line.ready) {
      button.className += ' kds-line--ready';
    }
    var item = el('li');
    item.appendChild(button);
    return item;
  }

  function cardNode(card, fresh) {
    var node = el('article', levelClass(card) + (fresh ? ' kds-card--new' : ''));
    node.setAttribute('data-card', card.id);
    if (card.cancelled) { node.appendChild(el('p', 'kds-banner', words.cancelled || '')); }
    var head = el('div', 'kds-card__head');
    head.appendChild(el('h2', 'kds-card__where', card.where));
    var age = el('span', 'kds-card__age', minutes(card));
    age.setAttribute('data-age', '');
    head.appendChild(age);
    node.appendChild(head);
    var meta = el('p', 'kds-card__meta');
    var number = el('span', '', card.number);
    number.setAttribute('dir', 'ltr');
    meta.appendChild(number);
    meta.appendChild(el('span', '', (words.ticket || '') + ' ' + card.sequence));
    if (card.station) { meta.appendChild(el('span', '', card.station)); }
    if (card.waiter) { meta.appendChild(el('span', '', card.waiter)); }
    var sent = el('span', '', card.sent);
    sent.setAttribute('dir', 'ltr');
    meta.appendChild(sent);
    node.appendChild(meta);
    if (card.notes) { node.appendChild(el('p', 'kds-line__note', card.notes)).style.padding = '0 14px 8px'; }
    var list = el('ul', 'kds-lines');
    card.lines.forEach(function (line) { list.appendChild(lineNode(line)); });
    node.appendChild(list);
    var foot = el('div', 'kds-card__foot');
    var done = el('button', 'kds-done', words.done || '');
    done.type = 'button';
    done.setAttribute('data-done', card.id);
    foot.appendChild(done);
    node.appendChild(foot);
    return node;
  }

  function sign(tickets) {
    return JSON.stringify(tickets.map(function (card) {
      return [card.id, card.done, card.cancelled, card.lines.map(function (line) { return [line.id, line.ready, line.void, line.seen]; })];
    }));
  }

  function refreshAges() {
    if (!data) { return; }
    data.tickets.forEach(function (card) {
      var node = board.querySelector('[data-card="' + card.id + '"]');
      if (!node) { return; }
      var age = node.querySelector('[data-age]');
      if (age) { age.textContent = minutes(card); }
      var wanted = levelClass(card);
      if (node.className.replace(' kds-card--new', '') !== wanted) { node.className = wanted; }
    });
  }

  function render(next) {
    data = next;
    fetchedAt = Date.now();
    showClock();
    var now = sign(data.tickets);
    if (now !== signature) {
      signature = now;
      var seenIds = known;
      while (board.firstChild) { board.removeChild(board.firstChild); }
      data.tickets.forEach(function (card) { board.appendChild(cardNode(card, seenIds !== null && seenIds.indexOf(card.id) === -1)); });
    } else {
      refreshAges();
    }
    known = data.tickets.map(function (card) { return card.id; });
    empty.hidden = data.tickets.length > 0;
    recall.disabled = !data.recall;
    recall.title = data.recall ? (words.recall || '') + ' · ' + data.recall.where : '';
    say('');
  }

  function failed(response) {
    if (response && (response.redirected || response.status === 401 || response.status === 403)) {
      stopped = true;
      say(words.signed_out || '');
      return;
    }
    say(words.offline || '');
  }

  function read(response) {
    var type = response.headers.get('content-type') || '';
    if (!response.ok || type.indexOf('json') === -1 || response.redirected) { failed(response); return null; }
    return response.json();
  }

  function poll() {
    if (stopped || inflight || document.hidden) { return; }
    inflight = true;
    fetch(feedUrl + query(), { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, cache: 'no-store' })
      .then(read)
      .then(function (payload) { if (payload) { render(payload); } })
      .catch(function () { failed(null); })
      .then(function () { inflight = false; });
  }

  function act(op, id) {
    if (stopped || inflight) { return; }
    inflight = true;
    var body = new URLSearchParams();
    body.set('op', op);
    body.set('id', id);
    if (station) { body.set('station', station); }
    fetch(actUrl + query(), { method: 'POST', credentials: 'same-origin', cache: 'no-store',
                              headers: { 'X-CSRFToken': csrf, 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' }, body: body.toString() })
      .then(read)
      .then(function (payload) { if (payload) { render(payload); } })
      .catch(function () { failed(null); })
      .then(function () { inflight = false; });
  }

  board.addEventListener('click', function (event) {
    var line = event.target.closest('[data-line]');
    if (line) { act('ready', line.getAttribute('data-line')); return; }
    var card = event.target.closest('[data-card]');
    if (card) { act('done', card.getAttribute('data-card')); }
  });
  recall.addEventListener('click', function () { if (data && data.recall) { act('recall', data.recall.id); } });

  function showClock() {
    // The feed's own clock (the business's time of day) plus the minutes since it arrived: the tablet's clock is never read for the hour.
    if (!data || !data.clock) { return; }
    var parts = data.clock.split(':');
    var minutes = (parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10) + Math.floor((Date.now() - fetchedAt) / 60000)) % 1440;
    clock.textContent = ('0' + Math.floor(minutes / 60)).slice(-2) + ':' + ('0' + (minutes % 60)).slice(-2);
  }

  function tick() {
    showClock();
    refreshAges();
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { poll(); } });
  tick();
  setInterval(tick, 15000);
  setInterval(poll, INTERVAL);
  poll();
})();
