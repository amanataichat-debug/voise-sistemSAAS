/* ============================================================================
 * agent/panels.js — Шторки «Работа» (слева) и «Агент» (справа)
 * Часть страницы /static/agent.html (агент обзвона VoksiAI).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Подключается ПЕРЕД init.js.
 *
 * По умолчанию обе панели открыты. Переключают: кнопки в топбаре, шевроны в
 * заголовках панелей, «язычки» по краям чата (любой элемент с data-panel="left|right")
 * и клавиши [ и ]. Состояние — localStorage.agent_panels_v1. На мобильной раскладке
 * (≤1100px) панели живут в drawer, шторки не действуют.
 * ========================================================================== */

const PANELS_KEY = 'agent_panels_v1';
let panelsState = { left: true, right: true };

function _readPanels(){
  try{
    const saved = JSON.parse(localStorage.getItem(PANELS_KEY) || '{}');
    panelsState = { left: saved.left !== false, right: saved.right !== false };
  }catch(e){ panelsState = { left: true, right: true }; }
}

function applyPanels(){
  const layout = document.getElementById('main-layout');
  if(!layout) return;
  layout.classList.toggle('left-closed', !panelsState.left);
  layout.classList.toggle('right-closed', !panelsState.right);
  document.getElementById('panel-btn-left')?.classList.toggle('is-on', panelsState.left);
  document.getElementById('panel-btn-right')?.classList.toggle('is-on', panelsState.right);
  document.querySelectorAll('.handle').forEach(h => {
    const side = h.dataset.panel;
    const open = panelsState[side];
    const name = side === 'left' ? '«Работа»' : '«Агент»';
    h.title = (open ? 'Скрыть панель ' : 'Показать панель ') + name;
    h.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
}

function togglePanel(side){
  if(side !== 'left' && side !== 'right') return;
  if(typeof MOBILE_MQ !== 'undefined' && MOBILE_MQ.matches){ openDrawer(); return; }
  panelsState[side] = !panelsState[side];
  try{ localStorage.setItem(PANELS_KEY, JSON.stringify(panelsState)); }catch(e){}
  applyPanels();
}

function initPanels(){
  _readPanels();
  applyPanels();
  document.addEventListener('click', (e) => {
    const el = e.target.closest ? e.target.closest('[data-panel]') : null;
    if(!el) return;
    e.preventDefault();
    togglePanel(el.dataset.panel);
  });
  document.addEventListener('keydown', (e) => {
    if(e.key !== '[' && e.key !== ']') return;
    if(e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    if(document.querySelector('.modal-overlay:not(.hidden)')) return;
    togglePanel(e.key === '[' ? 'left' : 'right');
  });
}
