// v2 demo data, made through the UI as the sandbox owner (scratch demo box only): node prep_data_v2.js <step>
// steps: entities | locations | cashboxes | transfers | sales | rename   (state: explore/state-p2.json)
const { chromium } = require('playwright'); const fs = require('fs');
const BASE = 'http://127.0.0.1:8099'; const step = process.argv[2];
const ENT = [['BR2', 'فرع المعادي'], ['BR3', 'فرع مدينة نصر']];
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'ar-EG', storageState: 'explore/state-p2.json' });
  const p = await ctx.newPage();
  const go = async u => { await p.goto(BASE + u, { waitUntil: 'load' }); };
  const errors = async () => p.evaluate(() => Array.from(document.querySelectorAll('.errorlist, .op-error, [role=alert], .field-error, .errors')).map(e => e.innerText.trim()).filter(Boolean).join(' | '));
  const submit = async () => { await Promise.all([p.waitForNavigation({ waitUntil: 'load' }), p.locator('main form[method=post] button[type=submit], form[method=post]:not([action]) button[type=submit]').last().click()]); };
  if (step === 'entities') {
    for (const [code, name] of ENT) {
      await go('/entities/?lang=ar'); if ((await p.content()).includes(name)) { console.log('exists', name); continue; }
      await go('/entities/new/?lang=ar');
      await p.fill('input[name=code]', code); await p.fill('input[name=name_ar]', name);
      await p.selectOption('select[name=kind]', 'branch'); await p.selectOption('select[name=activity]', '');
      if (!(await p.isChecked('input[name=active]'))) await p.check('input[name=active]');
      await submit(); console.log(name, '->', p.url(), await errors());
    }
  }

  const scope = async label => {   // press a tab of the «شغال في» bar
    await go('/dashboard/?lang=ar');
    const btn = p.locator('form[action*="/entities/switch/"] button', { hasText: label }).first();
    if (await btn.count()) await Promise.all([p.waitForNavigation({ waitUntil: 'load' }), btn.click()]);
  };
  const LOC = [['مخزن المعادي', 'فرع المعادي', 'WH-MAADI'], ['مخزن مدينة نصر', 'فرع مدينة نصر', 'WH-NASR']];
  const BOX = [['خزنة المعادي', 'فرع المعادي', 'CB-MAADI'], ['خزنة مدينة نصر', 'فرع مدينة نصر', 'CB-NASR']];
  if (step === 'locations' || step === 'cashboxes') {
    await scope('المجموعة كلها');
    const list = step === 'locations' ? LOC : BOX, url = step === 'locations' ? '/master-data/locations/' : '/master-data/cashboxes/';
    for (const [name, ent, code] of list) {
      await go(url + '?lang=ar'); if ((await p.evaluate(() => document.body.innerText)).includes(name)) { console.log('exists', name); continue; }
      await go(url + 'new/?lang=ar');
      await p.fill('main input[name=name_ar]', name);
      await p.selectOption('main select[name=entity]', { label: ent });
      const codeF = step === 'locations' ? 'input[name=location_code]' : 'input[name=cashbox_code]';
      if (await p.locator(codeF).count()) await p.fill(codeF, code);
      for (const cb of (step === 'locations' ? ['is_selling_location', 'is_receiving_location', 'active'] : ['active'])) { const c = p.locator(`main input[name=${cb}]`); if (await c.count() && !(await c.isChecked())) await c.check(); }
      await submit(); console.log(name, '->', p.url(), await errors());
    }
  }
  if (step === 'rename') {
    await go('/entities/1/?lang=ar'); console.log('main name was', await p.inputValue('input[name=name_ar]'));
    await p.fill('input[name=name_ar]', 'الفرع الرئيسي'); await submit(); console.log('->', p.url(), await errors());
  }

  const MOVE = [['أرز مصري 1 كجم', 20], ['زيت عباد الشمس 1 لتر', 12], ['سكر 1 كجم', 30], ['مكرونة 400 جم', 12], ['فول مدمس علبة 400 جم', 10], ['صلصة طماطم 360 جم', 8], ['مياه معدنية 1.5 لتر', 12], ['لبن كامل الدسم 1 لتر', 6]];
  if (step === 'transfers2') {   // fresh items for the Maadi till's quick buttons
    await scope('المجموعة كلها');
    for (const [item, q] of [['عيش بلدي (5 أرغفة)', 10], ['طماطم', 8]]) {
      await go('/inventory/operations/transfer/?lang=ar');
      await p.selectOption('main select[name=item]', { label: item });
      await p.selectOption('main select[name=source_location]', { label: 'المخزن الرئيسي' });
      await p.selectOption('main select[name=destination_location]', { label: 'مخزن المعادي' });
      await p.fill('main input[name=quantity]', String(q)); await p.fill('main textarea[name=reason]', 'تزويد الفرع');
      await submit(); console.log('مخزن المعادي', item, '->', p.url().replace(BASE, ''), await errors());
    }
  }
  if (step === 'transfers') {
    await scope('المجموعة كلها');
    for (const dest of ['مخزن المعادي', 'مخزن مدينة نصر']) for (const [item, q] of MOVE) {
      await go('/inventory/operations/transfer/?lang=ar');
      await p.selectOption('main select[name=item]', { label: item });
      await p.selectOption('main select[name=source_location]', { label: 'المخزن الرئيسي' });
      await p.selectOption('main select[name=destination_location]', { label: dest });
      await p.fill('main input[name=quantity]', String(dest === 'مخزن المعادي' ? q : Math.round(q * 0.75)));
      await p.fill('main textarea[name=reason]', 'تزويد الفرع');
      await submit(); console.log(dest, item, '->', p.url().replace(BASE, ''), await errors());
    }
  }
  // one sale in a branch: tab, till, tap quick buttons, pay with a cash button (a real POS sale in the demo box)
  const sale = async (branch, tiles, cash) => {
    await scope(branch); await go('/sales/pos/?lang=ar');
    const x = p.locator('[data-demo-tour-dismiss]'); if (await x.first().isVisible().catch(() => false)) await x.first().click();
    for (const t of tiles) {
      const tile = p.locator('[data-pos-tile]', { hasText: t }).first();
      if (!(await tile.isVisible())) { const more = p.locator('[data-pos-quick] button, [data-pos-quick] a', { hasText: 'المزيد' }).first(); if (await more.count()) { await more.click(); await p.waitForTimeout(300); } }
      await tile.click(); await p.waitForTimeout(300);
    }
    await p.locator(`[data-cash="${cash}"]`).first().click(); await p.waitForTimeout(300);
    const total = await p.locator('[data-pos-bar-total]').innerText();
    await Promise.all([p.waitForNavigation({ waitUntil: 'load' }), p.locator('[data-pos-pay]').first().click()]);
    const last = await p.locator('[data-pos-last]').innerText().catch(() => '(no confirmation) ' + (p.url()));
    console.log(branch, tiles.join('+'), 'total', total, '->', last.replace(/\s+/g, ' ').slice(0, 140), await errors());
  };
  if (step === 'testsale') await sale('فرع المعادي', ['أرز مصري', 'زيت عباد الشمس'], '200');
  if (step === 'sales') {
    const S = { 'فرع المعادي': [[['أرز مصري', 'زيت عباد الشمس', 'سكر 1'], '200'], [['مكرونة', 'فول مدمس', 'صلصة'], '100'], [['سكر 1', 'سكر 1', 'أرز مصري'], '200'], [['زيت عباد الشمس', 'مكرونة'], '200']],
                'فرع مدينة نصر': [[['زيت عباد الشمس', 'زيت عباد الشمس', 'أرز مصري'], '500'], [['سكر 1', 'مكرونة', 'مكرونة'], '100'], [['فول مدمس', 'صلصة', 'أرز مصري'], '100']],
                'الفرع الرئيسي': [[['عيش بلدي', 'طماطم', 'خيار'], '100'], [['أرز مصري', 'سكر 1'], '100']] };
    for (const [br, list] of Object.entries(S)) if (!process.argv[3] || process.argv[3].split(',').includes(br)) for (const [tiles, cash] of list) await sale(br, tiles, cash);
    await scope('المجموعة كلها');
  }

  if (step === 'fixnames') {   // QA: no real bank / brand names on screen (seeded cashbox names), renamed through their edit screens
    for (const [pk, name] of [[4, 'حساب البنك — الجاري'], [3, 'ماكينة الدفع بالكارت']]) {
      await go(`/master-data/cashboxes/${pk}/edit/?lang=ar`); const was = await p.inputValue('main input[name=name_ar]');
      await p.fill('main input[name=name_ar]', name); const en = p.locator('main input[name=name_en]'); if (await en.count()) await en.fill('');
      await submit(); console.log(`cashbox ${pk}: ${was} -> ${name}`, p.url().replace(BASE, ''), await errors());
    }
  }
  if (step === 'company') {    // obvious demo values so the «ناقص التليفون، العنوان، الرقم الضريبي» reminder goes away
    await go('/settings/company/?lang=ar');
    await p.fill('textarea[name="company.address"]', 'عنوان تجريبي — القاهرة');
    await p.fill('input[name="company.phone"]', '01000000000');
    await p.fill('input[name="company.tax_number"]', '000-000-000');
    await Promise.all([p.waitForNavigation({ waitUntil: 'load' }), p.locator('button[name=action][value=details]').click()]);
    console.log('company ->', p.url().replace(BASE, ''), await errors());
  }
  if (step === 'look') { await go(process.argv[3]); console.log(p.url()); fs.writeFileSync('explore/look.txt', await p.evaluate(() => document.body.innerText)); await p.screenshot({ path: 'explore/look.png', fullPage: true }); }
  await ctx.storageState({ path: 'explore/state-p2.json' });
  await b.close();
})().catch(e => { console.error(String(e).slice(0, 600)); process.exit(1); });
