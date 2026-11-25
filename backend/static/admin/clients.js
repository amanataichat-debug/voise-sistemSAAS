/**
 * Админка «Клиенты» (ветка 1811-niche-admin-speed).
 *
 * Сводка регистраций (кому позвонить, новые, без телефона, тарифы) → быстрый список клиентов
 * платформы с телефоном, нишей, тарифом и тем, что клиент настроил → карточка сбоку: контакты,
 * мини-CRM владельца (статус, заметки, дата следующего звонка), оплаты, лента, кнопки тарифа,
 * кошелька, кредитов, пароля и «Войти в кабинет».
 *
 * API: /api/admin/clients, /clients/summary, /clients/{id}, PATCH /clients/{id},
 *      POST /clients/{id}/impersonate, /api/admin/niches.
 * Окна тарифа, кошелька, кредитов и пароля — прежние из admin.html (window.open*Modal).
 * Вход в кабинет: localStorage.vf_admin_return (токен админа) + vf_impersonation (кого смотрим),
 * полосу «Вы в кабинете …» на всех страницах рисует js/ui.js.
 */
(function () {
  'use strict';

  var API = '/api';
  var PAGE = 50;
  var TZ = 'Asia/Bishkek';
  var STATUS = {
    new: { label: 'Новый', color: '#64748b', bg: '#f1f5f9' },
    contacted: { label: 'Позвонил', color: '#2a5ce8', bg: '#ebf0ff' },
    setup: { label: 'Настраиваю', color: '#b45309', bg: '#fdf3e2' },
    paying: { label: 'Платит', color: '#0f7a3a', bg: '#e7f7ee' },
    churned: { label: 'Ушёл', color: '#b91c1c', bg: '#fdecec' }
  };
  var SEGMENTS = [
    ['all', 'Все', null],
    ['call_today', 'Позвонить', 'call_today'],
    ['new_today', 'Новые сегодня', 'new_today'],
    ['new_week', 'За неделю', 'new_week'],
    ['no_phone', 'Без телефона', 'no_phone'],
    ['no_setup', 'Ничего не настроили', null],
    ['trial', 'Пробный период', 'trial'],
    ['paying', 'Платят', 'paying'],
    ['expiring', 'Заканчивается тариф', 'expiring'],
    ['expired', 'Истекли', null],
    ['admins', 'Админы', null]
  ];
  var SORTS = [['created_desc', 'Сначала новые'], ['login_desc', 'Недавно заходили'], ['next_contact', 'По дате звонка'],
    ['end_asc', 'Скоро кончится тариф'], ['name', 'По имени'], ['created_asc', 'Сначала старые']];
  var PLAN_LABEL = { start: 'Старт', profi: 'Profi', ai_voice: 'AI Voice', agent: 'Агент', free: 'Пробный', referral_trial: 'Пробный',
    beauty_start: 'Beauty Start', beauty_ai: 'Beauty AI', beauty_pro: 'Beauty Pro' };
  var AVA_COLORS = ['#2a5ce8', '#0d9488', '#7c3aed', '#db2777', '#ea580c', '#0891b2', '#4d7c0f', '#b45309'];

  var st = {
    q: '', segment: 'all', status: '', niche: '', plan: '', sort: 'created_desc',
    items: [], total: 0, loading: false, summary: null, niches: [], openId: null, detail: null, reqId: 0
  };
  var root;

  // ───────────────────────── утилиты ─────────────────────────
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function icon(name, cls) { return '<svg class="ic' + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="/static/icons/ui.svg#i-' + name + '"></use></svg>'; }
  var WA_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2.05 22l5.25-1.38c1.45.79 3.08 1.21 4.74 1.21 5.46 0 9.91-4.45 9.91-9.91C21.95 6.45 17.5 2 12.04 2zm5.8 14.04c-.24.68-1.42 1.32-1.96 1.37-.5.05-1.13.07-1.82-.11-.42-.13-.96-.31-1.65-.61-2.9-1.25-4.8-4.18-4.94-4.37-.14-.19-1.18-1.57-1.18-3s.75-2.13 1.02-2.42c.27-.29.58-.36.78-.36h.56c.18 0 .42-.07.66.5.24.58.82 2 .89 2.15.07.15.12.32.02.51-.1.19-.15.31-.29.48-.15.17-.31.37-.44.5-.15.15-.3.31-.13.6.17.29.76 1.25 1.63 2.03 1.12 1 2.06 1.31 2.35 1.46.29.15.46.12.63-.07.17-.19.73-.85.92-1.14.19-.29.39-.24.66-.15.27.1 1.7.8 1.99.95.29.15.49.22.56.34.07.12.07.7-.17 1.38z"/></svg>';
  function token() { try { return localStorage.getItem('auth_token'); } catch (e) { return null; } }
  function api(path, opts) {
    opts = opts || {};
    var headers = { 'Authorization': 'Bearer ' + token() };
    if (opts.body && typeof opts.body !== 'string') { headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(opts.body); }
    opts.headers = headers;
    return fetch(API + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d && (d.detail || d.message)) || ('Ошибка ' + r.status));
        return d;
      });
    });
  }
  function toast(msg) {
    var t = document.createElement('div'); t.className = 'ac-toast'; t.textContent = msg; document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2600);
  }
  function initials(c) {
    var n = (c.name || c.company_name || c.email || '?').trim();
    var p = n.split(/\s+/);
    return ((p[0] || '')[0] + ((p[1] || '')[0] || '')).toUpperCase();
  }
  function avaColor(id) { var h = 0; for (var i = 0; i < (id || '').length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0; return AVA_COLORS[h % AVA_COLORS.length]; }
  function digits(p) { return String(p || '').replace(/\D/g, ''); }
  function prettyPhone(p) {
    if (!p) return '';
    var d = digits(p);
    if (d.length === 12 && d.indexOf('996') === 0) return '+996 ' + d.slice(3, 6) + ' ' + d.slice(6, 9) + ' ' + d.slice(9);
    if (d.length === 11 && d[0] === '7') return '+7 ' + d.slice(1, 4) + ' ' + d.slice(4, 7) + '-' + d.slice(7, 9) + '-' + d.slice(9);
    return p;
  }
  function waLink(c) {
    var name = (c.first_name || c.name || '').trim();
    var text = 'Здравствуйте' + (name ? ', ' + name : '') + '! Это Voksy AI. Вы зарегистрировались у нас — помогу настроить кабинет под ваш бизнес. Удобно сейчас?';
    return 'https://wa.me/' + digits(c.phone) + '?text=' + encodeURIComponent(text);
  }
  function dt(iso) { return iso ? new Date(iso) : null; }
  function dayKey(d) { return d.toLocaleDateString('en-CA', { timeZone: TZ }); }
  function hm(d) { return d.toLocaleTimeString('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }); }
  function when(iso) {
    var d = dt(iso); if (!d) return '—';
    var now = new Date(), today = dayKey(now), yest = dayKey(new Date(now - 864e5));
    var k = dayKey(d);
    if (k === today) return 'сегодня ' + hm(d);
    if (k === yest) return 'вчера ' + hm(d);
    var days = Math.round((now - d) / 864e5);
    if (days > 0 && days < 7) return days + ' дн. назад';
    return d.toLocaleDateString('ru-RU', { timeZone: TZ, day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
  }
  function future(iso) {
    var d = dt(iso); if (!d) return '';
    var now = new Date();
    var k = dayKey(d), today = dayKey(now), tom = dayKey(new Date(+now + 864e5));
    if (d < now) return 'просрочено · ' + when(iso);
    if (k === today) return 'сегодня ' + hm(d);
    if (k === tom) return 'завтра ' + hm(d);
    return d.toLocaleDateString('ru-RU', { timeZone: TZ, day: 'numeric', month: 'short' }) + ' ' + hm(d);
  }
  function money(v) { return (Number(v) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' сом'; }
  function plural(n, a, b, c) { n = Math.abs(n) % 100; var n1 = n % 10; if (n > 10 && n < 20) return c; if (n1 > 1 && n1 < 5) return b; if (n1 === 1) return a; return c; }
  function planChip(p) {
    p = p || {};
    var label = p.kind === 'admin' ? 'Админ' : (PLAN_LABEL[p.code] || p.name || 'Без тарифа');
    var cls = p.kind === 'paid' ? 'paid' : p.kind === 'trial' ? 'trial' : p.kind === 'admin' ? 'admin' : p.kind === 'none' ? 'none' : 'expired';
    if (p.kind === 'expired' || p.kind === 'trial_over') label += ' · истёк';
    return '<span class="ac-plan ' + cls + '">' + esc(label) + '</span>';
  }
  function daysLine(p) {
    if (!p || !p.active || p.kind === 'admin') return '';
    var soon = p.days_left <= 3;
    return '<div class="ac-days' + (soon ? ' soon' : '') + '">' + (p.days_left === 0 ? 'последний день' : 'ещё ' + p.days_left + ' ' + plural(p.days_left, 'день', 'дня', 'дней')) + '</div>';
  }
  function statusSelect(c) {
    var s = STATUS[c.admin_status] || STATUS.new;
    return '<select class="ac-status" data-status="' + esc(c.id) + '" style="color:' + s.color + ';background-color:' + s.bg + '" aria-label="Статус клиента">' +
      Object.keys(STATUS).map(function (k) { return '<option value="' + k + '"' + (k === (c.admin_status || 'new') ? ' selected' : '') + '>' + STATUS[k].label + '</option>'; }).join('') +
      '</select>';
  }
  function setupDots(c) {
    var s = c.setup || {};
    var dots = [
      [s.agents || s.assistants, 'bot', s.agents ? 'Бот/агент: ' + (s.agent_name || 'создан') : (s.assistants ? 'Голосовой ассистент' : 'Бот не создан')],
      [s.whatsapp, 'wa', 'WhatsApp ' + (s.whatsapp ? 'подключён' : 'не подключён')],
      [s.telegram || s.tg_bot, 'send', 'Telegram ' + (s.telegram ? '(личный) подключён' : s.tg_bot ? '(бот) подключён' : 'не подключён')],
      [s.instagram, 'instagram', 'Instagram ' + (s.instagram ? 'подключён' : 'не подключён')],
      [(s.products || 0) + (s.knowledge || 0), 'package', 'Каталог: ' + (s.products || 0) + ', знания: ' + (s.knowledge || 0)],
      [s.numbers, 'phone', s.numbers ? 'Номер телефонии подключён' : 'Нет номера телефонии']
    ];
    return '<div class="ac-setup">' + dots.map(function (d) {
      var ic = d[1] === 'wa' ? WA_SVG : icon(d[1]);
      return '<span class="ac-dot' + (d[0] ? ' on' : '') + '" title="' + esc(d[2]) + '">' + ic + '</span>';
    }).join('') + '</div>';
  }

  // ───────────────────────── каркас ─────────────────────────
  function shell() {
    root.innerHTML =
      '<div class="ac-wrap">' +
      '<div class="ac-kpis" id="ac-kpis">' + new Array(8).join('<div class="ac-kpi"><div class="ac-sk" style="margin:0;height:46px"></div></div>') + '</div>' +
      '<div class="ac-toolbar">' +
      '<label class="ac-search">' + icon('search') + '<input id="ac-q" type="search" placeholder="Имя, компания, телефон, почта…" autocomplete="off"></label>' +
      '<select class="ac-select" id="ac-status"><option value="">Все статусы</option>' + Object.keys(STATUS).map(function (k) { return '<option value="' + k + '">' + STATUS[k].label + '</option>'; }).join('') + '</select>' +
      '<select class="ac-select" id="ac-niche"><option value="">Все ниши</option><option value="none">Ниша не выбрана</option></select>' +
      '<select class="ac-select" id="ac-plan"><option value="">Все тарифы</option><option value="start">Старт</option><option value="profi">Profi</option><option value="ai_voice">AI Voice</option><option value="free">Пробный</option><option value="agent">Агент (старый)</option></select>' +
      '<select class="ac-select" id="ac-sort">' + SORTS.map(function (s) { return '<option value="' + s[0] + '">' + s[1] + '</option>'; }).join('') + '</select>' +
      '<div class="ac-chips" id="ac-chips"></div>' +
      '</div>' +
      '<div class="ac-card"><div id="ac-body"></div><div class="ac-foot" id="ac-foot"></div></div>' +
      '</div>';
    drawChips();
    var q = root.querySelector('#ac-q'), tmr;
    q.addEventListener('input', function () { clearTimeout(tmr); tmr = setTimeout(function () { st.q = q.value.trim(); load(true); }, 220); });
    ['status', 'niche', 'plan', 'sort'].forEach(function (k) {
      root.querySelector('#ac-' + k).addEventListener('change', function (e) { st[k] = e.target.value; load(true); });
    });
    root.addEventListener('change', function (e) {
      var sel = e.target.closest && e.target.closest('[data-status]');
      if (sel) { e.stopPropagation(); setStatus(sel.getAttribute('data-status'), sel.value, sel); }
    });
    root.addEventListener('click', onListClick);
  }

  function drawChips() {
    var counts = (st.summary && st.summary.counts) || {};
    root.querySelector('#ac-chips').innerHTML = SEGMENTS.map(function (s) {
      var n = s[2] != null && counts[s[2]] != null ? '<b>' + counts[s[2]] + '</b>' : '';
      return '<button type="button" class="ac-chip' + (st.segment === s[0] ? ' on' : '') + '" data-seg="' + s[0] + '">' + esc(s[1]) + n + '</button>';
    }).join('');
  }

  function drawKpis() {
    var s = st.summary; if (!s) return;
    var c = s.counts || {};
    var days = s.signups_by_day || [];
    var max = Math.max.apply(null, days.map(function (d) { return d.count; }).concat([1]));
    var spark = '<div class="ac-spark">' + days.map(function (d, i) {
      return '<i class="' + (i === days.length - 1 ? 'today' : '') + '" style="height:' + Math.max(8, Math.round(d.count / max * 100)) + '%" title="' + esc(d.date + ': ' + d.count) + '"></i>';
    }).join('') + '</div>';
    var cards = [
      ['call_today', 'Позвонить', c.call_today, 'новые без звонка и назначенные', 'hot', 'phone-call'],
      ['new_today', 'Новые сегодня', c.new_today, 'за неделю: ' + (c.new_week || 0), '', 'user-check', spark],
      ['no_phone', 'Без телефона', c.no_phone, 'попросим в кабинете', c.no_phone ? 'warn' : '', 'phone-off'],
      ['no_setup', 'Ничего не настроили', c.week_no_setup, 'из новых за неделю', c.week_no_setup ? 'warn' : '', 'circle-alert'],
      ['trial', 'Пробный период', c.trial, 'сейчас на пробном', '', 'hourglass'],
      ['paying', 'Платят', c.paying, 'активный платный тариф', 'good', 'banknote'],
      ['expiring', 'Заканчивается', c.expiring, 'тариф в ближайшие 3 дня', c.expiring ? 'warn' : '', 'clock'],
      ['all', 'Всего клиентов', c.total, 'за месяц: +' + (c.new_month || 0), '', 'users']
    ];
    root.querySelector('#ac-kpis').innerHTML = cards.map(function (k) {
      return '<button type="button" class="ac-kpi ' + k[4] + (st.segment === k[0] ? ' on' : '') + '" data-seg="' + k[0] + '">' +
        '<div class="t">' + icon(k[5]) + esc(k[1]) + '</div><div class="v">' + (k[2] || 0) + '</div><div class="s">' + esc(k[3]) + '</div>' + (k[6] || '') + '</button>';
    }).join('');
  }

  function drawNicheOptions() {
    var sel = root.querySelector('#ac-niche'); if (!sel || !st.niches.length) return;
    var counts = {};
    ((st.summary && st.summary.by_niche) || []).forEach(function (n) { counts[n.niche] = n.count; });
    sel.innerHTML = '<option value="">Все ниши</option><option value="none">Ниша не выбрана</option>' + st.niches.map(function (n) {
      return '<option value="' + esc(n.key) + '">' + esc(n.icon + ' ' + n.title) + (counts[n.key] ? ' · ' + counts[n.key] : '') + '</option>';
    }).join('');
    sel.value = st.niche;
  }

  // ───────────────────────── список ─────────────────────────
  function query(offset) {
    var p = new URLSearchParams();
    if (st.q) p.set('q', st.q);
    if (st.segment && st.segment !== 'all') p.set('segment', st.segment);
    if (st.status) p.set('status', st.status);
    if (st.niche) p.set('niche', st.niche);
    if (st.plan) p.set('plan', st.plan);
    p.set('sort', st.sort);
    p.set('offset', offset || 0);
    p.set('limit', PAGE);
    return '/admin/clients?' + p.toString();
  }

  function load(reset) {
    var id = ++st.reqId;
    if (reset) {
      st.items = [];
      root.querySelector('#ac-body').innerHTML = '<div class="ac-sk"></div><div class="ac-sk"></div><div class="ac-sk"></div><div class="ac-sk"></div>';
    }
    st.loading = true;
    return api(query(reset ? 0 : st.items.length)).then(function (d) {
      if (id !== st.reqId) return;
      st.total = d.total || 0;
      st.items = reset ? (d.items || []) : st.items.concat(d.items || []);
      drawList();
    }).catch(function (e) {
      if (id !== st.reqId) return;
      root.querySelector('#ac-body').innerHTML = '<div class="ac-empty">' + esc(e.message) + '</div>';
    }).then(function () { st.loading = false; });
  }

  function contactCell(c) {
    var phone = c.phone
      ? '<div class="ac-phone-row"><a class="ac-phone" href="tel:' + esc(c.phone) + '" data-stop>' + esc(prettyPhone(c.phone)) + '</a>' +
        '<a class="ac-ib wa" href="' + esc(waLink(c)) + '" target="_blank" rel="noopener" title="Написать в WhatsApp" data-stop>' + WA_SVG + '</a></div>'
      : '<span class="ac-nophone">нет телефона</span>';
    return '<div class="ac-contact">' + phone + '<span class="ac-mail" title="' + esc(c.email) + '">' + esc(c.email) + (c.email_verified ? '' : ' · не подтв.') + '</span></div>';
  }

  function whoCell(c, withReg) {
    var line = [c.company_name, c.niche_title].filter(Boolean).join(' · ');
    return '<div class="ac-who"><span class="ac-ava" style="background:' + avaColor(c.id) + '">' + esc(initials(c)) + '</span><div>' +
      '<div class="n">' + esc(c.name || c.email.split('@')[0]) + '</div>' +
      '<div class="c">' + esc(line || 'компания и ниша не указаны') + '</div>' +
      (withReg ? '<div class="ac-reg">рег. ' + esc(when(c.created_at)) + ' · вход: ' + esc(c.last_login ? when(c.last_login) : 'не входил') + '</div>' : '') +
      '</div></div>';
  }

  function drawList() {
    var body = root.querySelector('#ac-body');
    if (!st.items.length) {
      body.innerHTML = '<div class="ac-empty">Никого не нашли. Попробуйте другой фильтр или поиск.</div>';
    } else {
      var rows = st.items.map(function (c) {
        var due = c.next_contact_at ? '<span class="ac-due">📞 ' + esc(future(c.next_contact_at)) + '</span>' : '';
        return '<tr data-id="' + esc(c.id) + '"' + (c.id === st.openId ? ' class="sel"' : '') + '>' +
          '<td>' + whoCell(c, true) + '</td>' +
          '<td>' + contactCell(c) + '</td>' +
          '<td>' + planChip(c.plan) + daysLine(c.plan) + '</td>' +
          '<td>' + setupDots(c) + '</td>' +
          '<td>' + statusSelect(c) + due + '</td>' +
          '<td>' + (c.is_admin ? '' : '<button class="ac-go" type="button" data-imp="' + esc(c.id) + '" title="Войти в кабинет клиента" aria-label="Войти в кабинет клиента">' + icon('arrow-right') + '<span class="lbl">Войти</span></button>') + '</td>' +
          '</tr>';
      }).join('');
      var cards = st.items.map(function (c) {
        var due = c.next_contact_at ? '<span class="ac-due">📞 ' + esc(future(c.next_contact_at)) + '</span>' : '';
        return '<div class="ac-item" data-id="' + esc(c.id) + '">' +
          '<div class="row">' + whoCell(c) + '<div style="text-align:right">' + planChip(c.plan) + daysLine(c.plan) + '</div></div>' +
          '<div class="row">' + contactCell(c) + setupDots(c) + '</div>' +
          '<div class="row"><div>' + statusSelect(c) + due + '</div><div class="acts">' +
          (c.is_admin ? '' : '<button class="ac-go" type="button" data-imp="' + esc(c.id) + '">' + icon('arrow-right') + 'Войти</button>') + '</div></div>' +
          '<div class="ac-when">Регистрация: ' + esc(when(c.created_at)) + ' · вход: ' + esc(c.last_login ? when(c.last_login) : 'не входил') + '</div>' +
          '</div>';
      }).join('');
      body.innerHTML = '<table class="ac-table"><thead><tr><th>Клиент</th><th>Контакты</th><th>Тариф</th><th>Что настроил</th><th>Статус</th>' +
        '<th></th></tr></thead><tbody>' + rows + '</tbody></table><div class="ac-list">' + cards + '</div>';
    }
    var foot = root.querySelector('#ac-foot');
    var more = st.items.length < st.total;
    foot.innerHTML = '<span>Показано ' + st.items.length + ' из ' + st.total + '</span>' +
      (more ? '<button class="ac-go ghost" type="button" data-more>Показать ещё</button>' : '');
  }

  function onListClick(e) {
    var t = e.target;
    var seg = t.closest('[data-seg]');
    if (seg) { st.segment = seg.getAttribute('data-seg'); drawChips(); drawKpis(); load(true); return; }
    if (t.closest('[data-more]')) { load(false); return; }
    var imp = t.closest('[data-imp]');
    if (imp) { e.stopPropagation(); impersonate(imp.getAttribute('data-imp'), imp); return; }
    if (t.closest('[data-stop]') || t.closest('select')) return;
    var row = t.closest('[data-id]');
    if (row) openDrawer(row.getAttribute('data-id'));
  }

  function patchItem(id, client) {
    st.items = st.items.map(function (x) { return x.id === id ? Object.assign({}, x, client) : x; });
  }

  function setStatus(id, value, el) {
    return api('/admin/clients/' + encodeURIComponent(id), { method: 'PATCH', body: { admin_status: value } }).then(function (d) {
      patchItem(id, d.client);
      if (el) { var s = STATUS[value] || STATUS.new; el.style.color = s.color; el.style.backgroundColor = s.bg; }
      if (st.openId === id) { st.detail = d.client; drawDrawer(); }
      toast('Статус: ' + (STATUS[value] || STATUS.new).label);
      refreshSummary();
    }).catch(function (e) { toast(e.message); });
  }

  // ───────────────────────── вход в кабинет ─────────────────────────
  function impersonate(id, btn) {
    var c = st.items.filter(function (x) { return x.id === id; })[0] || st.detail || { id: id };
    if (btn) btn.disabled = true;
    return api('/admin/clients/' + encodeURIComponent(id) + '/impersonate', { method: 'POST' }).then(function (d) {
      var adminToken = token();
      try {
        localStorage.setItem('vf_admin_return', JSON.stringify({ token: adminToken, client_id: id, at: Date.now() }));
        localStorage.setItem('vf_impersonation', JSON.stringify({
          client_id: id, name: (d.user && d.user.name) || c.name || '', email: (d.user && d.user.email) || c.email || '',
          company: (d.user && d.user.company_name) || c.company_name || '', until: Date.now() + (d.expires_in || 7200) * 1000
        }));
        if (window.VF && window.VF.resetUserCaches) window.VF.resetUserCaches();
        localStorage.setItem('auth_token', d.token);
      } catch (e) { /* ignore */ }
      location.href = '/static/dashboard.html';
    }).catch(function (e) { if (btn) btn.disabled = false; toast(e.message); });
  }

  // ───────────────────────── карточка клиента ─────────────────────────
  var drawer, scrim;
  function ensureDrawer() {
    if (drawer) return;
    scrim = document.createElement('div'); scrim.className = 'ac-scrim';
    drawer = document.createElement('aside'); drawer.className = 'ac-drawer'; drawer.setAttribute('aria-label', 'Карточка клиента');
    document.body.appendChild(scrim); document.body.appendChild(drawer);
    scrim.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && st.openId) closeDrawer(); });
    drawer.addEventListener('click', onDrawerClick);
    drawer.addEventListener('change', function (e) {
      var sel = e.target.closest('[data-status]');
      if (sel) setStatus(sel.getAttribute('data-status'), sel.value, sel);
    });
  }

  function openDrawer(id) {
    ensureDrawer();
    st.openId = id;
    var base = st.items.filter(function (x) { return x.id === id; })[0];
    st.detail = base || null;
    drawDrawer(true);
    drawer.classList.add('open'); scrim.classList.add('open');
    root.querySelectorAll('tr[data-id]').forEach(function (r) { r.classList.toggle('sel', r.getAttribute('data-id') === id); });
    try { history.replaceState(null, '', '#client=' + id); } catch (e) { /* ignore */ }
    return api('/admin/clients/' + encodeURIComponent(id)).then(function (d) {
      if (st.openId !== id) return;
      st.detail = d; drawDrawer();
    }).catch(function (e) { toast(e.message); });
  }

  function closeDrawer() {
    st.openId = null;
    if (drawer) { drawer.classList.remove('open'); scrim.classList.remove('open'); }
    root.querySelectorAll('tr.sel').forEach(function (r) { r.classList.remove('sel'); });
    try { history.replaceState(null, '', location.pathname); } catch (e) { /* ignore */ }
  }

  function check(on, text) {
    return '<div class="' + (on ? 'on' : '') + '"><span class="mk">' + (on ? icon('check') : '') + '</span>' + esc(text) + '</div>';
  }

  var TL_ICON = { impersonate: 'eye', note: 'sticky-note', status: 'tag', next_contact: 'calendar', profile: 'pen', subscription: 'receipt' };
  function tlText(t) {
    var d = t.details || {};
    if (t.action === 'impersonate') return 'Вход в кабинет клиента' + (t.admin ? ' — ' + t.admin : '');
    if (t.action === 'note') return t.text || '';
    if (t.action === 'status') return 'Статус: ' + ((STATUS[d.from] || {}).label || d.from || '—') + ' → ' + ((STATUS[d.to] || {}).label || d.to);
    if (t.action === 'next_contact') return d.at ? 'Следующий звонок: ' + future(d.at) : 'Дата звонка снята';
    if (t.action === 'profile') {
      var names = { phone: 'телефон', first_name: 'имя', company_name: 'компания', business_niche: 'ниша' };
      return 'Профиль: ' + Object.keys(d).map(function (k) { var v = d[k] || []; return (names[k] || k) + ' «' + (v[1] || '—') + '»'; }).join(', ');
    }
    if (t.action === 'subscription') return 'Подписка: ' + (t.text || '') + (d.plan ? ' (' + (PLAN_LABEL[d.plan] || d.plan) + ')' : '');
    return t.text || t.action;
  }

  function drawDrawer(loading) {
    var c = st.detail;
    if (!c) { drawer.innerHTML = '<div class="ac-db"><div class="ac-sk"></div><div class="ac-sk"></div><div class="ac-sk"></div></div>'; return; }
    var s = c.setup || {};
    var m = c.messages_30d || {};
    var msgIn = 0, msgOut = 0;
    Object.keys(m).forEach(function (k) { msgIn += m[k].inbound || 0; msgOut += m[k].outbound || 0; });
    var plan = c.plan || {};
    var niches = st.niches.map(function (n) { return '<option value="' + esc(n.key) + '"' + (n.key === c.niche ? ' selected' : '') + '>' + esc(n.icon + ' ' + n.title) + '</option>'; }).join('');
    var tl = (c.timeline || []).map(function (t) {
      return '<li><span class="ti">' + icon(TL_ICON[t.action] || 'info') + '</span><div class="tb"><div class="tt">' + esc(tlText(t)) + '</div>' +
        '<div class="tm">' + esc(when(t.created_at)) + (t.action === 'note' && t.admin ? ' · ' + esc(t.admin) : '') + '</div></div></li>';
    }).join('') || '<li style="color:var(--vf-text-4)">Пока пусто — добавьте первую заметку после звонка.</li>';
    var pays = (c.payments || []).map(function (p) {
      return '<div class="ac-pay"><span>' + esc(when(p.paid_at || p.created_at)) + ' · ' + esc(p.kind === 'wallet' ? 'кошелёк' : (PLAN_LABEL[p.plan] || p.plan_name || 'оплата')) + '</span>' +
        '<span><b>' + esc(money(p.amount)) + '</b> <span class="st ' + esc(p.status) + '">' + esc({ success: 'оплачено', pending: 'ожидает', failed: 'ошибка', cancelled: 'отменён' }[p.status] || p.status) + '</span></span></div>';
    }).join('') || '<div style="color:var(--vf-text-4);font-size:13px">Оплат не было</div>';

    drawer.innerHTML =
      '<div class="ac-dh"><div class="top"><span class="ac-ava" style="background:' + avaColor(c.id) + '">' + esc(initials(c)) + '</span>' +
      '<div style="min-width:0"><h3>' + esc(c.name || c.email) + '</h3><div class="sub">' + esc([c.company_name, c.niche_title].filter(Boolean).join(' · ') || 'Компания и ниша не указаны') + '</div></div>' +
      '<button class="ac-ib x" type="button" data-close aria-label="Закрыть">' + icon('x') + '</button></div>' +
      '<div class="btns">' +
      (c.is_admin ? '' : '<button class="ac-go" type="button" data-imp="' + esc(c.id) + '">' + icon('arrow-right') + 'Войти в кабинет</button>') +
      (c.phone ? '<a class="ac-go ghost" href="tel:' + esc(c.phone) + '">' + icon('phone') + 'Позвонить</a>' +
        '<a class="ac-go ghost" href="' + esc(waLink(c)) + '" target="_blank" rel="noopener" style="color:#16a34a">' + WA_SVG.replace('<svg ', '<svg width="15" height="15" ') + 'WhatsApp</a>' : '') +
      statusSelect(c) +
      '</div></div>' +
      '<div class="ac-db">' +

      '<section class="ac-sec"><h4>Контакты</h4><dl class="ac-kv">' +
      '<dt>Телефон</dt><dd>' + (c.phone ? esc(prettyPhone(c.phone)) : '<span class="ac-nophone">не указан</span>') + '</dd>' +
      '<dt>Почта</dt><dd>' + esc(c.email) + (c.email_verified ? '' : ' <span style="color:var(--vf-warning)">· не подтверждена</span>') + '</dd>' +
      '<dt>Регистрация</dt><dd>' + esc(when(c.created_at)) + '</dd>' +
      '<dt>Последний вход</dt><dd>' + esc(c.last_login ? when(c.last_login) : 'не входил') + '</dd>' +
      '</dl>' +
      '<details style="margin-top:10px"><summary style="cursor:pointer;font-size:13px;color:var(--vf-accent)">Изменить телефон, имя, компанию, нишу</summary>' +
      '<div class="ac-edit" style="margin-top:10px">' +
      '<label>Телефон<input id="ac-e-phone" type="tel" value="' + esc(c.phone || '') + '" placeholder="+996 700 123 456"></label>' +
      '<label>Имя<input id="ac-e-name" value="' + esc(c.first_name || '') + '"></label>' +
      '<label>Компания<input id="ac-e-company" value="' + esc(c.company_name || '') + '"></label>' +
      '<label>Ниша<select id="ac-e-niche"><option value="">Не выбрана</option>' + niches + '</select></label>' +
      '</div><div style="margin-top:8px"><button class="ac-go" type="button" data-save-profile>' + icon('save') + 'Сохранить</button></div></details>' +
      '</section>' +

      '<section class="ac-sec"><h4>Заметки и следующий звонок</h4><div class="ac-note-form">' +
      '<textarea id="ac-note" placeholder="Что обсудили, что нужно клиенту, о чём договорились…"></textarea>' +
      '<div class="ac-quick">' + ['Позвонил — не взял трубку', 'Позвонил, всё объяснил', 'Просит перезвонить', 'Настроили вместе', 'Не интересно'].map(function (q) { return '<button type="button" data-qnote="' + esc(q) + '">' + esc(q) + '</button>'; }).join('') + '</div>' +
      '<div class="line"><span style="font-size:13px;color:var(--vf-text-3)">Следующий звонок:</span>' +
      '<input type="datetime-local" id="ac-next" value="' + esc(localInput(c.next_contact_at)) + '">' +
      '<div class="ac-quick"><button type="button" data-next="2h">через 2 ч</button><button type="button" data-next="tomorrow">завтра 10:00</button><button type="button" data-next="3d">через 3 дня</button><button type="button" data-next="clear">убрать</button></div></div>' +
      '<div><button class="ac-go" type="button" data-save-note>' + icon('save') + 'Сохранить</button></div>' +
      '</div></section>' +

      '<section class="ac-sec"><h4>Тариф и деньги</h4><dl class="ac-kv">' +
      '<dt>Тариф</dt><dd>' + planChip(plan) + ' ' + (plan.end ? '<span style="font-size:12.5px;color:var(--vf-text-3)">до ' + esc(new Date(plan.end).toLocaleDateString('ru-RU', { timeZone: TZ })) + '</span>' : '') + daysLine(plan) + '</dd>' +
      '<dt>Кошелёк</dt><dd><span data-wallet-user="' + esc(c.id) + '">' + esc(money(c.wallet)) + '</span></dd>' +
      '<dt>Кредиты бота</dt><dd>' + esc((c.credits || 0).toLocaleString('ru-RU')) + '</dd>' +
      '<dt>Оплачено всего</dt><dd>' + esc(money(s.paid_total || 0)) + '</dd>' +
      '</dl><div class="ac-row-btns" style="margin-top:10px">' +
      '<button class="ac-go ghost" type="button" data-act="sub">' + icon('crown') + 'Тариф</button>' +
      '<button class="ac-go ghost" type="button" data-act="wallet">' + icon('wallet') + 'Кошелёк</button>' +
      '<button class="ac-go ghost" type="button" data-act="credits">' + icon('coins') + 'Кредиты</button>' +
      '<button class="ac-go ghost" type="button" data-act="password">' + icon('key') + 'Пароль</button>' +
      '</div><div style="margin-top:10px">' + pays + '</div></section>' +

      '<section class="ac-sec"><h4>Что настроил клиент <span style="text-transform:none;letter-spacing:0;font-weight:600">' + (c.setup_score ? c.setup_score.done + ' из ' + c.setup_score.total : '') + '</span></h4>' +
      '<div class="ac-check">' +
      check(s.agents, s.agents ? 'Бот: ' + (s.agent_name || 'создан') : 'Бот не создан') +
      check(s.assistants, s.assistants ? 'Голосовых ассистентов: ' + s.assistants : 'Нет голосового ассистента') +
      check(s.whatsapp, 'WhatsApp') +
      check(s.telegram, 'Telegram (личный)') +
      check(s.tg_bot, 'Telegram-бот') +
      check(s.instagram, 'Instagram') +
      check(s.numbers, s.numbers ? 'Номер телефонии' : 'Нет номера телефонии') +
      check(s.products, 'Каталог: ' + (s.products || 0) + ' поз.') +
      check(s.knowledge, 'База знаний: ' + (s.knowledge || 0)) +
      check(s.contacts, 'Клиентов в CRM: ' + (s.contacts || 0)) +
      '</div>' +
      '<div class="ac-mini"><div><b>' + (s.calls_30d || 0) + '</b><span>звонков за 30 дней</span></div><div><b>' + msgIn + '</b><span>сообщений от клиентов</span></div><div><b>' + msgOut + '</b><span>ответов бота/владельца</span></div></div>' +
      ((c.agents || []).length ? '<div style="margin-top:10px;font-size:13px;color:var(--vf-text-3)">Агенты: ' + c.agents.map(function (a) { return esc(a.name) + (a.active ? '' : ' (выкл.)'); }).join(', ') + '</div>' : '') +
      '<div style="margin-top:10px"><button class="ac-go ghost" type="button" data-act="usage">' + icon('chart-column') + 'Подробная аналитика агента</button></div>' +
      '</section>' +

      '<section class="ac-sec"><h4>История</h4><ul class="ac-tl">' + tl + '</ul></section>' +
      '</div>';
    if (loading) drawer.querySelector('.ac-db').insertAdjacentHTML('afterbegin', '<div class="ac-sk" style="margin:0;height:6px"></div>');
  }

  function localInput(iso) {
    var d = dt(iso); if (!d) return '';
    var p = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(d);
    var o = {}; p.forEach(function (x) { o[x.type] = x.value; });
    return o.year + '-' + o.month + '-' + o.day + 'T' + (o.hour === '24' ? '00' : o.hour) + ':' + o.minute;
  }
  function bishkekIso(local) { return local ? local + ':00+06:00' : null; }
  function presetNext(kind) {
    var now = new Date();
    if (kind === '2h') return localInput(new Date(+now + 2 * 3600e3).toISOString());
    if (kind === '3d') return localInput(new Date(+now + 3 * 864e5).toISOString()).slice(0, 11) + '10:00';
    if (kind === 'tomorrow') return localInput(new Date(+now + 864e5).toISOString()).slice(0, 11) + '10:00';
    return '';
  }

  function onDrawerClick(e) {
    var t = e.target, c = st.detail; if (!c) return;
    if (t.closest('[data-close]')) { closeDrawer(); return; }
    var imp = t.closest('[data-imp]'); if (imp) { impersonate(c.id, imp); return; }
    var q = t.closest('[data-qnote]');
    if (q) { var ta = drawer.querySelector('#ac-note'); ta.value = (ta.value ? ta.value + '\n' : '') + q.getAttribute('data-qnote'); ta.focus(); return; }
    var nx = t.closest('[data-next]');
    if (nx) { drawer.querySelector('#ac-next').value = presetNext(nx.getAttribute('data-next')); return; }
    if (t.closest('[data-save-note]')) {
      var note = drawer.querySelector('#ac-note').value.trim();
      var nextVal = drawer.querySelector('#ac-next').value;
      var body = {};
      if (note) body.note = note;
      if (nextVal !== localInput(c.next_contact_at)) body.next_contact_at = bishkekIso(nextVal);
      if (!Object.keys(body).length) { toast('Нечего сохранять'); return; }
      return save(c.id, body, 'Сохранено');
    }
    if (t.closest('[data-save-profile]')) {
      return save(c.id, {
        phone: drawer.querySelector('#ac-e-phone').value.trim(),
        first_name: drawer.querySelector('#ac-e-name').value.trim(),
        company_name: drawer.querySelector('#ac-e-company').value.trim(),
        niche: drawer.querySelector('#ac-e-niche').value
      }, 'Профиль обновлён');
    }
    var act = t.closest('[data-act]');
    if (act) {
      var a = act.getAttribute('data-act');
      if (a === 'sub' && window.openSubscriptionModal) window.openSubscriptionModal(c.id);
      else if (a === 'wallet' && window.openWalletModal) window.openWalletModal(c.id, c.email);
      else if (a === 'credits' && window.openCreditsModal) window.openCreditsModal(c.id, c.email, c.credits);
      else if (a === 'password' && window.openPasswordModal) window.openPasswordModal(c.id, c.email);
      else if (a === 'usage' && window.viewUserDetails) { closeDrawer(); window.viewUserDetails(c.id); }
    }
  }

  function save(id, body, okText) {
    return api('/admin/clients/' + encodeURIComponent(id), { method: 'PATCH', body: body }).then(function (d) {
      st.detail = d.client; patchItem(id, d.client); drawDrawer(); drawList(); toast(okText); refreshSummary();
    }).catch(function (e) { toast(e.message); });
  }

  // ───────────────────────── старт ─────────────────────────
  function refreshSummary() {
    return api('/admin/clients/summary').then(function (s) { st.summary = s; drawKpis(); drawChips(); drawNicheOptions(); }).catch(function () { /* ignore */ });
  }

  function refresh() {
    refreshSummary();
    load(true);
    if (st.openId) openDrawer(st.openId);
  }

  function init() {
    root = document.getElementById('clients-app');
    if (!root) return;
    shell();
    api('/admin/niches').then(function (d) { st.niches = d.niches || []; drawNicheOptions(); }).catch(function () { /* ignore */ });
    refreshSummary();
    load(true).then(function () {
      var m = /client=([0-9a-f-]{36})/.exec(location.hash || '');
      if (m) openDrawer(m[1]);
    });
    document.addEventListener('vf:admin-changed', function () { refresh(); });
  }

  window.VoksyAdminClients = { refresh: refresh, open: openDrawer, impersonate: impersonate };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
