/* APPT-001: choosing a service fills in its price, unless a price was typed. */
(function () {
  'use strict';
  document.querySelectorAll('[data-appointment-form]').forEach(function (form) {
    var service = form.querySelector('[name=service]');
    var price = form.querySelector('[name=price]');
    if (!service || !price) { return; }
    var auto = price.value === '';
    price.addEventListener('input', function () { auto = price.value === ''; });
    service.addEventListener('change', function () {
      var option = service.options[service.selectedIndex];
      if (auto && option && option.getAttribute('data-price') !== null) { price.value = option.getAttribute('data-price'); }
    });
  });
})();
