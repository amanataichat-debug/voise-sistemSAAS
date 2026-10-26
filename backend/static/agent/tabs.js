/* ============================================================================
 * agent/tabs.js — Полоса вкладок (Диалог · Клиенты · Воронка · Звонки · Задачи · Аналитика)
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): всё, что нужно разметке, выставлено
 * на window (switchTab, refreshTabCounts).
 *
 * Как работает:
 * - По клику на вкладку вешаем на <body> класс .tab-mode + .tab-<id>.
 *   .tab-mode превращает ЦЕЛЕВУЮ модалку в «страницу» (full-screen под
 *   топбаром, без затемнения и рамки) через CSS в agent.css.
 * - Содержимое вкладок — те же модалки, что открывались из левой панели
 *   (contacts-list / pipeline / calls / tasks-cal). Код загрузки не трогаем.
 * - «Диалог» ничего не открывает, просто возвращает в чат.
 * - «Аналитика» показывает отдельный блок (#analytics-view, agent/analytics.js).
 * - Esc или крестик в заголовке модалки → возврат в чат.
 * - Счётчики на вкладках обновляются из /api/agent/stats.
 * ========================================================================== */

(function(){
  const TABS = [
    { id: 'chat',      label: 'Диалог',    icon: 'fas fa-comments',       modal: null,                          open: null,                   countKey: null },
    { id: 'clients',   label: 'Клиенты',   icon: 'fas fa-users',          modal: 'contacts-list-modal-overlay', open: 'openContactsListModal', countKey: 'total_contacts' },
    { id: 'pipeline',  label: 'Воронка',   icon: 'fas fa-stream',         modal: 'pipeline-modal-overlay',      open: 'openPipelineModal',    countKey: 'active_contacts' },
    { id: 'calls',     label: 'Звонки',    icon: 'fas fa-phone-volume',   modal: 'calls-modal-overlay',         open: 'openCallsModal',       countKey: 'total_calls' },
    { id: 'tasks',     label: 'Задачи',    icon: 'far fa-calendar-check', modal: 'tasks-cal-modal-overlay',     open: 'openTasksCalendar',    countKey: 'scheduled_tasks' },
    { id: 'analytics', label: 'Аналитика', icon: 'fas fa-chart-column',   modal: 'analytics-view-overlay',      open: 'openAnalyticsView',    countKey: null },
  ];
  const TAB_IDS = TABS.map(t => t.id);
  const TAB_BY_ID = Object.fromEntries(TABS.map(t => [t.id, t]));
  let currentTab = 'chat';

  function renderTabsBar(){
    const host = document.getElementById('agent-tabs');
    if(!host) return;
    host.innerHTML = TABS.map(t => `
      <button class="atab" data-tab="${t.id}" type="button" onclick="switchTab('${t.id}')">
        <i class="${t.icon}"></i><span>${t.label}</span>
        ${t.countKey ? `<span class="acount is-zero" data-count="${t.id}">0</span>` : ''}
      </button>
    `).join('');
    highlight(currentTab);
  }

  function highlight(id){
    const host = document.getElementById('agent-tabs');
    if(!host) return;
    host.querySelectorAll('.atab').forEach(b => {
      b.classList.toggle('is-active', b.dataset.tab === id);
    });
  }

  function closeAllTabModals(){
    for(const t of TABS){
      if(!t.modal) continue;
      const el = document.getElementById(t.modal);
      if(el) el.classList.add('hidden');
    }
  }

  // Записать состояние вкладки в URL (?tab=…) — чтобы F5 вернул на то же место
  // и ссылкой на /static/agent.html?tab=pipeline сразу открывалась воронка.
  // skipHistory=true — когда событие пришло из popstate, иначе получим цикл.
  function pushUrlState(id, skipHistory){
    if(skipHistory) return;
    try{
      const url = new URL(location.href);
      if(id === 'chat') url.searchParams.delete('tab');
      else url.searchParams.set('tab', id);
      // Карточку клиента (?client=…) не трогаем — URL state для неё ставится из contacts.js
      history.replaceState({ tab: id }, '', url.pathname + (url.search ? url.search : '') + url.hash);
    }catch(e){ /* ignore */ }
  }

  window.switchTab = function(id, opts){
    opts = opts || {};
    if(!TAB_IDS.includes(id)) id = 'chat';
    currentTab = id;

    document.body.classList.remove('tab-mode');
    TAB_IDS.forEach(x => document.body.classList.remove('tab-' + x));
    document.body.classList.add('tab-' + id);

    if(id === 'chat'){
      closeAllTabModals();
    } else {
      document.body.classList.add('tab-mode');
      closeAllTabModals();
      const t = TAB_BY_ID[id];
      const fn = t && t.open && window[t.open];
      if(typeof fn === 'function'){
        try { fn(); } catch(e){ console.error('[tabs] open', id, e); }
      } else if(t && t.modal){
        const el = document.getElementById(t.modal);
        if(el) el.classList.remove('hidden');
      }
    }
    highlight(id);
    pushUrlState(id, opts.skipHistory);
    // Освежаем счётчики — особенно если возвращаемся на «Диалог» после работы
    refreshTabCounts();
  };

  // Восстановление вкладки/карточки клиента из URL при загрузке и Back/Forward
  function restoreFromUrl(skipHistory){
    try{
      const p = new URLSearchParams(location.search);
      const tab = p.get('tab') || 'chat';
      const client = p.get('client');
      window.switchTab(tab, { skipHistory: !!skipHistory });
      if(client && typeof window.openContactDetailsModal === 'function'){
        // Небольшая задержка — модалка вкладки должна успеть загрузиться
        setTimeout(() => { try { window.openContactDetailsModal(client); } catch(e){} }, 150);
      }
    }catch(e){}
  }
  window.restoreTabsFromUrl = restoreFromUrl;

  // ── Счётчики на вкладках: /api/agent/stats → acount бейджи ─────────────
  window.refreshTabCounts = async function refreshTabCounts(){
    try{
      if(typeof apiFetch !== 'function') return;
      const r = await apiFetch(API + '/stats');
      if(!r || r.status !== 200) return;
      const s = await r.json();
      TABS.forEach(t => {
        if(!t.countKey) return;
        const el = document.querySelector(`[data-count="${t.id}"]`);
        if(!el) return;
        const n = Number(s[t.countKey] || 0);
        el.textContent = n;
        el.classList.toggle('is-zero', n === 0);
      });
    }catch(e){ /* silent */ }
  };

  // Если модалку закрыли извне (крестиком/кнопкой "Закрыть") — возврат в чат
  function watchModalClose(){
    const obs = new MutationObserver(() => {
      if(!document.body.classList.contains('tab-mode')) return;
      const t = TAB_BY_ID[currentTab];
      if(!t || !t.modal) return;
      const el = document.getElementById(t.modal);
      if(el && el.classList.contains('hidden')){
        window.switchTab('chat');
      }
    });
    TABS.forEach(t => {
      if(!t.modal) return;
      const el = document.getElementById(t.modal);
      if(el) obs.observe(el, { attributes: true, attributeFilter: ['class'] });
    });
  }

  function onEsc(e){
    if(e.key === 'Escape' && document.body.classList.contains('tab-mode')){
      window.switchTab('chat');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderTabsBar();
    watchModalClose();
    document.addEventListener('keydown', onEsc);
    document.body.classList.add('tab-chat');
    // Поддержка Back/Forward: при popstate восстанавливаем вкладку из URL
    window.addEventListener('popstate', () => restoreFromUrl(true));
    // Восстановление из URL — чуть позже, когда /api/agent/* готов отвечать
    // и currentAgentId уже выставлен. Если вкладка не chat, модалка откроется.
    setTimeout(() => {
      if(document.getElementById('top-nav') && document.getElementById('top-nav').style.display !== 'none'){
        restoreFromUrl(true);
      }
    }, 1500);
    // Первый вызов — после того как кабинет точно виден (showDashboard уже
    // прошёл loadStats, но от нашей точки это может быть чуть позже).
    setTimeout(() => window.refreshTabCounts(), 1200);
    // Периодически обновлять, чтобы цифры жили (минута).
    setInterval(() => window.refreshTabCounts(), 60000);
  });
})();
