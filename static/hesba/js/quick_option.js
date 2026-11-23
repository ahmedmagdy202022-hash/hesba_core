/* FB-TYPES: "+ new …" on a list you pick from, anywhere it makes sense (item category, expense category, asset type…).
   A <select data-quick-add="+ New category"> gets one more option. Choosing it opens a small box under the list to
   type the name; with data-quick-add-url it is saved there (POST name → {ok, id, label}) and picked, and without a
   URL the typed name itself becomes the choice (data-quick-add-field names the hidden input that carries it).
   FB-26: with data-quick-add-form (a supplier, a customer) the record's full form opens in a box instead, and the
   record saved there is picked. */
(function () {
  'use strict';
  var NEW = '__new__';
  var en = document.body.dataset.lang === 'en';
  function csrf(select) {
    var form = select.form || document;
    var token = form.querySelector('input[name="csrfmiddlewaretoken"]') || document.querySelector('input[name="csrfmiddlewaretoken"]');
    return token ? token.value : '';
  }
  document.querySelectorAll('select[data-quick-add]').forEach(function (select) {
    var option = document.createElement('option');
    option.value = NEW;
    option.textContent = select.getAttribute('data-quick-add');
    select.appendChild(option);
    var previous = select.value === NEW ? '' : select.value;
    var box = document.createElement('div');
    box.className = 'hs-quick-option';
    box.hidden = true;
    box.innerHTML = '<input type="text" maxlength="60" autocomplete="off" dir="auto"><button type="button" class="op-secondary">' + (en ? 'Add' : 'إضافة') +
      '</button><button type="button" class="op-secondary">' + (en ? 'Cancel' : 'إلغاء') + '</button><small class="op-field-error" hidden></small>';
    select.insertAdjacentElement('afterend', box);
    var input = box.querySelector('input');
    var add = box.querySelectorAll('button')[0];
    var cancel = box.querySelectorAll('button')[1];
    var error = box.querySelector('small');
    input.setAttribute('aria-label', select.getAttribute('data-quick-add'));
    input.setAttribute('form', 'qo-detached');  // never sent with the form it sits in
    var field = select.getAttribute('data-quick-add-field');
    var carrier = field ? select.form && select.form.querySelector('input[name="' + field + '"]') : null;

    var formUrl = select.getAttribute('data-quick-add-form');
    var dialog = null, frame = null;
    function openForm() {
      if (!dialog) {
        dialog = document.createElement('dialog');
        dialog.className = 'hs-quick-item hs-quick-form';
        dialog.innerHTML = '<iframe title=""></iframe>';
        frame = dialog.querySelector('iframe');
        frame.setAttribute('title', select.getAttribute('data-quick-add'));
        document.body.appendChild(dialog);
        dialog.addEventListener('close', function () { if (select.value === NEW) { select.value = previous; } });
        window.addEventListener('message', function (event) {
          if (event.origin !== window.location.origin || event.source !== frame.contentWindow || !event.data) { return; }
          if (event.data.hesbaQuickAdd === 'saved') { pick(String(event.data.id), event.data.label); dialog.close(); }
          else if (event.data.hesbaQuickAdd === 'cancel') { dialog.close(); }
        });
      }
      frame.src = formUrl + (formUrl.indexOf('?') < 0 ? '?' : '&') + 'popup=1&lang=' + (en ? 'en' : 'ar');
      dialog.showModal();
    }

    select.addEventListener('change', function () {
      if (select.value !== NEW) { previous = select.value; if (carrier) { carrier.value = ''; } return; }
      if (formUrl && window.HTMLDialogElement) { openForm(); return; }
      box.hidden = false;
      error.hidden = true;
      input.value = '';
      input.focus();
    });
    cancel.addEventListener('click', function () { box.hidden = true; select.value = previous; });
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); add.click(); }
      if (event.key === 'Escape') { cancel.click(); }
    });
    function pick(value, label) {
      var made = document.createElement('option');
      made.value = value;
      made.textContent = label;
      select.insertBefore(made, option);
      select.value = value;
      previous = value;
      box.hidden = true;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    add.addEventListener('click', function () {
      var name = input.value.trim();
      if (!name) { input.focus(); return; }
      var url = select.getAttribute('data-quick-add-url');
      if (!url) {
        if (carrier) { carrier.value = name; }
        pick(name, name);
        return;
      }
      var body = new FormData();
      body.append('name', name);
      body.append('lang', en ? 'en' : 'ar');
      add.disabled = true;
      fetch(url, { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf(select) } })
        .then(function (response) { return response.json(); })
        .then(function (answer) {
          if (!answer.ok) { error.textContent = answer.error || ''; error.hidden = false; return; }
          pick(String(answer.id), answer.label);
        })
        .catch(function () { error.textContent = en ? 'Could not save; try again.' : 'محصلش حفظ؛ جرّب تاني.'; error.hidden = false; })
        .finally(function () { add.disabled = false; });
    });
  });
})();
