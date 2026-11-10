/* S1 UI: photos, documents and the customer's signature on a work order.
   Vanilla JS, no libraries. The server renders the card and answers JSON; after a change the page is reloaded so
   what is shown is always what was stored. Errors come from the backend in the page language and show inline. */
(function () {
  'use strict';

  var MAX_SIDE = 1600;          // phone photos are shrunk to this long side before they are sent
  var SHRINK_ABOVE = 900 * 1024; // smaller files are sent as they are
  var SIGNATURE_MAX_WIDTH = 900;
  var FLASH_KEY = 'hs_attachments_flash';

  function token(name, fallback) {
    var value = '';
    try { value = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); } catch (e) { value = ''; }
    return value || fallback;
  }

  function flashSet(text) { try { sessionStorage.setItem(FLASH_KEY, text); } catch (e) { /* storage may be blocked */ } }
  function flashTake() {
    try {
      var text = sessionStorage.getItem(FLASH_KEY);
      if (text !== null) { sessionStorage.removeItem(FLASH_KEY); }
      return text;
    } catch (e) { return null; }
  }

  function Card(root) {
    this.root = root;
    this.errorBox = root.querySelector('[data-att-error]');
    this.statusBox = root.querySelector('[data-att-status]');
    var csrf = root.querySelector('input[name=csrfmiddlewaretoken]');
    this.csrf = csrf ? csrf.value : '';
  }

  Card.prototype.msg = function (key) { return this.root.getAttribute('data-msg-' + key) || ''; };
  Card.prototype.error = function (text) {
    this.errorBox.textContent = text || '';
    this.errorBox.hidden = !text;
  };
  Card.prototype.status = function (text) { this.statusBox.textContent = text || ''; };
  Card.prototype.busy = function (on) { this.root.classList.toggle('is-busy', !!on); };
  // After a change the card is re-read from the server. A card marked data-refresh="inline" (the clinic's, next to a visit form
  // with unsaved typing) swaps only its own content; the work order's reloads the page, as before.
  Card.prototype.reload = function () {
    var root = this.root, anchor = root.id || 'wo-attachments', self = this;
    if (root.getAttribute('data-refresh') === 'inline' && window.fetch && window.DOMParser) {
      this.busy(true);
      fetch(location.pathname + location.search, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (response) { if (!response.ok) { throw new Error('refresh'); } return response.text(); })
        .then(function (html) {
          var fresh = new DOMParser().parseFromString(html, 'text/html').getElementById(anchor);
          if (!fresh) { throw new Error('refresh'); }
          root.innerHTML = fresh.innerHTML;
          self.busy(false);
          bind(root);
          var text = flashTake();
          if (text) { new Card(root).error(text); }
        })
        .catch(function () { location.reload(); });
      return;
    }
    try { history.replaceState(null, '', location.pathname + location.search + '#' + anchor); } catch (e) { /* ignore */ }
    location.reload();
  };

  // POST and read the JSON answer; reject with a readable message (the server's own when it gave one).
  Card.prototype.send = function (url, options) {
    var self = this;
    options.method = 'POST';
    options.credentials = 'same-origin';
    options.headers = Object.assign({ 'X-CSRFToken': this.csrf, 'X-Requested-With': 'XMLHttpRequest' }, options.headers || {});
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = JSON.parse(text); } catch (e) { data = null; }
        if (data && data.ok) { return data; }
        throw new Error((data && data.error) || self.msg('failed'));
      });
    }, function () { throw new Error(self.msg('network')); });
  };

  // ---- photos and documents ----

  function decode(file) {
    if (window.createImageBitmap) {
      return createImageBitmap(file, { imageOrientation: 'from-image' }).catch(function () { return createImageBitmap(file); });
    }
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('decode')); };
      img.src = url;
    });
  }

  // A big camera photo is re-drawn smaller as JPEG. Any problem at all: the original file goes up unchanged.
  function prepare(file) {
    var type = (file.type || '').toLowerCase();
    if (['image/jpeg', 'image/png', 'image/webp'].indexOf(type) < 0 || file.size < SHRINK_ABOVE) { return Promise.resolve(file); }
    return decode(file).then(function (image) {
      var width = image.width || image.naturalWidth, height = image.height || image.naturalHeight;
      var scale = Math.min(1, MAX_SIDE / Math.max(width, height));
      var canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      var ctx = canvas.getContext('2d');
      ctx.fillStyle = token('--hs-surface', 'white');
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      if (image.close) { image.close(); }
      return new Promise(function (resolve) {
        canvas.toBlob(function (blob) {
          if (!blob || blob.size >= file.size) { resolve(file); return; }
          var name = (file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg';
          resolve(new File([blob], name, { type: 'image/jpeg' }));
        }, 'image/jpeg', 0.85);
      });
    }).catch(function () { return file; });
  }

  Card.prototype.uploadOne = function (form, file) {
    var self = this;
    return prepare(file).then(function (ready) {
      var body = new FormData(form);
      body.delete('file');
      body.set('kind', ready.type === 'application/pdf' ? 'document' : 'photo');
      body.append('file', ready, ready.name || file.name);
      return self.send(form.getAttribute('action'), { body: body });
    });
  };

  Card.prototype.upload = function (form, input) {
    var self = this;
    var files = Array.prototype.slice.call(input.files || []);
    input.value = '';
    if (!files.length) { return; }
    self.error('');
    self.busy(true);
    self.status(self.msg('uploading'));
    var saved = 0, failures = [];
    files.reduce(function (chain, file) {
      return chain.then(function () {
        return self.uploadOne(form, file).then(function () { saved += 1; }, function (err) { failures.push(err.message); });
      });
    }, Promise.resolve()).then(function () {
      var unique = failures.filter(function (text, i) { return failures.indexOf(text) === i; }).join(' ');
      if (saved && unique) { flashSet(unique); }
      if (saved) { self.reload(); return; }
      self.busy(false);
      self.status('');
      self.error(unique);
    });
  };

  Card.prototype.remove = function (button) {
    var self = this;
    if (!window.confirm(self.msg('confirm'))) { return; }
    self.error('');
    self.busy(true);
    self.status(self.msg('deleting'));
    self.send(button.getAttribute('data-url'), { body: new FormData() }).then(function () {
      self.reload();
    }, function (err) {
      self.busy(false);
      self.status('');
      self.error(err.message);
    });
  };

  // ---- the signature pad ----

  function Pad(card, form) {
    this.card = card;
    this.form = form;
    this.box = form.querySelector('[data-att-pad]');
    this.canvas = form.querySelector('[data-att-canvas]');
    this.ctx = this.canvas.getContext('2d');
    this.strokes = [];   // each stroke is a list of points in 0..1 of the pad, so a resize or a rotation keeps the drawing
    this.current = null;
    this.size();
    this.bind();
  }

  Pad.prototype.size = function () {
    var rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) { return; }
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(rect.width * ratio);
    this.canvas.height = Math.round(rect.height * ratio);
    this.ratio = ratio;
    this.redraw();
  };

  Pad.prototype.paint = function (ctx, width, height, ratio) {
    ctx.lineWidth = 2.6 * ratio;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = token('--hs-navy', 'black');
    ctx.fillStyle = ctx.strokeStyle;
    this.strokes.forEach(function (stroke) {
      if (stroke.length === 1) {
        ctx.beginPath();
        ctx.arc(stroke[0].x * width, stroke[0].y * height, ctx.lineWidth / 2, 0, Math.PI * 2);
        ctx.fill();
        return;
      }
      ctx.beginPath();
      stroke.forEach(function (p, i) {
        if (i === 0) { ctx.moveTo(p.x * width, p.y * height); } else { ctx.lineTo(p.x * width, p.y * height); }
      });
      ctx.stroke();
    });
  };

  Pad.prototype.redraw = function () {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.paint(this.ctx, this.canvas.width, this.canvas.height, this.ratio || 1);
  };

  Pad.prototype.point = function (event) {
    var rect = this.canvas.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)) };
  };

  Pad.prototype.bind = function () {
    var self = this, canvas = this.canvas;
    canvas.addEventListener('pointerdown', function (event) {
      if (event.pointerType === 'mouse' && event.button !== 0) { return; }
      event.preventDefault();
      try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* not capturable: moves still arrive */ }
      self.current = [self.point(event)];
      self.strokes.push(self.current);
      self.box.classList.add('is-dirty');
      self.redraw();
    });
    canvas.addEventListener('pointermove', function (event) {
      if (!self.current) { return; }
      event.preventDefault();
      var events = event.getCoalescedEvents ? event.getCoalescedEvents() : [];
      (events.length ? events : [event]).forEach(function (e) { self.current.push(self.point(e)); });
      self.redraw();
    });
    function stop(event) {
      if (!self.current) { return; }
      try { canvas.releasePointerCapture(event.pointerId); } catch (e) { /* ignore */ }
      self.current = null;
    }
    canvas.addEventListener('pointerup', stop);
    canvas.addEventListener('pointercancel', stop);
    canvas.addEventListener('contextmenu', function (event) { event.preventDefault(); });
    if (window.ResizeObserver) { new ResizeObserver(function () { self.size(); }).observe(canvas); } else { window.addEventListener('resize', function () { self.size(); }); }
  };

  Pad.prototype.clear = function () {
    this.strokes = [];
    this.current = null;
    this.box.classList.remove('is-dirty');
    this.redraw();
  };

  Pad.prototype.empty = function () { return this.strokes.length === 0; };

  // The drawing on a surface-coloured ground, as a PNG data URL (no transparency, so it prints and shows the same everywhere).
  Pad.prototype.dataUrl = function () {
    var scale = Math.min(1, SIGNATURE_MAX_WIDTH / this.canvas.width);
    var out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(this.canvas.width * scale));
    out.height = Math.max(1, Math.round(this.canvas.height * scale));
    var ctx = out.getContext('2d');
    ctx.fillStyle = token('--hs-surface', 'white');
    ctx.fillRect(0, 0, out.width, out.height);
    this.paint(ctx, out.width, out.height, (this.ratio || 1) * scale);
    return out.toDataURL('image/png');
  };

  Card.prototype.initSignature = function (form) {
    var self = this, pad = new Pad(this, form);
    var stage = form.querySelectorAll('input[name=stage]');
    function syncConsent() {
      var chosen = form.querySelector('input[name=stage]:checked');
      form.querySelectorAll('[data-consent]').forEach(function (p) { p.hidden = !chosen || p.getAttribute('data-consent') !== chosen.value; });
    }
    Array.prototype.forEach.call(stage, function (radio) { radio.addEventListener('change', syncConsent); });
    form.querySelector('[data-att-clear]').addEventListener('click', function () { pad.clear(); self.error(''); });
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      self.error('');
      if (pad.empty()) { self.error(self.msg('empty-signature')); return; }
      var chosen = form.querySelector('input[name=stage]:checked');
      self.busy(true);
      self.status(self.msg('uploading'));
      self.send(form.getAttribute('action'), {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data_url: pad.dataUrl(), stage: chosen ? chosen.value : 'receive' })
      }).then(function () { self.reload(); }, function (err) {
        self.busy(false);
        self.status('');
        self.error(err.message);
      });
    });
  };

  // Wires one card. Safe to call again after the card's content was replaced: the click listener on the root stays.
  function bind(root) {
    var card = new Card(root);
    root.querySelectorAll('[data-att-upload]').forEach(function (form) {
      form.addEventListener('submit', function (event) { event.preventDefault(); });
      form.querySelectorAll('[data-att-file]').forEach(function (input) {
        input.addEventListener('change', function () { card.upload(form, input); });
      });
    });
    if (!root.hasAttribute('data-att-bound')) {
      root.setAttribute('data-att-bound', '');
      root.addEventListener('click', function (event) {
        var button = event.target.closest ? event.target.closest('[data-att-delete]') : null;
        if (button && root.contains(button)) { new Card(root).remove(button); }
      });
    }
    var signature = root.querySelector('[data-att-signature]');
    if (signature) { card.initSignature(signature); }
    return card;
  }

  document.querySelectorAll('[data-attachments]').forEach(function (root) {
    var card = bind(root);
    var flash = flashTake();
    if (flash) { card.error(flash); }
  });
})();
