/**
 * Автоматизации (Voksy Beauty, шаг 2.2): /static/automations.html.
 *
 * Вкладка «Сообщения»: правила по группам («Запись» — подтверждение, напоминания, «спасибо»/оценка, «не пришёл»;
 * «Возврат клиентов» — ДР, повтор услуги, «давно не были»), переключатель, когда отправляется, сколько ушло за
 * 30 дней; «Настроить» — окно: время, текст RU и KY со вставками {имя}, {когда}…, пример сообщения как в
 * мессенджере, «вернуть текст по умолчанию». Ограничения рассылок: сообщений в день, одному клиенту не чаще, язык.
 * Вкладка «Журнал отправок»: что ушло, что ждёт, что не доставилось (и почему), «отправить ещё раз», «снять».
 * API: /api/beauty/automations* (api/beauty.py → services/automations.py, notify_queue.py).
 */
(function () {
  'use strict';
  var S = window.StartApp;
  var root, esc, icon;
  var st = { data: null, tab: 'rules', queue: { status: '', items: [], next: null, busy: false } };
  var HOURS_LABEL = { confirm: '', reminder_24h: 'за', reminder_2h: 'за', thanks: 'через', no_show: 'через' };
  var Q_STATUSES = [['', 'Все'], ['pending', 'В очереди'], ['sent', 'Отправлено'], ['problems', 'Проблемы']];
  var STATUS_CLS = { sent: 'ok', pending: 'info', sending: 'info', failed: 'err', skipped: 'warn', cancelled: '' };

  function plural(n, a, b, c) {
    var n100 = Math.abs(n) % 100, n10 = n100 % 10;
    if (n100 > 10 && n100 < 20) return c;
    if (n10 > 1 && n10 < 5) return b;
    if (n10 === 1) return a;
    return c;
  }
  function hh(h) { return (h < 10 ? '0' : '') + h + ':00'; }

  function when(r) {
    var p = r.params || {};
    switch (r.key) {
      case 'confirm': return 'сразу после записи';
      case 'reminder_24h': case 'reminder_2h': return 'за ' + p.hours + ' ' + plural(p.hours, 'час', 'часа', 'часов') + ' до визита';
      case 'thanks': return 'через ' + p.hours + ' ' + plural(p.hours, 'час', 'часа', 'часов') + ' после визита' + (p.ask_review ? ' · с оценкой 1–5' : '');
      case 'no_show': return 'через ' + p.hours + ' ' + plural(p.hours, 'час', 'часа', 'часов') + ' после пропуска';
      case 'birthday': return (p.days_before ? 'за ' + p.days_before + ' ' + plural(p.days_before, 'день', 'дня', 'дней') + ' до ДР' : 'в день рождения') + ', в ' + hh(p.hour);
      case 'repeat': return 'когда пора по услуге, в ' + hh(p.hour);
      case 'winback': return 'не были ' + p.days + ' ' + plural(p.days, 'день', 'дня', 'дней') + ', в ' + hh(p.hour);
    }
    return '';
  }

  // ── каркас ──────────────────────────────────────────────────────────────
  function renderShell() {
    root.innerHTML =
      '<div class="au-head"><div><h2>Сообщения клиентам — сами</h2>' +
      '<p class="muted">Напоминания, подтверждения, поздравления и приглашения уходят в мессенджер, где клиент писал последним. ' +
      'Не доставилось — пробуем другой мессенджер и повторяем.</p></div></div>' +
      '<div class="au-tabs" role="tablist">' +
      '<button type="button" class="au-tab" data-tab="rules">' + icon('send') + '<span>Сообщения</span></button>' +
      '<button type="button" class="au-tab" data-tab="queue">' + icon('history') + '<span>Журнал отправок</span></button></div>' +
      '<div id="au-body"></div>';
    S.$$('.au-tab', root).forEach(function (b) {
      b.addEventListener('click', function () { st.tab = b.dataset.tab; writeUrl(); draw(); });
    });
  }
  function writeUrl() {
    try { history.replaceState(null, '', location.pathname + (st.tab === 'queue' ? '#queue' : '')); } catch (e) { /* ignore */ }
  }
  function draw() {
    S.$$('.au-tab', root).forEach(function (b) { b.classList.toggle('on', b.dataset.tab === st.tab); b.setAttribute('aria-selected', b.dataset.tab === st.tab); });
    if (st.tab === 'queue') drawQueue(); else drawRules();
  }

  // ── правила ─────────────────────────────────────────────────────────────
  function load() {
    return S.api('/api/beauty/automations').then(function (r) {
      if (!r.ok) {
        root.innerHTML = '<div class="st-empty">' + esc(r.status === 403 ? 'Автоматизации настраивает владелец салона.' : S.apiError(r)) + '</div>';
        S.ready();
        return false;
      }
      st.data = r.data;
      return true;
    });
  }

  function drawRules() {
    var box = document.getElementById('au-body');
    var d = st.data;
    var groups = {};
    d.rules.forEach(function (r) { (groups[r.group] = groups[r.group] || { title: r.group_title, items: [] }).items.push(r); });
    var lim = d.limits;
    box.innerHTML = Object.keys(groups).map(function (g) {
      var grp = groups[g];
      return '<section class="au-group"><h3>' + esc(grp.title) +
        '<span class="muted small">' + (g === 'booking' ? 'с ' + hh(d.windows.service[0]) + ' до ' + hh(d.windows.service[1])
          : 'с ' + hh(d.windows.marketing[0]) + ' до ' + hh(d.windows.marketing[1]) + ' · тем, кто не отказался от рассылок') + '</span></h3>' +
        '<div class="card au-list">' + grp.items.map(ruleRow).join('') + '</div></section>';
    }).join('') +
      '<section class="au-group"><h3>Ограничения рассылок<span class="muted small">поздравления и приглашения; напоминания о записи — без ограничений</span></h3>' +
      '<div class="card au-limits">' +
      limitSel('max_per_day', 'Не больше сообщений в день', [20, 50, 100, 200], lim.max_per_day, function (n) { return n + ''; }) +
      limitSel('gap_days', 'Одному клиенту — не чаще', [1, 3, 7, 14], lim.gap_days, function (n) { return 'раз в ' + n + ' ' + plural(n, 'день', 'дня', 'дней'); }) +
      '<label class="au-lim"><span>Язык сообщений</span><select class="select-native" data-lim="lang">' +
      [['auto', 'Как пишет клиент'], ['ru', 'Всегда по-русски'], ['ky', 'Всегда по-кыргызски']].map(function (o) {
        return '<option value="' + o[0] + '"' + (lim.lang === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
      }).join('') + '</select></label></div>' +
      '<p class="muted small au-note">Клиент попросил не присылать акции и поздравления — бот отмечает это сам, или отметьте в карточке клиента. ' +
      'Напоминания о записи ему всё равно придут.</p></section>';
    S.$$('[data-sw]', box).forEach(function (cb) { cb.addEventListener('change', function () { toggle(cb.dataset.sw, cb.checked, cb); }); });
    S.$$('[data-edit]', box).forEach(function (b) { b.addEventListener('click', function () { editor(b.dataset.edit); }); });
    S.$$('[data-lim]', box).forEach(function (sel) {
      sel.addEventListener('change', function () {
        var body = {}; body[sel.dataset.lim] = sel.dataset.lim === 'lang' ? sel.value : parseInt(sel.value, 10);
        S.api('/api/beauty/automations/limits', { method: 'PUT', body: body }).then(function (r) {
          if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
          st.data.limits = r.data.limits; S.toast('Сохранено', 'success');
        });
      });
    });
  }

  function limitSel(key, label, opts, val, fmt) {
    if (opts.indexOf(val) < 0) opts = opts.concat([val]).sort(function (a, b) { return a - b; });
    return '<label class="au-lim"><span>' + esc(label) + '</span><select class="select-native" data-lim="' + key + '">' +
      opts.map(function (n) { return '<option value="' + n + '"' + (n === val ? ' selected' : '') + '>' + esc(fmt(n)) + '</option>'; }).join('') +
      '</select></label>';
  }

  function ruleRow(r) {
    var s = r.stats || {};
    var chips = '<span class="au-chip">' + icon('clock') + esc(when(r)) + '</span>';
    if (s.sent) chips += '<span class="au-chip ok">' + icon('check') + 'отправлено ' + s.sent + ' за 30 дн.</span>';
    if (s.failed) chips += '<span class="au-chip err">не доставлено ' + s.failed + '</span>';
    if (s.pending) chips += '<span class="au-chip">в очереди ' + s.pending + '</span>';
    var covered = r.covered_by_manager;
    return '<div class="au-rule' + (r.enabled && !covered ? ' on' : '') + '" data-rule="' + esc(r.key) + '">' +
      '<div class="au-main"><div class="au-title">' + esc(r.title) +
      (covered ? ' <span class="st-badge info" title="Этот сценарий включён у ИИ-управляющего — он пишет сам, текстом под клиента">делает ИИ-управляющий</span>' : '') +
      ((r.custom_ru || r.custom_ky) ? ' <span class="st-badge">свой текст</span>' : '') + '</div>' +
      '<div class="au-desc">' + esc(r.desc) + '</div><div class="au-chips">' + chips + '</div></div>' +
      '<div class="au-side"><label class="switch" title="' + (r.enabled ? 'Выключить' : 'Включить') + '"><input type="checkbox" data-sw="' + esc(r.key) + '"' +
      (r.enabled ? ' checked' : '') + '><span class="track"></span></label>' +
      '<button type="button" class="btn btn-sm" data-edit="' + esc(r.key) + '">' + icon('pencil') + '<span>Настроить</span></button></div></div>';
  }

  function ruleByKey(k) { return st.data.rules.filter(function (r) { return r.key === k; })[0]; }

  function toggle(key, on, cb) {
    cb.disabled = true;
    S.api('/api/beauty/automations/' + key, { method: 'PUT', body: { enabled: on } }).then(function (r) {
      cb.disabled = false;
      if (!r.ok) { cb.checked = !on; S.toast(S.apiError(r), 'error'); return; }
      replaceRule(r.data.rule);
      S.toast(on ? 'Включено' : 'Выключено', 'success');
    });
  }
  function replaceRule(rule) {
    if (!rule) return;
    st.data.rules = st.data.rules.map(function (x) { return x.key === rule.key ? rule : x; });
    if (st.tab === 'rules') drawRules();
  }

  // ── окно правила ────────────────────────────────────────────────────────
  var PARAM_LABELS = {
    hours: { reminder_24h: 'За сколько до визита', reminder_2h: 'За сколько до визита', thanks: 'Через сколько после визита', no_show: 'Через сколько после пропуска' },
    days_before: 'Когда поздравлять', hour: 'Во сколько отправлять', days: 'Сколько клиент не был'
  };
  function paramLabel(key, rule) { var l = PARAM_LABELS[key]; return typeof l === 'object' ? l[rule] : l; }
  function optText(key, n) {
    if (key === 'hours') return n + ' ' + plural(n, 'час', 'часа', 'часов');
    if (key === 'days_before') return n === 0 ? 'в день рождения' : 'за ' + n + ' ' + plural(n, 'день', 'дня', 'дней');
    if (key === 'hour') return hh(n);
    if (key === 'days') return n + ' ' + plural(n, 'день', 'дня', 'дней');
    return String(n);
  }

  function editor(key) {
    var r = ruleByKey(key);
    if (!r) return;
    var draft = { lang: 'ru', ru: r.template_ru, ky: r.template_ky, rru: r.review_text_ru, rky: r.review_text_ky, params: JSON.parse(JSON.stringify(r.params || {})) };
    var params = Object.keys(r.options || {}).map(function (pk) {
      return '<label class="field"><span class="label">' + esc(paramLabel(pk, key)) + '</span><select class="select-native" data-p="' + pk + '">' +
        r.options[pk].map(function (n) { return '<option value="' + n + '"' + (n === draft.params[pk] ? ' selected' : '') + '>' + esc(optText(pk, n)) + '</option>'; }).join('') +
        '</select></label>';
    }).join('');
    if ('gift' in draft.params) {
      params += '<label class="field au-wide"><span class="label">Подарок (по желанию)</span><input class="input" data-p="gift" maxlength="200" value="' +
        esc(draft.params.gift || '') + '" placeholder="Например: скидка 10% на любую услугу в течение недели"><span class="hint">Попадает в текст вместо {подарок}</span></label>';
    }
    if ('ask_review' in draft.params) {
      params += '<label class="au-check au-wide"><input type="checkbox" data-p="ask_review"' + (draft.params.ask_review ? ' checked' : '') + '>' +
        '<span><b>Просить оценку визита от 1 до 5</b><br><span class="muted small">Оценки 1–3 сразу приходят вам в Telegram. Вместо «спасибо» уйдёт текст с просьбой оценить.</span></span></label>';
    }
    var d = S.dialog({
      title: r.title, size: 'lg',
      body: (r.covered_by_manager ? '<div class="au-warn">' + icon('sparkles') + '<span>Этот сценарий сейчас включён у ИИ-управляющего — он пишет клиентам сам. Правило сработает, если выключить сценарий там.</span></div>' : '') +
        '<p class="muted" style="margin:0 0 12px">' + esc(r.desc) + '</p>' +
        (params ? '<div class="au-params">' + params + '</div>' : '') +
        '<div class="au-ed">' +
        '<div class="au-ed-main"><div class="au-langs"><button type="button" class="au-lang on" data-lang="ru">Русский</button><button type="button" class="au-lang" data-lang="ky">Кыргызча</button>' +
        (key === 'thanks' ? '<span class="au-which muted small" id="au-which"></span>' : '') + '</div>' +
        '<textarea class="textarea au-text" id="au-text" rows="6" maxlength="1000"></textarea>' +
        '<div class="au-ph">' + st.data.placeholders.map(function (p) { return '<button type="button" class="au-phb" data-ph="' + esc(p.key) + '" title="' + esc(p.title) + '">{' + esc(p.key) + '}</button>'; }).join('') + '</div>' +
        '<div class="hint">Кусок в [квадратных скобках] выпадает целиком, если данных нет: «[ Адрес: {адрес}.]». ' +
        '<button type="button" class="au-reset" id="au-reset">Вернуть текст по умолчанию</button></div></div>' +
        '<div class="au-ed-prev"><div class="au-prev-h muted small">Так увидит клиент</div><div class="au-bubble" id="au-prev"><span class="skeleton" style="display:block;height:60px"></span></div></div></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" data-ok>Сохранить</button>'
    });
    var $ = function (s) { return S.$(s, d.el); };
    var ta = $('#au-text');
    function slot() {
      // у «спасибо» с оценкой правим текст просьбы оценить (правило review)
      var rev = key === 'thanks' && draft.params.ask_review;
      return rev ? (draft.lang === 'ky' ? 'rky' : 'rru') : draft.lang;
    }
    function syncText() {
      ta.value = draft[slot()] || '';
      var w = $('#au-which');
      if (w) w.textContent = draft.params.ask_review ? 'текст с просьбой оценить' : '';
      preview();
    }
    var tmr;
    function preview() {
      clearTimeout(tmr);
      tmr = setTimeout(function () {
        var rev = key === 'thanks' && draft.params.ask_review;
        S.api('/api/beauty/automations/' + (rev ? 'review' : key) + '/preview', { method: 'POST', body: { template: ta.value, lang: draft.lang } }).then(function (res) {
          var p = $('#au-prev');
          if (!p) return;
          p.classList.toggle('bad', !res.ok);
          p.textContent = res.ok ? res.data.text : S.apiError(res);
        });
      }, 250);
    }
    ta.addEventListener('input', function () { draft[slot()] = ta.value; preview(); });
    S.$$('.au-lang', d.el).forEach(function (b) {
      b.addEventListener('click', function () {
        draft.lang = b.dataset.lang;
        S.$$('.au-lang', d.el).forEach(function (x) { x.classList.toggle('on', x === b); });
        syncText();
      });
    });
    S.$$('[data-ph]', d.el).forEach(function (b) {
      b.addEventListener('click', function () {
        var ins = '{' + b.dataset.ph + '}', a = ta.selectionStart || ta.value.length, z = ta.selectionEnd || a;
        ta.value = ta.value.slice(0, a) + ins + ta.value.slice(z);
        ta.focus(); ta.selectionStart = ta.selectionEnd = a + ins.length;
        draft[slot()] = ta.value; preview();
      });
    });
    S.$$('[data-p]', d.el).forEach(function (el) {
      el.addEventListener('change', function () {
        var k = el.dataset.p;
        draft.params[k] = el.type === 'checkbox' ? el.checked : k === 'gift' ? el.value.trim() : parseInt(el.value, 10);
        if (k === 'ask_review' || k === 'gift') syncText();
      });
    });
    $('#au-reset').addEventListener('click', function () {
      var rev = key === 'thanks' && draft.params.ask_review;
      draft[slot()] = rev ? '' : (draft.lang === 'ky' ? r.default_ky : r.default_ru);
      if (rev) { draft[slot()] = null; ta.value = ''; preview(); S.toast('Вернём текст по умолчанию при сохранении', 'info'); return; }
      syncText();
    });
    $('[data-ok]').addEventListener('click', function () {
      var btn = $('[data-ok]'); btn.disabled = true;
      var body = { params: draft.params, template_ru: draft.ru === r.default_ru ? '' : draft.ru, template_ky: draft.ky === r.default_ky ? '' : draft.ky };
      var reqs = [S.api('/api/beauty/automations/' + key, { method: 'PUT', body: body })];
      if (key === 'thanks' && (draft.rru !== r.review_text_ru || draft.rky !== r.review_text_ky)) {
        reqs.push(S.api('/api/beauty/automations/review', { method: 'PUT', body: { template_ru: draft.rru || '', template_ky: draft.rky || '' } }));
      }
      Promise.all(reqs).then(function (res) {
        btn.disabled = false;
        var bad = res.filter(function (x) { return !x.ok; })[0];
        if (bad) { S.toast(S.apiError(bad), 'error'); return; }
        load().then(function (ok) { if (ok) drawRules(); });
        d.close();
        S.toast('Сохранено', 'success');
      });
    });
    syncText();
  }

  // ── журнал отправок ─────────────────────────────────────────────────────
  function ruleTitle(k) {
    if (k === 'review') return 'Просьба оценить визит';
    var r = st.data && ruleByKey(k);
    return r ? r.title : k;
  }
  function drawQueue() {
    var box = document.getElementById('au-body');
    var q = st.queue;
    box.innerHTML = '<div class="st-colbar au-qf">' + Q_STATUSES.map(function (s) {
      return '<button type="button" class="st-colchip' + (q.status === s[0] ? ' on' : '') + '" data-qs="' + s[0] + '">' + esc(s[1]) + '</button>';
    }).join('') + '</div><div class="card au-qlist" id="au-qlist"><div class="skeleton" style="height:180px"></div></div>';
    S.$$('[data-qs]', box).forEach(function (b) {
      b.addEventListener('click', function () { q.status = b.dataset.qs; drawQueue(); });
    });
    loadQueue(false);
  }
  function loadQueue(more) {
    var q = st.queue;
    if (q.busy) return;
    q.busy = true;
    var p = new URLSearchParams({ limit: '50' });
    if (q.status) p.set('status', q.status);
    if (more && q.next) p.set('before', q.next);
    S.api('/api/beauty/automations/queue?' + p).then(function (r) {
      q.busy = false;
      var list = document.getElementById('au-qlist');
      if (!list) return;
      if (!r.ok) { list.innerHTML = '<div class="st-empty">' + esc(S.apiError(r)) + '</div>'; return; }
      q.items = more ? q.items.concat(r.data.items) : r.data.items;
      q.next = r.data.next;
      list.innerHTML = q.items.length ? q.items.map(qRow).join('') + (q.next ? '<div class="st-more"><button class="btn" type="button" id="au-qmore">Показать ещё</button></div>' : '')
        : '<div class="st-empty">' + (q.status ? 'Здесь пусто.' : 'Сообщений ещё не было. Они появятся, как только клиенты начнут записываться.') + '</div>';
      S.$$('[data-retry]', list).forEach(function (b) { b.addEventListener('click', function () { act(b.dataset.retry, 'retry', b); }); });
      S.$$('[data-cancel]', list).forEach(function (b) { b.addEventListener('click', function () { act(b.dataset.cancel, 'cancel', b); }); });
      var m = document.getElementById('au-qmore');
      if (m) m.addEventListener('click', function () { m.disabled = true; loadQueue(true); });
    });
  }
  function qRow(it) {
    var t = it.sent_at || (it.status === 'pending' ? it.send_after : it.created_at);
    var who = it.client || (it.phone ? S.fmtPhone(it.phone) : 'Клиент');
    return '<div class="au-q">' +
      '<div class="au-q-top"><span class="au-q-when">' + esc(S.fmtDate(t, true)) + (it.status === 'pending' ? ' · отправим' : '') + '</span>' +
      '<span class="st-badge ' + (STATUS_CLS[it.status] || '') + '">' + esc(it.status_title) + (it.channel_title ? ' · ' + esc(it.channel_title) : '') + '</span></div>' +
      '<div class="au-q-who">' + (it.contact_id ? '<a href="/static/start-crm.html?contact=' + encodeURIComponent(it.contact_id) + '">' + esc(who) + '</a>' : esc(who)) +
      ' <span class="muted">· ' + esc(ruleTitle(it.rule)) + (it.lang === 'ky' ? ' · KY' : '') + '</span></div>' +
      '<div class="au-q-text">' + esc(it.text) + '</div>' +
      (it.error && it.status !== 'sent' ? '<div class="au-q-err">' + icon('circle-alert') + esc(it.error) + '</div>' : '') +
      ((it.status === 'failed' || it.status === 'skipped' || it.status === 'cancelled') ? '<div class="au-q-act"><button class="btn btn-sm" type="button" data-retry="' + esc(it.id) + '">' + icon('rotate-ccw') + '<span>Отправить ещё раз</span></button></div>' : '') +
      (it.status === 'pending' ? '<div class="au-q-act"><button class="btn btn-sm btn-ghost" type="button" data-cancel="' + esc(it.id) + '">' + icon('x') + '<span>Не отправлять</span></button></div>' : '') +
      '</div>';
  }
  function act(id, what, btn) {
    btn.disabled = true;
    S.api('/api/beauty/automations/queue/' + id + '/' + what, { method: 'POST' }).then(function (r) {
      btn.disabled = false;
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
      S.toast(what === 'retry' ? 'Отправим в ближайшую минуту' : 'Снято из очереди', 'success');
      loadQueue(false);
    });
  }

  function start() {
    root = document.getElementById('au');
    esc = S.esc; icon = S.icon;
    if (S.initMobileMenu) S.initMobileMenu();
    st.tab = location.hash === '#queue' ? 'queue' : 'rules';
    load().then(function (ok) {
      if (!ok) return;
      renderShell();
      draw();
      S.ready();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
