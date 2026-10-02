/* LABEL-003: live preview while designing a label. Sizes are real millimetres
   (CSS mm), scaled to fit the preview box; an A4 design also shows the sheet. */
(function () {
  'use strict';
  var form = document.querySelector('[data-designer]');
  if (!form) return;
  var stage = document.querySelector('[data-stage]');
  var label = document.querySelector('[data-label-preview]');
  var sheet = document.querySelector('[data-sheet]');
  var fit = document.querySelector('[data-fit]');
  var scaleNote = document.querySelector('[data-scale]');
  var a4Only = form.querySelector('[data-a4-only]');
  var num = function (name) { var v = parseFloat(String(form.elements[name].value).replace(',', '.')); return isFinite(v) ? v : 0; };
  var on = function (name) { return form.elements[name].checked; };

  function update() {
    var kind = form.querySelector('input[name=kind]:checked').value;
    var w = num('width'), h = num('height');
    a4Only.hidden = kind !== 'a4';
    label.style.width = w + 'mm';
    label.style.height = h + 'mm';
    label.querySelector('.ld-name').style.fontSize = num('name_pt') + 'pt';
    label.querySelector('.ld-price').style.fontSize = num('price_pt') + 'pt';
    label.querySelector('.ld-shop').style.fontSize = num('small_pt') + 'pt';
    label.querySelector('.ld-code').style.fontSize = num('small_pt') + 'pt';
    label.querySelector('[data-bars]').style.flex = '0 0 ' + num('barcode_pct') + '%';
    label.querySelectorAll('[data-p]').forEach(function (el) { el.hidden = !on(el.dataset.p); });

    var box = stage.clientWidth - 32;
    if (kind === 'a4') {
      var cols = Math.max(1, Math.round(num('columns'))), rows = Math.max(1, Math.round(num('rows')));
      var usedW = cols * w + 2 * num('side') + (cols - 1) * num('gap_x');
      var usedH = rows * h + num('top') + (rows - 1) * num('gap_y');
      var bad = usedW > 210 || usedH > 297;
      fit.textContent = fit.dataset.templateCount.replace('{count}', cols * rows) + ' · ' +
        fit.dataset.templateFit.replace('{used_w}', usedW.toFixed(1)).replace('{used_h}', usedH.toFixed(1));
      fit.classList.toggle('is-bad', bad);
      sheet.hidden = false;
      sheet.innerHTML = '';
      var rtl = document.documentElement.dir === 'rtl';
      for (var r = 0; r < rows; r++) for (var c = 0; c < cols; c++) {
        var cell = document.createElement('span');
        cell.className = 'ld-sheet__cell' + (r === 0 && c === 0 ? ' is-first' : '');
        var x = num('side') + c * (w + num('gap_x'));
        cell.style[rtl ? 'right' : 'left'] = x + 'mm';
        cell.style.top = (num('top') + r * (h + num('gap_y'))) + 'mm';
        cell.style.width = w + 'mm';
        cell.style.height = h + 'mm';
        sheet.appendChild(cell);
      }
      var mm = 3.7795;
      var sheetScale = Math.min(1, box / (210 * mm), 420 / (297 * mm));
      sheet.style.zoom = sheetScale;
    } else {
      sheet.hidden = true;
    }
    var labelScale = Math.min(3, Math.max(0.5, (box * 0.9) / (w * 3.7795), 0.5));
    label.style.zoom = labelScale;
    scaleNote.textContent = w + ' × ' + h + ' mm  (×' + labelScale.toFixed(1) + ')';
  }
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  window.addEventListener('resize', update);
  update();
})();
