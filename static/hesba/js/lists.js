/* UX Wave 2: list screens (lists.css).
   - A filter panel folded behind «فلترة» on a phone ([data-filter-fold]) stays open on wider screens,
     for browsers that cannot open it from CSS alone.
   - A «⋯» menu (.hs-more) closes on a tap outside it or on Escape.
   Without this script the panel and the menu still open and close by tapping their summary. */
(function () {
  'use strict';
  var wide = window.matchMedia ? window.matchMedia('(min-width: 701px)') : null;
  function sync() {
    var open = !!(wide && wide.matches);
    document.querySelectorAll('details[data-filter-fold]').forEach(function (node) {
      if (open) node.open = true;
    });
  }
  sync();
  if (wide && wide.addEventListener) wide.addEventListener('change', sync);

  function closeMenus(except) {
    document.querySelectorAll('details.hs-more[open]').forEach(function (menu) {
      if (menu !== except) menu.open = false;
    });
  }
  document.addEventListener('click', function (event) {
    closeMenus(event.target.closest ? event.target.closest('details.hs-more') : null);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeMenus(null);
  });
})();
