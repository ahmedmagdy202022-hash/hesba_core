/* Contracting C2b: the daily report form. «+ صف تاني» adds one more free row to the workers or the equipment list.
   Without this script the form still works: it offers a few blank rows already. Vanilla JS, no libraries. */
(function () {
  'use strict';
  document.querySelectorAll('[data-sr-add]').forEach(function (button) {
    button.addEventListener('click', function () {
      var kind = button.getAttribute('data-sr-add');
      var box = button.closest('[data-sr-rows]');
      var template = box && box.querySelector('[data-sr-template="' + kind + '"]');
      if (!template) { return; }
      var row = template.content.firstElementChild.cloneNode(true);
      box.insertBefore(row, button);
      var field = row.querySelector('input');
      if (field) { field.focus(); }
      if (box.querySelectorAll('[data-sr-row]').length >= 15) { button.hidden = true; }
    });
  });
})();
