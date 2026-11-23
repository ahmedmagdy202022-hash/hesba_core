/* A1 (HG-079): the dashboard's voice summary player.
   - Nothing is fetched on page load. The first press of play POSTs (with the CSRF token) to voice the summary, unless
     the page already knows the cached audio's URL.
   - The audio is fetched as a blob and played from an object URL, so the private, no-store answer plays on iOS Safari
     too (no range requests needed).
   - If the browser refuses to play after the round trip (the press no longer counts as a gesture), the card says the
     audio is ready and the next press plays it.
   - A refusal (feature off, cap reached, not connected, being prepared) is shown on the card in the page's language.
   - A cached URL that no longer matches the summary (a figure changed while the page stayed open) is dropped, and the
     next press voices the current text. */
(function () {
  'use strict';

  var card = document.querySelector('[data-voice-summary]');
  if (!card) return;

  var button = card.querySelector('[data-voice-play]');
  var audio = card.querySelector('[data-voice-audio]');
  var fill = card.querySelector('[data-voice-fill]');
  var bar = card.querySelector('[data-voice-bar]');
  var current = card.querySelector('[data-voice-current]');
  var total = card.querySelector('[data-voice-total]');
  var status = card.querySelector('[data-voice-status]');
  var iconPlay = card.querySelector('[data-voice-icon="play"]');
  var iconPause = card.querySelector('[data-voice-icon="pause"]');
  var textBox = card.querySelector('[data-voice-text]');
  var tokenInput = card.querySelector('input[name="csrfmiddlewaretoken"]');
  var audioUrl = card.getAttribute('data-audio-url') || '';
  var loaded = false;
  var busy = false;

  function word(name) { return card.getAttribute('data-word-' + name) || ''; }

  function clock(seconds) {
    if (!isFinite(seconds) || seconds < 0) return '-:--';
    var whole = Math.floor(seconds);
    var rest = whole % 60;
    return Math.floor(whole / 60) + ':' + (rest < 10 ? '0' : '') + rest;
  }

  function say(message, isError) {
    status.textContent = message || '';
    status.hidden = !message;
    status.classList.toggle('is-error', !!isError);
  }

  function setBusy(on) {
    busy = on;
    button.setAttribute('aria-busy', on ? 'true' : 'false');
    if (on) say(word('loading'), false);
  }

  function showPlaying(playing) {
    iconPlay.hidden = playing;
    iconPause.hidden = !playing;
    button.setAttribute('aria-label', playing ? word('pause') : word('play'));
  }

  function start() {
    var attempt = audio.play();
    if (attempt && typeof attempt.catch === 'function') {
      attempt.then(function () { say('', false); }).catch(function () { say(word('again'), false); });
    } else {
      say('', false);
    }
  }

  function load(url) {
    return fetch(url, { credentials: 'same-origin', cache: 'no-store' }).then(function (response) {
      // A figure changed since the page loaded (a new text, a new hash): forget the old URL so the next press voices
      // the current summary.
      if (!response.ok) { audioUrl = ''; throw new Error('audio ' + response.status); }
      return response.blob();
    }).then(function (blob) {
      audio.src = URL.createObjectURL(blob);
      loaded = true;
    });
  }

  function voice() {
    var body = new FormData();
    body.append('lang', card.getAttribute('data-lang') || 'ar');
    if (tokenInput) body.append('csrfmiddlewaretoken', tokenInput.value);
    return fetch(card.getAttribute('data-endpoint'), {
      method: 'POST', body: body, credentials: 'same-origin',
      headers: { 'X-CSRFToken': tokenInput ? tokenInput.value : '', 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (response) {
      return response.json().catch(function () { return { ok: false }; });
    }).then(function (data) {
      if (!data.ok || !data.audio_url) {
        var error = new Error('refused');
        error.message = data.error || word('failed');
        error.refusal = true;
        throw error;
      }
      if (data.text && textBox) textBox.textContent = '«' + data.text + '»';  // what will actually be heard
      audioUrl = data.audio_url;
      return audioUrl;
    });
  }

  button.addEventListener('click', function () {
    if (busy) return;
    if (loaded) {
      if (audio.paused) start(); else audio.pause();
      return;
    }
    setBusy(true);
    (audioUrl ? Promise.resolve(audioUrl) : voice()).then(load).then(function () {
      setBusy(false);
      start();
    }).catch(function (error) {
      setBusy(false);
      say(error && error.refusal ? error.message : word('failed'), true);
    });
  });

  audio.addEventListener('play', function () { showPlaying(true); });
  audio.addEventListener('pause', function () { showPlaying(false); });
  audio.addEventListener('ended', function () { showPlaying(false); });
  audio.addEventListener('loadedmetadata', function () { total.textContent = clock(audio.duration); });
  audio.addEventListener('timeupdate', function () {
    var pct = audio.duration ? Math.min(100, Math.round(audio.currentTime / audio.duration * 100)) : 0;
    fill.style.width = pct + '%';
    bar.setAttribute('aria-valuenow', String(pct));
    current.textContent = clock(audio.currentTime);
  });
})();
