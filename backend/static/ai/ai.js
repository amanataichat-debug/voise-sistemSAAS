/* ============================================================================
 * ai/ai.js — экран «AI» на телефоне (28.11.2026, решение владельца): чат владельца со своим агентом.
 * Пишете или говорите — агент отвечает отчётом и сам выполняет команды (записать клиента, закрыть время
 * мастеру, записать расход, написать клиентам, позвать клиентов в пустые окна…). Это тот же оркестратор,
 * что в чате страницы агента (/api/agent/chat/stream, история — agent_configs.chat_history), и тот же
 * голосовой помощник (VoksyVoice, /ws/agent-voice). Права — как у чата владельца: на Старте агент не
 * звонит сам и не ставит задачи (agent_mode), остальное по тарифу.
 * Вкладка «AI» нижней панели — business_profile.tabs_for. Стили — ai/ai.css.
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (v) { return S.esc(v); };
  var ky = function () { try { return localStorage.getItem('vs_lang') === 'ky'; } catch (e) { return false; } };
  var feed, input, sendBtn, busy = false, started = false;

  function biz() { try { return JSON.parse(localStorage.getItem('vf_business') || 'null') || {}; } catch (e) { return {}; } }
  function full() { return S.mode === 'full'; }

  // ── Быстрые команды под сферу бизнеса ────────────────────────────────────
  // send — отправить сразу; fill — вставить в поле, владелец допишет (имя, сумму, время).
  // full — только когда агент сам пишет/звонит клиентам (Profi, Beauty Pro).
  function commandGroups() {
    var b = biz(), mods = b.modules || {};
    if (mods.booking) {
      return [
        { title: 'Отчёты', items: [
          { ic: 'sun', t: 'Итоги дня', send: 'Подведи итоги дня: кто пришёл, кто не пришёл, сколько заработали, что в кассе' },
          { ic: 'calendar', t: 'Записи сегодня', send: 'Кто записан сегодня? По мастерам, и кто ещё не подтвердил' },
          { ic: 'trending-up', t: 'Выручка за неделю', send: 'Какая выручка за неделю, сколько визитов и средний чек? Сравни с прошлой неделей' },
          { ic: 'hand-coins', t: 'Кто должен', send: 'Кто из клиентов должен салону и сколько?' },
          { ic: 'users', t: 'Мастера за месяц', send: 'Сравни мастеров за месяц: записи, загрузка, выручка' },
          { ic: 'clock', t: 'Свободные окна', send: 'Какие свободные окна есть сегодня и завтра по мастерам?' }
        ] },
        { title: 'Команды', items: [
          { ic: 'calendar-plus', t: 'Записать клиента', fill: 'Запиши клиента ' },
          { ic: 'refresh-cw', t: 'Перенести запись', fill: 'Перенеси запись клиента ' },
          { ic: 'user-x', t: 'Мастер заболел', fill: 'Мастер  заболел сегодня — закрой ему время и скажи, кого нужно перенести', caret: 7 },
          { ic: 'ban', t: 'Закрыть время', fill: 'Закрой время у мастера  завтра с 14:00 до 16:00', caret: 23 },
          { ic: 'banknote', t: 'Записать расход', fill: 'Запиши расход  сом на ', caret: 14 },
          { ic: 'bell', t: 'Напомнить о записи', send: 'Кто записан на завтра и ещё не подтвердил? Напиши им напоминание в мессенджер' },
          { ic: 'sparkles', t: 'Заполнить пустые окна', send: 'У кого из мастеров сегодня и завтра пустые окна? Позови давних клиентов в эти окна', full: true }
        ] },
        { title: 'Клиенты', items: [
          { ic: 'history', t: 'Кто давно не был', send: 'Кто из клиентов давно не приходил и кому пора на повтор?' },
          { ic: 'circle-x', t: 'Кто не пришёл', send: 'Кто не пришёл за эту неделю?' },
          { ic: 'search', t: 'Найти клиента', fill: 'Что знаешь о клиенте ' },
          { ic: 'send', t: 'Написать клиенту', fill: 'Напиши клиенту : ', caret: 15 }
        ] }
      ];
    }
    if (mods.orders) {
      return [
        { title: 'Отчёты', items: [
          { ic: 'sun', t: 'Итоги дня', send: 'Подведи итоги дня: заказы, выручка, кто писал' },
          { ic: 'shopping-cart', t: 'Новые заказы', send: 'Какие заказы новые и в работе?' },
          { ic: 'package', t: 'Что заканчивается', send: 'Что заканчивается или закончилось на складе?' },
          { ic: 'trending-up', t: 'Выручка за неделю', send: 'Какая выручка по заказам за неделю? Сравни с прошлой' }
        ] },
        { title: 'Команды', items: [
          { ic: 'plus', t: 'Оформить заказ', fill: 'Оформи заказ для клиента ' },
          { ic: 'package-check', t: 'Обновить остаток', fill: 'Остаток: ' },
          { ic: 'send', t: 'Написать клиенту', fill: 'Напиши клиенту : ', caret: 15 },
          { ic: 'phone-call', t: 'Позвонить клиенту', fill: 'Позвони клиенту ', full: true }
        ] },
        { title: 'Клиенты', items: [
          { ic: 'inbox', t: 'Кто ждёт ответа', send: 'Кто из клиентов ждёт ответа?' },
          { ic: 'search', t: 'Найти клиента', fill: 'Что знаешь о клиенте ' }
        ] }
      ];
    }
    return [
      { title: 'Отчёты', items: [
        { ic: 'sun', t: 'Итоги дня', send: 'Подведи итоги дня: кто писал и звонил, что сделано, что ждёт' },
        { ic: 'trending-up', t: 'Отчёт за неделю', send: 'Сделай отчёт за неделю и сравни с прошлой' },
        { ic: 'layers', t: 'Воронка', send: 'Покажи воронку: сколько клиентов на каждом этапе' },
        { ic: 'calendar', t: 'Планы на сегодня', send: 'Что запланировано на сегодня?' }
      ] },
      { title: 'Команды', items: [
        { ic: 'send', t: 'Написать клиенту', fill: 'Напиши клиенту : ', caret: 15 },
        { ic: 'phone-call', t: 'Позвонить клиенту', fill: 'Позвони клиенту ', full: true },
        { ic: 'calendar-plus', t: 'Напомнить мне', fill: 'Напомни мне завтра в 10:00 ', full: true }
      ] },
      { title: 'Клиенты', items: [
        { ic: 'inbox', t: 'Кто ждёт ответа', send: 'Кто из клиентов ждёт ответа?' },
        { ic: 'search', t: 'Найти клиента', fill: 'Что знаешь о клиенте ' }
      ] }
    ];
  }
  function groupsNow() {
    return commandGroups().map(function (g) {
      return { title: g.title, items: g.items.filter(function (c) { return !c.full || full(); }) };
    }).filter(function (g) { return g.items.length; });
  }
  var cmdIndex = [];
  function chipHtml(c) {
    cmdIndex.push(c);
    return '<button type="button" class="ai-chip" data-cmd="' + (cmdIndex.length - 1) + '">' + S.icon(c.ic) +
      '<span>' + esc(c.t) + '</span></button>';
  }

  // ── Разметка ────────────────────────────────────────────────────────────
  function welcomeHtml() {
    var name = (S.agent && S.agent.name && S.agent.name !== 'Мой агент') ? S.agent.name : '';
    var b = biz();
    var salon = (b.modules || {}).booking;
    var what = salon
      ? 'Спросите отчёт или дайте команду — я сам сделаю: запишу клиента, закрою время мастеру, запишу расход, напишу клиентам.'
      : 'Спросите отчёт или дайте команду — я сам сделаю: найду клиента, напишу ему, покажу цифры за любой период.';
    return '<section class="ai-hero">' +
      '<div class="ai-hero-top"><div class="ai-orb" aria-hidden="true"><i></i><i></i></div>' +
      '<div><div class="ai-hero-k">' + (salon ? 'ИИ-управляющий' : 'AI-помощник') + (name ? ' · ' + esc(name) : '') + '</div>' +
      '<h2>Чем помочь?</h2></div></div>' +
      '<p>' + esc(what) + '</p>' +
      '<button type="button" class="ai-hero-talk" data-talk="1"><span class="ai-wave" aria-hidden="true"><i></i><i></i><i></i><i></i></span>' +
      '<span>Поговорить голосом</span></button>' +
      '</section>' +
      groupsNow().map(function (g) {
        return '<section class="ai-group"><h3>' + esc(g.title) + '</h3><div class="ai-chips">' + g.items.map(chipHtml).join('') + '</div></section>';
      }).join('');
  }
  function quickStripHtml() {
    var all = [];
    groupsNow().forEach(function (g) { all = all.concat(g.items); });
    return all.slice(0, 10).map(chipHtml).join('');
  }

  function md(text) {
    text = String(text == null ? '' : text);
    if (window.marked && window.DOMPurify) {
      try {
        var html = window.DOMPurify.sanitize(window.marked.parse(text, { breaks: true, gfm: true }));
        var box = document.createElement('div'); box.innerHTML = html;
        Array.prototype.forEach.call(box.querySelectorAll('table'), function (t) {
          var w = document.createElement('div'); w.className = 'ai-table'; t.parentNode.insertBefore(w, t); w.appendChild(t);
        });
        Array.prototype.forEach.call(box.querySelectorAll('a[href]'), function (a) { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
        return box.innerHTML;
      } catch (e) { /* ниже — простой текст */ }
    }
    return esc(text).replace(/\n/g, '<br>');
  }
  function timeOf(iso) {
    try {
      var d = iso ? new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z') : new Date();
      return d.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
    } catch (e) { return ''; }
  }
  function msgHtml(m) {
    var voice = m.voice || /^🎙/.test(m.text || '');
    var text = String(m.text || '').replace(/^🎙\s*\(голосом\)\s*/, '');
    var stamp = '<div class="ai-time">' + (voice ? '🎙 голосом · ' : '') + timeOf(m.ts) + '</div>';
    if (m.role === 'user') {
      return '<div class="ai-msg me"><div class="ai-bub">' + esc(text).replace(/\n/g, '<br>') + '</div>' + stamp + '</div>';
    }
    return '<div class="ai-msg bot"><div class="ai-ava" aria-hidden="true"><i></i></div><div class="ai-body">' +
      '<div class="ai-bub md">' + md(text) + '</div>' + stamp + '</div></div>';
  }
  function scrollDown(smooth) {
    var y = document.documentElement.scrollHeight;
    try { window.scrollTo({ top: y, behavior: smooth ? 'smooth' : 'auto' }); } catch (e) { window.scrollTo(0, y); }
  }

  function drawFeed(messages) {
    cmdIndex = [];
    if (!messages.length) {
      feed.innerHTML = welcomeHtml();
      started = false;
      window.scrollTo(0, 0);
    } else {
      feed.innerHTML = '<div class="ai-msgs" id="ai-msgs">' + messages.map(msgHtml).join('') + '</div>';
      started = true;
    }
    drawQuick();
    if (started) scrollDown(false);
  }
  function drawQuick() {
    var c = $('ai-composer');
    var strip = $('ai-quick');
    if (!started) { if (strip) strip.remove(); return; }
    if (!strip) { strip = document.createElement('div'); strip.id = 'ai-quick'; strip.className = 'ai-quick'; c.insertBefore(strip, c.firstChild); }
    strip.innerHTML = quickStripHtml();
  }
  function ensureList() {
    var list = $('ai-msgs');
    if (list) return list;
    feed.innerHTML = '<div class="ai-msgs" id="ai-msgs"></div>';
    started = true; drawQuick();
    return $('ai-msgs');
  }

  // ── Отправка: NDJSON-поток оркестратора, шаги инструментов, ответ по словам ──
  var TOOL_WORDS = {
    get_salon_report: 'Считаю отчёт салона', get_cash_report: 'Смотрю кассу', list_bookings: 'Смотрю записи',
    find_booking_slots: 'Ищу свободное время', create_booking: 'Записываю клиента', reschedule_booking: 'Переношу запись',
    cancel_booking: 'Отменяю запись', block_master_time: 'Закрываю время', add_expense: 'Записываю расход',
    offer_free_slots: 'Подбираю клиентов в окна', get_services: 'Смотрю прайс', get_staff: 'Смотрю мастеров',
    search_contacts: 'Ищу клиента', get_contact_details: 'Открываю карточку', get_period_report: 'Готовлю отчёт',
    get_agent_stats: 'Считаю цифры', whatsapp_send_message: 'Пишу в WhatsApp', telegram_send_message: 'Пишу в Telegram',
    instagram_send_message: 'Пишу в Instagram', create_agent_task: 'Ставлю задачу', trigger_immediate_call: 'Звоню',
    create_order: 'Оформляю заказ', list_orders: 'Смотрю заказы', update_order: 'Меняю заказ',
    get_catalog_overview: 'Смотрю склад', update_product_stock: 'Меняю остаток', move_contact_stage: 'Меняю этап',
    get_upcoming_schedule: 'Смотрю расписание', add_to_waitlist: 'Ставлю в лист ожидания'
  };
  function send(text) {
    text = String(text || '').trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true;
    input.value = ''; autosize();
    var list = ensureList();
    list.insertAdjacentHTML('beforeend', msgHtml({ role: 'user', text: text }));
    var wrap = document.createElement('div');
    wrap.className = 'ai-msg bot';
    wrap.innerHTML = '<div class="ai-ava live" aria-hidden="true"><i></i></div><div class="ai-body">' +
      '<div class="ai-steps"><div class="ai-think"><span></span><span></span><span></span><em>Думаю</em></div></div>' +
      '<div class="ai-bub md" hidden></div></div>';
    list.appendChild(wrap);
    scrollDown(true);
    var stepsEl = wrap.querySelector('.ai-steps'), bub = wrap.querySelector('.ai-bub'), ava = wrap.querySelector('.ai-ava');
    var acc = '', raf = 0, done = false, steps = [];

    function paint() {
      raf = 0; bub.hidden = false; bub.innerHTML = md(acc); scrollDown(false);
    }
    function drawSteps() {
      var think = steps.length && steps[steps.length - 1].st === 'run' ? '' :
        (acc ? '' : '<div class="ai-think"><span></span><span></span><span></span><em>Думаю</em></div>');
      stepsEl.innerHTML = steps.map(function (s) {
        return '<div class="ai-step ' + s.st + '">' + (s.st === 'run' ? '<span class="sp"></span>' : S.icon(s.st === 'err' ? 'x' : 'check')) +
          '<span>' + esc(TOOL_WORDS[s.tool] || 'Работаю с данными') + '</span></div>';
      }).join('') + think;
    }
    function finish(reply, isErr) {
      done = true;
      if (raf) cancelAnimationFrame(raf);
      ava.classList.remove('live');
      bub.hidden = false;
      bub.innerHTML = md(reply != null ? reply : acc);
      if (isErr) bub.classList.add('err');
      if (steps.length) {
        stepsEl.innerHTML = '<button type="button" class="ai-steps-sum">' + S.icon('zap') + '<span>' + steps.length + ' ' +
          plural(steps.length, 'действие', 'действия', 'действий') + '</span></button>' +
          '<div class="ai-steps-list" hidden>' + steps.map(function (s) {
            return '<div class="ai-step ' + s.st + '">' + S.icon(s.st === 'err' ? 'x' : 'check') + '<span>' + esc(TOOL_WORDS[s.tool] || s.tool) + '</span></div>';
          }).join('') + '</div>';
        stepsEl.querySelector('.ai-steps-sum').onclick = function () {
          var l = stepsEl.querySelector('.ai-steps-list'); l.hidden = !l.hidden;
        };
      } else stepsEl.remove();
      bub.insertAdjacentHTML('afterend', '<div class="ai-time">' + timeOf() + '</div>');
      busy = false; sendBtn.disabled = !input.value.trim();
      scrollDown(true);
    }
    function onEvent(ev) {
      if (!ev || !ev.type) return;
      if (ev.type === 'tool_call') { steps.push({ tool: ev.tool, st: 'run' }); drawSteps(); scrollDown(false); }
      else if (ev.type === 'tool_result' || ev.type === 'tool_error') {
        for (var i = steps.length - 1; i >= 0; i--) if (steps[i].st === 'run') { steps[i].st = ev.type === 'tool_error' ? 'err' : 'ok'; break; }
        drawSteps();
      } else if (ev.type === 'token') {
        acc += ev.text || '';
        var th = stepsEl.querySelector('.ai-think'); if (th) th.remove();
        if (!raf) raf = requestAnimationFrame(paint);
      } else if (ev.type === 'clear_partial') { acc = ''; bub.innerHTML = ''; }
      else if (ev.type === 'done') finish(ev.reply);
      else if (ev.type === 'error') finish(acc || 'Не получилось выполнить. Попробуйте ещё раз.', !acc);
    }

    var url = '/api/agent/chat/stream' + (S.agentId ? '?agent_id=' + encodeURIComponent(S.agentId) : '');
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + (S.token() || '') },
      body: JSON.stringify({ message: text }) })
      .then(function (resp) {
        if (resp.status === 401) { location.href = '/static/login.html'; return; }
        if (!resp.ok || !resp.body) {
          return resp.json().catch(function () { return {}; }).then(function (d) { finish(errorText(resp.status, d), true); });
        }
        var reader = resp.body.getReader(), dec = new TextDecoder(), buf = '';
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) {
              if (buf.trim()) { try { onEvent(JSON.parse(buf.trim())); } catch (e) { /* ignore */ } }
              if (!done) finish(acc || 'Связь прервалась. Попробуйте ещё раз.', !acc);
              return;
            }
            buf += dec.decode(r.value, { stream: true });
            var nl;
            while ((nl = buf.indexOf('\n')) >= 0) {
              var line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
              if (line) { try { onEvent(JSON.parse(line)); } catch (e) { /* ignore */ } }
            }
            return pump();
          });
        }
        return pump();
      })
      .catch(function () { if (!done) finish('Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.', true); });
  }
  function errorText(status, d) {
    var det = d && (d.detail != null ? d.detail : d.message);
    var code = det && typeof det === 'object' ? (det.error || det.message) : det;
    if (status === 402 || code === 'insufficient_credits') {
      return code === 'insufficient_credits'
        ? 'Кончились кредиты агента. Пополните их: **Ещё → Кредиты бота → Купить**.'
        : 'Тариф закончился — продлите его: **Ещё → Тариф**.';
    }
    if (status === 404) return 'Агент не найден — создайте его на странице агента.';
    return S.errText(code) || 'Не получилось выполнить. Попробуйте ещё раз.';
  }
  function plural(n, a, b, c) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return a;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return b;
    return c;
  }

  // ── Голос: тот же помощник, что кнопка «Спросить голосом» ─────────────────
  function loadVoice(cb) {
    if (window.VoksyVoice) { cb(); return; }
    if (!document.getElementById('vf-voice-css')) {
      var l = document.createElement('link'); l.id = 'vf-voice-css'; l.rel = 'stylesheet'; l.href = '/static/css/voice-live.css';
      document.head.appendChild(l);
    }
    var sc = document.createElement('script'); sc.src = '/static/agent/voice-chat.js';
    sc.onload = cb; sc.onerror = function () { S.toast('Не удалось открыть голос. Проверьте интернет', 'error'); setTalkBusy(false); };
    document.head.appendChild(sc);
  }
  function setTalkBusy(on) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-talk], #ai-talk'), function (b) { b.classList.toggle('busy', !!on); });
  }
  function talk() {
    if (!S.agent) { S.toast('Сначала создайте агента', 'error'); return; }
    setTalkBusy(true);
    loadVoice(function () {
      setTalkBusy(false);
      if (!window.VoksyVoice || window.VoksyVoice.isOpen()) return;
      window.VoksyVoice.open({
        agentId: S.agentId || null,
        token: function () { return S.token(); },
        title: (S.agent && S.agent.name && S.agent.name !== 'Мой агент') ? S.agent.name : (ky() ? 'Үн жардамчы' : 'Голосовой помощник'),
        onCredits: function () { location.href = full() ? '/static/agent.html' : '/static/start-bot.html#credits'; },
        // Разговор сервер уже записал в историю чата — показываем её заново
        onClose: function (lines) { if (lines && lines.length) setTimeout(loadHistory, 600); }
      });
    });
  }

  // ── Шапка: чаты (непрочитанные), новый разговор ─────────────────────────
  function drawTop(unread) {
    var top = $('ai-top');
    if (!top) return;
    top.innerHTML =
      '<a class="ai-topbtn ai-chats" href="/static/inbox.html" title="Чаты с клиентами">' + S.icon('inbox') + '<span>Чаты</span>' +
      (unread ? '<b>' + (unread > 99 ? '99+' : unread) + '</b>' : '') + '</a>' +
      (full() ? '<a class="ai-topbtn ai-desk" href="/static/agent.html" title="Агент обзвона: настройки, звонки, знания">' + S.icon('headset') + '</a>' : '') +
      '<button type="button" class="ai-topbtn" id="ai-new" title="Новый разговор">' + S.icon('square-pen') + '</button>';
    $('ai-new').onclick = function () {
      if (busy) return;
      S.confirm({ title: 'Начать новый разговор?', message: 'Агент забудет эту переписку. Клиенты, записи и отчёты не пострадают.',
        confirmText: 'Начать заново' }).then(function (ok) {
        if (!ok) return;
        S.api('/chat/clear', { method: 'POST' }).then(function () { drawFeed([]); });
      });
    };
  }
  function loadUnread() {
    S.api('/api/inbox/dialogs?folder=unread&limit=1').then(function (r) {
      drawTop(r.ok && r.data && r.data.counts ? r.data.counts.unread || 0 : 0);
    });
  }

  function loadHistory() {
    return S.api('/chat/history').then(function (r) {
      if (!r.ok) { drawFeed([]); return; }
      drawFeed((r.data && r.data.messages) || []);
    });
  }

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    sendBtn.disabled = busy || !input.value.trim();
  }
  function useCommand(c) {
    if (!c) return;
    if (c.send) { send(c.send); return; }
    input.value = c.fill || '';
    autosize();
    input.focus();
    var pos = c.caret != null ? c.caret : input.value.length;
    try { input.setSelectionRange(pos, pos); } catch (e) { /* ignore */ }
  }

  function bind() {
    input = $('ai-input'); sendBtn = $('ai-send');
    input.addEventListener('input', autosize);
    // Телефон: пока печатаете, нижняя панель прячется — поле ввода у клавиатуры
    input.addEventListener('focus', function () { if (window.matchMedia('(max-width: 760px)').matches) document.body.classList.add('ai-typing'); });
    input.addEventListener('blur', function () { setTimeout(function () { document.body.classList.remove('ai-typing'); }, 120); });
    input.addEventListener('keydown', function (e) {
      // Enter отправляет на компьютере; на телефоне Enter — новая строка, отправка кнопкой
      if (e.key === 'Enter' && !e.shiftKey && !window.matchMedia('(max-width: 760px)').matches) { e.preventDefault(); send(input.value); }
    });
    sendBtn.onclick = function () { send(input.value); };
    $('ai-talk').onclick = talk;
    document.addEventListener('click', function (e) {
      var chip = e.target.closest && e.target.closest('[data-cmd]');
      if (chip) { useCommand(cmdIndex[+chip.getAttribute('data-cmd')]); return; }
      if (e.target.closest && e.target.closest('[data-talk]')) talk();
    });
  }

  function start() {
    feed = $('ai-feed');
    S.boot({ allowFull: true }).then(function (ok) {
      if (!ok) return;
      S.ready();
      if (!S.agent) {
        feed.innerHTML = S.noBotHtml();
        return;
      }
      $('ai-composer').hidden = false;
      document.body.classList.add('ai-has-composer');
      bind();
      drawTop(0);
      loadHistory();
      loadUnread();
      // голос открывается быстрее, если скрипт уже в кэше
      setTimeout(function () { try { fetch('/static/agent/voice-chat.js'); } catch (e) { /* ignore */ } }, 2500);
      if (/[?&]talk=1/.test(location.search)) talk();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
