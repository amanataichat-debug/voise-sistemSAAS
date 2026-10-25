/* ============================================================================
 * agent/tabs.js — Полоса вкладок (Диалог · Клиенты · Воронка · Звонки · Задачи)
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): всё, что нужно разметке, выставлено
 * на window (switchTab).
 *
 * Как работает:
 * - По клику на вкладку вешаем на <body> класс .tab-mode + .tab-<id>.
 *   .tab-mode превращает ЦЕЛЕВУЮ модалку в «страницу» (full-screen под
 *   топбаром, без затемнения и рамки) через CSS в agent.css.
 * - Содержимое вкладок — те же модалки, что открывались из левой панели
 *   (contacts-list / pipeline / calls / tasks-cal). Код загрузки не трогаем.
 * - «Диалог» ничего не открывает, просто возвращает в чат.
 * - Esc или крестик в заголовке модалки → возврат в чат.
 * ========================================================================== */

(function(){
  const TABS = [
    { id: 'chat',     label: 'Диалог',   icon: 'fas fa-comments',       modal: null,                           open: null },
    { id: 'clients',  label: 'Клиенты',  icon: 'fas fa-users',          modal: 'contacts-list-modal-overlay',  open: 'openContactsListModal' },
    { id: 'pipeline', label: 'Воронка',  icon: 'fas fa-stream',         modal: 'pipeline-modal-overlay',       open: 'openPipelineModal' },
    { id: 'calls',    label: 'Звонки',   icon: 'fas fa-phone-volume',   modal: 'calls-modal-overlay',          open: 'openCallsModal' },
    { id: 'tasks',    label: 'Задачи',   icon: 'far fa-calendar-check', modal: 'tasks-cal-modal-overlay',      open: 'openTasksCalendar' },
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

  window.switchTab = function(id){
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
  });
})();
