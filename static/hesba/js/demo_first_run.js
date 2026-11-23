/* DEMO-FIRST-RUN: the «جرّب دلوقتي» card stays until this browser dismisses it. */
(function () {
  var card = document.querySelector('[data-demo-tour]');
  if (!card) return;
  var key = 'hesba-demo-tour:' + card.getAttribute('data-demo-tour');
  function seen() { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  if (seen()) { card.hidden = true; return; }
  var close = card.querySelector('[data-demo-tour-dismiss]');
  if (close) close.addEventListener('click', function () {
    try { window.localStorage.setItem(key, '1'); } catch (e) { /* private window: shown again next time */ }
    card.hidden = true;
  });
})();
