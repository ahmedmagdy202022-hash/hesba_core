/* DATE-001: one date picker for every app page.
 *
 * The browser's own <input type="date"> speaks the browser's language: an
 * Arabic screen showed "mm/dd/yyyy" and an English calendar. Each date field
 * is upgraded in place:
 *
 *   - the original input keeps its name and becomes hidden, still holding the
 *     ISO value (yyyy-mm-dd) the server has always read;
 *   - a text field takes its place (and its id, so the <label> still works),
 *     showing day/month/year; a date can be typed (9/10/2026, 09-10-2026,
 *     2026-10-09, Arabic digits too) or picked;
 *   - the calendar is in the page's language, the week starts on Saturday,
 *     min/max are respected, and it works from the keyboard.
 *
 * Without JavaScript the native field stays, so nothing breaks.
 */
(function () {
  'use strict';

  var WORDS = {
    ar: {
      months: ['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر'],
      days: ['سبت', 'أحد', 'إثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة'],
      placeholder: 'يوم/شهر/سنة', open: 'اختار التاريخ', today: 'النهارده', clear: 'مسح', prev: 'الشهر اللي فات', next: 'الشهر الجاي',
      month: 'الشهر', year: 'السنة', invalid: 'التاريخ مش صحيح؛ اكتبه يوم/شهر/سنة، مثلاً 9/10/2026.',
      early: 'التاريخ قبل أول تاريخ مسموح ({d}).', late: 'التاريخ بعد آخر تاريخ مسموح ({d}).'
    },
    en: {
      months: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
      days: ['Sat', 'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
      placeholder: 'dd/mm/yyyy', open: 'Choose the date', today: 'Today', clear: 'Clear', prev: 'Previous month', next: 'Next month',
      month: 'Month', year: 'Year', invalid: 'Invalid date; type it as day/month/year, e.g. 9/10/2026.',
      early: 'The date is before the earliest allowed ({d}).', late: 'The date is after the latest allowed ({d}).'
    }
  };
  var ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/></svg>';
  var counter = 0;
  var openPicker = null;

  function lang() { return (document.documentElement.lang || 'ar').slice(0, 2) === 'en' ? 'en' : 'ar'; }
  function words() { return WORDS[lang()]; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function shown(d) { return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear(); }
  function sameDay(a, b) { return a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
  function make(y, m, d) {
    var date = new Date(y, m, d);
    return date.getFullYear() === y && date.getMonth() === m && date.getDate() === d ? date : null;
  }
  function fromIso(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    return m ? make(+m[1], +m[2] - 1, +m[3]) : null;
  }
  function westernDigits(text) {
    return String(text || '').replace(/[٠-٩]/g, function (c) { return '٠١٢٣٤٥٦٧٨٩'.indexOf(c); })
      .replace(/[۰-۹]/g, function (c) { return '۰۱۲۳۴۵۶۷۸۹'.indexOf(c); });
  }
  function parse(text) {
    text = westernDigits(text).trim();
    if (!text) return '';
    var m = /^(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})$/.exec(text);
    if (m) return make(+m[1], +m[2] - 1, +m[3]);
    m = /^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2}|\d{4})$/.exec(text);
    if (!m) return null;
    var year = +m[3];
    if (m[3].length === 2) year += 2000;
    return make(year, +m[2] - 1, +m[1]);
  }

  function Picker(native) {
    var self = this;
    this.native = native;
    var id = native.id || ('hs-date-' + (++counter));
    this.text = document.createElement('input');
    this.text.type = 'text';
    this.text.id = id;
    native.id = id + '-value';
    this.text.className = native.className;
    this.text.classList.add('hs-date__text');
    this.text.setAttribute('inputmode', 'numeric');
    this.text.setAttribute('autocomplete', 'off');
    this.text.setAttribute('dir', 'ltr');
    this.text.placeholder = words().placeholder;
    ['aria-label', 'aria-describedby', 'aria-invalid', 'title', 'form'].forEach(function (name) {  // aria-invalid: Django's error state
      if (native.hasAttribute(name)) self.text.setAttribute(name, native.getAttribute(name));
    });
    this.text.required = native.required;
    this.text.disabled = native.disabled;
    this.text.readOnly = native.readOnly;
    native.required = false;
    this.min = fromIso(native.min);
    this.max = fromIso(native.max);

    this.wrap = document.createElement('span');
    this.wrap.className = 'hs-date';
    this.wrap.hsPicker = this;  // a cloned wrapper (a copied invoice line) has no picker: see revive()
    native.parentNode.insertBefore(this.wrap, native);
    this.wrap.appendChild(this.text);
    this.button = document.createElement('button');
    this.button.type = 'button';
    this.button.className = 'hs-date__open';
    this.button.innerHTML = ICON;
    this.button.setAttribute('aria-label', words().open);
    this.button.setAttribute('aria-haspopup', 'dialog');
    this.button.setAttribute('aria-expanded', 'false');
    this.button.disabled = native.disabled || native.readOnly;
    this.wrap.appendChild(this.button);
    this.wrap.appendChild(native);
    native.type = 'hidden';
    native.dataset.hsDate = '1';
    var initial = fromIso(native.value) || parse(native.value) || null;  // a localized initial value too
    native.value = initial ? iso(initial) : '';
    this.text.value = initial ? shown(initial) : '';

    this.text.addEventListener('change', function () { self.commitTyped(); });
    this.text.addEventListener('blur', function () { self.commitTyped(); });
    this.text.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' && (e.altKey || !self.text.value)) { e.preventDefault(); self.open(); }
    });
    this.button.addEventListener('click', function () { self.isOpen() ? self.close(true) : self.open(); });
    if (native.form) {
      native.form.addEventListener('submit', function (e) {
        if (!self.commitTyped()) { e.preventDefault(); self.text.reportValidity(); }
      });
    }
  }

  Picker.prototype.value = function () { return fromIso(this.native.value); };

  Picker.prototype.bounds = function (date) {
    var w = words();
    if (this.min && date < this.min) return w.early.replace('{d}', shown(this.min));
    if (this.max && date > this.max) return w.late.replace('{d}', shown(this.max));
    return '';
  };

  Picker.prototype.set = function (date) {
    var before = this.native.value;
    this.native.value = date ? iso(date) : '';
    this.text.value = date ? shown(date) : '';
    this.text.setCustomValidity('');
    if (date) this.text.removeAttribute('aria-invalid');
    if (before !== this.native.value) {
      this.native.dispatchEvent(new Event('input', { bubbles: true }));
      this.native.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };

  /* Read what was typed. False when it is not a date (the field says why). */
  Picker.prototype.commitTyped = function () {
    var date = parse(this.text.value);
    if (date === '') { this.set(null); return true; }
    if (!date) { this.native.value = ''; this.text.setCustomValidity(words().invalid); this.text.setAttribute('aria-invalid', 'true'); return false; }
    var outside = this.bounds(date);
    if (outside) { this.native.value = ''; this.text.setCustomValidity(outside); this.text.setAttribute('aria-invalid', 'true'); return false; }
    this.set(date);
    return true;
  };

  Picker.prototype.isOpen = function () { return !!this.pop; };

  Picker.prototype.open = function () {
    if (this.text.disabled || this.text.readOnly) return;
    if (openPicker && openPicker !== this) openPicker.close(false);
    this.commitTyped();
    var today = new Date();
    this.cursor = this.value() || (this.max && today > this.max ? this.max : this.min && today < this.min ? this.min : today);
    this.pop = document.createElement('div');
    this.pop.className = 'hs-date__pop';
    this.pop.setAttribute('role', 'dialog');
    this.pop.setAttribute('aria-label', words().open);
    this.wrap.appendChild(this.pop);
    this.button.setAttribute('aria-expanded', 'true');
    openPicker = this;
    this.render(true);
    this.place();
  };

  Picker.prototype.close = function (refocus) {
    if (!this.pop) return;
    this.pop.remove();
    this.pop = null;
    this.button.setAttribute('aria-expanded', 'false');
    if (openPicker === this) openPicker = null;
    if (refocus) this.text.focus();
  };

  Picker.prototype.place = function () {
    // The popup is position:fixed, so no overflow:auto ancestor (a scrolling
    // table, the invoice lines) clips it. It sits under the field, aligned to
    // its inline end, kept on screen, and opens upward when there is no room below.
    var pop = this.pop, field = this.wrap.getBoundingClientRect(), gutter = 8, gap = 6;
    var width = pop.offsetWidth, height = pop.offsetHeight;
    var rtl = getComputedStyle(this.wrap).direction === 'rtl';
    var left = rtl ? field.left : field.right - width;
    left = Math.max(gutter, Math.min(left, window.innerWidth - gutter - width));
    var top = field.bottom + gap;
    if (top + height > window.innerHeight - gutter && field.top - gap - height >= gutter) top = field.top - gap - height;
    pop.style.left = left + 'px';
    pop.style.top = Math.max(gutter, top) + 'px';
  };

  Picker.prototype.render = function (focusDay) {
    var self = this, w = words(), cursor = this.cursor, selected = this.value(), today = new Date();
    var year = cursor.getFullYear(), month = cursor.getMonth();
    var html = '<div class="hs-date__head">' +
      '<button type="button" class="hs-date__nav" data-step="-1" aria-label="' + w.prev + '">‹</button>' +
      '<select class="hs-date__month" aria-label="' + w.month + '">' + w.months.map(function (name, i) {
        return '<option value="' + i + '"' + (i === month ? ' selected' : '') + '>' + name + '</option>';
      }).join('') + '</select>' +
      '<select class="hs-date__year" aria-label="' + w.year + '">';
    var first = Math.min(year - 100, this.min ? this.min.getFullYear() : year - 100), last = Math.max(year + 20, this.max ? this.max.getFullYear() : year + 20);
    if (this.min) first = Math.max(first, this.min.getFullYear());
    if (this.max) last = Math.min(last, this.max.getFullYear());
    for (var y = last; y >= first; y--) html += '<option value="' + y + '"' + (y === year ? ' selected' : '') + '>' + y + '</option>';
    html += '</select><button type="button" class="hs-date__nav" data-step="1" aria-label="' + w.next + '">›</button></div>';
    html += '<table class="hs-date__grid" role="grid"><thead><tr>' + w.days.map(function (d) { return '<th scope="col">' + d + '</th>'; }).join('') + '</tr></thead><tbody>';
    var start = new Date(year, month, 1);
    var offset = (start.getDay() + 1) % 7; // Saturday first
    var day = new Date(year, month, 1 - offset);
    for (var row = 0; row < 6; row++) {
      html += '<tr>';
      for (var col = 0; col < 7; col++) {
        var out = day.getMonth() !== month;
        var blocked = (this.min && day < this.min) || (this.max && day > this.max);
        var classes = ['hs-date__day'];
        if (out) classes.push('is-out');
        if (sameDay(day, today)) classes.push('is-today');
        if (sameDay(day, selected)) classes.push('is-selected');
        html += '<td><button type="button" class="' + classes.join(' ') + '" data-date="' + iso(day) + '" tabindex="' + (sameDay(day, cursor) ? '0' : '-1') + '"' +
          (blocked ? ' disabled' : '') + (sameDay(day, selected) ? ' aria-pressed="true"' : '') +
          ' aria-label="' + day.getDate() + ' ' + w.months[day.getMonth()] + ' ' + day.getFullYear() + '">' + day.getDate() + '</button></td>';
        day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
      }
      html += '</tr>';
    }
    html += '</tbody></table><div class="hs-date__foot">' +
      '<button type="button" class="hs-date__today"' + (this.bounds(today) ? ' disabled' : '') + '>' + w.today + '</button>' +
      (this.text.required ? '' : '<button type="button" class="hs-date__clear">' + w.clear + '</button>') + '</div>';
    this.pop.innerHTML = html;

    this.pop.querySelectorAll('.hs-date__nav').forEach(function (b) {
      b.addEventListener('click', function () { self.move(0, +b.dataset.step); });
    });
    this.pop.querySelector('.hs-date__month').addEventListener('change', function (e) { self.jump(year, +e.target.value, '.hs-date__month'); });
    this.pop.querySelector('.hs-date__year').addEventListener('change', function (e) { self.jump(+e.target.value, month, '.hs-date__year'); });
    this.pop.querySelectorAll('.hs-date__day').forEach(function (b) {
      b.addEventListener('click', function () { self.pick(fromIso(b.dataset.date)); });
    });
    this.pop.querySelector('.hs-date__today').addEventListener('click', function () { self.pick(new Date(today.getFullYear(), today.getMonth(), today.getDate())); });
    var clear = this.pop.querySelector('.hs-date__clear');
    if (clear) clear.addEventListener('click', function () { self.set(null); self.close(true); });
    this.pop.addEventListener('keydown', function (e) { self.key(e); });
    if (focusDay) {
      var current = this.pop.querySelector('.hs-date__day[tabindex="0"]');
      if (current) current.focus();
    }
  };

  Picker.prototype.jump = function (year, month, control) {
    var last = new Date(year, month + 1, 0).getDate();
    this.cursor = new Date(year, month, Math.min(this.cursor.getDate(), last));
    this.render(false);
    // The rebuild replaced the select being used: keep the keyboard on its replacement.
    var again = control && this.pop.querySelector(control);
    if (again) again.focus();
  };

  Picker.prototype.move = function (days, months) {
    var c = this.cursor;
    if (months) {
      var last = new Date(c.getFullYear(), c.getMonth() + months + 1, 0).getDate();
      this.cursor = new Date(c.getFullYear(), c.getMonth() + months, Math.min(c.getDate(), last));
    } else {
      this.cursor = new Date(c.getFullYear(), c.getMonth(), c.getDate() + days);
    }
    this.render(true);
  };

  Picker.prototype.pick = function (date) {
    if (!date || this.bounds(date)) return;
    this.set(date);
    this.close(true);
  };

  Picker.prototype.key = function (e) {
    var rtl = document.documentElement.dir === 'rtl';
    var onDay = e.target.classList && e.target.classList.contains('hs-date__day');
    switch (e.key) {
      case 'Escape': e.preventDefault(); this.close(true); return;
      case 'Tab': return;
    }
    if (!onDay) return;
    var step = { ArrowLeft: rtl ? 1 : -1, ArrowRight: rtl ? -1 : 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
    if (step) { e.preventDefault(); this.move(step, 0); return; }
    if (e.key === 'PageUp') { e.preventDefault(); this.move(0, e.shiftKey ? -12 : -1); return; }
    if (e.key === 'PageDown') { e.preventDefault(); this.move(0, e.shiftKey ? 12 : 1); return; }
    if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      var offset = (this.cursor.getDay() + 1) % 7;
      this.move(e.key === 'Home' ? -offset : 6 - offset, 0);
    }
  };

  /* A row copied with cloneNode (invoice_form.js "+ line") carries the
     picker's markup but none of its listeners. Put the original date input
     back, with the copy's (renumbered) id, name and required, and upgrade it
     like any other. */
  function revive(wrap) {
    var hidden = wrap.querySelector('input[data-hs-date]'), text = wrap.querySelector('.hs-date__text');
    if (!hidden || !text) return;
    hidden.type = 'date';
    hidden.removeAttribute('data-hs-date');
    hidden.id = text.id;
    hidden.required = text.required;
    hidden.disabled = text.disabled;
    wrap.parentNode.insertBefore(hidden, wrap);
    wrap.remove();
  }

  function upgrade(root) {
    var scope = root.querySelectorAll ? root : document;
    if (scope.classList && scope.classList.contains('hs-date') && !scope.hsPicker) revive(scope);
    scope.querySelectorAll('.hs-date').forEach(function (wrap) { if (!wrap.hsPicker) revive(wrap); });
    scope.querySelectorAll('input[type="date"]:not([data-hs-date])').forEach(function (input) {
      if (input.closest('[data-native-date]')) return;
      input.hsPicker = new Picker(input);
    });
  }

  document.addEventListener('click', function (e) {
    // A click that re-drew the calendar leaves its target detached: that is still inside.
    if (openPicker && e.target.isConnected && !openPicker.wrap.contains(e.target)) openPicker.close(false);
  });
  window.addEventListener('resize', function () { if (openPicker) openPicker.place(); });
  // Any scroll (the page or a box inside it) moves the field: follow it.
  window.addEventListener('scroll', function () { if (openPicker && openPicker.pop) openPicker.place(); }, true);

  function start() {
    upgrade(document);
    new MutationObserver(function (records) {
      records.forEach(function (r) { r.addedNodes.forEach(function (n) { if (n.nodeType === 1) upgrade(n.matches && n.matches('input[type="date"]') ? n.parentNode : n); }); });
    }).observe(document.body, { childList: true, subtree: true });
  }

  window.HesbaDate = { parse: parse, upgrade: upgrade };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
