/* AN6 (Ahmed: "مرتجع البيع فيها مشكلة ومحتاج تعديل"): the return screen adds up what goes back,
   returns everything in one press, and offers the usual reasons. */
(function () {
  var form = document.querySelector('[data-return-lines]');
  if (!form) return;
  form = form.closest('form');
  var boxes = Array.prototype.slice.call(form.querySelectorAll('[data-return-qty]'));
  var total = form.querySelector('[data-return-total]');

  function number(text) { var n = parseFloat(String(text || '').replace(',', '.')); return isNaN(n) ? 0 : n; }
  function money(n) { return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function recalc() {
    var sum = 0;
    boxes.forEach(function (box) {
      var q = Math.min(number(box.value), number(box.dataset.left));
      sum += q * number(box.dataset.unitValue);
      box.closest('tr').classList.toggle('ret-row--on', number(box.value) > 0);
    });
    if (total) total.textContent = money(sum);
  }
  boxes.forEach(function (box) { box.addEventListener('input', recalc); });
  var all = form.querySelector('[data-return-all]'), clear = form.querySelector('[data-return-clear]');
  if (all) all.addEventListener('click', function () { boxes.forEach(function (box) { box.value = String(number(box.dataset.left)); }); recalc(); });
  if (clear) clear.addEventListener('click', function () { boxes.forEach(function (box) { box.value = ''; }); recalc(); });

  var holder = form.querySelector('[data-return-reasons]'), reason = form.querySelector('[name="reason"]');
  if (holder && reason) {
    var list = [];
    try { list = JSON.parse(holder.dataset.returnReasons || '[]'); } catch (e) {}
    list.forEach(function (text) {
      var chip = document.createElement('button');
      chip.type = 'button'; chip.className = 'ret-reason'; chip.textContent = text;
      chip.addEventListener('click', function () { reason.value = text; reason.focus(); });
      holder.appendChild(chip);
    });
  }
  recalc();
})();
