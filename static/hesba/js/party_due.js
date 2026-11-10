/* UX Wave 2: on a collection (or supplier payment) form, show what the chosen party owes
   («مستحق عليه 5,234.87 ج.م») from its account, and a «المبلغ كله» chip that puts that amount in the box.
   Reads the existing balance endpoint (/parties/<kind>/<pk>/balance/); if it refuses or fails, the line hides.
   Nothing is saved from here: the amount box is only filled, the person still presses save. */
(function () {
  'use strict';
  var box = document.querySelector('[data-party-due]');
  if (!box) return;
  var form = box.closest('form');
  var kind = box.dataset.kind;
  var select = form && form.querySelector('select[name="' + kind + '"]');
  var amount = form && form.querySelector('input[name="amount"]');
  var text = box.querySelector('[data-due-text]');
  var chip = box.querySelector('[data-due-fill]');
  var words = (box.dataset.words || '').split('|');
  var unit = box.dataset.unit || '';
  if (!select || !amount || !text || !chip) return;

  // Next to the party's box, where the eye already is.
  var field = select.closest('.op-field, .md-field');
  if (field && field.parentNode) field.parentNode.insertBefore(box, field.nextSibling);
  box.classList.add('hs-field--wide');

  function money(n) {
    return Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function show(balance) {
    text.textContent = '';
    if (balance === null) { box.hidden = true; return; }
    var label = balance > 0 ? words[0] : (balance < 0 ? words[1] : words[2]);
    text.appendChild(document.createTextNode(label + (balance ? ' ' : '')));
    if (balance) {
      var b = document.createElement('b');
      var bdi = document.createElement('bdi');
      bdi.textContent = money(balance) + (unit ? ' ' + unit : '');
      b.appendChild(bdi);
      text.appendChild(b);
    }
    chip.hidden = !(balance > 0);
    chip.dataset.amount = balance > 0 ? balance.toFixed(2) : '';
    box.hidden = false;
  }
  var asked = 0;
  function load() {
    var pk = select.value;
    var mine = ++asked;
    if (!pk || !/^\d+$/.test(pk)) { show(null); return; }
    fetch(box.dataset.url.replace('/0/', '/' + pk + '/'), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (mine !== asked) return;
        var value = data && data.balance !== undefined ? parseFloat(data.balance) : NaN;
        show(isNaN(value) ? null : value);
      })
      .catch(function () { if (mine === asked) show(null); });
  }
  var filled = null;  // the amount the chip put in the box, so a switch to another party does not keep the old one
  chip.addEventListener('click', function () {
    if (!chip.dataset.amount) return;
    amount.value = chip.dataset.amount;
    filled = amount.value;
    amount.dispatchEvent(new Event('input', { bubbles: true }));
    amount.focus();
  });
  select.addEventListener('change', function () {
    if (filled !== null && amount.value === filled) {  // untouched since the chip filled it: it was the other party's figure
      amount.value = '';
      amount.dispatchEvent(new Event('input', { bubbles: true }));
    }
    filled = null;
    load();
  });
  if (select.value && box.hidden) load();
})();
