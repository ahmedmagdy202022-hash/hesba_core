/* RESTO-001: filter the menu by category and search, and confirm voids/cancels. */
(function () {
  'use strict';
  var menu = document.querySelector('[data-menu]');
  if (menu) {
    var search = menu.querySelector('[data-menu-search]');
    var cats = Array.prototype.slice.call(menu.querySelectorAll('[data-menu-cat]'));
    var dishes = Array.prototype.slice.call(menu.querySelectorAll('[data-dish]'));
    var current = '';
    var apply = function () {
      var text = (search && search.value || '').trim().toLowerCase();
      dishes.forEach(function (dish) {
        var okCat = !current || dish.getAttribute('data-cat') === current;
        var okText = !text || (dish.getAttribute('data-name') || '').indexOf(text) !== -1;
        dish.hidden = !(okCat && okText);
      });
    };
    cats.forEach(function (button) {
      button.addEventListener('click', function () {
        current = button.getAttribute('data-menu-cat') || '';
        cats.forEach(function (other) { other.setAttribute('aria-pressed', other === button ? 'true' : 'false'); });
        apply();
      });
    });
    if (search) { search.addEventListener('input', apply); }
  }
  Array.prototype.forEach.call(document.querySelectorAll('form[data-confirm]'), function (form) {
    form.addEventListener('submit', function (event) {
      if (!window.confirm(form.getAttribute('data-confirm'))) { event.preventDefault(); }
    });
  });
})();
