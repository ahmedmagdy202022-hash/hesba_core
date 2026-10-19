/* AN6 (Ahmed: "شاشة الكاشير محتاجة تكون أسهل وأسلس"): tap-to-add tiles for the items sold most,
   one-tap cash amounts, and the warehouse/cashbox tucked away with their names showing.
   Tiles go through the till's own name search, so pricing, units and serials stay pos.js's job. */
(function () {
  var form = document.querySelector('[data-pos]');
  if (!form) return;
  var catalog = JSON.parse((document.getElementById('hs-item-catalog') || {}).textContent || '[]');
  var picks = JSON.parse((document.getElementById('hs-quick-picks') || {}).textContent || '[]');
  var byId = {};
  catalog.forEach(function (item) { byId[String(item.id)] = item; });
  var box = form.querySelector('[data-pos-quick]'), grid = form.querySelector('[data-pos-quick-grid]');
  var search = form.querySelector('[data-pos-search]');
  if (box && grid && search && picks.length) {
    picks.forEach(function (pick) {
      var item = byId[String(pick.id)];
      if (!item) return;
      var tile = document.createElement('button');
      tile.type = 'button'; tile.className = 'pos-tile'; tile.setAttribute('data-pos-tile', item.id);
      var name = document.createElement('span'); name.textContent = pick.name || item.label;
      var price = document.createElement('b'); price.textContent = Number(item.price || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      tile.appendChild(name); tile.appendChild(price);
      tile.addEventListener('click', function () {
        search.value = item.label;
        search.dispatchEvent(new Event('change', { bubbles: true }));
        tile.classList.add('is-added'); setTimeout(function () { tile.classList.remove('is-added'); }, 350);
      });
      grid.appendChild(tile);
    });
    box.hidden = !grid.children.length;
    // AN7 (G07), AN8 (Ahmed: "قلل عددهم وحط كلمة المزيد"): the first eight (six on a phone) show; the rest
    // wait behind "المزيد", so the basket stays in view.
    if (grid.children.length > 6) {
      var more = document.createElement('button');
      more.type = 'button'; more.className = 'pos-quick__more'; more.setAttribute('data-pos-quick-more', '');
      var all = box.getAttribute('data-all') || 'All buttons', fewer = box.getAttribute('data-fewer') || 'Fewer';
      more.textContent = all + ' (' + grid.children.length + ')';
      more.addEventListener('click', function () {
        var open = grid.classList.toggle('is-open');
        more.textContent = open ? fewer : all + ' (' + grid.children.length + ')';
      });
      grid.after(more);
      if (grid.children.length <= 8) more.classList.add('pos-quick__more--phone');
    }
  }

  var tendered = form.querySelector('[data-pos-tendered]'), total = form.querySelector('[data-pos-total]');
  form.querySelectorAll('[data-cash]').forEach(function (button) {
    button.addEventListener('click', function () {
      var due = parseFloat(String(total.textContent || '0').replace(/,/g, '')) || 0;
      // Notes add up (100 + 50 = 150) once a button has started the amount; typing starts over.
      var current = tendered.dataset.cashAdded ? (parseFloat(tendered.value) || 0) : 0;
      var value = button.dataset.cash === 'exact' ? due : current + parseFloat(button.dataset.cash);
      tendered.dataset.cashAdded = '1';
      tendered.value = value.toFixed(2);
      tendered.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });
  tendered.addEventListener('keydown', function () { delete tendered.dataset.cashAdded; });
  form.addEventListener('reset', function () { delete tendered.dataset.cashAdded; });

  // AN8 (Ahmed: "ضيف طرق دفع زي دفع كاش او فيزا"): a card pays the total exactly, so no notes and no change.
  var method = form.querySelector('[data-pos-method]'), cashButtons = form.querySelector('[data-pos-cash]');
  function pay(kind) {
    if (!method) return;
    method.value = kind;
    form.querySelectorAll('[data-pay]').forEach(function (button) {
      var on = button.getAttribute('data-pay') === kind;
      button.classList.toggle('is-on', on); button.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    if (cashButtons) cashButtons.hidden = kind === 'card';
    tendered.readOnly = kind === 'card';
    if (kind === 'card') {
      delete tendered.dataset.cashAdded;
      tendered.value = '';
      tendered.dispatchEvent(new Event('input', { bubbles: true }));  // the till fills the total back in
    }
  }
  form.querySelectorAll('[data-pay]').forEach(function (button) {
    button.addEventListener('click', function () { pay(button.getAttribute('data-pay')); });
  });
  form.addEventListener('reset', function () { pay('cash'); });
  var clear = form.querySelector('[data-pos-clear]');
  if (clear) clear.addEventListener('click', function () { pay('cash'); });

  var more = form.querySelector('.pos-more'), summary = form.querySelector('[data-pos-more-summary]');
  function show() {
    if (!more || !summary) return;
    var parts = [];
    more.querySelectorAll('select').forEach(function (select) { var option = select.options[select.selectedIndex]; if (option) parts.push(option.textContent.trim()); });
    summary.textContent = parts.length ? '· ' + parts.join(' · ') : '';
  }
  if (more) more.addEventListener('change', show);
  show();
})();
