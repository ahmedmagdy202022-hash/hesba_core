/*
 * MOBILE-002: on phones, every data table reads as a stack of cards.
 *
 * The audit (docs/MOBILE_001_AUDIT.md, M1) found 29 screens whose tables are
 * wider than a phone, hiding the column the user came for (total, quantity,
 * balance) behind an unmarked sideways scroll. This labels each cell with its
 * column header once; table_cards.css then lays each row out as a card below
 * 700px. Without this script the table keeps its current scrolling layout,
 * so nothing breaks if it fails to load.
 */
(function () {
  function label(table) {
    var head = table.tHead && table.tHead.rows[0];
    if (!head) return;
    var names = [];
    for (var i = 0; i < head.cells.length; i++) {
      var cell = head.cells[i];
      var span = cell.colSpan || 1;
      for (var s = 0; s < span; s++) names.push((cell.textContent || '').trim());
    }
    for (var b = 0; b < table.tBodies.length; b++) {
      var rows = table.tBodies[b].rows;
      for (var r = 0; r < rows.length; r++) {
        var col = 0;
        for (var c = 0; c < rows[r].cells.length; c++) {
          var td = rows[r].cells[c];
          if (td.colSpan > 1) { td.setAttribute('data-card-full', ''); col += td.colSpan; continue; }
          if (names[col] && !td.hasAttribute('data-label')) td.setAttribute('data-label', names[col]);
          col += 1;
        }
      }
    }
    table.classList.add('hs-cards');
  }
  function run() {
    var tables = document.querySelectorAll('table.op-table, table.md-table');
    for (var i = 0; i < tables.length; i++) label(tables[i]);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
