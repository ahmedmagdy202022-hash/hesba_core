/* M9 (HG-092): the sample box. A hardware scanner types the code and presses Enter, which submits the form by itself. The phone
   camera (camera_scan.js) writes the code into the box and sends a synthetic Enter, which a browser does not turn into a submit,
   so this does. Nothing else lives here: the code is looked up on the server. */
(function () {
  'use strict';
  var box = document.querySelector('[data-lab-sample-box]');
  if (!box || !box.form) { return; }
  box.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && event.isTrusted === false && box.value.trim()) {
      event.preventDefault();
      if (box.form.requestSubmit) { box.form.requestSubmit(); } else { box.form.submit(); }
    }
  });
}());
