/* AN5: the item form's sizes and colours picker. Shows what will be made, and hides the single
   size, colour and barcode fields while the item comes in several (each combination gets its own). */
(function () {
  var box = document.querySelector('[data-variant-pick]');
  if (!box) return;
  var form = box.closest('form');
  var toggle = box.querySelector('input[name="has_variants"]');
  var body = box.querySelector('[data-variant-body]');
  var count = box.querySelector('[data-variant-count]');
  var list = box.querySelector('[data-variant-list]');
  var single = [form.querySelector('[data-section="variant"]'), (form.querySelector('[name="barcode"]') || {}).parentNode].filter(Boolean);
  var MAX_SHOWN = 12;

  function values(kind) {
    var out = [];
    box.querySelectorAll('[data-variant-kind="' + kind + '"] input[type=checkbox]:checked').forEach(function (input) { out.push(input.value); });
    var extra = box.querySelector('[data-variant-extra="' + kind + '"]');
    (extra && extra.value || '').split(/[,،\n]+/).forEach(function (part) {
      var value = part.trim();
      if (value && out.every(function (seen) { return seen.toLowerCase() !== value.toLowerCase(); })) out.push(value);
    });
    return out;
  }

  function render() {
    var on = toggle.checked;
    body.hidden = !on;
    single.forEach(function (node) { node.hidden = on; });
    if (!on) return;
    var sizes = values('sizes'), colors = values('colors');
    var combos = [];
    (sizes.length ? sizes : ['']).forEach(function (size) {
      (colors.length ? colors : ['']).forEach(function (color) { if (size || color) combos.push([size, color].filter(Boolean).join(' · ')); });
    });
    var name = (form.querySelector('[name="item_name"]') || {}).value || '';
    count.textContent = combos.length ? box.dataset.wordsCount.replace('{n}', combos.length) : '';
    list.innerHTML = '';
    combos.slice(0, MAX_SHOWN).forEach(function (combo) {
      var li = document.createElement('li');
      li.textContent = (name ? name + ' · ' : '') + combo;
      list.appendChild(li);
    });
    if (combos.length > MAX_SHOWN) {
      var more = document.createElement('li');
      more.textContent = '+' + (combos.length - MAX_SHOWN);
      list.appendChild(more);
    }
  }

  form.addEventListener('input', render);
  form.addEventListener('change', render);
  render();
})();
