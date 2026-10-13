/* FB-REPORTS: print (or save as PDF) and download for Excel, on every reading screen.
   Works on what the page shows (its summary figures and its tables, as this viewer sees them),
   and on every page of a long list: the other pages are fetched the same way the screen would show them. */
(function () {
  'use strict';
  var tools = document.querySelector('[data-report-tools]');
  var head = document.querySelector('.hs-print-head');
  var main = document.getElementById('hs-main') || document.querySelector('main');
  if (!tools || !main) return;
  var en = document.body.dataset.lang === 'en';

  function clean(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  function visible(el) { return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length); }
  function tablesIn(root, mustShow) {
    return Array.prototype.filter.call(root.querySelectorAll('table'), function (t) {
      return (!mustShow || visible(t)) && !t.closest('form, [data-report-tools], .hs-feedback') && t.querySelector('td, th');
    });
  }
  function summaries() {
    return Array.prototype.filter.call(main.querySelectorAll('.op-summary-grid, .op-aging-summary'), visible);
  }
  if (!tablesIn(main, true).length && !summaries().length) return;  // nothing to print or export on this screen

  var title = clean((main.querySelector('h1') || {}).textContent || document.title);
  var row = main.querySelector('.op-title-row, .md-title-row');
  if (row && row.parentNode) row.parentNode.insertBefore(tools, row.nextSibling);
  else main.insertBefore(tools, main.firstChild);
  if (head) {
    main.insertBefore(head, main.firstChild);
    head.querySelector('[data-print-title]').textContent = title;
  }
  tools.hidden = false;

  /* ---- every page of a long list (whatever the pager looks like: follow "page=" links until there is no next) ---- */
  function hasPage(root, n) {
    return Array.prototype.some.call(root.querySelectorAll('a[href*="page="]'), function (a) {
      return new URL(a.getAttribute('href'), window.location.href).searchParams.get('page') === String(n);
    });
  }
  function pageUrl(n) {
    var url = new URL(window.location.href);
    url.searchParams.set('page', String(n));
    return url.toString();
  }
  var allRows = null;  // per table index: the body rows of every page (empty: the page already has them all)
  function loadAll() {
    if (allRows) return Promise.resolve(allRows);
    if (!main.querySelector('a[href*="page="]')) {
      allRows = [];
      return Promise.resolve(allRows);
    }
    var collected = [];
    function fetchPage(n) {
      return fetch(pageUrl(n), { credentials: 'same-origin' }).then(function (response) { return response.text(); }).then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var root = doc.getElementById('hs-main') || doc.querySelector('main') || doc.body;
        tablesIn(root, false).forEach(function (table, index) {
          var rows = table.tBodies.length ? table.tBodies[0].rows : table.rows;
          collected[index] = (collected[index] || []).concat(Array.prototype.slice.call(rows));
        });
        return n < 200 && hasPage(root, n + 1) ? fetchPage(n + 1) : collected;
      });
    }
    return fetchPage(1).then(function (rows) { allRows = rows; return rows; });
  }

  /* ---- Excel (CSV, UTF-8 with a byte-order mark so Arabic opens right) ---- */
  function quote(text) {
    text = clean(text);
    return /[",\n;]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }
  function cell(el) {
    var input = el.querySelector('input, select, textarea');
    return quote(input ? (input.value || '') : (el.innerText || el.textContent));
  }
  var FIELD_NAMES = en ? { date_from: 'From', date_to: 'To', q: 'Search', status: 'Status', period: 'Period' }
    : { date_from: 'من', date_to: 'إلى', q: 'بحث', status: 'الحالة', period: 'الفترة' };
  function filterLines() {
    var lines = [];
    var form = main.querySelector('form.op-filters, form[method="get"]');
    if (!form) return lines;
    Array.prototype.forEach.call(form.querySelectorAll('input[name], select[name]'), function (field) {
      if (field.type === 'hidden' || field.type === 'submit' || !field.value) return;
      var label = field.id && form.querySelector('label[for="' + field.id + '"]');
      var name = label ? label.textContent : (field.getAttribute('aria-label') || FIELD_NAMES[field.name] || field.placeholder || field.name);
      var value = field.tagName === 'SELECT' ? (field.options[field.selectedIndex] || {}).text : field.value;
      lines.push([quote(name), quote(value)].join(','));
    });
    return lines;
  }
  function headingFor(table) {
    var node = table.closest('.op-table-wrap') || table;
    for (var prev = node.previousElementSibling; prev; prev = prev.previousElementSibling) {
      if (/^H[1-4]$/.test(prev.tagName)) return clean(prev.textContent);
      if (prev.tagName === 'TABLE' || prev.classList.contains('op-table-wrap')) break;
    }
    var caption = table.querySelector('caption');
    return caption ? clean(caption.textContent) : '';
  }
  function bodyRows(table, rows, index) {
    if (rows[index]) return rows[index];
    return Array.prototype.slice.call(table.tBodies.length ? table.tBodies[0].rows : table.rows);
  }
  function csv(rows) {
    var lines = [quote(title)].concat(filterLines());
    summaries().forEach(function (grid) {
      lines.push('');
      Array.prototype.forEach.call(grid.querySelectorAll('article'), function (card) {
        var label = card.querySelector('small');
        var value = card.querySelector('strong');
        if (label && value) lines.push([cell(label), cell(value)].join(','));
      });
    });
    tablesIn(main, false).forEach(function (table, index) {
      lines.push('');
      var heading = headingFor(table);
      if (heading) lines.push(quote(heading));
      var headRows = table.tHead ? Array.prototype.slice.call(table.tHead.rows) : [];
      var footRows = table.tFoot ? Array.prototype.slice.call(table.tFoot.rows) : [];
      // A column with no heading holds buttons and links ("edit", "statement"): not data.
      var header = headRows.length ? headRows[headRows.length - 1] : null;
      var skip = {};
      if (header) Array.prototype.forEach.call(header.cells, function (th, i) { if (!clean(th.textContent)) skip[i] = true; });
      var width = header ? header.cells.length : -1;
      headRows.concat(bodyRows(table, rows, index), footRows).forEach(function (tr) {
        var cells = Array.prototype.filter.call(tr.cells, function (td, i) { return tr.cells.length !== width || !skip[i]; });
        lines.push(cells.map(cell).join(','));
      });
    });
    return '﻿' + lines.join('\r\n');
  }
  function stamp() {
    var d = new Date();
    function two(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate());
  }
  function busy(button, on) {
    button.disabled = on;
    button.setAttribute('aria-busy', on ? 'true' : 'false');
  }

  var excel = tools.querySelector('[data-report-excel]');
  excel.addEventListener('click', function () {
    busy(excel, true);
    loadAll().then(function (rows) {
      var name = title.replace(/[\\/:*?"<>|]+/g, ' ').slice(0, 60) || 'hesba';
      var link = document.createElement('a');
      link.href = URL.createObjectURL(new Blob([csv(rows)], { type: 'text/csv;charset=utf-8' }));
      link.download = name + ' ' + stamp() + '.csv';
      document.body.appendChild(link);
      link.click();
      setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 0);
    }).finally(function () { busy(excel, false); });
  });

  /* ---- print: every page of the list on paper, then the screen as it was ---- */
  var printButton = tools.querySelector('[data-report-print]');
  printButton.addEventListener('click', function () {
    busy(printButton, true);
    var when = head && head.querySelector('[data-print-when]');
    if (when) when.textContent = new Date().toLocaleString(en ? 'en-GB' : 'ar-EG');
    var filters = head && head.querySelector('[data-print-filters]');
    if (filters) filters.textContent = filterLines().map(function (line) { return line.replace(/"/g, '').replace(',', ': '); }).join(' · ');
    loadAll().then(function (rows) {
      var swapped = [];
      tablesIn(main, false).forEach(function (table, index) {
        if (!rows[index] || !table.tBodies.length) return;
        var full = document.createElement('tbody');
        rows[index].forEach(function (tr) { full.appendChild(document.importNode(tr, true)); });
        swapped.push([table, full, table.tBodies[0]]);
        table.replaceChild(full, table.tBodies[0]);
      });
      var restore = function () {
        swapped.forEach(function (entry) { entry[0].replaceChild(entry[2], entry[1]); });
        window.removeEventListener('afterprint', restore);
      };
      window.addEventListener('afterprint', restore);
      window.print();
    }).finally(function () { busy(printButton, false); });
  });
})();
