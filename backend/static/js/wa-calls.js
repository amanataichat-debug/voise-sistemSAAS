/* ============================================================================
 * js/wa-calls.js — «Звонки WhatsApp»: агент отвечает на звонки в WhatsApp
 * Общий модуль для «Чат-бота» Старта / Beauty AI (start-bot.html → Каналы) и
 * страницы агента Profi / Beauty Pro (agent.html → Коннекторы).
 *
 *   VoksyWaCalls.mount(container, { agentId: () => id|null })
 *
 * Строка канала: статус, переключатель «Агент отвечает на звонки» (вкл/выкл одним
 * нажатием), «Подключить» → окно: предупреждение → QR (WhatsApp → Связанные устройства)
 * → «Готово». «Настройки» — исходящие (Profi/Beauty Pro), лимит, первая фраза, отвязать номер.
 * Бэкенд — /api/agent/wa-calls (backend/api/agent_wa_calls.py). Классический скрипт, IIFE.
 * ========================================================================== */
(function () {
  'use strict';
  var API = '/api/agent/wa-calls';
  var state = null, loadedAt = 0, mounts = [], loading = null, stateAgent = null;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function ic(n) { return (window.VF && VF.icon) ? VF.icon(n) : ''; }
  function toast(msg, type) { if (window.VF && VF.toast) VF.toast(msg, { type: type || 'info' }); else alert(msg); }
  function agentId() {
    for (var i = 0; i < mounts.length; i++) { var f = mounts[i].opts.agentId; var v = f && f(); if (v) return v; }
    return null;
  }
  function url(path) {
    var u = API + (path || ''), id = agentId();
    return id ? u + (u.indexOf('?') < 0 ? '?' : '&') + 'agent_id=' + encodeURIComponent(id) : u;
  }
  function errOf(d) {
    var m = d && (d.detail != null ? d.detail : d.message);
    if (m && typeof m === 'object') m = m.message || m.detail || JSON.stringify(m);
    return m || 'Не получилось, попробуйте ещё раз';
  }
  function api(path, method, body) {
    var tok = ''; try { tok = localStorage.getItem('auth_token') || ''; } catch (e) { /* ignore */ }
    return fetch(url(path), {
      method: method || 'GET',
      headers: Object.assign({ Authorization: 'Bearer ' + tok }, body ? { 'Content-Type': 'application/json' } : {}),
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
    }).catch(function () { return { ok: false, status: 0, data: { detail: 'Нет связи с сервером' } }; });
  }

  function load(force) {
    if (agentId() !== stateAgent) { force = true; state = null; }
    if (!force && state && Date.now() - loadedAt < 5000) { drawAll(); return Promise.resolve(state); }
    if (loading) return loading;
    var forAgent = agentId();
    loading = api('').then(function (r) {
      loading = null;
      stateAgent = forAgent;
      state = r.ok ? r.data : null; loadedAt = Date.now();
      drawAll();
      return state;
    });
    return loading;
  }

  // ---------------------------------------------------------------- стили
  function css() {
    if (document.getElementById('wac-css')) return;
    var st = document.createElement('style'); st.id = 'wac-css';
    st.textContent =
      '.wac-row{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--vf-border,#e2e8f0)}' +
      '.wac-logo{width:38px;height:38px;border-radius:10px;display:grid;place-items:center;flex:0 0 auto;background:#e7f8ee;color:#128c4b}' +
      '.wac-logo svg{width:20px;height:20px}' +
      '.wac-info{flex:1;min-width:0}.wac-info b{font-size:14px}.wac-sub{font-size:12.5px;color:var(--vf-text-muted,#64748b);margin-top:2px}' +
      '.wac-sub.ok{color:var(--vf-success,#15803d)}.wac-sub.warn{color:var(--vf-warning,#b45309)}' +
      '.wac-act{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}' +
      '.wac-tg{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--vf-text-muted,#64748b);cursor:pointer;white-space:nowrap}' +
      '.wac-qr{display:flex;flex-direction:column;align-items:center;gap:12px;text-align:center}' +
      '.wac-qr img{width:260px;height:260px;max-width:100%;border-radius:12px;border:1px solid var(--vf-border,#e2e8f0);background:#fff}' +
      '.wac-ph{width:260px;height:260px;max-width:100%;border-radius:12px;display:grid;place-items:center;background:var(--vf-surface-2,#f1f5f9);color:var(--vf-text-muted,#64748b);font-size:13px;padding:16px}' +
      '.wac-steps{margin:0;padding-left:20px;text-align:left;font-size:13.5px;line-height:1.55}' +
      '.wac-big{font-size:40px;color:var(--vf-success,#15803d)}.wac-big svg{width:44px;height:44px}' +
      '.wac-field{margin:14px 0}.wac-field label.l{display:block;font-weight:600;font-size:13px;margin-bottom:6px}' +
      '.wac-hint{font-size:12px;color:var(--vf-text-muted,#64748b);margin-top:4px}' +
      '.wac-sw{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:10px 0;border-bottom:1px solid var(--vf-border,#e2e8f0)}' +
      '.wac-sw .t b{font-size:14px}.wac-sw .t div{font-size:12.5px;color:var(--vf-text-muted,#64748b);margin-top:2px}' +
      '@media (max-width:760px){.wac-row{flex-wrap:wrap}.wac-act{width:100%;justify-content:flex-start}}';
    document.head.appendChild(st);
  }

  // ---------------------------------------------------------------- строка канала
  function rowHtml(s) {
    var acc = s.account, st = s.status, sub, cls = '', act = '';
    var logo = '<span class="wac-logo">' + ic('phone-call') + '</span>';
    if (!s.plan_ok) {
      sub = 'Агент отвечает на звонки в WhatsApp. Доступно на тарифах ' + esc(s.plans_text);
      act = '<a class="btn btn-sm" href="/static/dashboard.html#plans">Тарифы</a>';
    } else if (!s.agent.has_voice) {
      sub = 'Сначала выберите голос агента для звонков, потом подключите номер';
      cls = 'warn';
    } else if (!acc || st === 'not_connected' || st === 'logged_out') {
      sub = acc && st === 'logged_out' ? 'Номер отвязан. Подключите заново, чтобы агент отвечал на звонки'
        : 'Агент сам отвечает, когда вам звонят в WhatsApp. Подключение по QR за минуту';
      act = '<button class="btn btn-sm btn-primary" type="button" data-wac="connect">' + ic('qr-code') + ' Подключить</button>';
    } else if (st === 'pending_qr') {
      sub = 'Ждём сканирования QR-кода'; cls = 'warn';
      act = '<button class="btn btn-sm btn-primary" type="button" data-wac="connect">' + ic('qr-code') + ' Показать QR</button>' +
        '<button class="btn btn-sm btn-ghost" type="button" data-wac="off">Отмена</button>';
    } else {
      var on = acc.inbound_enabled;
      if (st === 'connected') {
        sub = esc(acc.phone_masked || '') + ' · ' + (on ? 'агент отвечает на звонки' : 'агент выключен — звонки идут вам на телефон');
        cls = on ? 'ok' : 'warn';
        if (on && acc.outbound_enabled) sub += ' · звонит клиентам сам';
        if (!s.agent.is_active && on) { sub += ' · но сам агент выключен'; cls = 'warn'; }
      } else if (st === 'disconnected') {
        sub = esc(acc.phone_masked || '') + ' · связь с WhatsApp прервалась, переподключаемся сами…'; cls = 'warn';
      } else if (st === 'banned') {
        sub = 'WhatsApp временно ограничил номер. Звонки агента остановлены'; cls = 'warn';
      } else {
        sub = 'Ошибка подключения' + (acc.last_error ? ': ' + esc(acc.last_error) : '') + '. Подключите номер заново'; cls = 'warn';
        act += '<button class="btn btn-sm btn-primary" type="button" data-wac="connect">Переподключить</button>';
      }
      act = '<label class="wac-tg" title="Включить или выключить агента на звонках WhatsApp">' +
        '<span>' + (on ? 'Агент включён' : 'Агент выключен') + '</span>' +
        '<span class="switch"><input type="checkbox" data-wac="toggle"' + (on ? ' checked' : '') + '><span class="track"></span></span></label>' +
        act + '<button class="btn btn-sm" type="button" data-wac="settings">' + ic('settings') + ' Настройки</button>';
    }
    return logo + '<div class="wac-info"><b>Звонки WhatsApp</b><div class="wac-sub ' + cls + '">' + sub + '</div></div>' +
      '<div class="wac-act">' + act + '</div>';
  }

  function drawAll() {
    mounts = mounts.filter(function (m) { return m.el && document.body.contains(m.el); });
    mounts.forEach(function (m) {
      if (!state || !state.available) { m.el.innerHTML = ''; m.el.style.display = 'none'; return; }
      m.el.style.display = '';
      m.el.className = 'wac-row';
      m.el.innerHTML = rowHtml(state);
      bind(m.el);
    });
  }

  function bind(el) {
    el.querySelectorAll('[data-wac]').forEach(function (b) {
      var a = b.getAttribute('data-wac');
      if (a === 'toggle') b.addEventListener('change', function () { toggle(b.checked, b); });
      else b.addEventListener('click', function () {
        if (a === 'connect') openConnect(); else if (a === 'settings') openSettings(); else if (a === 'off') disconnect(true);
      });
    });
  }

  function toggle(on, input) {
    input.disabled = true;
    api('/settings', 'PATCH', { inbound_enabled: on }).then(function (r) {
      input.disabled = false;
      if (!r.ok) { input.checked = !on; toast(errOf(r.data), 'error'); return; }
      state = r.data; loadedAt = Date.now(); drawAll();
      toast(on ? 'Агент снова отвечает на звонки WhatsApp' : 'Агент выключен: звонки WhatsApp идут вам на телефон', 'success');
    });
  }

  // ---------------------------------------------------------------- окно
  function dialog(title, body, foot, onClose) {
    var back = document.createElement('div');
    back.className = 'vf-backdrop';
    back.innerHTML = '<div class="vf-dialog vf-dialog-sm" role="dialog" aria-modal="true">' +
      '<div class="vf-dialog-head"><h3>' + esc(title) + '</h3><button class="btn btn-ghost btn-icon btn-sm" type="button" data-close aria-label="Закрыть">' + ic('x') + '</button></div>' +
      '<div class="vf-dialog-body"></div>' + (foot ? '<div class="vf-dialog-foot">' + foot + '</div>' : '') + '</div>';
    back.querySelector('.vf-dialog-body').innerHTML = body;
    document.body.appendChild(back);
    var m;
    function cleanup() { setTimeout(function () { back.remove(); }, 260); if (onClose) onClose(); }
    if (window.VF && VF.modal) {
      m = VF.modal(back, { onClose: cleanup });
      m.open();
    } else {
      back.style.display = 'flex'; back.classList.add('open');
      back.querySelectorAll('[data-close]').forEach(function (x) { x.addEventListener('click', function () { m.close(); }); });
      m = { close: function () { back.remove(); if (onClose) onClose(); } };
    }
    function onKey(e) { if (e.key === 'Escape') { document.removeEventListener('keydown', onKey); m.close(); } }
    document.addEventListener('keydown', onKey);
    return { el: back, body: back.querySelector('.vf-dialog-body'), close: function () { document.removeEventListener('keydown', onKey); m.close(); } };
  }

  var timer = null;
  function stopPoll() { if (timer) { clearInterval(timer); timer = null; } }

  function openConnect() {
    var s = state || {};
    var acc = s.account;
    if (acc && acc.status === 'pending_qr') return showQr();
    var same = s.messenger_phone
      ? '<li>Подключите <b>тот же номер</b>, что и переписку (' + esc(s.messenger_phone) + '): клиент пишет и звонит в одно место.</li>' : '';
    var d = dialog('Звонки WhatsApp',
      '<p style="margin-top:0">Агент будет брать трубку, когда клиенты звонят вам в WhatsApp, и говорить тем же голосом, что и по телефону. Разговор, запись и итог появятся в «Диалогах» и в карточке клиента.</p>' +
      '<ul class="wac-steps">' + same +
      '<li>Ваш WhatsApp продолжит работать на телефоне: агент подключается как ещё одно «связанное устройство».</li>' +
      '<li>Выключить агента можно одним переключателем — тогда звонки снова звонят вам.</li>' +
      '<li>Минута разговора списывается с кошелька Voksy по цене голоса, оператору платить не нужно.</li></ul>' +
      '<div class="note note-warning" style="margin-top:14px"><b>Важно:</b> подключение неофициальное (как WhatsApp Web). При жалобах клиентов WhatsApp может временно ограничить номер. Не обзванивайте холодную базу.</div>',
      '<button class="btn" type="button" data-close>Отмена</button><button class="btn btn-primary" type="button" id="wac-go">' + ic('qr-code') + ' Показать QR-код</button>');
    d.el.querySelector('#wac-go').addEventListener('click', function () {
      var b = this; b.disabled = true;
      api('/connect', 'POST').then(function (r) {
        b.disabled = false;
        if (!r.ok) { toast(errOf(r.data), 'error'); return; }
        state = r.data; loadedAt = Date.now(); drawAll();
        d.close(); showQr();
      });
    });
  }

  function showQr() {
    var started = Date.now();
    var d = dialog('Отсканируйте QR-код', '<div class="wac-qr" id="wac-qr"></div>',
      '<button class="btn" type="button" data-close>Закрыть</button>', function () { stopPoll(); load(true); });
    function paint(s) {
      var box = d.el.querySelector('#wac-qr'); if (!box) return;
      var acc = s && s.account;
      if (acc && acc.status === 'connected') {
        stopPoll();
        box.innerHTML = '<div class="wac-big">' + ic('circle-check') + '</div><b style="font-size:16px">Готово, номер подключён</b>' +
          '<div style="font-size:14px">' + esc(acc.phone_masked || '') + '</div>' +
          '<div class="wac-hint" style="max-width:320px">Проверьте: позвоните на этот номер в WhatsApp с другого телефона — ответит агент. Выключить его можно переключателем в строке «Звонки WhatsApp».</div>';
        return;
      }
      var wait = (Date.now() - started) / 1000;
      var img = s && s.qr ? '<img src="' + esc(s.qr) + '" alt="QR-код">'
        : '<div class="wac-ph">' + (acc && acc.last_error ? esc(acc.last_error) + '<br><br>Нажмите «Новый код»'
          : wait > 45 ? 'Сервер звонков пока не ответил. Подождите ещё минуту или напишите в поддержку'
          : 'Готовим QR-код… обычно 5–15 секунд') + '</div>';
      box.innerHTML = img +
        '<ol class="wac-steps"><li>Откройте WhatsApp на телефоне с этим номером.</li>' +
        '<li>Android: ⋮ → <b>Связанные устройства</b>. iPhone: Настройки → <b>Связанные устройства</b>.</li>' +
        '<li>«Привязать устройство» и наведите камеру на код.</li></ol>' +
        (s && s.qr_expires_in ? '<div class="wac-hint">Код обновляется сам.</div>' : '') +
        ((acc && acc.last_error) || wait > 45 ? '<button class="btn btn-sm" type="button" id="wac-new">Новый код</button>' : '');
      var nb = box.querySelector('#wac-new');
      if (nb) nb.addEventListener('click', function () {
        nb.disabled = true; started = Date.now();
        api('/connect', 'POST').then(function (r) { if (!r.ok) toast(errOf(r.data), 'error'); else { state = r.data; paint(state); } });
      });
    }
    paint(state);
    stopPoll();
    timer = setInterval(function () {
      api('').then(function (r) { if (r.ok) { state = r.data; loadedAt = Date.now(); paint(state); drawAll(); } });
    }, 2000);
  }

  function openSettings() {
    var s = state || {}, acc = s.account || {};
    var out = s.can_outbound
      ? '<div class="wac-sw"><div class="t"><b>Звонить клиентам самому</b><div>Агент может перезвонить через WhatsApp тем, кто сам писал или звонил (номера не на O!). Номера O! агент набирает через оператора.</div></div>' +
        '<label class="switch"><input type="checkbox" id="wac-out"' + (acc.outbound_enabled ? ' checked' : '') + '><span class="track"></span></label></div>' +
        '<div class="wac-field"><label class="l">Звонков в сутки, не больше</label><input class="input" type="number" min="1" max="50" id="wac-lim" style="max-width:120px" value="' + Number(acc.daily_outbound_limit || s.default_daily_limit || 20) + '">' +
        '<div class="wac-hint">Защита номера от блокировки. В час — не больше ' + Number(s.hourly_limit || 6) + '.</div></div>'
      : '<div class="wac-hint" style="margin:10px 0">На вашем тарифе агент отвечает на входящие звонки. Звонить клиентам сам через WhatsApp он может на Profi и Beauty Pro.</div>';
    var d = dialog('Звонки WhatsApp: настройки',
      '<div class="wac-sw" style="padding-top:0"><div class="t"><b>Агент отвечает на звонки</b><div>Выключите — и звонки WhatsApp снова будут звонить вам на телефон.</div></div>' +
      '<label class="switch"><input type="checkbox" id="wac-in"' + (acc.inbound_enabled ? ' checked' : '') + '><span class="track"></span></label></div>' + out +
      '<div class="wac-field"><label class="l">Первая фраза агента</label><input class="input" id="wac-fp" maxlength="300" placeholder="Как у входящих звонков по телефону" value="' + esc(acc.first_phrase || '') + '">' +
      '<div class="wac-hint">Пусто — та же, что у входящих звонков на номер агента.</div></div>' +
      '<div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--vf-border,#e2e8f0)">' +
      '<button class="btn btn-sm btn-ghost" type="button" id="wac-unlink" style="color:var(--vf-danger,#b91c1c)">' + ic('unlink') + ' Отвязать номер</button>' +
      '<div class="wac-hint">Устройство пропадёт из «Связанных устройств» WhatsApp, настройки сохранятся.</div></div>',
      '<button class="btn" type="button" data-close>Отмена</button><button class="btn btn-primary" type="button" id="wac-save">Сохранить</button>');
    d.el.querySelector('#wac-save').addEventListener('click', function () {
      var body = { inbound_enabled: d.el.querySelector('#wac-in').checked, first_phrase: d.el.querySelector('#wac-fp').value };
      var o = d.el.querySelector('#wac-out'); if (o) body.outbound_enabled = o.checked;
      var l = d.el.querySelector('#wac-lim'); if (l) body.daily_outbound_limit = parseInt(l.value || '20', 10) || 20;
      api('/settings', 'PATCH', body).then(function (r) {
        if (!r.ok) { toast(errOf(r.data), 'error'); return; }
        state = r.data; loadedAt = Date.now(); drawAll(); d.close(); toast('Сохранено', 'success');
      });
    });
    d.el.querySelector('#wac-unlink').addEventListener('click', function () { d.close(); disconnect(false); });
  }

  function disconnect(silent) {
    var go = function () {
      api('', 'DELETE').then(function (r) {
        if (!r.ok) { toast(errOf(r.data), 'error'); return; }
        state = r.data; loadedAt = Date.now(); drawAll();
        if (!silent) toast('Номер отвязан: звонки WhatsApp идут вам на телефон', 'success');
      });
    };
    if (silent || !(window.VF && VF.confirm)) return go();
    VF.confirm({ title: 'Отвязать номер от звонков?', message: 'Агент перестанет отвечать на звонки WhatsApp. Журнал звонков сохранится.', confirmText: 'Отвязать', danger: true })
      .then(function (ok) { if (ok) go(); });
  }

  window.VoksyWaCalls = {
    mount: function (el, opts) {
      if (typeof el === 'string') el = document.querySelector(el);
      if (!el) return;
      css();
      mounts = mounts.filter(function (m) { return m.el !== el && document.body.contains(m.el); });
      mounts.push({ el: el, opts: opts || {} });
      if (state) drawAll();
      load(false);
    },
    reload: function () { return load(true); },
    state: function () { return state; }
  };
})();
