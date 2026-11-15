/* R5a (HG-115): the public table menu's steppers and the order button's count. The page works without this: every dish has a
   quantity box. A dish with required choices opens them when it is first added. Nothing is stored or sent from here. */
(function () {
  'use strict';
  var form = document.querySelector('[data-tm-form]');
  if (!form) { return; }
  var order = form.querySelector('[data-tm-order]');
  var clamp = function (input, value) {
    var max = parseInt(input.getAttribute('max') || '10', 10);
    return Math.max(0, Math.min(max, isNaN(value) ? 0 : value));
  };
  var refresh = function () {
    var count = 0;
    Array.prototype.forEach.call(form.querySelectorAll('[data-tm-dish]'), function (dish) {
      var input = dish.querySelector('[data-tm-qty]');
      if (!input) { return; }
      var qty = clamp(input, parseInt(input.value, 10));
      count += qty;
      dish.classList.toggle('is-on', qty > 0);
    });
    if (order) {
      var label = order.getAttribute('data-label') || '';
      var withCount = order.getAttribute('data-label-n') || '';
      order.textContent = count > 0 && withCount ? withCount.replace('{n}', String(count)) : label;
    }
  };
  form.addEventListener('click', function (event) {
    var button = event.target.closest('[data-step]');
    if (!button) { return; }
    var dish = button.closest('[data-tm-dish]');
    var input = dish && dish.querySelector('[data-tm-qty]');
    if (!input) { return; }
    var before = clamp(input, parseInt(input.value, 10));
    var after = clamp(input, before + parseInt(button.getAttribute('data-step'), 10));
    input.value = String(after);
    var more = dish.querySelector('[data-tm-more]');
    if (before === 0 && after > 0 && more && more.querySelector('.tm-group')) { more.open = true; }
    refresh();
  });
  form.addEventListener('input', function (event) {
    if (event.target.matches('[data-tm-qty]')) { refresh(); }
  });
  refresh();
}());
