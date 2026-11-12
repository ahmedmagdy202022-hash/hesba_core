/* E2: the mark sheet. Typing a mark clears «غايب» and ticking «غايب» clears the mark (the server refuses both together, so nothing is guessed there);
   Enter moves to the next mark box; the count in the save bar follows what is filled. Nothing here is sent anywhere. */
(function () {
  'use strict';
  var form = document.querySelector('[data-marks-form]');
  if (!form) { return; }
  var rows = Array.prototype.slice.call(form.querySelectorAll('[data-mark-row]'));
  var count = form.querySelector('[data-entered-count]');
  function update() {
    if (!count) { return; }
    var n = rows.filter(function (row) {
      var score = row.querySelector('[data-score]');
      var absent = row.querySelector('[data-absent]');
      return (score && score.value.trim() !== '') || (absent && absent.checked);
    }).length;
    count.textContent = count.getAttribute('data-template').replace('{n}', n).replace('{m}', rows.length);
  }
  rows.forEach(function (row, index) {
    var score = row.querySelector('[data-score]');
    var absent = row.querySelector('[data-absent]');
    if (!score || !absent) { return; }
    score.addEventListener('input', function () { if (score.value.trim() !== '') { absent.checked = false; } update(); });
    absent.addEventListener('change', function () { if (absent.checked) { score.value = ''; } update(); });
    score.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') { return; }
      event.preventDefault();
      var next = rows[index + 1] && rows[index + 1].querySelector('[data-score]');
      if (next) { next.focus(); next.select(); } else { var save = form.querySelector('[data-save-marks]'); if (save) { save.focus(); } }
    });
  });
  update();
}());
