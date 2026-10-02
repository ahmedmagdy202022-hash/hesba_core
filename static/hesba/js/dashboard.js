/* DEMO-FEEDBACK: the dashboard's live parts.
   - the bell opens a list of what needs attention (each one a link);
   - the insights card rotates its tabs every few seconds, stops for good once
     someone picks a tab, pauses while hovered or focused, and never moves for
     people who asked for reduced motion;
   - chart bars show their values on hover, focus or tap (a <title> alone never
     shows on a phone). */
(function () {
  'use strict';

  // ---- bell ----
  var bell = document.querySelector('[data-bell]');
  if (bell) {
    var toggle = bell.querySelector('[data-bell-toggle]');
    var panel = bell.querySelector('[data-bell-panel]');
    var setOpen = function (open) {
      panel.hidden = !open;
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    toggle.addEventListener('click', function (event) {
      event.stopPropagation();
      setOpen(panel.hidden);
    });
    document.addEventListener('click', function (event) {
      if (!panel.hidden && !bell.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !panel.hidden) { setOpen(false); toggle.focus(); }
    });
    var all = panel.querySelector('[data-bell-all]');
    if (all) all.addEventListener('click', function () {
      setOpen(false);
      var target = document.getElementById('dash-alerts');
      if (target) setTimeout(function () { target.focus({ preventScroll: true }); }, 0);
    });
  }

  // ---- rotating insights ----
  var card = document.querySelector('[data-insights]');
  if (card) {
    var tabs = Array.prototype.slice.call(card.querySelectorAll('[data-tab]'));
    var progress = card.querySelector('[data-progress]');
    var rotateButton = card.querySelector('[data-rotate-toggle]');
    var rotateIcon = card.querySelector('[data-rotate-icon]');
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var ms = 8000;
    var index = 0;
    var timer = null;
    var stopped = reduce || tabs.length < 2;
    var hovering = false;
    card.style.setProperty('--dash-rotate-ms', ms + 'ms');

    var show = function (next, focus) {
      index = (next + tabs.length) % tabs.length;
      tabs.forEach(function (tab, i) {
        var on = i === index;
        tab.classList.toggle('is-current', on);
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.tabIndex = on ? 0 : -1;
        var panelEl = document.getElementById(tab.getAttribute('aria-controls'));
        if (panelEl) panelEl.hidden = !on;
      });
      if (focus) tabs[index].focus();
    };
    var restartBar = function () {
      if (!progress) return;
      progress.classList.remove('is-running');
      void progress.offsetWidth; // restart the CSS animation
      if (!stopped) progress.classList.add('is-running');
    };
    var schedule = function () {
      clearTimeout(timer);
      if (stopped) { restartBar(); return; }
      restartBar();
      timer = setTimeout(function tick() {
        if (hovering) { timer = setTimeout(tick, 500); return; }
        show(index + 1);
        schedule();
      }, ms);
    };
    var setStopped = function (value) {
      stopped = value;
      card.classList.toggle('is-paused', value);
      if (rotateButton) {
        var label = value ? rotateButton.dataset.labelPlay : rotateButton.dataset.labelPause;
        rotateButton.setAttribute('aria-label', label);
        rotateButton.title = label;
        if (rotateIcon) rotateIcon.textContent = value ? '▶' : '❚❚';
      }
      schedule();
    };

    tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function () { show(i); setStopped(true); });
      tab.addEventListener('keydown', function (event) {
        var rtl = document.documentElement.dir === 'rtl';
        var forward = rtl ? 'ArrowLeft' : 'ArrowRight';
        var back = rtl ? 'ArrowRight' : 'ArrowLeft';
        if (event.key === forward) { event.preventDefault(); show(index + 1, true); setStopped(true); }
        if (event.key === back) { event.preventDefault(); show(index - 1, true); setStopped(true); }
      });
    });
    if (rotateButton) {
      if (reduce || tabs.length < 2) rotateButton.hidden = true;
      rotateButton.addEventListener('click', function () { setStopped(!stopped); });
    }
    card.addEventListener('mouseenter', function () { hovering = true; card.classList.add('is-paused'); });
    card.addEventListener('mouseleave', function () { hovering = false; if (!stopped) card.classList.remove('is-paused'); });
    card.addEventListener('focusin', function () { hovering = true; card.classList.add('is-paused'); });
    card.addEventListener('focusout', function () { hovering = false; if (!stopped) card.classList.remove('is-paused'); });
    setStopped(stopped);
  }

  // ---- chart tooltips ----
  Array.prototype.forEach.call(document.querySelectorAll('[data-plot]'), function (plot) {
    var tip = plot.querySelector('[data-tip-box]');
    if (!tip) return;
    var place = function (bar) {
      var box = plot.getBoundingClientRect();
      var mark = bar.getBoundingClientRect();
      tip.textContent = bar.getAttribute('data-tip');
      tip.hidden = false;
      var x = mark.left + mark.width / 2 - box.left;
      if (document.documentElement.dir === 'rtl') {
        tip.style.left = 'auto';
        tip.style.right = (box.width - x) + 'px';
      } else {
        tip.style.right = 'auto';
        tip.style.left = x + 'px';
      }
      var half = tip.offsetWidth / 2;
      var clamped = Math.min(Math.max(x, half), box.width - half);
      if (document.documentElement.dir === 'rtl') tip.style.right = (box.width - clamped) + 'px';
      else tip.style.left = clamped + 'px';
      tip.style.top = '-6px';
    };
    var hide = function () { tip.hidden = true; };
    Array.prototype.forEach.call(plot.querySelectorAll('[data-tip]'), function (bar) {
      bar.addEventListener('mouseenter', function () { place(bar); });
      bar.addEventListener('focus', function () { place(bar); });
      bar.addEventListener('click', function () { place(bar); });
      bar.addEventListener('mouseleave', hide);
      bar.addEventListener('blur', hide);
    });
  });
})();
