/* ============================================================================
 * agent/presence.js — UI Фаза 3: присутствие агента (живой орб + ripple дашборда)
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Подключается из agent.html ПОСЛЕ chat.js и ПЕРЕД init.js.
 *
 * Принцип: слой полностью аддитивный. Орб инъектируется в .chat-header из JS
 * (разметка не трогается). Поведение навешивается поверх существующих
 * sendMessage() и handleStreamEvent() через обёртку глобалов — сами функции
 * чата не редактируются. init.js биндит уже обёрнутые версии (грузится позже).
 * Уважает prefers-reduced-motion (CSS-анимации гаснут глобальным правилом).
 * ========================================================================== */

const PRESENCE_REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let _orbEl = null;

// ── Состояния орба: idle (дышит) / thinking (думает) / speaking (говорит) ──
function _orbSet(state){
  if(_orbEl) _orbEl.dataset.state = state;
}
// Короткий «пинг» орба на каждый вызов инструмента.
function _orbPing(){
  if(!_orbEl || PRESENCE_REDUCED) return;
  _orbEl.classList.remove('ping');
  void _orbEl.offsetWidth;
  _orbEl.classList.add('ping');
  setTimeout(() => _orbEl && _orbEl.classList.remove('ping'), 650);
}

// ── Карта: инструмент оркестратора → какие карточки дашборда «оживить» ──
const TOOL_RIPPLE = {
  trigger_immediate_call:['calls','recent'],
  create_agent_task:['tasks'], bulk_schedule_calls:['tasks'], update_agent_task:['tasks'],
  delete_agent_task:['tasks'], get_agent_tasks:['tasks'], get_upcoming_schedule:['tasks'],
  create_agent_contact:['contacts'], bulk_create_contacts:['contacts'], update_contact_info:['contacts'],
  append_contact_note:['contacts'], move_contact_stage:['contacts'], delete_agent_contact:['contacts'],
  snooze_contact:['contacts'], search_contacts:['contacts'], get_agent_contacts:['contacts'],
  get_contacts_by_stage:['contacts'], get_contact_details:['contacts'], update_contact_memory:['contacts'],
  get_agent_stats:['calls','contacts'],
  get_contact_call_history:['recent'], get_call_transcript:['recent'],
  get_failed_calls:['recent','calls'], get_period_report:['calls'],
  send_telegram_notification:['telegram'],
};

function _rippleTarget(key){
  switch(key){
    case 'calls':    return document.getElementById('stat-calls')?.closest('.stat-card');
    case 'contacts': return document.getElementById('stat-contacts')?.closest('.stat-card');
    case 'recent':   return document.getElementById('recent-calls')?.closest('.card');
    case 'tasks':    return document.getElementById('tasks-list')?.closest('.card');
    case 'telegram': return document.getElementById('telegram-status-block')?.closest('.card');
  }
  return null;
}
function _rippleCard(el){
  if(!el || PRESENCE_REDUCED) return;
  el.classList.remove('card-ripple');
  void el.offsetWidth;
  el.classList.add('card-ripple');
  setTimeout(() => el.classList.remove('card-ripple'), 1000);
}

// ── Реакция орба и дашборда на события стрима чата ──
function _presenceOnStream(ev){
  if(!ev || !ev.type) return;
  if(ev.type === 'start')       _orbSet('thinking');
  else if(ev.type === 'token')  _orbSet('speaking');
  else if(ev.type === 'tool_call'){
    _orbSet('thinking');
    _orbPing();
    const keys = TOOL_RIPPLE[ev.tool];
    if(keys) keys.forEach(k => _rippleCard(_rippleTarget(k)));
  }
  else if(ev.type === 'done' || ev.type === 'error') _orbSet('idle');
}

// ── Инъекция орба в шапку чата (первым флекс-элементом) ──
function _initPresenceOrb(){
  const header = document.querySelector('.chat-header');
  if(!header || document.getElementById('agent-orb')) return;
  const orb = document.createElement('div');
  orb.className = 'agent-orb';
  orb.id = 'agent-orb';
  orb.dataset.state = 'idle';
  orb.setAttribute('aria-hidden', 'true');
  orb.innerHTML = '<span class="orb-core"></span><span class="orb-ring"></span>';
  header.insertBefore(orb, header.firstChild);
  _orbEl = orb;
}

// ── Обёртки глобалов чата (сами функции из chat.js не редактируются) ──
if(typeof handleStreamEvent === 'function'){
  const _origHandleStreamEvent = handleStreamEvent;
  handleStreamEvent = function(ev, st){
    try{ _presenceOnStream(ev); }catch(e){}
    return _origHandleStreamEvent.apply(this, arguments);
  };
}
if(typeof sendMessage === 'function'){
  const _origSendMessage = sendMessage;
  sendMessage = async function(){
    _orbSet('thinking');
    try{ return await _origSendMessage.apply(this, arguments); }
    finally{ _orbSet('idle'); }
  };
}

document.addEventListener('DOMContentLoaded', _initPresenceOrb);
