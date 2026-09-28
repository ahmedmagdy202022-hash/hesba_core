/* POS-003: keep selling when the internet drops; sync each sale once when it is back.
 *
 * While the server answers, the till works exactly as before (normal form post).
 * When it does not, a sale is kept on this device with a key made now, and the
 * cart is cleared for the next customer. As soon as the server answers again the
 * queued sales are sent one by one to /sales/pos/sync/, which posts each key
 * once (a retry returns the same invoice). A sale the server refuses stays in
 * the list with the reason, so nothing disappears silently.
 */
(function () {
  'use strict';
  var form = document.querySelector('[data-pos]');
  var panel = document.querySelector('[data-pos-offline]');
  if (!form || !panel) { return; }
  var syncUrl = form.getAttribute('data-sync-url');
  var storeKey = 'hesba.pos.queue.v1:' + (form.getAttribute('data-pos-user') || '0');
  var en = document.documentElement.lang === 'en';
  var T = en ? {
    offline: 'No connection — sales are kept on this device.', pending: 'waiting to sync', syncing: 'Syncing…',
    synced: 'Synced: ', refused: 'Refused', discard: 'Remove', keep: 'Do not reload or close this page until the sales sync.',
    saved: 'Saved on this device. It will be posted when the connection is back.', login: 'Sign in again to sync the saved sales.', back: 'Connection is back.'
  } : {
    offline: 'مفيش اتصال — الفواتير بتتحفظ على الجهاز ده.', pending: 'مستنية المزامنة', syncing: 'بنزامن…',
    synced: 'اتزامنت: ', refused: 'اترفضت', discard: 'شيلها', keep: 'متعملش Refresh ومتقفلش الصفحة لحد ما الفواتير تتزامن.',
    saved: 'اتحفظت على الجهاز، وهتترحّل أول ما النت يرجع.', login: 'سجّل دخول تاني عشان الفواتير المحفوظة تتزامن.', back: 'النت رجع.'
  };
  var reachable = navigator.onLine;
  var syncing = false;

  function load() { try { return JSON.parse(localStorage.getItem(storeKey) || '[]'); } catch (e) { return []; } }
  function save(queue) { try { localStorage.setItem(storeKey, JSON.stringify(queue)); } catch (e) { /* storage full or blocked: nothing we can do here */ } }
  function newKey() {
    if (window.crypto && crypto.randomUUID) { return crypto.randomUUID(); }
    var bytes = new Uint8Array(16); crypto.getRandomValues(bytes); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    var hex = Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
    return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
  }
  function csrf() { var node = form.querySelector('[name=csrfmiddlewaretoken]'); return node ? node.value : ''; }
  function money(value) { return (Math.round(value * 100) / 100).toFixed(2); }

  function render(note) {
    var queue = load();
    var pending = queue.filter(function (row) { return !row.error; });
    var refused = queue.filter(function (row) { return row.error; });
    panel.hidden = reachable && !queue.length && !note;
    panel.classList.toggle('pos-offline--down', !reachable);
    panel.textContent = '';
    var head = document.createElement('p');
    function part(tag, text, attr) {
      if (head.childNodes.length) { head.appendChild(document.createTextNode(' · ')); }
      var node = document.createElement(tag); node.textContent = text;
      if (attr) { node.setAttribute(attr[0], attr[1]); }
      head.appendChild(node);
    }
    if (!reachable) { part('strong', T.offline); }
    if (pending.length) { part('span', pending.length + ' ' + T.pending + (syncing ? ' — ' + T.syncing : ''), ['data-offline-pending', String(pending.length)]); }
    if (pending.length && !reachable) { part('small', T.keep); }
    if (note) { part('span', note); }
    panel.appendChild(head);
    refused.forEach(function (row) {
      var line = document.createElement('p');
      line.className = 'pos-offline__refused';
      line.setAttribute('data-offline-refused', row.key);
      line.textContent = T.refused + ' (' + money(row.total) + ', ' + new Date(row.recorded_at).toLocaleString() + '): ' + row.error + ' ';
      var drop = document.createElement('button');
      drop.type = 'button'; drop.className = 'op-secondary'; drop.textContent = T.discard;
      drop.addEventListener('click', function () { save(load().filter(function (other) { return other.key !== row.key; })); render(); });
      line.appendChild(drop);
      panel.appendChild(line);
    });
  }

  function payloadFromForm() {
    var data = new FormData(form);
    var lines = [];
    try { lines = JSON.parse(data.get('cart_json') || '[]'); } catch (e) { lines = []; }
    var total = lines.reduce(function (sum, row) { return sum + (parseFloat(row.qty) || 0) * (parseFloat(row.price) || 0); }, 0) - (parseFloat(data.get('discount')) || 0);
    return {
      key: newKey(), recorded_at: new Date().toISOString(), customer: data.get('customer'), location: data.get('location'), cashbox: data.get('cashbox'),
      discount: data.get('discount') || '0', tendered: data.get('tendered') || '0', lines: lines, total: total
    };
  }

  // Runs after pos.js has written the cart into the form.
  form.addEventListener('submit', function (event) {
    if (reachable) { return; }
    event.preventDefault();
    var sale = payloadFromForm();
    if (!sale.lines.length) { return; }
    var queue = load(); queue.push(sale); save(queue);
    var clear = form.querySelector('[data-pos-clear]');
    if (clear) { clear.click(); }
    render(T.saved);
  });

  function sync() {
    if (syncing || !reachable) { return; }
    var next = load().filter(function (row) { return !row.error; })[0];
    if (!next) { render(); return; }
    syncing = true; render();
    fetch(syncUrl + (en ? '?lang=en' : ''), {
      method: 'POST', credentials: 'same-origin', redirect: 'manual',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      body: JSON.stringify(next)
    }).then(function (response) {
      var type = response.headers.get('Content-Type') || '';
      if (response.type === 'opaqueredirect' || response.status === 403 || type.indexOf('application/json') === -1) { throw new Error('login'); }
      return response.json();
    }).then(function (answer) {
      var queue = load();
      if (answer.status === 'posted' || answer.status === 'already') {
        save(queue.filter(function (row) { return row.key !== next.key; }));
        syncing = false; render(T.synced + answer.number); sync();
      } else {
        queue.forEach(function (row) { if (row.key === next.key) { row.error = answer.error || T.refused; } });
        save(queue); syncing = false; render(); sync();
      }
    }).catch(function (error) {
      syncing = false;
      if (error && error.message === 'login') { render(T.login); return; }
      reachable = false; render();
    });
  }

  function ping() {
    var controller = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (controller) { controller.abort(); } }, 5000);
    fetch('/healthz/', { cache: 'no-store', credentials: 'same-origin', signal: controller ? controller.signal : undefined })
      .then(function (response) { return response.ok; }, function () { return false; })
      .then(function (ok) {
        clearTimeout(timer);
        var wasDown = !reachable;
        reachable = ok;
        render(ok && wasDown ? T.back : undefined);
        if (ok) { sync(); }
      });
  }

  window.addEventListener('online', ping);
  window.addEventListener('offline', function () { reachable = false; render(); });
  window.addEventListener('beforeunload', function (event) {
    if (!reachable && load().some(function (row) { return !row.error; })) { event.preventDefault(); event.returnValue = ''; }
  });
  setInterval(ping, 20000);
  render();
  ping();
})();
