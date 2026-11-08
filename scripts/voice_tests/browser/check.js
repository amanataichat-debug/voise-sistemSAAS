const { chromium } = require('playwright');
const fs = require('fs');
const data = JSON.parse(fs.readFileSync(__dirname + '/report.sample.json', 'utf8'));
const OUT = process.env.VOICE_SHOTS ? process.env.VOICE_SHOTS + '/' : require('os').tmpdir() + '/';
let fails = 0;
const ok = (n, c, extra) => { console.log((c ? 'PASS ' : 'FAIL ') + n + (extra !== undefined ? '  [' + extra + ']' : '')); if (!c) fails++; };

(async () => {
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream'] });
  const page = await browser.newPage({ viewport: { width: 420, height: 880 }, deviceScaleFactor: 2 });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('http://127.0.0.1:8753/harness.html');
  await page.evaluate(() => window.__open(false));
  await page.waitForTimeout(400);
  await page.evaluate(() => window.__push({ type: 'connection_status', status: 'connected', audio_rate: 24000, full_duplex: true, owner_name: 'Азамат', prefs: { address: 'name', formality: 'vy' } }));
  await page.evaluate(d => window.__push({ type: 'owner.dashboard', cards: d.dashboard }), data);
  await page.waitForTimeout(300);
  ok('карточки старта нарисованы', await page.locator('.vv-card').count() === 4, await page.locator('.vv-card').count());
  ok('карточка балансов есть', await page.locator('.vv-money-r').count() >= 3);

  // отчёт приходит результатом функции
  await page.evaluate(d => window.__push({ type: 'function_call.completed', function: 'get_report', result: { ok: true, card: d.card } }), data);
  await page.waitForTimeout(350);
  const total = await page.locator('.vv-card.vv-wide').first().locator('.vv-scene').count();
  ok('сцены отчёта созданы', total === data.card.scenes.length, total);
  const shown = () => page.locator('.vv-card.vv-wide').first().locator('.vv-scene:not([hidden])').count();
  ok('сразу видна только первая сцена', await shown() === 1, await shown());
  await page.screenshot({ path: OUT + '1-report-start.png' });

  // речь агента по script — сцены должны загораться по очереди
  const seen = [];
  for (const line of data.script) {
    for (const chunk of line.match(/.{1,14}/g)) {
      await page.evaluate(t => window.__push({ type: 'transcript.delta', role: 'assistant', delta: t }), chunk + ' ');
      await page.waitForTimeout(25);
    }
    seen.push(await shown());
  }
  ok('каждая фраза открывает следующий блок', seen.join(',') === [1,2,3,4,5,6,7].join(','), seen.join(','));
  const ids = await page.$$eval('.vv-card.vv-wide:first-of-type .vv-scene:not([hidden]) h5', els => els.map(e => e.textContent));
  ok('блоки в порядке рассказа', ids[0] === 'Звонки' && ids[1] === 'Переписка' && ids[ids.length - 1] === 'Балансы', ids.join(' / '));
  ok('подсвечен последний названный блок', await page.locator('.vv-scene.vv-now h5').first().textContent() === 'Балансы');
  ok('числа в плитках досчитались', await page.locator('.vv-card.vv-wide .vv-tile b').first().textContent() !== '0');
  await page.screenshot({ path: OUT + '2-report-full.png' });

  // отчёт не должен застревать, если агент молчит
  await page.evaluate(d => window.__push({ type: 'function_call.completed', function: 'get_report', result: { ok: true, card: d.card } }), data);
  await page.waitForTimeout(300);
  ok('новый отчёт снова начинается с одной сцены', await shown() === 1, await shown());
  await page.waitForTimeout(3200);
  ok('агент молчит — блоки показываются сами', await shown() >= 2, await shown());
  await page.evaluate(() => window.__push({ type: 'turn.saved' }));
  await page.waitForTimeout(1900);
  ok('после реплики отчёт дорисован целиком', await shown() === data.card.scenes.length, await shown());

  // демо-режим
  await page.evaluate(() => window.VoksyVoice.close());
  await page.waitForTimeout(300);
  await page.evaluate(() => window.__open(true));
  await page.waitForTimeout(400);
  await page.evaluate(() => window.__push({ type: 'connection_status', status: 'connected', audio_rate: 24000, demo: true, company: 'Окна Комфорт' }));
  await page.waitForTimeout(250);
  const url = await page.evaluate(() => window.__ws.url);
  ok('демо идёт в свой сокет', url.includes('/ws/demo-voice'), url);
  ok('в демо нет шестерёнки настроек', !(await page.locator('.vv-set').isVisible()));
  ok('в шапке — название выдуманной компании', (await page.locator('.vv-name').textContent()).includes('Окна Комфорт'));
  await page.screenshot({ path: OUT + '3-demo.png' });

  ok('ошибок в консоли нет', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  console.log('\nИТОГО провалов: ' + fails);
  process.exit(fails ? 1 : 0);
})();
