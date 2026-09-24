// MOBILE-001 responsive audit: opens every screen at mobile (390x844, AR + EN)
// and tablet landscape (1024x768, AR) and records sideways scroll, the element
// causing it, tables wider than the screen, tap targets under 36px, text under
// 12px and JS errors. Read-only: it only issues GET requests after logging in.
//
// Usage (see docs/MOBILE_001_AUDIT.md for the full recipe):
//   npm i playwright-core            # in any scratch folder, then point NODE_PATH at its node_modules
//   NODE_PATH=/scratch/node_modules S=/path/to/outdir BASE=http://127.0.0.1:8020 AUDIT_USER=owner AUDIT_PASS=... \
//   CHROME=/path/to/chrome node scripts/mobile_audit.js
// $S/urls.json must hold the list of paths to visit; results go to $S/audit.json
// and full-page screenshots to $S/mobile_shots/.
const { chromium } = require('playwright-core');
const fs = require('fs');
const S = process.env.S, BASE = process.env.BASE || 'http://127.0.0.1:8020', urls = JSON.parse(fs.readFileSync(S + '/urls.json'));
const modes = [
  { key: 'mobile_ar', w: 390, h: 844, lang: 'ar', touch: true },
  { key: 'mobile_en', w: 390, h: 844, lang: 'en', touch: true },
  { key: 'tablet_ar', w: 1024, h: 768, lang: 'ar', touch: true },
];
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME });
  const results = [];
  for (const m of modes) {
    const ctx = await b.newContext({ viewport: { width: m.w, height: m.h }, hasTouch: m.touch, isMobile: m.w < 800, deviceScaleFactor: 2 });
    const p = await ctx.newPage();
    await p.goto(BASE + '/login/');
    await p.fill('#id_username', process.env.AUDIT_USER || 'owner'); await p.fill('#id_password', process.env.AUDIT_PASS || '');
    await Promise.all([p.waitForNavigation(), p.click('button[type=submit]')]);
    for (const u of urls) {
      const errors = [];
      const onErr = e => errors.push(String(e.message || e).slice(0, 160));
      const onCon = msg => { if (msg.type() === 'error') errors.push(msg.text().slice(0, 160)); };
      p.on('pageerror', onErr); p.on('console', onCon);
      let status = 0;
      try {
        const sep = u.includes('?') ? '&' : '?';
        const resp = await p.goto(`${BASE}${u}${sep}lang=${m.lang}`, { waitUntil: 'networkidle', timeout: 20000 });
        status = resp ? resp.status() : 0;
      } catch (e) { errors.push('NAV ' + e.message.slice(0, 100)); }
      const data = await p.evaluate(() => {
        const W = window.innerWidth;
        const doc = document.documentElement;
        const scrollW = Math.max(doc.scrollWidth, document.body ? document.body.scrollWidth : 0);
        const inScroller = el => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const ox = getComputedStyle(a).overflowX; if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true; } return false; };
        const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
        const desc = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
        const over = [];
        for (const el of document.querySelectorAll('body *')) {
          if (!vis(el) || inScroller(el)) continue;
          const r = el.getBoundingClientRect();
          if (r.right > W + 2 || r.left < -2) {
            // report only outermost offender
            let parentOff = false;
            for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const pr = a.getBoundingClientRect(); if (pr.right > W + 2 || pr.left < -2) { parentOff = true; break; } }
            if (!parentOff) over.push(desc(el) + ` [${Math.round(r.left)}..${Math.round(r.right)}]`);
          }
        }
        const small = [];
        for (const el of document.querySelectorAll('a, button, input:not([type=hidden]), select, textarea, [role=button]')) {
          if (!vis(el)) continue;
          const r = el.getBoundingClientRect();
          if (r.height < 36 || r.width < 36) small.push(desc(el) + ` ${Math.round(r.width)}x${Math.round(r.height)} "` + (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 25) + '"');
        }
        let tiny = 0;
        for (const el of document.querySelectorAll('body *')) {
          if (!vis(el) || !el.childNodes.length) continue;
          const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
          if (hasText && parseFloat(getComputedStyle(el).fontSize) < 12) tiny++;
        }
        const tablesWide = [...document.querySelectorAll('table')].filter(t => t.scrollWidth > W).length;
        return {
          title: document.title, finalPath: location.pathname,
          viewportMeta: !!document.querySelector('meta[name=viewport]'),
          htmlLang: doc.lang, dir: doc.dir,
          W, scrollW, hOverflow: scrollW > W + 2, over: over.slice(0, 6), small: small.slice(0, 8), smallCount: small.length, tiny, tablesWide,
          bodyText: (document.body ? document.body.innerText : '').slice(0, 300),
        };
      });
      const name = `${m.key}__${u.replace(/\//g, '_').replace(/^_|_$/g, '') || 'root'}.png`;
      if (m.key !== 'mobile_en') await p.screenshot({ path: `${S}/mobile_shots/${name}`, fullPage: true }).catch(() => {});
      results.push({ mode: m.key, url: u, status, errors, shot: name, ...data });
      p.off('pageerror', onErr); p.off('console', onCon);
    }
    await ctx.close();
  }
  fs.writeFileSync(S + '/audit.json', JSON.stringify(results, null, 1));
  await b.close();
  console.log('done', results.length);
})();
