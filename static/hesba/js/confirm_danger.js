/* FB-27: "are you sure?" before anything that deletes, removes or cancels.
 *
 * A form sent by a red button (op-danger), or whose action is remove…, delete… or cancel…, first opens a small box
 * naming what is about to happen; only "Yes" sends it. A form with its own confirm (onsubmit, data-confirm) keeps
 * it, and data-no-confirm opts a form out.
 */
(function () {
  'use strict';
  var en = document.body.dataset.lang === 'en';
  var dialog = null, pending = null;

  function risky(form, submitter) {
    if (form.hasAttribute('onsubmit') || form.hasAttribute('data-confirm') || form.hasAttribute('data-no-confirm')) { return false; }
    if (submitter && submitter.classList && submitter.classList.contains('op-danger')) { return true; }
    var action = (submitter && submitter.name === 'action' && submitter.value) || (form.elements.action && form.elements.action.value) || '';
    return /^(remove|delete|cancel)/.test(String(action));
  }

  function label(form, submitter) {
    var text = submitter && (submitter.textContent || submitter.value || '').trim();
    if (!text) { var first = form.querySelector('button[type="submit"], button:not([type])'); text = first ? first.textContent.trim() : ''; }
    return text;
  }

  function box() {
    if (dialog) { return dialog; }
    dialog = document.createElement('dialog');
    dialog.className = 'hs-confirm';
    dialog.setAttribute('data-confirm-box', '');
    dialog.innerHTML = '<h2></h2><p></p><div class="hs-confirm__actions"><button type="button" class="op-danger" data-confirm-yes></button>' +
      '<button type="button" class="op-secondary" data-confirm-no></button></div>';
    dialog.querySelector('h2').textContent = en ? 'Are you sure?' : 'متأكد؟';
    dialog.querySelector('[data-confirm-no]').textContent = en ? 'Go back' : 'رجوع';
    document.body.appendChild(dialog);
    dialog.querySelector('[data-confirm-no]').addEventListener('click', function () { dialog.close(); });
    dialog.addEventListener('close', function () { pending = null; });
    dialog.querySelector('[data-confirm-yes]').addEventListener('click', function () {
      var go = pending;
      pending = null;
      dialog.close();
      if (!go) { return; }
      go.form.setAttribute('data-confirmed', '');
      if (go.form.requestSubmit) { go.form.requestSubmit(go.submitter || undefined); } else { go.form.submit(); }
    });
    return dialog;
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !window.HTMLDialogElement) { return; }
    if (form.hasAttribute('data-confirmed')) { form.removeAttribute('data-confirmed'); return; }
    var submitter = event.submitter || null;
    if (!risky(form, submitter)) { return; }
    event.preventDefault();
    var what = label(form, submitter);
    var d = box();
    d.querySelector('p').textContent = what ? (en ? 'You are about to: ' + what + '.' : 'إنت هتعمل: ' + what + '.') : '';
    d.querySelector('[data-confirm-yes]').textContent = what || (en ? 'Yes' : 'أيوه');
    pending = { form: form, submitter: submitter };
    d.showModal();
    d.querySelector('[data-confirm-no]').focus();  // the safe choice is the one under the finger
  }, true);
})();
