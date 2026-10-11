/* UX-ONCE: a form that changes something is sent once, however fast it is clicked.
 *
 * The second click on "Save", "Sell" or "Bill" used to send the form twice and make two records. Once a POST form is
 * on its way, a further submit of it is ignored and its buttons are shown busy. A page shown again from the back
 * button, or a form whose request did not leave the page (a file download, a script that handled it itself), is
 * usable again. A form can opt out with data-submit-many (one that opens a new tab, for instance).
 */
(function () {
  'use strict';

  var BUSY_FOR_MS = 8000;  // after this the form is usable again, in case the page never moved on

  function sendsOnce(form) {
    return (form.method || '').toLowerCase() === 'post' && !form.hasAttribute('data-submit-many') && form.target !== '_blank';
  }

  function release(form) {
    delete form.dataset.submitting;
    form.removeAttribute('aria-busy');
    form.querySelectorAll('[data-submit-busy]').forEach(function (button) {
      button.removeAttribute('data-submit-busy');
      button.removeAttribute('aria-disabled');
    });
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !sendsOnce(form)) {
      return;
    }
    if (form.dataset.submitting === '1') {  // already on its way
      event.preventDefault();
      return;
    }
    if (event.defaultPrevented) {  // the page's own script handled it (validation, fetch): nothing was sent
      return;
    }
    form.dataset.submitting = '1';
    form.setAttribute('aria-busy', 'true');
    // Marked after the browser has read the form, so the button that was pressed still sends its name and value.
    window.setTimeout(function () {
      form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]').forEach(function (button) {
        button.setAttribute('data-submit-busy', '');
        button.setAttribute('aria-disabled', 'true');
      });
    }, 0);
    window.setTimeout(function () { release(form); }, BUSY_FOR_MS);
  });

  window.addEventListener('pageshow', function () {  // back to a page kept in the browser's memory
    document.querySelectorAll('form[data-submitting]').forEach(release);
  });
})();
