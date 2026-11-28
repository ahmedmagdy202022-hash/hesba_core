// v2 promo: capture every real screen (one run, one clock): node capture_p2.js [shot ids...]
// Needs the local demo (./serve.sh) and the v2 sandbox explore/state-p2.json, prepared with prep_data_v2.js.
// The «شغال في» scope lives in the session, so shots run in order and each one first presses its own tab.
// Output: shots_p2/<id>.png + .txt + .rects.json (named target boxes, CSS px of the viewport after scrolling) + manifest.json.
// The till is never paid here: the script only taps two quick buttons.
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = process.env.BASE || 'http://127.0.0.1:8099';
const OUT = 'shots_p2', STATE = 'explore/state-p2.json';
const HIDE = `.fr-demobar,[data-demo-bar],.hs-feedback-fab,.hs-feedback,.hs-hello,[data-hello],.ai-sample,[data-ai-sample]{display:none!important}
html{scroll-behavior:auto!important} *{caret-color:transparent!important}
.set-nav__link.is-todo .set-nav__status{display:none!important} .set-nav__link.is-todo::before,.set-nav__link.is-todo::after,.set-nav__link.is-todo .set-nav__label::before,.set-nav__link.is-todo .set-nav__label::after{display:none!important}`;   // v2 QA: the backup set-up reminder is hidden like the demo bar
const BANNED = ['ملاحظاتك', 'ديمو', 'أهلاً بيك', 'رد تجريبي', 'جرّب دلوقتي', 'DEMO-AI'];
const DESK = { width: 1024, height: 642, dpr: 3 };
const PHONE = { width: 390, height: 844, dpr: 3, touch: true, mobile: true };
const d = new Date(); const TODAY = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const G = 'المجموعة كلها', MAADI = 'فرع المعادي';
const BAR = { sel: 'nav.hs-entities' };
// targets: name -> {sel} | {text, closest} | {col: header text, table: n} (a table column: header x-range, table y-range)
const SHOTS = [
  { id: 'dash_nums_desk', dev: DESK, scope: G, path: '/dashboard/?lang=ar', scroll: { el: '.dash-today', off: -36 }, targets: { today: { sel: '.dash-today' } } },
  { id: 'dash_hero_desk', dev: DESK, scope: G, path: '/dashboard/?lang=ar', scroll: { el: '.dash-hero', off: -18 }, targets: { sales: { sel: '.dash-hero .dash-hstat', has: 'مبيعات اليوم' }, hero: { sel: '.dash-hero' } } },
  { id: 'dash_top_desk', dev: DESK, scope: G, path: '/dashboard/?lang=ar', scroll: 0, targets: { bar: BAR } },
  { id: 'dash_phone', dev: PHONE, scope: G, path: '/dashboard/?lang=ar', scroll: 0 },
  { id: 'dash_nums_phone', dev: PHONE, scope: G, path: '/dashboard/?lang=ar', scroll: { el: '.dash-today', off: -90 } },
  { id: 'ent_desk', dev: DESK, scope: G, path: '/entities/?lang=ar', scroll: 200, targets: { bar: BAR, newbtn: { sel: 'main a[href*="/entities/new/"]' }, table: { sel: 'main table' } } },
  { id: 'ent_phone', dev: PHONE, scope: G, path: '/entities/?lang=ar', scroll: { text: 'الفروع', off: -90 } },
  { id: 'entnew_desk', dev: DESK, scope: G, path: '/entities/new/?lang=ar', scroll: 0, fill: { 'input[name=code]': 'BR4', 'input[name=name_ar]': 'فرع الإسكندرية', 'select[name=kind]': 'branch' }, targets: { kind: { sel: 'select[name=kind]', closest: '.op-field, p, div' } } },
  { id: 'wh_desk', dev: DESK, scope: G, path: '/inventory/warehouses/?lang=ar', scroll: 0 },
  { id: 'users_desk', dev: DESK, scope: G, path: '/settings/users/?lang=ar', scroll: 200 },
  { id: 'roles_desk', dev: DESK, scope: G, path: '/settings/roles/?lang=ar', scroll: { text: 'الأدوار والصلاحيات', tag: 'h1,h2', off: -14 }, targets: { cashier: { text: 'البيع والتحصيل', closest: 'article' }, cashier_desc: { text: 'البيع والتحصيل' } } },
  { id: 'roles_phone', dev: PHONE, scope: G, path: '/settings/roles/?lang=ar', scrollTarget: { text: 'البيع والتحصيل', closest: 'article', off: -90 } },
  { id: 'where_desk', dev: DESK, scope: G, path: '/entities/stock/?lang=ar&q=' + encodeURIComponent('أرز'), scroll: 0, targets: { table: { sel: 'main table' }, block: { sel: 'main table', closest: 'section, article, .op-card' } } },
  { id: 'where_phone', dev: PHONE, scope: G, path: '/entities/stock/?lang=ar&q=' + encodeURIComponent('أرز'), scroll: 0 },
  { id: 'stock_desk', dev: DESK, scope: G, path: '/inventory/?lang=ar', scroll: 172, targets: { low: { rowText: 'مخزون منخفض' }, out: { rowText: 'نفد المخزون' } } },
  { id: 'stock_phone', dev: PHONE, scope: G, path: '/inventory/?lang=ar', scroll: 0, nudge: false },
  { id: 'alerts_phone', dev: PHONE, scope: G, path: '/dashboard/?lang=ar', scroll: { el: '#dash-alerts', off: -78 } },
  { id: 'reorder_desk', dev: DESK, scope: G, path: '/inventory/reorder/?lang=ar', scroll: 12, targets: { col: { col: 'المقترح', table: 0 }, days: { col: 'يكفي كام يوم', table: 0 } } },
  { id: 'reorder_phone', dev: PHONE, scope: G, path: '/inventory/reorder/?lang=ar', scroll: 12 },
  { id: 'profit_desk', dev: DESK, scope: G, path: '/reports/profit/?lang=ar', chip: 'الشهر', scroll: 0, targets: { summary: { sel: 'section.op-summary-grid' } } },
  { id: 'daily_desk', dev: DESK, scope: G, path: `/reports/daily/?lang=ar&date=${TODAY}`, scroll: { text: 'الربح', off: -14 }, targets: { khazna: { text: 'الخزن', tag: 'h2', withNext: true } } },
  { id: 'daily_phone', dev: PHONE, scope: G, path: `/reports/daily/?lang=ar&date=${TODAY}`, scroll: 0 },
  { id: 'pos_desk', dev: DESK, scope: MAADI, path: '/sales/pos/?lang=ar', cart: ['3', '3'], scroll: 0, targets: { bar: BAR, cart: { sel: '.pos-cart' } } },
  { id: 'pos_phone', dev: PHONE, scope: MAADI, path: '/sales/pos/?lang=ar', cart: ['3', '3'], scroll: 0 },
];

const only = process.argv.slice(2);
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const man = fs.existsSync(`${OUT}/manifest.json`) ? JSON.parse(fs.readFileSync(`${OUT}/manifest.json`, 'utf8')) : {};
  const b = await chromium.launch(); let bad = 0;
  const setScope = async label => {    // press the tab in the bar (a real POST to /entities/switch/)
    const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'ar-EG', storageState: STATE }); const p = await ctx.newPage();
    await p.goto(BASE + '/dashboard/?lang=ar', { waitUntil: 'load' });
    const btn = p.locator('form[action*="/entities/switch/"] button', { hasText: label }).first();
    await Promise.all([p.waitForNavigation({ waitUntil: 'load' }), btn.click()]);
    await ctx.storageState({ path: STATE }); await ctx.close();
  };
  let cur = null;
  for (const s of SHOTS) {
    if (only.length && !only.includes(s.id)) continue;
    if (s.scope !== cur) { await setScope(s.scope); cur = s.scope; }
    const dv = s.dev;
    const ctx = await b.newContext({ viewport: { width: dv.width, height: dv.height }, deviceScaleFactor: dv.dpr, locale: 'ar-EG', hasTouch: !!dv.touch, isMobile: !!dv.mobile, storageState: STATE });
    const p = await ctx.newPage();
    const prep = async () => {
      await p.addStyleTag({ content: HIDE });
      const x = p.locator('[data-demo-tour-dismiss]');
      if (await x.first().isVisible().catch(() => false)) { await x.first().click(); await p.waitForTimeout(500); await p.addStyleTag({ content: HIDE }); }
      await p.evaluate(() => document.fonts.ready);
    };
    await p.goto(BASE + s.path, { waitUntil: 'load' }); await prep();
    if (s.chip) { await Promise.all([p.waitForNavigation({ waitUntil: 'load' }), p.locator('main a', { hasText: s.chip }).first().click()]); await prep(); }
    if (s.fill) for (const [sel, v] of Object.entries(s.fill)) { if (sel.startsWith('select')) await p.selectOption(sel, v); else await p.fill(sel, v); }   // typed, never submitted
    if (s.fill) await p.evaluate(() => document.activeElement && document.activeElement.blur());
    if (s.cart) for (const t of s.cart) { await p.locator(`[data-pos-tile="${t}"]`).first().click(); await p.waitForTimeout(450); }
    await p.waitForTimeout(400);
    // phone: the «شغال في» bar scrolls sideways; bring the active tab into view (it does not do that by itself)
    if (dv.mobile) await p.evaluate(label => { const b = Array.from(document.querySelectorAll('nav.hs-entities button, nav.hs-entities a, nav.hs-entities span')).find(e => e.innerText.trim() === label);
      if (b) { const nav = b.closest('nav'); const scroller = [nav, ...nav.querySelectorAll('*')].find(e => e.scrollWidth > e.clientWidth + 2) || nav; const r = b.getBoundingClientRect(), n = scroller.getBoundingClientRect(); scroller.scrollLeft += (r.left + r.width / 2) - (n.left + n.width / 2); } }, s.scope);
    const y = await p.evaluate(([sc, st]) => {
      const find = t => { const c = Array.from(document.querySelectorAll(t.tag || 'h1,h2,h3')).filter(h => h.offsetParent); return c.find(h => h.innerText.trim() === t.text) || c.find(h => h.innerText.trim().startsWith(t.text)); };
      if (st) { const leaf = Array.from(document.querySelectorAll('main *')).find(e => e.children.length === 0 && e.textContent.trim().startsWith(st.text)); const el = leaf && leaf.closest(st.closest); if (!el) return -1; return Math.max(0, Math.round(el.getBoundingClientRect().top + scrollY + st.off)); }
      if (typeof sc === 'number') return sc;
      const el = sc.el ? document.querySelector(sc.el) : find(sc); if (!el) return -1;
      return Math.max(0, Math.round(el.getBoundingClientRect().top + scrollY + sc.off));
    }, [s.scroll, s.scrollTarget || null]);
    if (y < 0) console.log(s.id, 'SCROLL TARGET NOT FOUND');
    let yy = Math.max(0, y);
    if (dv.mobile && s.nudge !== false) yy = await p.evaluate(y0 => {   // phone: nudge the scroll so no line of text is cut by the fixed bottom bar or the sticky header
      const fixed = Array.from(document.querySelectorAll('body *')).filter(e => { const c = getComputedStyle(e); return (c.position === 'fixed' || c.position === 'sticky') && e.offsetHeight > 20 && e.offsetHeight < 200 && c.display !== 'none' && c.visibility !== 'hidden'; });
      const H = innerHeight; let navTop = H, headBot = 0;
      for (const e of fixed) { const r = e.getBoundingClientRect(); if (r.bottom >= H - 2 && r.top > H / 2) navTop = Math.min(navTop, r.top); if (r.top <= 2 && r.bottom < H / 3) headBot = Math.max(headBot, r.bottom); }
      const inFixed = el => fixed.some(f => f.contains(el)); const leaves = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (w.nextNode()) { const n = w.currentNode; if (!n.nodeValue.trim()) continue; const el = n.parentElement; if (!el.checkVisibility() || inFixed(el)) continue; const rg = document.createRange(); rg.selectNodeContents(n);
        for (const r of rg.getClientRects()) if (r.width && r.height) leaves.push([r.top + scrollY, r.bottom + scrollY]); }
      for (const el of document.querySelectorAll('input, select, button, img, svg')) { if (inFixed(el) || !el.checkVisibility()) continue; const r = el.getBoundingClientRect(); if (r.width && r.height) leaves.push([r.top + scrollY, r.bottom + scrollY]); }
      const maxY = document.documentElement.scrollHeight - H, ok = y => !leaves.some(([a, b]) => (a < y + navTop - 2 && b > y + navTop + 2) || (headBot && a < y + headBot - 2 && b > y + headBot + 2));
      for (let d = 0; d < 260; d += 2) for (const y of [y0 + d, y0 - d]) if (y >= 0 && y <= maxY && ok(y)) return y;
      return y0;
    }, yy);
    await p.evaluate(v => window.scrollTo(0, v), yy); await p.waitForTimeout(500);
    const sy = await p.evaluate(() => Math.round(window.scrollY));
    if (!dv.touch) { await p.mouse.move(2, dv.height - 2); await p.waitForTimeout(300); }
    await p.screenshot({ path: `${OUT}/${s.id}.png` });
    fs.writeFileSync(`${OUT}/${s.id}.txt`, await p.evaluate(() => document.body.innerText));
    const seen = await p.evaluate(words => {
      const hits = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (w.nextNode()) { const n = w.currentNode, word = words.find(x => n.nodeValue.includes(x)); if (!word) continue;
        const el = n.parentElement, r = el.getBoundingClientRect();
        if (r.width && r.height && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth && el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) hits.push(word); }
      return hits;
    }, BANNED);
    if (seen.length) { bad++; console.log(s.id, 'VISIBLE BANNED TEXT', seen); }
    if (s.targets) {
      const rects = await p.evaluate(T => {
        const box = e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
        const union = bs => { const x0 = Math.min(...bs.map(b => b[0])), y0 = Math.min(...bs.map(b => b[1])), x1 = Math.max(...bs.map(b => b[0] + b[2])), y1 = Math.max(...bs.map(b => b[1] + b[3])); return [x0, y0, x1 - x0, y1 - y0]; };
        const o = {};
        for (const [k, t] of Object.entries(T)) {
          let el = null;
          if (t.sel) { el = t.has ? Array.from(document.querySelectorAll(t.sel)).find(e => e.innerText.includes(t.has)) : document.querySelector(t.sel); if (el && t.closest) el = el.closest(t.closest) || el; }
          else if (t.text && t.tag) { el = Array.from(document.querySelectorAll(t.tag)).find(h => h.innerText.trim() === t.text); if (el && t.withNext) { o[k] = union([box(el), box(el.nextElementSibling)]); continue; } }
          else if (t.text) { const leaf = Array.from(document.querySelectorAll('main *')).find(e => e.children.length === 0 && e.textContent.trim().startsWith(t.text)); el = leaf && (leaf.closest(t.closest) || leaf); }
          else if (t.rowText) { el = Array.from(document.querySelectorAll('main tr')).find(r => r.innerText.includes(t.rowText)); }
          else if (t.col) { const tab = document.querySelectorAll('main table')[t.table]; const th = tab && Array.from(tab.querySelectorAll('th')).find(h => h.innerText.trim() === t.col);
            if (th) { const a = box(th), tb = box(tab); o[k] = [a[0], tb[1], a[2], tb[3]]; } continue; }
          if (el) o[k] = box(el);
        }
        return o;
      }, s.targets);
      for (const k of Object.keys(s.targets)) if (!rects[k]) { bad++; console.log(s.id, 'TARGET NOT FOUND', k); }
      fs.writeFileSync(`${OUT}/${s.id}.rects.json`, JSON.stringify(Object.fromEntries(Object.entries(rects).map(([k, v]) => [k, v.map(n => Math.round(n))]))));
    }
    man[s.id] = { url: p.url().replace(BASE, ''), scope: s.scope, viewport: `${dv.width}x${dv.height} @${dv.dpr}x`, scrollY: sy,
      sandbox: 'commercial / grocery («سوبر ماركت حِسبة التجريبي»), v2 sandbox with branches made by prep_data_v2.js', role: 'sandbox owner «أ. سعيد عبد العال»',
      actions: [s.cart && `tapped quick buttons ${s.cart.join(', ')} (not paid)`, s.chip && `period «${s.chip}»`].filter(Boolean) };
    console.log(s.id, man[s.id].url, 'scope', s.scope, 'scrollY', sy);
    await ctx.close();
  }
  if (cur && cur !== G) await setScope(G);
  await b.close();
  fs.writeFileSync(`${OUT}/manifest.json`, JSON.stringify(man, null, 1));
  if (bad) { console.log(bad, 'problem(s)'); process.exit(2); }
})().catch(e => { console.error(String(e).slice(0, 800)); process.exit(1); });
