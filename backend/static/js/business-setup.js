/**
 * «Чем занимается ваш бизнес?» — первый экран после регистрации (ветка 1811-niche-admin-speed).
 *
 * Шаг 1: сфера (поиск + плитки по группам, «Другое» своими словами).
 * Шаг 2: 2–3 вопроса ниши (готовые ответы по умолчанию) + телефон/компания, если их нет.
 * Шаг 3: «Настраиваем…» → «Готово, мы настроили: …» (воронка, каталог, бот, частые вопросы).
 *
 * Открывается сам из sidebar.js, если /api/users/me → business.needs_setup (кроме админа и входа
 * админа в кабинет клиента); из «Настроек» — VoksyBusinessSetup.open({ edit: true }).
 * API: GET /api/business/niches, POST /api/business/setup.
 */
(function () {
  'use strict';
  if (window.VoksyBusinessSetup) return;

  var KY = (function () { try { return localStorage.getItem('vs_lang') === 'ky'; } catch (e) { return false; } })();
  function L(ru, ky) { return KY && ky ? ky : ru; }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function token() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  function api(path, opts) {
    opts = opts || {};
    var h = { 'Authorization': 'Bearer ' + token() };
    if (opts.body) { h['Content-Type'] = 'application/json'; opts.body = JSON.stringify(opts.body); }
    opts.headers = h;
    return fetch('/api' + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && (d.detail || d.message)) || ('Ошибка ' + r.status));
        return d;
      });
    });
  }

  var CSS = [
    '.bs-ov{position:fixed;inset:0;z-index:1300;background:var(--vf-bg,#fff);overflow-y:auto;-webkit-overflow-scrolling:touch;opacity:0;transition:opacity .25s ease}',
    '.bs-ov.in{opacity:1}',
    '.bs-wrap{max-width:920px;margin:0 auto;padding:28px 20px 120px}',
    '.bs-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:26px}',
    '.bs-brand{display:flex;align-items:center;gap:10px;font:700 18px var(--vf-font-display,system-ui);color:var(--vf-text,#0e1729)}',
    '.bs-brand img{width:30px;height:30px}',
    '.bs-steps{display:flex;gap:6px}.bs-steps i{width:28px;height:5px;border-radius:5px;background:var(--vf-surface-3,#edf1f8);transition:background .3s}',
    '.bs-steps i.on{background:var(--vf-accent,#2a5ce8)}',
    '.bs-skip{border:0;background:none;color:var(--vf-text-4,#93a2b8);font:500 13px var(--vf-font,system-ui);cursor:pointer}',
    '.bs-h{font:700 clamp(24px,4vw,34px)/1.15 var(--vf-font-display,system-ui);letter-spacing:-.02em;margin:0 0 8px;color:var(--vf-text,#0e1729)}',
    '.bs-sub{font-size:15px;color:var(--vf-text-3,#63738b);margin:0 0 22px;line-height:1.5}',
    '.bs-search{position:relative;margin-bottom:18px}',
    '.bs-search input{width:100%;height:50px;border:1px solid var(--vf-border,#e2e7f0);border-radius:14px;padding:0 16px 0 46px;font:16px var(--vf-font,system-ui);background:var(--vf-surface,#fff);color:var(--vf-text,#0e1729)}',
    '.bs-search input:focus{outline:none;border-color:var(--vf-accent,#2a5ce8);box-shadow:0 0 0 4px var(--vf-accent-ring,rgba(42,92,232,.18))}',
    '.bs-search svg{position:absolute;left:16px;top:50%;transform:translateY(-50%);width:18px;height:18px;color:var(--vf-text-4,#93a2b8)}',
    '.bs-group{margin:22px 0 10px;font:600 12px var(--vf-font,system-ui);text-transform:uppercase;letter-spacing:.06em;color:var(--vf-text-4,#93a2b8)}',
    '.bs-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}',
    '.bs-tile{position:relative;display:flex;flex-direction:column;align-items:flex-start;gap:10px;padding:14px;border:1px solid var(--vf-border,#e2e7f0);border-radius:16px;background:var(--vf-surface,#fff);cursor:pointer;text-align:left;font:inherit;color:inherit;transition:transform .12s ease,border-color .15s,box-shadow .15s;-webkit-tap-highlight-color:transparent}',
    '.bs-tile:hover{border-color:var(--vf-border-strong,#cdd5e2);box-shadow:0 6px 18px -10px rgba(15,23,42,.25)}',
    '.bs-tile:active{transform:scale(.97)}',
    '.bs-tile .em{width:44px;height:44px;border-radius:12px;background:var(--vf-surface-2,#f6f8fc);display:grid;place-items:center;font-size:24px;line-height:1}',
    '.bs-tile .t{font:600 14px/1.3 var(--vf-font,system-ui);color:var(--vf-text,#0e1729)}',
    '.bs-tile.on{border-color:var(--vf-accent,#2a5ce8);box-shadow:0 0 0 3px var(--vf-accent-ring,rgba(42,92,232,.18));background:linear-gradient(180deg,#f5f8ff,#fff)}',
    '.bs-tile.on::after{content:"";position:absolute;top:10px;right:10px;width:20px;height:20px;border-radius:50%;background:var(--vf-accent,#2a5ce8) url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'white\' stroke-width=\'3\'%3E%3Cpath d=\'M5 12l5 5 9-9\'/%3E%3C/svg%3E") center/12px no-repeat}',
    '.bs-other{margin-top:14px;display:none}.bs-other.show{display:block}',
    '.bs-other input,.bs-field input{width:100%;height:48px;border:1px solid var(--vf-border,#e2e7f0);border-radius:12px;padding:0 14px;font:16px var(--vf-font,system-ui);color:var(--vf-text,#0e1729);background:var(--vf-surface,#fff)}',
    '.bs-other input:focus,.bs-field input:focus{outline:none;border-color:var(--vf-accent,#2a5ce8);box-shadow:0 0 0 4px var(--vf-accent-ring,rgba(42,92,232,.18))}',
    '.bs-q{border:1px solid var(--vf-border,#e2e7f0);border-radius:16px;padding:16px;margin-bottom:12px;background:var(--vf-surface,#fff)}',
    '.bs-q .qt{font:600 15px/1.35 var(--vf-font,system-ui);color:var(--vf-text,#0e1729)}',
    '.bs-q .qh{font-size:13px;color:var(--vf-text-3,#63738b);margin-top:4px}',
    '.bs-opts{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}',
    '.bs-opt{min-height:40px;padding:0 16px;border-radius:999px;border:1px solid var(--vf-border,#e2e7f0);background:var(--vf-surface,#fff);font:500 14px var(--vf-font,system-ui);color:var(--vf-text-2,#46556b);cursor:pointer;transition:all .12s}',
    '.bs-opt.on{background:var(--vf-accent,#2a5ce8);border-color:var(--vf-accent,#2a5ce8);color:#fff}',
    '.bs-field{margin-bottom:12px}.bs-field label{display:block;font:600 13px var(--vf-font,system-ui);color:var(--vf-text-2,#46556b);margin-bottom:6px}',
    '.bs-bar{position:fixed;left:0;right:0;bottom:0;z-index:1301;padding:14px 20px calc(14px + env(safe-area-inset-bottom,0px));background:rgba(255,255,255,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-top:1px solid var(--vf-border,#e2e7f0)}',
    '.bs-bar .in{max-width:920px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:12px}',
    '.bs-bar .pick{font-size:14px;color:var(--vf-text-3,#63738b);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.bs-bar .pick b{color:var(--vf-text,#0e1729)}',
    '.bs-btn{height:48px;padding:0 24px;border-radius:14px;border:0;background:var(--vf-accent,#2a5ce8);color:#fff;font:600 15px var(--vf-font,system-ui);cursor:pointer;display:inline-flex;align-items:center;gap:8px;flex:none;box-shadow:0 8px 20px -8px rgba(42,92,232,.6)}',
    '.bs-btn[disabled]{opacity:.45;cursor:default;box-shadow:none}',
    '.bs-btn.ghost{background:var(--vf-surface,#fff);color:var(--vf-text,#0e1729);border:1px solid var(--vf-border,#e2e7f0);box-shadow:none}',
    '.bs-err{color:var(--vf-danger,#dd2a2a);font-size:14px;margin:8px 0}',
    '.bs-done{text-align:center;padding-top:6vh}',
    '.bs-ring{width:72px;height:72px;margin:0 auto 20px;border-radius:50%;border:4px solid var(--vf-surface-3,#edf1f8);border-top-color:var(--vf-accent,#2a5ce8);animation:bsSpin .9s linear infinite}',
    '@keyframes bsSpin{to{transform:rotate(360deg)}}',
    '.bs-ok{width:76px;height:76px;margin:0 auto 18px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(135deg,#2a5ce8,#7c3aed);color:#fff;font-size:34px;box-shadow:0 16px 40px -16px rgba(42,92,232,.8);animation:bsPop .45s cubic-bezier(.2,.9,.3,1.3)}',
    '@keyframes bsPop{from{transform:scale(.4);opacity:0}to{transform:none;opacity:1}}',
    '.bs-list{max-width:560px;margin:22px auto 0;text-align:left;display:flex;flex-direction:column;gap:10px}',
    '.bs-li{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border:1px solid var(--vf-border,#e2e7f0);border-radius:14px;background:var(--vf-surface,#fff);font-size:14.5px;line-height:1.45;color:var(--vf-text,#0e1729);opacity:0;transform:translateY(8px);animation:bsIn .35s ease forwards}',
    '.bs-li .ck{flex:none;width:22px;height:22px;border-radius:50%;background:var(--vf-success,#159a4a);color:#fff;display:grid;place-items:center;font-size:13px;margin-top:1px}',
    '@keyframes bsIn{to{opacity:1;transform:none}}',
    '@media (max-width:760px){.bs-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.bs-wrap{padding:18px 16px 120px}.bs-tile{padding:12px}.bs-tile .em{width:40px;height:40px;font-size:22px}}'
  ].join('');

  var st = { data: null, niche: null, other: '', answers: {}, me: null, step: 1, edit: false, root: null, onDone: null };

  function niches() { return (st.data && st.data.niches) || []; }
  function current() { return niches().filter(function (n) { return n.key === st.niche; })[0] || null; }
  function title(n) { return KY && n.ky ? n.ky : n.title; }

  function mount() {
    if (!document.getElementById('bs-style')) {
      var s = document.createElement('style'); s.id = 'bs-style'; s.textContent = CSS; document.head.appendChild(s);
    }
    var ov = document.createElement('div');
    ov.className = 'bs-ov'; ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-modal', 'true');
    document.body.appendChild(ov);
    document.documentElement.style.overflow = 'hidden';
    requestAnimationFrame(function () { ov.classList.add('in'); });
    st.root = ov;
    ov.addEventListener('click', onClick);
    ov.addEventListener('input', onInput);
  }
  function unmount() {
    if (!st.root) return;
    st.root.remove(); st.root = null;
    var bar = document.getElementById('bs-bar'); if (bar) bar.remove();
    document.documentElement.style.overflow = '';
  }

  function head() {
    return '<div class="bs-top"><div class="bs-brand"><img src="/static/brand/logo.svg" alt="">Voksy AI</div>' +
      '<div class="bs-steps">' + [1, 2, 3].map(function (i) { return '<i class="' + (i <= st.step ? 'on' : '') + '"></i>'; }).join('') + '</div>' +
      (st.edit ? '<button class="bs-skip" type="button" data-close>' + L('Закрыть', 'Жабуу') + '</button>' : '<span></span>') + '</div>';
  }

  function bar(html) {
    var b = document.getElementById('bs-bar');
    if (!b) { b = document.createElement('div'); b.id = 'bs-bar'; b.className = 'bs-bar'; document.body.appendChild(b); b.addEventListener('click', onClick); }
    b.innerHTML = '<div class="in">' + html + '</div>';
  }

  // ── Шаг 1: сфера ──
  function step1() {
    st.step = 1;
    var q = (st.q || '').toLowerCase();
    var groups = (st.data.groups || []).map(function (g) {
      var list = niches().filter(function (n) { return n.group === g.key && (!q || (n.title + ' ' + (n.ky || '')).toLowerCase().indexOf(q) >= 0); });
      if (!list.length) return '';
      return '<div class="bs-group">' + esc(KY && g.ky ? g.ky : g.title) + '</div><div class="bs-grid">' + list.map(function (n) {
        return '<button type="button" class="bs-tile' + (n.key === st.niche ? ' on' : '') + '" data-niche="' + esc(n.key) + '"><span class="em">' + esc(n.icon) + '</span><span class="t">' + esc(title(n)) + '</span></button>';
      }).join('') + '</div>';
    }).join('');
    st.root.innerHTML = '<div class="bs-wrap">' + head() +
      '<h1 class="bs-h">' + L('Чем занимается ваш бизнес?', 'Бизнесиңиз эмне менен алектенет?') + '</h1>' +
      '<p class="bs-sub">' + L('Настроим под вашу сферу CRM, каталог, онлайн-запись и бота — займёт полминуты. Изменить можно в любой момент в настройках.',
        'CRM, каталог, онлайн-жазуу жана ботту сиздин тармакка ылайыктайбыз — жарым мүнөт. Каалаган убакта жөндөөлөрдөн өзгөртсөңүз болот.') + '</p>' +
      '<label class="bs-search"><svg aria-hidden="true"><use href="/static/icons/ui.svg#i-search"></use></svg><input id="bs-q" type="search" placeholder="' + L('Найти: цветы, стоматология, СТО…', 'Издөө: гүлдөр, стоматология, СТО…') + '" value="' + esc(st.q || '') + '" autocomplete="off"></label>' +
      (groups || '<p class="bs-sub">' + L('Не нашли? Выберите «Другое» и опишите своими словами.', 'Таппадыңызбы? «Башка» тандап, өз сөзүңүз менен жазыңыз.') + '</p>') +
      '<div class="bs-other' + (st.niche === 'other' ? ' show' : '') + '"><input id="bs-other" maxlength="120" placeholder="' + L('Опишите сферу: например, ремонт часов', 'Тармакты жазыңыз: мисалы, саат оңдоо') + '" value="' + esc(st.other) + '"></div>' +
      '</div>';
    var c = current();
    bar('<span class="pick">' + (c ? L('Выбрано: ', 'Тандалды: ') + '<b>' + esc(c.icon + ' ' + title(c)) + '</b>' : L('Выберите сферу', 'Тармакты тандаңыз')) + '</span>' +
      '<button class="bs-btn" type="button" data-next1' + (c ? '' : ' disabled') + '>' + L('Дальше', 'Андан ары') + ' →</button>');
  }

  // ── Шаг 2: вопросы ──
  function step2(err) {
    st.step = 2;
    var c = current();
    var qs = (c.questions || []).map(function (q) {
      var val = st.answers[q.key] != null ? st.answers[q.key] : q['default'];
      st.answers[q.key] = val;
      return '<div class="bs-q"><div class="qt">' + esc(q.title) + '</div>' + (q.hint ? '<div class="qh">' + esc(q.hint) + '</div>' : '') +
        '<div class="bs-opts">' + q.options.map(function (o) {
          return '<button type="button" class="bs-opt' + (o[0] === val ? ' on' : '') + '" data-q="' + esc(q.key) + '" data-v="' + esc(o[0]) + '">' + esc(o[1]) + '</button>';
        }).join('') + '</div></div>';
    }).join('');
    var me = st.me || {};
    var extra = '';
    if (!me.phone) extra += '<div class="bs-field"><label for="bs-phone">' + L('Ваш телефон — поможем с настройкой', 'Телефонуңуз — жөндөөгө жардам беребиз') + '</label><input id="bs-phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+996 700 123 456" value="' + esc(st.phone || '+996 ') + '"></div>';
    if (!me.company_name) extra += '<div class="bs-field"><label for="bs-company">' + L('Название компании (необязательно)', 'Компаниянын аталышы (милдеттүү эмес)') + '</label><input id="bs-company" maxlength="100" value="' + esc(st.company || '') + '"></div>';
    st.root.innerHTML = '<div class="bs-wrap">' + head() +
      '<h1 class="bs-h">' + esc(c.icon) + ' ' + L('Пара вопросов', 'Бир нече суроо') + '</h1>' +
      '<p class="bs-sub">' + L('Ответы уже выбраны так, как подходит большинству в вашей сфере — поменяйте, если у вас иначе.',
        'Жоопторду тармагыңыздагы көпчүлүккө ылайык тандап койдук — сизде башкача болсо өзгөртүңүз.') + '</p>' +
      qs + extra + (err ? '<div class="bs-err">' + esc(err) + '</div>' : '') + '</div>';
    bar('<button class="bs-btn ghost" type="button" data-back>← ' + L('Назад', 'Артка') + '</button>' +
      '<button class="bs-btn" type="button" data-save>' + L('Настроить кабинет', 'Кабинетти жөндөө') + '</button>');
  }

  // ── Шаг 3: настройка и итог ──
  function step3(result) {
    st.step = 3;
    if (!result) {
      st.root.innerHTML = '<div class="bs-wrap">' + head() + '<div class="bs-done"><div class="bs-ring"></div><h1 class="bs-h">' +
        L('Настраиваем кабинет…', 'Кабинетти жөндөп жатабыз…') + '</h1><p class="bs-sub">' +
        L('Воронка, каталог, бот и частые вопросы клиентов', 'Воронка, каталог, бот жана кардарлардын көп берген суроолору') + '</p></div></div>';
      bar('');
      return;
    }
    var items = (result.summary || []).map(function (t, i) {
      return '<div class="bs-li" style="animation-delay:' + (i * 90) + 'ms"><span class="ck">✓</span><span>' + esc(t) + '</span></div>';
    }).join('');
    st.root.innerHTML = '<div class="bs-wrap">' + head() + '<div class="bs-done"><div class="bs-ok">✓</div><h1 class="bs-h">' +
      L('Готово! Кабинет настроен под вас', 'Даяр! Кабинет сизге ылайыкталды') + '</h1><p class="bs-sub">' +
      L('Вот что мы сделали. Всё можно поменять в «Настройках → Мой бизнес».', 'Эмне жасалды. Баарын «Жөндөөлөр → Менин бизнесим» бөлүмүнөн өзгөртсөңүз болот.') +
      '</p><div class="bs-list">' + items + '</div></div></div>';
    var noBot = (result.summary || []).some(function (x) { return /после создания/.test(x); });
    var cat = (result.business && result.business.catalog && result.business.catalog.title) || L('каталог', 'каталог');
    bar('<span class="pick">' + (noBot ? L('Дальше: создайте бота и подключите WhatsApp', 'Андан ары: бот түзүп, WhatsApp туташтырыңыз')
      : L('Дальше: заполните «' + cat + '» и подключите WhatsApp', 'Андан ары: «' + cat + '» толтуруп, WhatsApp туташтырыңыз')) + '</span>' +
      '<button class="bs-btn" type="button" data-finish>' + L('Открыть кабинет', 'Кабинетти ачуу') + ' →</button>');
  }

  function save() {
    var phoneEl = st.root.querySelector('#bs-phone');
    var compEl = st.root.querySelector('#bs-company');
    if (phoneEl) st.phone = phoneEl.value.trim();
    if (compEl) st.company = compEl.value.trim();
    if (phoneEl && (!st.phone || st.phone === '+996' || st.phone.replace(/\D/g, '').length < 9)) {
      step2(L('Укажите телефон: например, +996 700 123 456', 'Телефонуңузду жазыңыз: мисалы, +996 700 123 456'));
      return;
    }
    var body = { niche: st.niche, answers: st.answers };
    if (st.niche === 'other') body.niche_other = st.other;
    if (phoneEl) body.phone = st.phone;
    if (compEl && st.company) body.company_name = st.company;
    step3(null);
    var started = Date.now();
    api('/business/setup', { method: 'POST', body: body }).then(function (res) {
      try { localStorage.setItem('vf_business', JSON.stringify(res.business || null)); } catch (e) { /* ignore */ }
      if (window.VF && window.VF.memoClear) window.VF.memoClear();
      setTimeout(function () { step3(res); }, Math.max(0, 900 - (Date.now() - started)));   // анимация не мелькает
      document.dispatchEvent(new CustomEvent('vf:business', { detail: res.business }));
    }).catch(function (e) { step2(e.message); });
  }

  function onInput(e) {
    if (e.target.id === 'bs-q') { st.q = e.target.value; var pos = e.target.selectionStart; step1(); var i = st.root.querySelector('#bs-q'); i.focus(); try { i.setSelectionRange(pos, pos); } catch (x) { /* ignore */ } }
    if (e.target.id === 'bs-other') { st.other = e.target.value; }
  }

  function onClick(e) {
    var t = e.target;
    var tile = t.closest('[data-niche]');
    if (tile) {
      st.niche = tile.getAttribute('data-niche');
      st.answers = {};
      step1();
      if (st.niche === 'other') { var o = st.root.querySelector('#bs-other'); if (o) o.focus(); }
      return;
    }
    if (t.closest('[data-next1]')) {
      if (st.niche === 'other' && !(st.other || '').trim()) { var oi = st.root.querySelector('#bs-other'); if (oi) { oi.focus(); oi.style.borderColor = '#dd2a2a'; } return; }
      step2(); window.scrollTo(0, 0); st.root.scrollTop = 0; return;
    }
    var opt = t.closest('[data-q]');
    if (opt) {
      st.answers[opt.getAttribute('data-q')] = opt.getAttribute('data-v');
      Array.prototype.forEach.call(opt.parentNode.children, function (b) { b.classList.toggle('on', b === opt); });
      return;
    }
    if (t.closest('[data-back]')) { step1(); return; }
    if (t.closest('[data-save]')) { save(); return; }
    if (t.closest('[data-finish]')) { unmount(); if (st.onDone) st.onDone(); else if (!st.edit) location.reload(); return; }
    if (t.closest('[data-close]')) { unmount(); }
  }

  function open(opts) {
    opts = opts || {};
    if (st.root) return;
    st.edit = !!opts.edit; st.onDone = opts.onDone || null; st.q = '';
    st.me = opts.me || null;
    var biz = st.me && st.me.business;
    st.niche = (biz && biz.niche) || null;
    st.other = (st.me && st.me.business_niche_other) || '';
    st.answers = (biz && biz.answers) ? Object.assign({}, biz.answers) : {};
    mount();
    st.root.innerHTML = '<div class="bs-wrap">' + head() + '<div class="bs-done"><div class="bs-ring"></div></div></div>';
    var mePromise = st.me ? Promise.resolve(st.me) : api('/users/me');
    Promise.all([api('/business/niches'), mePromise]).then(function (res) {
      st.data = res[0]; st.me = res[1];
      if (!st.niche && st.me.business && st.me.business.niche) st.niche = st.me.business.niche;
      step1();
    }).catch(function (e) {
      st.root.innerHTML = '<div class="bs-wrap">' + head() + '<p class="bs-err">' + esc(e.message) + '</p></div>';
    });
  }

  window.VoksyBusinessSetup = { open: open };
})();
