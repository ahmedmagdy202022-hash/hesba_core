/* DEMO-FEEDBACK: open the note dialog, send it, say thanks. */
(function () {
  var openers = document.querySelectorAll('[data-feedback-open]');
  var dialog = document.querySelector('[data-feedback-dialog]');
  if (!openers.length || !dialog || typeof dialog.showModal !== 'function') return;
  var form = dialog.querySelector('[data-feedback-form]');
  var status = dialog.querySelector('[data-feedback-status]');
  var send = dialog.querySelector('[data-feedback-send]');
  function lang() { return (document.body.dataset.lang || document.documentElement.lang || 'ar').indexOf('en') === 0 ? 'en' : 'ar'; }
  var words = {
    ar: { sending: 'بيتبعت…', thanks: 'وصلت، شكرًا جدًا! 🙏', failed: 'ماوصلتش، جرّب تاني.' },
    en: { sending: 'Sending…', thanks: 'Got it, thank you! 🙏', failed: 'Not sent, please try again.' }
  };
  // Pages without the language helper still show one language.
  if (!document.body.dataset.lang) document.body.dataset.lang = lang();
  // DEMO-FIRST-RUN: the demo bar's «ملاحظاتك» and the hello banner open the same dialog as the floating button.
  Array.prototype.forEach.call(openers, function (open) {
    open.addEventListener('click', function () {
      status.textContent = '';
      status.className = 'hs-feedback__status';
      dialog.showModal();
      form.querySelector('[data-feedback-message]').focus();
    });
  });
  dialog.querySelector('[data-feedback-close]').addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('click', function (event) { if (event.target === dialog) dialog.close(); });
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var current = lang();
    form.querySelector('[data-feedback-path]').value = window.location.pathname + window.location.search;
    form.querySelector('[data-feedback-viewport]').value = window.innerWidth + 'x' + window.innerHeight;
    form.querySelector('[data-feedback-lang]').value = current;
    send.disabled = true;
    status.textContent = words[current].sending;
    fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (response) { return response.json().then(function (data) { return { ok: response.ok && data.ok, data: data }; }); })
      .then(function (result) {
        if (result.ok) {
          status.textContent = words[current].thanks;
          status.className = 'hs-feedback__status is-ok';
          form.reset();
          setTimeout(function () { dialog.close(); }, 1600);
        } else {
          status.textContent = (result.data && result.data.error) || words[current].failed;
          status.className = 'hs-feedback__status is-error';
        }
      })
      .catch(function () { status.textContent = words[current].failed; status.className = 'hs-feedback__status is-error'; })
      .then(function () { send.disabled = false; });
  });
})();

/* DEMO-TRACK: a one-time hello banner that says pages are recorded and offers the note dialog. Shown once per browser. */
(function () {
  var card = document.querySelector('[data-hello]');
  if (!card) return;
  var KEY = 'hesba-demo-hello';
  function seen() { try { return window.localStorage.getItem(KEY); } catch (e) { return '1'; } }
  function remember(value) { try { window.localStorage.setItem(KEY, value); } catch (e) { /* private window: shown again next time */ } }
  if (seen()) return;
  card.hidden = false;
  card.querySelector('[data-hello-skip]').addEventListener('click', function () { remember('skipped'); card.hidden = true; });
  var open = card.querySelector('[data-hello-open]');
  if (open) open.addEventListener('click', function () { remember('opened'); card.hidden = true; });
})();
