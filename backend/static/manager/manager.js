/**
 * ИИ-управляющий салона (Voksy Beauty, шаг 1.6б): /static/manager.html.
 *
 * Вкладки (якорь в адресе — ссылки из Telegram и push ведут сразу куда нужно):
 *   #reports  — утренний и недельный отчёт: последний крупно (сегодня, вчера, агент), ниже история;
 *               «Получать отчёт уведомлением на телефон» (Web Push, /api/push/*).
 *   #approve  — что агент предлагает сделать (режим «на одобрение»): кому, почему, какие окна, сообщение
 *               или звонок; одобрить / отклонить по одному или все сразу.
 *   #journal  — что агент сделал за 14 дней: написал, позвонил, пропустил (клиент записался сам…).
 *   #settings — «агент решает сам / на одобрение», сценарии с переключателями, время отчёта, уведомления.
 * API: /api/manager/* (services/salon_manager.py). Клиентские сценарии — только автономный агент (Beauty Pro);
 * на других тарифах они с замком, отчёты и «спасибо + оценка» работают у всех.
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var root, top, esc, icon;
  var TABS = [['reports', 'Отчёты', 'sun'], ['approve', 'Одобрить', 'circle-check'], ['journal', 'Что сделал агент', 'history'],
              ['settings', 'Настройки', 'settings']];
  var st = { tab: 'reports', ov: null, reports: null, actions: null, journal: null, preview: null };
  var SC_ICON = { winback: 'users', fill_gaps: 'calendar', repeat: 'refresh-cw' };
  var MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

  function money(v) { return (Math.round(Number(v) || 0)).toLocaleString('ru-RU') + ' сом'; }
  function dayWords(iso) { if (!iso) return ''; var p = iso.split('-'); return (+p[2]) + ' ' + MON[+p[1] - 1]; }
  function ago(iso) {
    if (!iso) return '';
    var d = new Date(iso), m = Math.round((Date.now() - d) / 60000);
    if (m < 1) return 'только что';
    if (m < 60) return m + ' мин назад';
    if (m < 60 * 24) return Math.round(m / 60) + ' ч назад';
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  // ── загрузка ──
  function loadOverview() {
    return S.api('/api/manager/overview').then(function (r) {
      if (!r.ok) {
        root.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>';
        S.ready();
        return false;
      }
      st.ov = r.data;
      return true;
    });
  }
  function loadTab() {
    var t = st.tab;
    if (t === 'reports') return S.api('/api/manager/reports').then(function (r) { st.reports = r.ok ? r.data.reports : []; });
    if (t === 'approve') return S.api('/api/manager/actions?status=proposed').then(function (r) { st.actions = r.ok ? r.data.actions : []; });
    if (t === 'journal') return S.api('/api/manager/actions?status=approved,running,done,failed,skipped,rejected').then(function (r) { st.journal = r.ok ? r.data.actions : []; });
    return Promise.resolve();
  }
  function go(tab) {
    st.tab = tab;
    try { history.replaceState(null, '', location.pathname + location.search + '#' + tab); } catch (e) { /* ignore */ }
    render();
    loadTab().then(render);
  }

  // ── шапка и вкладки ──
  function renderTop() {
    var ov = st.ov || {}, auto = ov.settings && ov.settings.autonomy === 'auto';
    top.innerHTML = '<span class="mg-mode ' + (auto ? 'auto' : 'approve') + '" title="Меняется в настройках">' +
      icon(auto ? 'zap' : 'circle-check') + '<span>' + (auto ? 'Агент решает сам' : 'Действия — с вашего одобрения') + '</span></span>';
  }
  function tabsHtml() {
    var ov = st.ov || {};
    return '<div class="mg-tabs" role="tablist">' + TABS.map(function (t) {
      var badge = t[0] === 'approve' && ov.pending ? '<span class="mg-badge">' + ov.pending + '</span>'
        : (t[0] === 'reports' && ov.unread_reports ? '<span class="mg-dot"></span>' : '');
      return '<button type="button" role="tab" class="mg-tab' + (st.tab === t[0] ? ' on' : '') + '" data-tab="' + t[0] + '">' +
        icon(t[2]) + '<span>' + t[1] + '</span>' + badge + '</button>';
    }).join('') + '</div>';
  }

  function render() {
    if (!st.ov) return;
    renderTop();
    var body = st.tab === 'reports' ? reportsHtml() : st.tab === 'approve' ? approveHtml()
      : st.tab === 'journal' ? journalHtml() : settingsHtml();
    root.innerHTML = tabsHtml() + '<div class="mg-body">' + body + '</div>';
    bind();
    S.ready();
  }

  // ── отчёты ──
  function pushBanner() {
    var ov = st.ov;
    if (ov.push_devices) return '';
    var supported = ('serviceWorker' in navigator) && ('PushManager' in window) && ('Notification' in window);
    var ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    var standalone = window.matchMedia && window.matchMedia('(display-mode: standalone)').matches;
    var hint = supported ? 'Отчёт в ' + ov.settings.morning_report.hour + ':00 и «нужно ваше одобрение» придут на экран телефона, даже когда приложение закрыто.'
      : (ios && !standalone ? 'На iPhone уведомления работают в приложении: «Поделиться» → «На экран Домой», затем откройте Voksy AI с экрана и включите здесь.'
        : 'Этот браузер не умеет уведомления — откройте кабинет в Chrome или в установленном приложении.');
    return '<div class="mg-push">' + icon('bell') + '<div><b>Получать отчёт уведомлением на телефон</b><span>' + esc(hint) + '</span></div>' +
      (supported ? '<button type="button" class="btn btn-primary btn-sm" data-push>Включить</button>' : '') + '</div>';
  }

  function tile(label, value, sub, tone) {
    return '<div class="mg-tile' + (tone ? ' ' + tone : '') + '"><span>' + esc(label) + '</span><b>' + esc(value) + '</b>' +
      (sub ? '<i>' + esc(sub) + '</i>' : '') + '</div>';
  }

  function morningCard(rep) {
    var d = rep.data || {}, t = d.today || {}, y = d.yesterday || {}, a = d.agent || {};
    var html = '<div class="mg-rep-h"><div><span class="mg-kicker">' + icon('sun') + 'Утренний отчёт · ' + esc(dayWords(rep.day)) +
      (rep.preview ? ' · предпросмотр' : '') + '</span><h2>' + esc(rep.title) + '</h2></div></div>';
    html += '<div class="mg-tiles">' +
      tile('Записей сегодня', t.count || 0, t.first ? 'первая в ' + t.first : 'пока пусто') +
      tile('Ожидаем выручку', money(t.expected), 'по ценам записей') +
      tile('Вчера пришли', y.came || 0, y.no_show ? 'не пришли ' + y.no_show : 'без неявок', y.no_show ? 'warn' : '') +
      tile('Выручка вчера', money(y.revenue), y.avg_check ? 'средний чек ' + money(y.avg_check) : '') + '</div>';
    if ((t.by_master || []).length) {
      html += '<h3 class="mg-h3">Сегодня по мастерам</h3><div class="mg-rows">' + t.by_master.map(function (m) {
        return '<div class="mg-row"><span class="mg-ava">' + esc((m.name || '?').charAt(0)) + '</span><b>' + esc(m.name) + '</b>' +
          '<span class="muted">с ' + esc(m.first || '') + '</span><span class="mg-num">' + m.count + '</span></div>';
      }).join('') + '</div>';
    }
    if ((t.free || []).length) {
      html += '<h3 class="mg-h3">Свободно сегодня</h3><div class="mg-free">' + t.free.map(function (f) {
        return '<div class="mg-free-i"><b>' + esc(f.master) + '</b><span>' + f.hours + ' ч</span>' +
          (f.times && f.times.length ? '<i>' + esc(f.times.join(', ')) + '</i>' : '') + '</div>';
      }).join('') + '</div>';
    }
    if ((y.no_show_names || []).length) html += '<p class="mg-note warn">' + icon('triangle-alert') + 'Вчера не пришли: ' + esc(y.no_show_names.join(', ')) + '</p>';
    if (y.review_n) html += '<p class="mg-note">' + icon('star') + 'Оценки клиентов: ' + esc(y.review_avg) + ' из 5 (' + y.review_n + ')</p>';
    if (a.messages || a.calls || a.booked_by_bot) {
      html += '<p class="mg-note">' + icon('sparkles') + 'ИИ-управляющий вчера: написал ' + (a.messages || 0) + ', позвонил ' + (a.calls || 0) +
        '; бот записал ' + (a.booked_by_bot || 0) + (a.booked_after_manager ? ', вернулись после его сообщений ' + a.booked_after_manager : '') + '</p>';
    }
    if (d.pending) html += '<button type="button" class="mg-cta" data-go="approve">' + icon('circle-check') + 'Ждут вашего одобрения: ' + d.pending + icon('chevron-right') + '</button>';
    return html;
  }
  function weeklyCard(rep) {
    var d = rep.data || {}, w = d.week || {}, p = d.prev || {}, a = d.agent || {};
    var diff = (w.revenue || 0) - (p.revenue || 0);
    var html = '<div class="mg-rep-h"><div><span class="mg-kicker">' + icon('chart-column') + 'Итоги недели · ' + esc(dayWords(rep.day)) + '</span><h2>' + esc(rep.title) + '</h2></div></div>';
    html += '<div class="mg-tiles">' + tile('Выручка', money(w.revenue), p.revenue ? (diff >= 0 ? '+' : '−') + money(Math.abs(diff)) + ' к прошлой' : '', diff < 0 ? 'warn' : 'good') +
      tile('Визитов', w.came || 0, w.no_show ? 'не пришли ' + w.no_show : '') +
      tile('Новых клиентов', d.new_clients || 0, '') + tile('Давно не были', d.lapsed || 0, '60+ дней', d.lapsed ? 'warn' : '') + '</div>';
    if ((d.masters || []).length) {
      html += '<h3 class="mg-h3">Мастера</h3><div class="mg-rows">' + d.masters.map(function (m) {
        return '<div class="mg-row"><span class="mg-ava">' + esc((m.name || '?').charAt(0)) + '</span><b>' + esc(m.name) + '</b>' +
          '<span class="muted">' + m.visits + ' визитов</span><span class="mg-num">' + money(m.revenue) + '</span></div>';
      }).join('') + '</div>';
    }
    html += '<p class="mg-note">' + icon('sparkles') + 'ИИ-управляющий за неделю: написал ' + (a.messages || 0) + ', позвонил ' + (a.calls || 0) +
      (a.booked_after_manager ? ', вернулись ' + a.booked_after_manager : '') + '</p>';
    return html;
  }
  function reportCard(rep) {
    return '<article class="mg-card mg-rep">' + (rep.kind === 'weekly' ? weeklyCard(rep) : morningCard(rep)) + '</article>';
  }

  function reportsHtml() {
    var list = st.reports;
    var html = '';
    if (list === null) return '<div class="skeleton" style="height:320px;border-radius:18px"></div>';
    if (!list.length && !st.preview) {
      html += '<div class="mg-card mg-empty">' + icon('sun') + '<h2>Первый отчёт придёт завтра в ' + st.ov.settings.morning_report.hour + ':00</h2>' +
        '<p>Каждое утро: сколько записей сегодня, кто свободен, сколько заработали вчера, кто не пришёл и что сделал ИИ-управляющий. ' +
        'По понедельникам — итоги недели. Отчёт будет здесь' + (st.ov.telegram ? ', в Telegram-боте' : '') + ' и уведомлением на телефон.</p>' +
        '<button type="button" class="btn btn-primary" data-preview>' + icon('sun') + '<span>Показать, как он выглядит сейчас</span></button></div>';
      return html + pushBanner();
    }
    if (st.preview) html += reportCard(st.preview);
    if (list.length) html += reportCard(list[0]);
    html += pushBanner();
    if (list.length) {
      if (list.length > 1) {
        html += '<h3 class="mg-h3">Прошлые отчёты</h3><div class="mg-hist">' + list.slice(1).map(function (r) {
          return '<details class="mg-hist-i"><summary><span>' + icon(r.kind === 'weekly' ? 'chart-column' : 'sun') + '</span><b>' + esc(dayWords(r.day)) + '</b>' +
            '<span class="muted">' + esc(r.title) + '</span></summary><pre>' + esc(r.text) + '</pre></details>';
        }).join('') + '</div>';
      }
    }
    return html;
  }

  // ── одобрение ──
  function actionCard(a, withButtons) {
    var slots = (a.slots || []).map(function (s) { return '<span class="mg-slot">' + esc(s.label) + (s.master ? ' · ' + esc(s.master) : '') + '</span>'; }).join('');
    return '<div class="mg-act" data-id="' + esc(a.id) + '">' +
      '<span class="mg-act-ic ' + esc(a.scenario) + '">' + icon(SC_ICON[a.scenario] || 'sparkles') + '</span>' +
      '<div class="mg-act-b"><div class="mg-act-t"><b>' + esc(a.contact_name) + '</b><span class="mg-chip">' + esc(a.scenario_title) + '</span>' +
      '<span class="mg-chip ghost">' + icon(a.kind === 'call' ? 'phone' : 'message-circle') + (a.kind === 'call' ? 'Звонок' : 'Сообщение') + '</span></div>' +
      '<p>' + esc(a.reason || '') + '</p>' + (slots ? '<div class="mg-slots">' + slots + '</div>' : '') +
      (!withButtons ? '<div class="mg-res ' + esc(a.status) + '"><span class="mg-st">' + esc(a.status_title) + '</span>' + (a.result ? esc(a.result) : '') +
        '<span class="muted">' + esc(ago(a.done_at || a.created_at)) + '</span></div>' : '') + '</div>' +
      (withButtons ? '<div class="mg-act-btns"><button type="button" class="btn btn-sm btn-primary" data-ok="' + esc(a.id) + '">' + icon('check') + '<span>Одобрить</span></button>' +
        '<button type="button" class="btn btn-sm btn-ghost" data-no="' + esc(a.id) + '" aria-label="Отклонить">' + icon('x') + '</button></div>' : '') +
      '</div>';
  }
  function approveHtml() {
    var list = st.actions, auto = st.ov.settings.autonomy === 'auto';
    if (list === null) return '<div class="skeleton" style="height:240px;border-radius:18px"></div>';
    if (!list.length) {
      return '<div class="mg-card mg-empty">' + icon('circle-check') + '<h2>Нечего одобрять</h2><p>' +
        (auto ? 'Сейчас агент решает сам и не ждёт одобрения. Хотите сначала смотреть, кому и что он напишет, — включите «На одобрение» в настройках.'
          : 'Каждый день в 10:00 агент находит клиентов по включённым сценариям и присылает список сюда, в Telegram и уведомлением.') + '</p></div>';
    }
    return '<div class="mg-approve-h"><p>Агент ничего не отправит этим клиентам, пока вы не одобрите. Текст он напишет сам под каждого — с окнами из журнала записи.</p>' +
      '<button type="button" class="btn btn-primary" data-ok-all>' + icon('check') + '<span>Одобрить все (' + list.length + ')</span></button></div>' +
      '<div class="mg-acts">' + list.map(function (a) { return actionCard(a, true); }).join('') + '</div>';
  }
  function journalHtml() {
    var list = st.journal;
    if (list === null) return '<div class="skeleton" style="height:240px;border-radius:18px"></div>';
    if (!list.length) return '<div class="mg-card mg-empty">' + icon('history') + '<h2>Пока ничего</h2><p>Здесь будет всё, что ИИ-управляющий сделал за две недели: кому написал, кому позвонил, кого пропустил и почему.</p></div>';
    return '<div class="mg-acts">' + list.map(function (a) { return actionCard(a, false); }).join('') + '</div>';
  }

  // ── настройки ──
  function sw(key, title, sub, on, locked) {
    return '<div class="mg-sw' + (locked ? ' locked' : '') + '"><div class="t"><b>' + esc(title) + (locked ? ' <span class="mg-lock">' + icon('lock') + 'Beauty Pro</span>' : '') +
      '</b><div>' + sub + '</div></div><label class="switch"><input type="checkbox" data-sw="' + key + '"' + (on ? ' checked' : '') + (locked ? ' disabled' : '') +
      '><span class="track"></span></label></div>';
  }
  function sel(key, opts, val) {
    return '<select class="select-native mg-sel" data-sel="' + key + '">' + opts.map(function (o) {
      return '<option value="' + o[0] + '"' + (+val === o[0] ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('') + '</select>';
  }
  function settingsHtml() {
    var ov = st.ov, s = ov.settings, lock = !ov.scenarios_available;
    var html = '<div class="mg-card"><h3 class="mg-h3 first">Как агент действует</h3><div class="mg-seg">' +
      '<button type="button" data-auto="auto" class="' + (s.autonomy === 'auto' ? 'on' : '') + '"><b>' + icon('zap') + 'Решает сам</b><span>Пишет и звонит клиентам по сценариям, вам — отчёт утром.</span></button>' +
      '<button type="button" data-auto="approve" class="' + (s.autonomy === 'approve' ? 'on' : '') + '"><b>' + icon('circle-check') + 'На одобрение</b><span>Сначала присылает список «кому и что», выполняет то, что вы одобрили.</span></button></div>' +
      '<p class="muted small">Клиентам агент пишет с ' + ov.work_hours[0] + ':00 до ' + ov.work_hours[1] + ':00, одному клиенту — не чаще раза в неделю. Клиентов на паузе, в «не звонить» и уже записанных не трогает.</p></div>';
    html += '<div class="mg-card"><h3 class="mg-h3 first">Сценарии</h3>' +
      sw('winback', 'Вернуть ушедших клиентов', 'Клиент не приходит дольше ' + sel('winback.days', [[45, '45 дней'], [60, '60 дней'], [90, '90 дней'], [120, '120 дней']], s.winback.days) +
        ' — агент тепло напишет ему и предложит время у его мастера. Не больше ' + sel('winback.per_day', [[3, '3'], [5, '5'], [8, '8'], [15, '15']], s.winback.per_day) + ' в день.', s.winback.on, lock) +
      sw('fill_gaps', 'Заполнить пустые окна', 'У мастера завтра свободно 3+ часа — агент предложит это время его клиентам, которые давно не были. До ' +
        sel('fill_gaps.per_day', [[3, '3'], [5, '5'], [10, '10']], s.fill_gaps.per_day) + ' в день.', s.fill_gaps.on, lock) +
      sw('repeat_review', 'Повтор услуги и отзыв', 'После визита — «спасибо» и просьба оценить от 1 до 5 (оценку 1–3 сразу пришлём вам). ' +
        (lock ? '' : 'Когда подходит срок повтора — напомнит и предложит время. ') + 'Срок задаётся у услуги: Записи → Услуги → «Напомнить о повторе через».', s.repeat_review.on, false) +
      (lock ? '<p class="mg-note">' + icon('lock') + 'Писать и звонить клиентам сам агент может на тарифе Beauty Pro. Отчёты и отзывы работают и на вашем тарифе.</p>' : '') +
      '<p class="muted small">Сообщение — если клиент писал вам в WhatsApp, Telegram или Instagram. Иначе звонок, если номер O! (звонки — минуты с кошелька).</p></div>';
    html += '<div class="mg-card"><h3 class="mg-h3 first">Отчёты</h3>' +
      sw('morning_report', 'Утренний отчёт', 'Каждый день в ' + sel('morning_report.hour', [[7, '7:00'], [8, '8:00'], [9, '9:00'], [10, '10:00'], [11, '11:00']], s.morning_report.hour) +
        ': записи на сегодня, свободное время, вчерашняя выручка и неявки.', s.morning_report.on, false) +
      sw('weekly_report', 'Итоги недели', 'По понедельникам: выручка к прошлой неделе, мастера, новые клиенты, кто давно не был.', s.weekly_report.on, false) +
      '<div class="mg-where">' + '<span class="' + (st.ov.push_devices ? 'on' : '') + '">' + icon('bell') + (st.ov.push_devices ? 'Уведомления на телефон: включены' : 'Уведомления на телефон: выключены') + '</span>' +
      '<span class="' + (st.ov.telegram ? 'on' : '') + '">' + icon('send') + (st.ov.telegram ? 'Telegram-бот: подключён' : 'Telegram-бот: не подключён') + '</span>' +
      '<span class="on">' + icon('sparkles') + 'В приложении: всегда</span></div>' +
      (st.ov.push_devices ? '<button type="button" class="btn btn-sm" data-push-test>' + icon('bell') + '<span>Прислать проверочное уведомление</span></button>'
        : '<button type="button" class="btn btn-sm btn-primary" data-push>' + icon('bell') + '<span>Включить уведомления на этом телефоне</span></button>') + '</div>';
    if (!lock) {
      html += '<div class="mg-card"><h3 class="mg-h3 first">Проверить сейчас</h3><p class="muted small">Обычно агент ищет клиентов в 10:00. Можно запустить поиск прямо сейчас — ' +
        (s.autonomy === 'auto' ? 'найденным он напишет в рабочее время.' : 'найденные появятся во вкладке «Одобрить».') + '</p>' +
        '<button type="button" class="btn btn-sm" data-plan>' + icon('search') + '<span>Найти клиентов сейчас</span></button></div>';
    }
    return html;
  }

  function saveSettings(patch, msg) {
    return S.api('/api/manager/settings', { method: 'PUT', body: patch }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); render(); return; }
      st.ov.settings = r.data.settings;
      S.toast(msg || 'Сохранено');
      render();
    });
  }

  // ── push ──
  function b64ToBytes(b64) {
    var pad = '='.repeat((4 - b64.length % 4) % 4), raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function enablePush(btn) {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) { S.toast('Этот браузер не умеет уведомления', 'error'); return; }
    if (btn) btn.disabled = true;
    Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') throw new Error('Разрешите уведомления для Voksy AI в настройках браузера');
      return navigator.serviceWorker.register('/static/sw.js', { scope: '/static/' }).then(function () { return navigator.serviceWorker.ready; });
    }).then(function (reg) {
      return S.api('/api/push/key').then(function (r) {
        if (!r.ok) throw new Error(S.apiError(r));
        return reg.pushManager.getSubscription().then(function (old) {
          return old || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(r.data.key) });
        });
      });
    }).then(function (sub) {
      return S.api('/api/push/subscribe', { method: 'POST', body: { subscription: sub.toJSON() } });
    }).then(function (r) {
      if (!r.ok) throw new Error(S.apiError(r));
      st.ov.push_devices = r.data.devices;
      S.toast('Готово: отчёт будет приходить на этот телефон');
      S.api('/api/push/test', { method: 'POST' });
      render();
    }).catch(function (e) {
      if (btn) btn.disabled = false;
      S.toast((e && e.message) || 'Не получилось включить уведомления', 'error');
    });
  }

  // ── события ──
  function decide(ids, approve) {
    return S.api('/api/manager/actions/decide', { method: 'POST', body: { ids: ids, approve: approve } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      st.ov.pending = r.data.pending;
      S.toast(approve ? 'Одобрено — агент выполнит в рабочее время' : 'Отклонено');
      return loadTab().then(render);
    });
  }
  function bind() {
    S.$$('[data-tab]', root).forEach(function (b) { b.addEventListener('click', function () { go(b.getAttribute('data-tab')); }); });
    S.$$('[data-go]', root).forEach(function (b) { b.addEventListener('click', function () { go(b.getAttribute('data-go')); }); });
    S.$$('[data-push]', root).forEach(function (b) { b.addEventListener('click', function () { enablePush(b); }); });
    var pt = S.$('[data-push-test]', root);
    if (pt) pt.addEventListener('click', function () {
      S.api('/api/push/test', { method: 'POST' }).then(function (r) { S.toast(r.ok && r.data.ok ? 'Отправили — проверьте телефон' : 'Не дошло: включите уведомления заново', r.ok && r.data.ok ? '' : 'error'); });
    });
    var pv = S.$('[data-preview]', root);
    if (pv) pv.addEventListener('click', function () {
      pv.disabled = true;
      S.api('/api/manager/reports/now', { method: 'POST' }).then(function (r) {
        if (!r.ok) { pv.disabled = false; S.toast(S.apiError(r), 'error'); return; }
        st.preview = r.data.report; render();
      });
    });
    var all = S.$('[data-ok-all]', root);
    if (all) all.addEventListener('click', function () { all.disabled = true; decide(null, true); });
    S.$$('[data-ok]', root).forEach(function (b) { b.addEventListener('click', function () { b.disabled = true; decide([b.getAttribute('data-ok')], true); }); });
    S.$$('[data-no]', root).forEach(function (b) { b.addEventListener('click', function () { b.disabled = true; decide([b.getAttribute('data-no')], false); }); });
    S.$$('[data-auto]', root).forEach(function (b) {
      b.addEventListener('click', function () {
        var v = b.getAttribute('data-auto');
        if (v === st.ov.settings.autonomy) return;
        saveSettings({ autonomy: v }, v === 'auto' ? 'Агент решает сам' : 'Теперь агент ждёт вашего одобрения');
      });
    });
    S.$$('[data-sw]', root).forEach(function (i) {
      i.addEventListener('change', function () { var p = {}; p[i.getAttribute('data-sw')] = { on: i.checked }; saveSettings(p, i.checked ? 'Включено' : 'Выключено'); });
    });
    S.$$('[data-sel]', root).forEach(function (sl) {
      sl.addEventListener('change', function () {
        var k = sl.getAttribute('data-sel').split('.'), p = {};
        p[k[0]] = {}; p[k[0]][k[1]] = +sl.value; saveSettings(p);
      });
    });
    var plan = S.$('[data-plan]', root);
    if (plan) plan.addEventListener('click', function () {
      plan.disabled = true;
      S.api('/api/manager/plan-now', { method: 'POST' }).then(function (r) {
        plan.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        if (!r.data.planned) { S.toast('Сейчас подходящих клиентов нет — включите сценарии или загляните завтра'); return; }
        S.toast('Найдено: ' + r.data.planned);
        loadOverview().then(function () { go(r.data.autonomy === 'approve' ? 'approve' : 'journal'); });
      });
    });
    var rep = st.tab === 'reports' && st.reports && st.reports[0];
    if (rep && !rep.read) {
      rep.read = true;
      S.api('/api/manager/reports/' + rep.id + '/read', { method: 'POST' }).then(function () { if (st.ov.unread_reports) st.ov.unread_reports -= 1; });
    }
  }

  function start() {
    root = document.getElementById('mg');
    top = document.getElementById('mg-top');
    esc = S.esc; icon = S.icon;
    var h = (location.hash || '').replace('#', '');
    if (TABS.some(function (t) { return t[0] === h; })) st.tab = h;
    loadOverview().then(function (ok) {
      if (!ok) return;
      render();
      loadTab().then(render);
    });
    window.addEventListener('hashchange', function () {
      var t = (location.hash || '').replace('#', '');
      if (t && t !== st.tab && TABS.some(function (x) { return x[0] === t; })) go(t);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
