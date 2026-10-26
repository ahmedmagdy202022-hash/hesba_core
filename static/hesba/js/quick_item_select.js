/* FB-22: "+ new item" beside a plain item list, as on the purchase order's line form. The item is saved through the
 * same quick form and endpoint as on the invoice, then added to the list and picked, with its purchase price.
 * The form names its list and price box: data-quick-item-select="#po-item" data-quick-item-price="#po-price".
 */
(function () {
  'use strict';

  document.querySelectorAll('[data-quick-item-select]').forEach(function (form) {
    var quick = form.querySelector('[data-quick-item]');
    var opener = form.querySelector('[data-quick-item-open]');
    var select = document.querySelector(form.getAttribute('data-quick-item-select'));
    var price = form.getAttribute('data-quick-item-price') ? document.querySelector(form.getAttribute('data-quick-item-price')) : null;
    if (!quick || !opener || !select || !quick.showModal) { return; }
    var error = quick.querySelector('[data-quick-item-error]');
    var save = quick.querySelector('[data-quick-item-save]');
    var csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');

    opener.addEventListener('click', function () {
      error.hidden = true;
      quick.showModal();
      quick.querySelector('[name="item_name"]').focus();
    });
    quick.querySelector('[data-quick-item-close]').addEventListener('click', function () { quick.close(); });
    quick.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && event.target.tagName === 'INPUT') { event.preventDefault(); save.click(); }
    });
    save.addEventListener('click', function () {
      var body = new FormData();
      quick.querySelectorAll('input[name], select[name]').forEach(function (input) {
        if (input.type === 'checkbox') { if (input.checked) { body.append(input.name, 'on'); } } else { body.append(input.name, input.value); }
      });
      body.append('lang', document.body.dataset.lang === 'en' ? 'en' : 'ar');
      save.disabled = true;
      fetch(quick.getAttribute('data-url'), { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf ? csrf.value : '' } })
        .then(function (response) { return response.json(); })
        .then(function (answer) {
          if (!answer.ok) {
            var detail = answer.errors ? Object.keys(answer.errors).map(function (k) { return k + ': ' + answer.errors[k].join(' '); }).join(' · ') : '';
            error.textContent = (answer.error || '') + (detail ? ' ' + detail : '');
            error.hidden = false;
            return;
          }
          var option = document.createElement('option');
          option.value = String(answer.item.id);
          option.textContent = answer.item.label;
          select.appendChild(option);
          select.value = option.value;
          if (price && answer.item.purchase_price && answer.item.purchase_price !== '0.00') { price.value = answer.item.purchase_price; }
          quick.querySelectorAll('input[name]').forEach(function (input) {
            if (input.type === 'checkbox') { input.checked = true; } else { input.value = /price/.test(input.name) ? '0' : ''; }
          });
          quick.close();
          select.dispatchEvent(new Event('change', { bubbles: true }));
        })
        .catch(function () {
          error.textContent = document.body.dataset.lang === 'en' ? 'Not saved. Check the connection and try again.' : 'متحفظش. اتأكد من النت وجرّب تاني.';
          error.hidden = false;
        })
        .then(function () { save.disabled = false; });
    });
  });
})();
