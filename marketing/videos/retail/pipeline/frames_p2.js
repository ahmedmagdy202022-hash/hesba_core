// v2 promo layers at DPR 2 (3840x2160): node frames_p2.js
// build_p2/layers/:
//   lap.png, phone.png          device bodies (bezel, shadows, floor glow) with a blank screen in the app's page colour
//   shot_<id>.png               one capture placed on its device screen (transparent elsewhere), so screens can change on their own
//   slogo.png                   small logo (top left), as v1
//   <line>/head.png             band caption (Kufi 700, gold rule) as v1;  <line>/hl<k>.png  gold outline k (full frame)
//   cards/<name>/*.png          card pieces: q (p2 question), logo_big (p3), p8 (logo + slogan), p9 (logo + caption + URL pill)
// meta.json: piece boxes (1x px), screen/phone rects, rendered caption texts. Strings from script_p2.json, colours/fonts from the repo tokens.css.
const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path');
const HERE = __dirname, OUT = path.join(HERE, 'build_p2/layers');
const SRC = path.resolve(HERE, '../retail-src/static/hesba');
const C = JSON.parse(fs.readFileSync(path.join(HERE, 'build_p2/p2_resolved.json'), 'utf8')); const L = C.layout;
const script = JSON.parse(fs.readFileSync(path.join(HERE, 'script_p2.json'), 'utf8'));
const LOGO = `file://${SRC}/brand/hesba-logo-reversed.png`;
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const BLANK = 'rgb(237,241,245)';   // the app's page background as captured (blank screen during a screen change)
const HP = C.head_px;
const CSS = `
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1920px;height:1080px;overflow:hidden;background:transparent}
body{position:relative;direction:rtl;font-family:var(--hs-font-body);color:var(--hs-surface)}
img{display:block}
.lap{position:absolute}
.lap .panel{position:absolute;border-radius:20px;background:linear-gradient(160deg,rgba(255,255,255,.10),rgba(255,255,255,0) 22%),var(--hs-ink);
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.16),0 60px 110px -30px rgba(0,0,0,.60),0 22px 40px -14px rgba(0,0,0,.50),0 4px 10px rgba(0,0,0,.30)}
.lap .deck{position:absolute;height:18px;border-radius:2px 2px 16px 16px;
  background:linear-gradient(180deg,rgba(255,255,255,.30),rgba(255,255,255,.08) 30%,rgba(0,0,0,.25) 100%),var(--hs-navy-raised);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.35),0 18px 30px -8px rgba(0,0,0,.55),0 4px 8px rgba(0,0,0,.35)}
.lap .notch{position:absolute;top:0;height:6px;border-radius:0 0 8px 8px;background:rgba(0,0,0,.30)}
.scr{position:absolute;background:${BLANK}}
.ph{position:absolute;background:linear-gradient(145deg,rgba(255,255,255,.22),rgba(255,255,255,0) 26%,rgba(255,255,255,0) 74%,rgba(255,255,255,.10)),var(--hs-ink);
  box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.22),inset 0 0 0 4px rgba(0,0,0,.35),0 50px 90px -24px rgba(0,0,0,.60),0 18px 34px -12px rgba(0,0,0,.50),0 3px 8px rgba(0,0,0,.30)}
.key{position:absolute;width:4px;border-radius:2px;background:linear-gradient(90deg,rgba(255,255,255,.25),rgba(0,0,0,.2)),var(--hs-ink)}
.glow{position:absolute;border-radius:50%;background:radial-gradient(closest-side,rgba(var(--hs-teal-rgb),.26),rgba(var(--hs-teal-rgb),.08) 55%,rgba(var(--hs-teal-rgb),0));filter:blur(6px)}
.hl{position:absolute;border:3px solid var(--hs-gold-bright);border-radius:14px;box-shadow:0 0 26px 6px rgba(242,213,142,.42),inset 0 0 20px rgba(242,213,142,.16)}
.wrap{position:absolute;padding:24px}
.head{display:flex;align-items:center;gap:30px;flex-direction:row}
.head h1{font-family:var(--hs-font-head);font-weight:700;font-size:${HP}px;line-height:1.3;color:var(--hs-surface);white-space:nowrap;text-shadow:0 4px 18px rgba(0,0,0,.28)}
.rule{width:3px;height:${Math.round(HP * 0.98)}px;border-radius:2px;background:linear-gradient(180deg,var(--hs-gold-bright),rgba(242,213,142,.35))}
.logo-sm{height:56px}
.card-h{font-family:var(--hs-font-head);font-weight:700;font-size:96px;line-height:1.35;color:var(--hs-surface);white-space:nowrap;text-shadow:0 6px 24px rgba(0,0,0,.30);text-align:center}
.cta-h{font-family:var(--hs-font-head);font-weight:700;font-size:80px;line-height:1.35;color:var(--hs-surface);white-space:nowrap;text-shadow:0 6px 24px rgba(0,0,0,.30);text-align:center}
.pill{direction:ltr;unicode-bidi:isolate;font-weight:700;font-size:50px;letter-spacing:.5px;color:var(--hs-surface);padding:22px 64px;border-radius:999px;
  border:2px solid var(--hs-gold-bright);background:rgba(20,56,107,.55);box-shadow:0 0 36px rgba(242,213,142,.28),inset 0 0 22px rgba(242,213,142,.10)}
.logo-lg{height:150px}
.logo-xl{height:200px}
`;
const doc = body => `<!doctype html><html dir="rtl" lang="ar"><head><meta charset="utf-8"><link rel="stylesheet" href="file://${SRC}/css/tokens.css"><style>${CSS}</style></head><body>${body}</body></html>`;
const S = L.screen_scale, SW = Math.round(1024 * S), SH = Math.round(642 * S);
const P = L.phone, PBZ = 11, PW = P.w + 2 * PBZ, PX = P.cx - PW / 2;
const geo = { screen: [L.screen_x, L.screen_y, S, SW, SH], phone: [PX + PBZ, P.top + PBZ, P.w / 390, P.w, P.h],
  laptop_box: [L.screen_x - 12, L.screen_y - 14, SW + 24, SH + 26 + 18], phone_box: [PX - 4, P.top, PW + 8, P.h + 2 * PBZ], focus: [(PX + L.screen_x + SW) / 2, L.screen_y + SH / 2] };

function laptop(x, y, sw, sh) {
  const bz = 12, bt = 14, pw = sw + 2 * bz, phh = sh + bt + bz, dx = 46;
  return `<div class="glow" style="left:${x - 160}px;top:${y + sh + bz + 6}px;width:${sw + 320}px;height:90px"></div>` +
    `<div class="lap" style="left:${x - bz}px;top:${y - bt}px"><div class="panel" style="left:0;top:0;width:${pw}px;height:${phh}px"><div class="scr" style="left:${bz}px;top:${bt}px;width:${sw}px;height:${sh}px;border-radius:5px"></div></div>` +
    `<div class="deck" style="left:${-dx}px;top:${phh}px;width:${pw + 2 * dx}px"><div class="notch" style="left:${pw / 2 + dx - 70}px;width:140px"></div></div></div>`;
}
function phone(x, y, w, h, bz, rad) {
  const ow = w + 2 * bz, oh = h + 2 * bz;
  return `<div class="glow" style="left:${x - 60}px;top:${y + oh - 30}px;width:${ow + 120}px;height:70px"></div>` +
    `<div class="key" style="left:${x - 3}px;top:${y + 150}px;height:60px"></div><div class="key" style="left:${x - 3}px;top:${y + 225}px;height:60px"></div><div class="key" style="left:${x + ow - 1}px;top:${y + 180}px;height:90px"></div>` +
    `<div class="ph" style="left:${x}px;top:${y}px;width:${ow}px;height:${oh}px;border-radius:${rad}px"><div class="scr" style="left:${bz}px;top:${bz}px;width:${w}px;height:${h}px;border-radius:${rad - bz}px"></div></div>`;
}

(async () => {
  let problems = 0;
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
  const tmp = path.join(HERE, 'build_p2/html'); fs.mkdirSync(tmp, { recursive: true }); fs.mkdirSync(OUT, { recursive: true });
  const load = async (html, name) => { const f = path.join(tmp, name + '.html'); fs.writeFileSync(f, html); await p.goto('file://' + f, { waitUntil: 'load' }); await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(120); };
  const box = sel => p.evaluate(s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }, sel);
  const piece = async (k, file, meta) => {   // one .wrap piece, the others hidden
    await p.evaluate(id => document.querySelectorAll('.wrap').forEach(e => { e.style.visibility = e.id === id ? 'visible' : 'hidden'; }), k);
    meta[k] = await box('#' + k); await p.locator('#' + k).screenshot({ path: file, omitBackground: true });
  };
  const full = async file => p.screenshot({ path: file, omitBackground: true });
  const kufi = () => p.evaluate(() => Array.from(document.fonts).some(f => f.status === 'loaded' && f.family.replace(/["']/g, '') === 'Noto Kufi Arabic' && f.weight === '700'));
  const meta = { geo, lines: {}, cards: {} };
  // device bodies
  await load(doc(laptop(L.screen_x, L.screen_y, SW, SH)), 'lap'); await full(path.join(OUT, 'lap.png'));
  await load(doc(phone(PX, P.top, P.w, P.h, PBZ, 50)), 'phone'); await full(path.join(OUT, 'phone.png'));
  // every capture used, on its screen
  const used = new Set(); for (const s of script) for (const g of (C.lines[s.id].segs || [])) { if (g.desk) used.add(g.desk); if (g.phone) used.add(g.phone); }
  for (const id of used) {
    const isPhone = id.endsWith('_phone'); const src = `file://${HERE}/build_p2/img/${id}.png`;
    const html = isPhone ? `<img src="${src}" style="position:absolute;left:${PX + PBZ}px;top:${P.top + PBZ}px;width:${P.w}px;height:${P.h}px;border-radius:${50 - PBZ}px">`
      : `<img src="${src}" style="position:absolute;left:${L.screen_x}px;top:${L.screen_y}px;width:${SW}px;height:${SH}px;border-radius:5px">`;
    await load(doc(html), 'shot_' + id);
    if (!(await p.evaluate(() => document.images[0].complete && document.images[0].naturalWidth))) { problems++; console.log('missing image', id); }
    await full(path.join(OUT, `shot_${id}.png`));
  }
  await load(doc(`<div class="wrap" id="slogo" style="left:40px;top:44px"><img class="logo-sm" src="${LOGO}"></div>`), 'slogo'); await piece('slogo', path.join(OUT, 'slogo.png'), meta);
  // per line: band caption + outlines
  for (const s of script) {
    const cfg = C.lines[s.id], dir = path.join(OUT, s.id); fs.mkdirSync(dir, { recursive: true });
    const m = meta.lines[s.id] = { hl: [] };
    if (cfg.band !== false) {
      await load(doc(`<div class="wrap" id="head" style="right:52px;top:30px"><div class="head"><div class="rule"></div><h1>${esc(s.caption)}</h1></div></div>`), `head_${s.id}`);
      m.caption_text = await p.evaluate(() => document.querySelector('h1').innerText.trim());
      if (!(await kufi())) { problems++; console.log(s.id, 'Kufi 700 not loaded'); }
      await piece('head', path.join(dir, 'head.png'), m);
      if (m.head[0] < meta.slogo[0] + meta.slogo[2] + 10) { problems++; console.log(s.id, 'caption runs into the small logo'); }
    }
    for (const [k, h] of (cfg.highlights || []).entries()) {
      const [ox, oy, sc] = h.on === 'phone' ? geo.phone : geo.screen;
      const r = h.rect_px; const R = [ox + r[0] * sc, oy + r[1] * sc, r[2] * sc, r[3] * sc];
      await load(doc(`<div class="hl" style="left:${R[0]}px;top:${R[1]}px;width:${R[2]}px;height:${R[3]}px"></div>`), `hl_${s.id}_${k}`);
      await full(path.join(dir, `hl${k}.png`)); m.hl.push(R);
    }
  }
  // cards
  const card = async (name, body, keys, check) => {
    const dir = path.join(OUT, 'cards', name); fs.mkdirSync(dir, { recursive: true }); const m = meta.cards[name] = {};
    await load(doc(body), 'card_' + name);
    if (check) for (const [k, sel] of Object.entries(check)) m[k + '_text'] = await p.evaluate(s => document.querySelector(s).innerText.trim(), sel);
    if (!(await kufi()) && keys.includes('head')) { problems++; console.log(name, 'Kufi 700 not loaded'); }
    for (const k of keys) await piece(k, path.join(dir, k + '.png'), m);
    for (const k of keys) if (m[k][0] < 0 || m[k][0] + m[k][2] > 1920) { problems++; console.log(name, k, 'overflows'); }
  };
  const byId = Object.fromEntries(script.map(s => [s.id, s]));
  await card('q', `<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center"><div class="wrap" id="head" style="position:relative"><div class="card-h">${esc(byId.p2.card)}</div></div></div>`, ['head'], { head: '.card-h' });
  await card('logo_big', `<div class="wrap" id="logo" style="left:50%;top:${L.screen_y + SH / 2}px;transform:translate(-50%,-50%)"><img class="logo-xl" src="${LOGO}"></div>`, ['logo']);
  await card('p8', `<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px">
    <div class="wrap" id="logo" style="position:relative"><img class="logo-xl" src="${LOGO}"></div>
    <div class="wrap" id="head" style="position:relative"><div class="cta-h">${esc(byId.p8.caption)}</div></div></div>`, ['logo', 'head'], { head: '.cta-h' });
  await card('p9', `<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px">
    <div class="wrap" id="logo" style="position:relative"><img class="logo-lg" src="${LOGO}"></div>
    <div class="wrap" id="head" style="position:relative"><div class="cta-h">${esc(byId.p9.caption)}</div></div>
    <div class="wrap" id="pill" style="position:relative;padding:48px"><div class="pill">${esc(byId.p9.url)}</div></div></div>`, ['logo', 'head', 'pill'], { head: '.cta-h', pill: '.pill' });
  // caption checks (no normalisation)
  for (const s of script) {
    const got = C.lines[s.id].band === false ? (s.id === 'p8' ? meta.cards.p8.head_text : meta.cards.p9.head_text) : meta.lines[s.id].caption_text;
    if (got !== s.caption) { problems++; console.log(s.id, 'CAPTION DIFFERS', JSON.stringify(got), JSON.stringify(s.caption)); }
  }
  if (meta.cards.q.head_text !== byId.p2.card) { problems++; console.log('p2 card differs'); }
  if (meta.cards.p9.pill_text !== byId.p9.url) { problems++; console.log('url differs'); }
  fs.writeFileSync(path.join(OUT, 'meta.json'), JSON.stringify(meta));
  await b.close();
  console.log('frames_p2', problems ? problems + ' problem(s)' : 'ok', used.size, 'captures');
  if (problems) process.exit(2);
})().catch(e => { console.error(e); process.exit(1); });
