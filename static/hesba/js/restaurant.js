/* RESTO-001: filter the menu by category and search, and confirm voids/cancels. R1a: the dish's choice sheet and the size sheet.
   R2b: the bill's «ادفع بأكتر من طريقة» box. R2a: the «ادفع جزء» sheet's steppers and running amount. R1b: the combo sheet
   shows a chosen dish's own add-ons and asks them only while that dish is chosen. */
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
      var first = sheet.querySelector('.rs-sheet__body input');
      if (first) { first.focus(); }
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-choice-sheet], [data-size-sheet], [data-part-sheet]'), function (sheet) {
    var groups = Array.prototype.slice.call(sheet.querySelectorAll('[data-group-max]'));
    var own = function (group, selector) {  /* R1b: a combo slot's own inputs, not the add-ons nested under its dishes */
      return Array.prototype.slice.call(group.querySelectorAll(selector)).filter(function (input) { return input.closest('[data-group-max]') === group; });
    };
    var limit = function (group) {
      var boxes = own(group, 'input[type=checkbox]');
      var max = parseInt(group.getAttribute('data-group-max'), 10) || 0;
      var ticked = boxes.filter(function (box) { return box.checked; }).length;
      boxes.forEach(function (box) { box.disabled = !box.checked && max > 0 && ticked >= max; });
      if (ticked >= (parseInt(group.getAttribute('data-group-min'), 10) || 0)) {
        var error = own(group, '[data-group-error]')[0];
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
          if (group.closest('fieldset[disabled]')) { return; }  /* R1b: a dish not chosen in its combo slot asks nothing */
          var min = parseInt(group.getAttribute('data-group-min'), 10) || 0;
          var ticked = own(group, 'input:checked').length;
          var error = own(group, '[data-group-error]')[0];
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
    /* R1b: choosing a dish in a combo slot shows its own add-ons and hides (and stops sending) the others' of that slot */
    Array.prototype.forEach.call(sheet.querySelectorAll('[data-combo-slot]'), function (slot) {
      var sync = function () {
        Array.prototype.forEach.call(slot.querySelectorAll('[data-combo-extras]'), function (extras) {
          var radio = slot.querySelector('[data-combo-choice="' + extras.getAttribute('data-combo-extras') + '"]');
          extras.disabled = !(radio && radio.checked);
        });
      };
      slot.addEventListener('change', function (event) { if (event.target.hasAttribute('data-combo-choice')) { sync(); } });
      sync();
    });
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
  /* R2b (HG-110): «ادفع بأكتر من طريقة». While it is ticked the box shows what is still unassigned («الباقي» = the bill with
     VAT, less the discount, less the amounts typed) and the pay button waits until that is exactly 0.00. A convenience only:
     the server checks every amount, every cashbox and the exact total again. Money is counted in piastres. */
  Array.prototype.forEach.call(document.querySelectorAll('[data-split]'), function (box) {
    var form = box.closest('form');
    var toggle = box.querySelector('[data-split-toggle]');
    var left = box.querySelector('[data-split-left]');
    var leftLine = box.querySelector('[data-split-left-line]');
    var due = box.querySelector('[data-split-due]');
    var amounts = Array.prototype.slice.call(box.querySelectorAll('[data-tender-amount]'));
    var discount = form ? form.querySelector('[name="discount"]') : null;
    var pay = form ? form.querySelector('[data-pay]') : null;
    if (!form || !toggle || !left) { return; }
    var piastres = function (text) {
      var value = parseFloat(String(text || '').replace(/,/g, '').trim());
      return isFinite(value) ? Math.round(value * 100) : 0;
    };
    var shown = function (cents) {
      var sign = cents < 0 ? '-' : '';
      cents = Math.abs(cents);
      return sign + Math.floor(cents / 100) + '.' + ('0' + (cents % 100)).slice(-2);
    };
    var remaining = function () {
      var rest = piastres(box.getAttribute('data-bill')) - piastres(discount && discount.value);
      if (due) { due.textContent = shown(rest); }
      amounts.forEach(function (input) { rest -= piastres(input.value); });
      return rest;
    };
    var update = function () {
      var on = toggle.checked;
      var rest = remaining();
      form.classList.toggle('is-split', on);
      left.textContent = shown(rest);
      if (leftLine) { leftLine.classList.toggle('is-zero', rest === 0); }
      if (pay) { pay.disabled = on && rest !== 0; }
    };
    toggle.addEventListener('change', function () {
      update();
      if (toggle.checked && amounts[0]) { amounts[0].focus(); }
    });
    amounts.forEach(function (input) { input.addEventListener('input', update); });
    if (discount) { discount.addEventListener('input', update); }
    form.addEventListener('submit', function (event) {
      if (toggle.checked && remaining() !== 0) { event.preventDefault(); update(); }
    });
    update();
  });
  /* R2a (HG-111): «ادفع جزء». Each line's stepper goes from 0 to the line's quantity by whole plates (a line of 1.5 kg goes
     whole or not at all); the sheet shows the part's amount before VAT and opens it only when something is chosen. The
     server checks every quantity again. */
  Array.prototype.forEach.call(document.querySelectorAll('[data-part-form]'), function (form) {
    var rows = Array.prototype.slice.call(form.querySelectorAll('[data-part-row]'));
    var amount = form.querySelector('[data-part-amount]');
    var submit = form.querySelector('[data-part-submit]');
    var cents = function (text) { var value = parseFloat(String(text || '').replace(/,/g, '')); return isFinite(value) ? Math.round(value * 100) : 0; };
    var count = function (row) { var value = parseFloat(row.querySelector('[data-part-qty]').value); return isFinite(value) && value > 0 ? value : 0; };
    var update = function () {
      var total = 0;
      rows.forEach(function (row) {
        var chosen = count(row);
        total += Math.round(chosen * cents(row.getAttribute('data-each')));
        row.classList.toggle('is-chosen', chosen > 0);
      });
      if (amount) { amount.textContent = Math.floor(total / 100) + '.' + ('0' + (total % 100)).slice(-2); }
      if (submit) { submit.disabled = total === 0 && !rows.some(function (row) { return count(row) > 0; }); }
    };
    rows.forEach(function (row) {
      var input = row.querySelector('[data-part-qty]');
      var max = parseFloat(row.getAttribute('data-max')) || 0;
      Array.prototype.forEach.call(row.querySelectorAll('[data-step]'), function (button) {
        button.addEventListener('click', function () {
          var step = parseInt(button.getAttribute('data-step'), 10);
          var next = count(row) + step;
          if (max % 1) { next = step > 0 ? max : 0; }
          input.value = String(Math.max(0, Math.min(max, next)));
          update();
        });
      });
      input.addEventListener('input', update);
    });
    update();
  });
  Array.prototype.forEach.call(document.querySelectorAll('form[data-confirm]'), function (form) {
    form.addEventListener('submit', function (event) {
      if (!window.confirm(form.getAttribute('data-confirm'))) { event.preventDefault(); }
    });
  });
})();
