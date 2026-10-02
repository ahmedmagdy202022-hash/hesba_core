/* LABEL-003: "Download PDF" for barcode labels, built in the browser.
 *
 * Each page is drawn on a canvas at print resolution (600 dpi for a roll label,
 * 300 dpi for an A4 sheet) and written into a small PDF by hand: one JPEG per
 * page at the exact paper size, so a printer prints it 1:1. The browser draws
 * the text, so Arabic is shaped correctly without embedding a font. Bars are
 * snapped to whole pixels so scanners read them as cleanly as the print view.
 */
(function () {
  'use strict';

  var button = document.querySelector('[data-label-pdf]');
  var source = document.getElementById('label-pdf-data');
  if (!button || !source) return;
  var data = JSON.parse(source.textContent);
  var spec = data.spec;
  var PT = 0.3528; // mm per point
  var FONT = '"IBM Plex Sans Arabic", Tahoma, Arial, sans-serif';

  function fitText(ctx, text, maxWidth, sizePx, weight, maxLines) {
    var size = sizePx;
    ctx.font = weight + ' ' + size + 'px ' + FONT;
    if (maxLines === 1) {
      while (ctx.measureText(text).width > maxWidth && size > sizePx * 0.7) {
        size -= 1;
        ctx.font = weight + ' ' + size + 'px ' + FONT;
      }
      return { lines: [ellipsize(ctx, text, maxWidth)], size: size };
    }
    var words = text.split(/\s+/), lines = [], line = '';
    words.forEach(function (word) {
      var next = line ? line + ' ' + word : word;
      if (ctx.measureText(next).width <= maxWidth || !line) line = next;
      else { lines.push(line); line = word; }
    });
    if (line) lines.push(line);
    if (lines.length > maxLines) {
      lines = lines.slice(0, maxLines);
      lines[maxLines - 1] = ellipsize(ctx, lines[maxLines - 1] + '…', maxWidth);
    }
    return { lines: lines.map(function (l) { return ellipsize(ctx, l, maxWidth); }), size: size };
  }

  function ellipsize(ctx, text, maxWidth) {
    if (ctx.measureText(text).width <= maxWidth) return text;
    while (text.length > 1 && ctx.measureText(text + '…').width > maxWidth) text = text.slice(0, -1);
    return text + '…';
  }

  function drawLabel(ctx, label, x, y, w, h, s) {
    var roll = spec.kind === 'roll';
    var padX = (roll ? 2 : 3) * s, padY = (roll ? 1 : 2) * s;
    var innerW = w * s - 2 * padX;
    var blocks = [];
    var shown = data.shown;
    var px = function (pt) { return pt * PT * s; };
    ctx.direction = data.rtl ? 'rtl' : 'ltr';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    if (shown.shop && data.shop) blocks.push({ text: data.shop, size: px(spec.small_pt), weight: '400', lines: 1, color: '#475467' });
    if (shown.name) blocks.push({ text: label.name, size: px(spec.name_pt), weight: '600', lines: 2, color: '#0B1F3A' });
    if (shown.price) blocks.push({ text: label.price, size: px(spec.price_pt), weight: '700', lines: 1, color: '#092851', ltr: true });
    var after = [];
    if (shown.code) after.push({ text: label.code, size: px(spec.small_pt), weight: '400', lines: 1, color: '#475467' });

    var measure = function (block) {
      var fit = fitText(ctx, block.text, innerW, block.size, block.weight, block.lines);
      block.fit = fit;
      block.height = fit.lines.length * fit.size * 1.2;
      return block.height;
    };
    var textH = blocks.reduce(function (sum, b) { return sum + measure(b); }, 0) + after.reduce(function (sum, b) { return sum + measure(b); }, 0);
    var free = h * s - 2 * padY - textH;
    var digitsH = spec.barcode_text ? Math.max(px(6.5), 2.4 * s) : 0;
    var area = spec.barcode_pct ? Math.min(free, h * s * spec.barcode_pct / 100) : free;
    area = Math.max(area, digitsH + 3 * s);
    var top = y * s + (h * s - (textH + area)) / 2;

    var drawBlock = function (block) {
      ctx.fillStyle = block.color;
      ctx.direction = block.ltr ? 'ltr' : (data.rtl ? 'rtl' : 'ltr'); // "249.00 EGP" reads number first, as on screen
      ctx.font = block.weight + ' ' + block.fit.size + 'px ' + FONT;
      block.fit.lines.forEach(function (line) {
        ctx.fillText(line, x * s + w * s / 2, top + block.fit.size * 0.1);
        top += block.fit.size * 1.2;
      });
    };
    blocks.forEach(drawBlock);

    // Bars: whole pixels per module, centred, quiet zones kept.
    var total = label.modules.length + 2 * label.quiet;
    var maxW = Math.min(spec.barcode_width * s, innerW);
    var module = Math.max(1, Math.floor(maxW / total));
    var barsW = module * total;
    var left = Math.round(x * s + (w * s - barsW) / 2) + label.quiet * module;
    var barH = Math.max(area - digitsH, 2 * s);
    ctx.fillStyle = '#000000';
    var runStart = -1;
    for (var i = 0; i <= label.modules.length; i++) {
      var bit = label.modules.charAt(i) === '1';
      if (bit && runStart < 0) runStart = i;
      if (!bit && runStart >= 0) {
        ctx.fillRect(left + runStart * module, Math.round(top), (i - runStart) * module, Math.round(barH));
        runStart = -1;
      }
    }
    if (spec.barcode_text) {
      ctx.direction = 'ltr';
      ctx.font = '400 ' + Math.min(px(7), digitsH * 0.85) + 'px ui-monospace, Menlo, Consolas, monospace';
      ctx.fillText(label.text, x * s + w * s / 2, top + barH + digitsH * 0.08);
      ctx.direction = data.rtl ? 'rtl' : 'ltr';
    }
    top += area;
    after.forEach(drawBlock);
  }

  function pages() {
    var out = [];
    var dx = data.offset[0] || 0, dy = data.offset[1] || 0;
    if (spec.kind === 'roll') {
      data.labels.forEach(function (label) { out.push({ w: spec.width, h: spec.height, dpi: 600, items: [{ label: label, x: dx, y: dy }] }); });
      return out;
    }
    var perPage = spec.columns * spec.rows;
    var gridW = spec.columns * spec.width + (spec.columns - 1) * spec.gap_x;
    var x0 = spec.side === null || spec.side === undefined ? (210 - gridW) / 2 : spec.side;
    for (var start = 0; start < data.labels.length; start += perPage) {
      var items = [];
      data.labels.slice(start, start + perPage).forEach(function (label, i) {
        var col = i % spec.columns, row = Math.floor(i / spec.columns);
        if (data.rtl) col = spec.columns - 1 - col; // the print grid fills right to left in Arabic
        items.push({ label: label, x: x0 + col * (spec.width + spec.gap_x) + dx, y: spec.top + row * (spec.height + spec.gap_y) + dy });
      });
      out.push({ w: 210, h: 297, dpi: 300, items: items });
    }
    return out;
  }

  function renderPage(page) {
    var s = page.dpi / 25.4;
    var canvas = document.createElement('canvas');
    canvas.width = Math.round(page.w * s);
    canvas.height = Math.round(page.h * s);
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    page.items.forEach(function (item) { drawLabel(ctx, item.label, item.x, item.y, spec.width, spec.height, s); });
    var jpeg = canvas.toDataURL('image/jpeg', 0.95);
    var raw = atob(jpeg.split(',')[1]);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    canvas.width = canvas.height = 0; // free the memory on phones
    return { wPt: page.w * 72 / 25.4, hPt: page.h * 72 / 25.4, pxW: Math.round(page.w * s), pxH: Math.round(page.h * s), jpeg: bytes };
  }

  function buildPdf(rendered) {
    var parts = [], offsets = [], length = 0;
    var enc = new TextEncoder();
    var push = function (chunk) {
      var bytes = typeof chunk === 'string' ? enc.encode(chunk) : chunk;
      parts.push(bytes);
      length += bytes.length;
    };
    var object = function (n, body) { offsets[n] = length; push(n + ' 0 obj\n' + body + '\nendobj\n'); };
    var fixed = function (n) { return n.toFixed(2); };
    push('%PDF-1.4\n');
    push(new Uint8Array([0x25, 0xE2, 0xE3, 0xCF, 0xD3, 0x0A]));
    var kids = rendered.map(function (_, i) { return (3 + i * 3) + ' 0 R'; }).join(' ');
    object(1, '<< /Type /Catalog /Pages 2 0 R >>');
    object(2, '<< /Type /Pages /Kids [' + kids + '] /Count ' + rendered.length + ' >>');
    rendered.forEach(function (page, i) {
      var pageN = 3 + i * 3, contentN = pageN + 1, imageN = pageN + 2;
      object(pageN, '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ' + fixed(page.wPt) + ' ' + fixed(page.hPt) + '] ' +
        '/Resources << /XObject << /Im0 ' + imageN + ' 0 R >> >> /Contents ' + contentN + ' 0 R >>');
      var draw = 'q ' + fixed(page.wPt) + ' 0 0 ' + fixed(page.hPt) + ' 0 0 cm /Im0 Do Q';
      object(contentN, '<< /Length ' + draw.length + ' >>\nstream\n' + draw + '\nendstream');
      offsets[imageN] = length;
      push(imageN + ' 0 obj\n<< /Type /XObject /Subtype /Image /Width ' + page.pxW + ' /Height ' + page.pxH +
        ' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ' + page.jpeg.length + ' >>\nstream\n');
      push(page.jpeg);
      push('\nendstream\nendobj\n');
    });
    var count = 3 + rendered.length * 3;
    var xref = length;
    var table = 'xref\n0 ' + count + '\n0000000000 65535 f \n';
    for (var n = 1; n < count; n++) table += String(offsets[n]).padStart(10, '0') + ' 00000 n \n';
    push(table + 'trailer\n<< /Size ' + count + ' /Root 1 0 R >>\nstartxref\n' + xref + '\n%%EOF\n');
    return new Blob(parts, { type: 'application/pdf' });
  }

  var label = button.textContent;
  var status = document.querySelector('[data-pdf-status]');
  button.addEventListener('click', function () {
    button.disabled = true;
    button.textContent = button.dataset.labelWorking;
    var ready = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    ready.then(function () {
      return new Promise(function (resolve) { setTimeout(resolve, 30); });
    }).then(function () {
      var blob = buildPdf(pages().map(renderPage));
      var url = URL.createObjectURL(blob);
      var link = document.createElement('a');
      link.href = url;
      link.download = data.file;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
      window.__labelPdfSize = blob.size; // read by the browser test
    }).catch(function () {
      if (status) status.textContent = button.dataset.labelFailed;
    }).then(function () {
      button.disabled = false;
      button.textContent = label;
    });
  });
})();
