/* RESTO-001: filter the menu by category and search, and confirm voids/cancels. R1a: the dish's choice sheet. */
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
        /* RS-02: a raw ingredient stays out of the «الكل» view; its own category and the search still show it. */
        var okRaw = !dish.hasAttribute('data-raw') || !!current || !!text;
        dish.hidden = !(okCat && okText && okRaw);
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
    apply();
  }
  /* R1a (HG-109): a dish that asks questions opens its choice sheet instead of being added in one tap. Each group keeps
     to its maximum (the rest of its boxes are disabled once it is reached) and the sheet does not send until every minimum
     is met; the server checks both again. Without JavaScript the tile adds the dish plainly and the server says what is missing. */
  var opener = null;
  var lock = function (on) { document.documentElement.classList.toggle('rs-sheet-open', on); };
  Array.prototype.forEach.call(document.querySelectorAll('[data-opens-sheet]'), function (button) {
    var sheet = document.getElementById(button.getAttribute('data-opens-sheet'));
    if (!sheet || typeof sheet.showModal !== 'function') { return; }
    button.addEventListener('click', function (event) {
      event.preventDefault();
      opener = button;
      sheet.showModal();
      lock(true);
      var first = sheet.querySelector('input[name^="option"]');
      if (first) { first.focus(); }
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-choice-sheet]'), function (sheet) {
    var groups = Array.prototype.slice.call(sheet.querySelectorAll('[data-group-max]'));
    var limit = function (group) {
      var boxes = Array.prototype.slice.call(group.querySelectorAll('input[type=checkbox]'));
      var max = parseInt(group.getAttribute('data-group-max'), 10) || 0;
      var ticked = boxes.filter(function (box) { return box.checked; }).length;
      boxes.forEach(function (box) { box.disabled = !box.checked && max > 0 && ticked >= max; });
      if (ticked >= (parseInt(group.getAttribute('data-group-min'), 10) || 0)) {
        var error = group.querySelector('[data-group-error]');
        if (error) { error.hidden = true; error.textContent = ''; }
      }
    };
    groups.forEach(function (group) {
      group.addEventListener('change', function () { limit(group); });
      limit(group);
    });
    var form = sheet.querySelector('[data-sheet-form]');
    if (form) {
      form.addEventListener('submit', function (event) {
        var missing = null;
        groups.forEach(function (group) {
          var min = parseInt(group.getAttribute('data-group-min'), 10) || 0;
          var ticked = group.querySelectorAll('input:checked').length;
          var error = group.querySelector('[data-group-error]');
          if (ticked < min) {
            if (error) { error.textContent = group.getAttribute('data-group-message') || ''; error.hidden = false; }
            missing = missing || group;
          }
        });
        if (missing) {
          event.preventDefault();
          var input = missing.querySelector('input:not(:disabled)');
          if (input) { input.focus(); }
        }
      });
    }
    var close = sheet.querySelector('[data-sheet-close]');
    if (close) { close.addEventListener('click', function () { sheet.close(); }); }
    sheet.addEventListener('close', function () {
      lock(false);
      if (opener) { opener.focus(); opener = null; }
    });
    sheet.addEventListener('click', function (event) {  /* a tap on the dimmed backdrop closes the sheet */
      if (event.target === sheet) { sheet.close(); }
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll('form[data-confirm]'), function (form) {
    form.addEventListener('submit', function (event) {
      if (!window.confirm(form.getAttribute('data-confirm'))) { event.preventDefault(); }
    });
  });
})();
