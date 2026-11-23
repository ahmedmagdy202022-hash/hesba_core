/* CAM-001: scan barcodes with the phone's camera, next to every scan box.
 *
 * A hardware scanner keeps working exactly as before: it types the code and
 * presses Enter. This adds a camera button beside the same box. A code read by
 * the camera is written into the box and "Enter" is sent, so the screen's own
 * scan logic (item, carton, serial) handles it the same way; nothing about
 * prices, stock or posting lives here.
 *
 * Reading: the browser's own BarcodeDetector when it has one (Chrome on
 * Android), otherwise the ZXing library shipped with Hesba (iPhone and the
 * rest). The library is a local file, fetched once in the background, so the
 * camera still works when the internet drops. The camera needs HTTPS (or
 * localhost); without it, or without a camera, no button is shown.
 */
(function () {
  'use strict';
  var boxes = Array.prototype.slice.call(document.querySelectorAll('[data-pos-scan], [data-scan-input], [data-camera-scan]'));
  var self = document.currentScript || document.querySelector('script[data-zxing]');
  if (!boxes.length || !window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { return; }

  var en = document.documentElement.lang === 'en';
  var T = en ? {
    open: 'Scan with the camera', close: 'Close', aim: 'Point the camera at the barcode', added: 'Read: ', torch: 'Light',
    denied: 'Camera permission was refused. Allow the camera for this site in the browser settings.', none: 'No camera was found on this device.',
    failed: 'The camera could not start.', loading: 'Starting the camera…'
  } : {
    open: 'امسح بالكاميرا', close: 'اقفل', aim: 'وجّه الكاميرا على الباركود', added: 'اتقري: ', torch: 'الفلاش',
    denied: 'الكاميرا مرفوضة. اسمح للموقع ده بالكاميرا من إعدادات المتصفح.', none: 'مفيش كاميرا على الجهاز ده.',
    failed: 'الكاميرا مش راضية تشتغل.', loading: 'بنشغّل الكاميرا…'
  };
  var FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'code_93', 'itf', 'codabar', 'qr_code'];
  var zxingUrl = self && self.getAttribute('data-zxing');
  var nativeFormats = null; // null: not checked yet; []: no native reader
  var zxingPromise = null;

  function checkNative() {
    if (!('BarcodeDetector' in window) || !window.BarcodeDetector.getSupportedFormats) { return Promise.resolve([]); }
    return window.BarcodeDetector.getSupportedFormats().then(function (list) {
      return FORMATS.filter(function (f) { return list.indexOf(f) !== -1; });
    }, function () { return []; });
  }
  function loadZxing() {
    if (window.ZXing) { return Promise.resolve(window.ZXing); }
    if (!zxingPromise) {
      zxingPromise = new Promise(function (resolve, reject) {
        var tag = document.createElement('script');
        tag.src = zxingUrl; tag.async = true;
        tag.onload = function () { if (window.ZXing) { resolve(window.ZXing); } else { reject(new Error('zxing')); } };
        tag.onerror = function () { zxingPromise = null; reject(new Error('zxing')); };
        document.head.appendChild(tag);
      });
    }
    return zxingPromise;
  }

  // Fetch the fallback reader while the page is idle, so it is there offline.
  checkNative().then(function (formats) {
    nativeFormats = formats;
    if (!formats.length && zxingUrl) {
      var later = window.requestIdleCallback || function (fn) { return setTimeout(fn, 1500); };
      later(function () { loadZxing().catch(function () { /* retried when the camera opens */ }); });
    }
  });

  // ---- the overlay -------------------------------------------------------
  var overlay, video, status, torchBtn, stream, target, timer, zxReader, lastCode = '', lastAt = 0;

  function build() {
    overlay = document.createElement('div');
    overlay.className = 'hs-cam';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', T.open);
    overlay.hidden = true;
    overlay.innerHTML = '<div class="hs-cam__stage"><video class="hs-cam__video" playsinline muted autoplay></video><div class="hs-cam__frame" aria-hidden="true"></div></div>' +
      '<p class="hs-cam__status" aria-live="polite"></p>' +
      '<div class="hs-cam__bar"><button type="button" class="hs-cam__torch" hidden></button><button type="button" class="hs-cam__close"></button></div>';
    document.body.appendChild(overlay);
    video = overlay.querySelector('video');
    status = overlay.querySelector('.hs-cam__status');
    torchBtn = overlay.querySelector('.hs-cam__torch');
    torchBtn.textContent = T.torch;
    overlay.querySelector('.hs-cam__close').textContent = T.close;
    overlay.querySelector('.hs-cam__close').addEventListener('click', close);
    torchBtn.addEventListener('click', toggleTorch);
    overlay.addEventListener('keydown', function (event) { if (event.key === 'Escape') { close(); } });
  }

  function say(text, bad) { status.textContent = text; status.classList.toggle('is-bad', !!bad); }

  function beep() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        var ctx = new Ctx(); var osc = ctx.createOscillator(); var gain = ctx.createGain();
        osc.frequency.value = 1400; gain.gain.value = 0.08; osc.connect(gain); gain.connect(ctx.destination);
        osc.start(); osc.stop(ctx.currentTime + 0.09); setTimeout(function () { ctx.close(); }, 300);
      }
    } catch (e) { /* sound is a nicety */ }
    if (navigator.vibrate) { navigator.vibrate(60); }
  }

  function deliver(code) {
    code = String(code || '').trim();
    if (!code) { return; }
    var now = Date.now();
    if (code === lastCode && now - lastAt < 2000) { return; } // the same label held in front of the camera
    lastCode = code; lastAt = now;
    beep();
    say(T.added + code, false);
    target.value = code;
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    target.blur(); // the screen refocuses the box; keep the phone keyboard from covering the camera
  }

  function scanNative() {
    var detector = new window.BarcodeDetector({ formats: nativeFormats });
    var busy = false;
    timer = setInterval(function () {
      if (busy || !video.videoWidth) { return; }
      busy = true;
      detector.detect(video).then(function (found) {
        busy = false;
        if (found && found.length) { deliver(found[0].rawValue); }
      }, function () { busy = false; });
    }, 180);
  }

  function scanZxing(ZX) {
    var hints = new Map();
    hints.set(ZX.DecodeHintType.TRY_HARDER, true);
    zxReader = new ZX.BrowserMultiFormatReader(hints, 250);
    zxReader.decodeFromStream(stream, video, function (result) {
      if (result) { deliver(result.getText()); }
    }).catch(function () { say(T.failed, true); });
  }

  function toggleTorch() {
    var track = stream && stream.getVideoTracks()[0];
    if (!track) { return; }
    var on = torchBtn.getAttribute('aria-pressed') !== 'true';
    track.applyConstraints({ advanced: [{ torch: on }] }).then(function () { torchBtn.setAttribute('aria-pressed', on ? 'true' : 'false'); }, function () { torchBtn.hidden = true; });
  }

  function open(box) {
    if (!overlay) { build(); }
    target = box; lastCode = ''; lastAt = 0;
    overlay.hidden = false;
    document.documentElement.classList.add('hs-cam-open');
    say(T.loading, false);
    overlay.querySelector('.hs-cam__close').focus();
    var ready = nativeFormats === null ? checkNative().then(function (f) { nativeFormats = f; }) : Promise.resolve();
    ready.then(function () {
      return navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
    }).then(function (media) {
      if (overlay.hidden) { media.getTracks().forEach(function (t) { t.stop(); }); return; }
      stream = media;
      video.srcObject = media;
      var play = video.play(); if (play && play.catch) { play.catch(function () {}); }
      var track = media.getVideoTracks()[0];
      var caps = track && track.getCapabilities ? track.getCapabilities() : {};
      torchBtn.hidden = !caps.torch;
      torchBtn.setAttribute('aria-pressed', 'false');
      say(T.aim, false);
      if (nativeFormats.length) { scanNative(); return null; }
      return loadZxing().then(scanZxing);
    }).catch(function (error) {
      var name = error && error.name;
      say(name === 'NotAllowedError' || name === 'SecurityError' ? T.denied : (name === 'NotFoundError' || name === 'OverconstrainedError' ? T.none : T.failed), true);
    });
  }

  function close() {
    if (timer) { clearInterval(timer); timer = null; }
    if (zxReader) { try { zxReader.reset(); } catch (e) { /* already stopped */ } zxReader = null; }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (video) { video.srcObject = null; }
    if (overlay) { overlay.hidden = true; }
    document.documentElement.classList.remove('hs-cam-open');
    if (target) { target.focus(); }
  }

  document.addEventListener('visibilitychange', function () { if (document.hidden && overlay && !overlay.hidden) { close(); } });

  boxes.forEach(function (box) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'hs-cam-btn';
    button.setAttribute('aria-label', T.open);
    button.setAttribute('title', T.open);
    button.setAttribute('data-camera-button', '');
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" width="22" height="22"><path d="M4 8h3l2-2.5h6L17 8h3v11H4z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="13" r="3.6" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
    button.addEventListener('click', function () { open(box); });
    var wrap = document.createElement('span');
    wrap.className = 'hs-cam-wrap';
    box.parentNode.insertBefore(wrap, box);
    wrap.appendChild(box);
    wrap.appendChild(button);
  });
})();
