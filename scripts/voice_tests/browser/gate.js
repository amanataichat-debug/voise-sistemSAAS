// Эхо-ворота микрофона: эхо динамика в модель не уходит, настоящая речь — уходит.
// Время кадров задаём сами (gateFrame принимает now), поэтому проверка не зависит от реальных пауз.
const { chromium } = require('playwright');
let fails = 0;
const ok = (n, c, extra) => { console.log((c ? 'PASS ' : 'FAIL ') + n + (extra !== undefined ? '  [' + extra + ']' : '')); if (!c) fails++; };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto('http://127.0.0.1:8753/harness.html');
  await page.evaluate(() => window.__open(false));
  await page.waitForTimeout(300);

  let off = 10;                       // виртуальные часы: мс от performance.now() страницы
  const run = async (count, rms, active) => {
    const r = await page.evaluate(a => {
      const V = window.VoksyVoice, t0 = performance.now() + a.off, out = [];
      for (let i = 0; i < a.count; i++) out.push(V.__gate(a.rms, a.active, t0 + i * 40));
      return { out, stats: V.stats() };
    }, { count, rms, active, off });
    off += count * 40 + 40;
    return r;
  };
  const replyStarted = async () => { await page.evaluate(() => window.VoksyVoice._handle({ type: 'assistant.speech.started' })); };

  // 1. начало реплики: 2 с замера эха — ворота закрыты, уровень эха выучен
  await replyStarted();
  let r = await run(40, 0.05, true);
  ok('во время замера ворота закрыты', r.out.every(x => x === false));
  ok('уровень эха измерен по приветствию', r.stats.echoLvl > 0.03, r.stats.echoLvl);

  // 2. агент говорит, в микрофон идёт только его эхо — глушим
  r = await run(25, 0.052, true);
  ok('эхо динамика в модель не уходит', r.out.every(x => x === false), r.out.filter(Boolean).length + ' кадров прошло');

  // 3. одиночный всплеск (щелчок, стук) ворота не открывает
  r = await run(1, 0.4, true);
  ok('одиночный щелчок не перебивает агента', r.out[0] === false, r.out.join(','));
  await run(3, 0.05, true);

  // 4. настоящая речь поверх агента — ворота открываются, но не с первого кадра
  r = await run(6, 0.22, true);
  ok('речь владельца открывает ворота', r.out[r.out.length - 1] === true, r.out.join(','));
  ok('открылись не раньше четвёртого кадра', r.out[0] === false && r.out[2] === false, r.out.join(','));

  // 5. владелец замолчал — после удержания ворота закрываются
  r = await run(30, 0.05, true);
  ok('после паузы ворота закрываются', r.out[r.out.length - 1] === false, r.out.join(',').slice(-30));

  // 6. агент молчит — слушаем как есть
  r = await run(1, 0.01, false);
  ok('пока агент молчит, микрофон не глушим', r.out[0] === true);

  // 7. громкая связь: эхо вдвое громче минимального порога
  await page.evaluate(() => window.VoksyVoice.close());
  await page.waitForTimeout(200);
  await page.evaluate(() => window.__open(false));
  await page.waitForTimeout(300);
  off = 10;
  await replyStarted();
  await run(40, 0.12, true);
  r = await run(20, 0.125, true);
  ok('на громкой связи порог поднимается за уровнем эха', r.out.every(x => x === false), r.stats.echoLvl);
  r = await run(6, 0.5, true);
  ok('но перебить агента громко по-прежнему можно', r.out[r.out.length - 1] === true, r.out.join(','));

  // 8. хозяин прибавил звук посреди разговора — следующая реплика перемеряет эхо
  await page.waitForTimeout(3000);              // переживаем прежнее удержание ворот
  off = 10;                                     // окно замера ставится по настоящим часам страницы
  await replyStarted();
  await run(10, 0.5, true);                     // 400 мс замера в начале новой реплики
  r = await run(20, 0.5, true);
  ok('после новой реплики громкое эхо снова глохнет', r.out.every(x => x === false),
     r.out.filter(Boolean).length + ' кадров прошло, эхо ' + r.stats.echoLvl);

  ok('ошибок в консоли нет', errors.length === 0, errors.slice(0, 2).join(' | '));
  await browser.close();
  console.log('\nИТОГО провалов: ' + fails);
  process.exit(fails ? 1 : 0);
})();
