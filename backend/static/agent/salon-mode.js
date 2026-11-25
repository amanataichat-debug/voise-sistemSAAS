/* ============================================================================
 * agent/salon-mode.js — страница агента в салонном виде (Voksy Beauty, шаг 1.6а).
 * Часть страницы /static/agent.html. Классический скрипт; грузится СРАЗУ после core.js,
 * до вкладок и чата, — чтобы они с первого кадра рисовались по-салонному.
 *
 * Двигатель тот же, что у Profi (оркестратор, единый чат, агент сам пишет и звонит) — меняется вид:
 *   - приветствие и подсказки чата — управляющий салона (chat.js → SALON_SUGGESTIONS);
 *   - вкладки: Диалог · Клиенты · Записи (журнал) · Задачи · Аналитика (tabs.js);
 *   - слева вместо «Всего звонков» — «Записи сегодня» (пришли / впереди) и список записей на сегодня;
 *   - инструкции агента: «Наши клиенты», «Правила записи и цели» вместо «Кому звоним», «Правила и цели».
 * Салон = профиль бизнеса business.beauty (кэш localStorage.vf_business, свежий — /api/users/me).
 * ========================================================================== */

(function () {
  var KEY = 'vf_business';

  function readBiz() {
    try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { return null; }
  }
  function isSalon(biz) {
    return !!(biz && biz.beauty && (!biz.modules || biz.modules.booking !== false));
  }
  function escH(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var DOC_TITLES = { doc_who_we_call: 'Наши клиенты', doc_rules_and_goals: 'Правила записи и цели' };
  var DOC_LABELS = { doc_who_we_call: 'Наши клиенты — кто к нам ходит и чего ждёт' };

  function applyDocTitles() {
    if (typeof DOC_DEFS !== 'undefined') {
      DOC_DEFS.forEach(function (d) { if (DOC_TITLES[d.key]) d.title = DOC_TITLES[d.key]; });
    }
    document.querySelectorAll('.doc-tab[data-tab]').forEach(function (t) {
      var k = t.getAttribute('data-tab');
      if (DOC_TITLES[k]) t.textContent = DOC_TITLES[k];
    });
    Object.keys(DOC_LABELS).forEach(function (k) {
      var lab = document.querySelector('[data-pane="' + k + '"] .form-label');
      if (lab) lab.textContent = DOC_LABELS[k];
    });
  }

  // ── слева: записи сегодня ──
  function statCard() {
    var num = document.getElementById('stat-calls');
    return num ? num.closest('.stat-card') : null;
  }
  function setupStatCard() {
    var card = statCard();
    if (!card || card.getAttribute('data-salon')) return;
    card.setAttribute('data-salon', '1');
    card.setAttribute('onclick', "location.href='/static/booking.html'");
    card.title = 'Открыть журнал записи';
    var ic = card.querySelector('.stat-icon i');
    if (ic) ic.className = 'far fa-calendar';
    var lab = card.querySelector('.stat-label');
    if (lab) lab.textContent = 'Записи сегодня';
    var cl = document.getElementById('stat-contacts');
    var cLab = cl && cl.closest('.stat-card') && cl.closest('.stat-card').querySelector('.stat-label');
    if (cLab) cLab.textContent = 'Клиенты';
    // Карточка «Записи сегодня» — над «Последними звонками»
    var calls = document.getElementById('recent-calls');
    var callsCard = calls && calls.closest('.card');
    if (callsCard && !document.getElementById('salon-today')) {
      var box = document.createElement('div');
      box.className = 'card';
      box.innerHTML = '<div class="card-head"><div class="card-head-title"><i class="far fa-calendar"></i> Записи на сегодня</div>' +
        '<a class="hint" href="/static/booking.html" title="Открыть журнал"><i class="fas fa-arrow-right"></i></a></div>' +
        '<div id="salon-today"><div class="empty">Загружаем…</div></div>';
      callsCard.parentNode.insertBefore(box, callsCard);
    }
  }

  function hm(iso) {
    try {
      var d = new Date(iso);
      return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bishkek' });
    } catch (e) { return ''; }
  }

  window.loadSalonToday = function () {
    var token = (typeof getToken === 'function') ? getToken() : null;
    if (!token) return;
    fetch('/api/booking/overview?days=1', { headers: { Authorization: 'Bearer ' + token } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var rows = (d.bookings || []).filter(function (b) { return b.status !== 'cancelled'; });
        var came = rows.filter(function (b) { return b.status === 'arrived' || b.status === 'completed'; }).length;
        var noShow = rows.filter(function (b) { return b.status === 'no_show'; }).length;
        var ahead = rows.filter(function (b) { return (b.status === 'booked' || b.status === 'confirmed') && new Date(b.start) > new Date(); }).length;
        var num = document.getElementById('stat-calls');
        if (num) num.textContent = rows.length;
        var tr = document.getElementById('stat-calls-trend');
        if (tr) tr.innerHTML = 'пришли <b>' + came + '</b> · впереди <b>' + ahead + '</b>' + (noShow ? ' · не пришли <b>' + noShow + '</b>' : '');
        var box = document.getElementById('salon-today');
        if (!box) return;
        if (!rows.length) { box.innerHTML = '<div class="empty">Сегодня записей нет</div>'; return; }
        box.innerHTML = rows.slice(0, 8).map(function (b) {
          var who = b.client_name || 'Клиент';
          var sub = [b.service, b.resource].filter(Boolean).join(' · ');
          return '<div class="call-item"><div class="avatar">' + escH(hm(b.start)) + '</div><div class="call-info">' +
            '<div class="call-name">' + escH(who) + '</div><div class="call-meta">' + escH(sub || b.status_title || '') +
            '</div></div></div>';
        }).join('') + (rows.length > 8 ? '<div class="empty">и ещё ' + (rows.length - 8) + ' — в журнале</div>' : '');
      })
      .catch(function () { /* журнал обновится при следующем заходе */ });
  };

  function apply(rerender) {
    if (window.AGENT_SALON) return;
    window.AGENT_SALON = true;
    document.documentElement.classList.add('salon-mode');
    if (document.body) document.body.classList.add('salon-mode');
    applyDocTitles();
    setupStatCard();
    if (rerender) {
      if (typeof window.renderAgentTabs === 'function') window.renderAgentTabs();
      if (document.getElementById('chat-welcome') && typeof renderWelcome === 'function') renderWelcome();
      if (typeof renderDocsGrid === 'function' && typeof agentData !== 'undefined' && agentData) {
        try { renderDocsGrid(); } catch (e) { /* ignore */ }
      }
      window.loadSalonToday();
    }
  }

  if (isSalon(readBiz())) apply(false);

  document.addEventListener('DOMContentLoaded', function () {
    if (window.AGENT_SALON) {
      document.body.classList.add('salon-mode');
      applyDocTitles();
      setupStatCard();
      window.loadSalonToday();
      setInterval(window.loadSalonToday, 120000);
      return;
    }
    // Кэша профиля нет (первый заход) — спросим сервер и переоденем страницу, если это салон
    var token = (typeof getToken === 'function') ? getToken() : null;
    if (!token) return;
    fetch('/api/users/me', { headers: { Authorization: 'Bearer ' + token } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (me) {
        if (!me || !me.business) return;
        try { localStorage.setItem(KEY, JSON.stringify(me.business)); } catch (e) { /* ignore */ }
        if (isSalon(me.business)) apply(true);
      })
      .catch(function () { /* ignore */ });
  });
})();
