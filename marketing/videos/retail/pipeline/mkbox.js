// off camera: make a demo sandbox through the landing (/demo/<activity>/ -> sub tile), save explore/state-<sub>.json
// usage: node mkbox.js commercial grocery
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:8099';
const [act, sub] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1180, height: 740 }, locale: 'ar-EG' });
  const p = await ctx.newPage();
  if (act === 'commercial' || act === 'services') {   // activities with a second screen of sub tiles
    await p.goto(`${BASE}/demo/${act}/?lang=ar`, { waitUntil: 'load' });
    await Promise.all([p.waitForURL(u => !String(u).includes('/demo/'), { timeout: 120000 }), p.locator(`[data-demo-sub="${sub}"]`).first().click()]);
  } else {   // one sub-activity: post the landing tile's own form
    await p.goto(`${BASE}/demo/?lang=ar`, { waitUntil: 'load' });
    await p.evaluate(([a, s]) => { const f = document.querySelector(`form[data-demo-tile="${a}"]`) || document.querySelector('form[data-demo-tile]');
      f.querySelector('input[name=activity]').value = a; const i = f.querySelector('input[name=sub_activity]'); if (i) i.value = s; window.__f = f; }, [act, sub]);
    await Promise.all([p.waitForURL(u => !String(u).includes('/demo/'), { timeout: 120000 }), p.evaluate(() => window.__f.querySelector('button').click())]);
  }
  console.log('url', p.url());
  await ctx.storageState({ path: `explore/state-${sub}.json` });
  await b.close();
})().catch(e => { console.error(String(e).slice(0, 500)); process.exit(1); });
