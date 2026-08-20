/* ============================================================================
 * agent/dashboard.js — Раскладка/drawer и дашборд: статистика, последние звонки, задачи
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ── Мобильный drawer + перенос колонок между раскладками ──
const MOBILE_MQ = window.matchMedia('(max-width: 1100px)');
let _migrations = null;

function _collectMigrations(){
  if(_migrations) return _migrations;
  const make = (el, slot) => {
    if(!el) return null;
    const anchor = document.createComment('mig');   // невидимый якорь исходной позиции
    el.parentNode.insertBefore(anchor, el);
    return { el, slot, anchor };
  };
  _migrations = [
    make(document.querySelector('.toggle-wrap'), 'drawer-toggle-slot'),
    make(document.getElementById('sub-badge'), 'drawer-badge-slot'),
    make(document.getElementById('delete-agent-btn'), 'drawer-actions'),
    make(document.getElementById('profile-btn'), 'drawer-actions'),
    make(document.querySelector('.col-left'), 'drawer-body'),
    make(document.querySelector('.col-right'), 'drawer-body'),
  ].filter(Boolean);
  return _migrations;
}

function applyLayout(isMobile){
  const migs = _collectMigrations();
  if(isMobile){
    migs.forEach(m => {
      const slot = document.getElementById(m.slot);
      if(slot && m.el.parentNode !== slot) slot.appendChild(m.el);
    });
  } else {
    closeDrawer();
    migs.forEach(m => {
      if(m.el.parentNode !== m.anchor.parentNode) m.anchor.parentNode.insertBefore(m.el, m.anchor);
    });
  }
}

function openDrawer(){
  document.getElementById('drawer-overlay')?.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}
function closeDrawer(){
  document.getElementById('drawer-overlay')?.classList.add('hidden');
  document.body.style.overflow = '';
}


// ════════════════ DASHBOARD ════════════════
function showDashboard(){
  document.getElementById('loading-screen').classList.add('hidden');
  document.getElementById('wizard-overlay').classList.add('hidden');
  document.getElementById('top-nav').style.display='flex';
  document.getElementById('main-layout').style.display='flex';
  document.getElementById('app-footer').style.display='flex';

  renderAgentHeader();
  renderDocsGrid();
  loadModels();
  loadStats();
  loadRecentCalls();
  loadAgentHistory();
  loadTasks();
  loadPhoneNumbers();
  loadTelegramStatus();
  loadKnowledgeBaseStatus();
  loadConnectors();
  loadTgAccount();
  loadCredits();
  if(creditsTimer) clearInterval(creditsTimer);
  creditsTimer = setInterval(loadCredits, 30000);
  const msgs = document.getElementById('chat-messages');
  if(!msgs.children.length){
    renderWelcome();
  }
}

function renderAgentHeader(){
  const active = !!agentData.is_active;
  document.getElementById('nav-agent-name').textContent = agentData.name || 'Агент';
  const ns = document.getElementById('nav-agent-status');
  ns.classList.toggle('off', !active);
  ns.querySelector('span').textContent = active ? 'Агент активен' : 'Агент неактивен';

  document.getElementById('active-toggle').checked = active;
  const tl = document.getElementById('toggle-label');
  tl.textContent = active ? 'Активен' : 'Неактивен';
  tl.classList.toggle('on', active);

  document.getElementById('agent-avatar').textContent = (agentData.name||'A').trim().charAt(0).toUpperCase();
  document.getElementById('agent-card-name').textContent = agentData.name || 'Агент';
  const cs = document.getElementById('agent-card-status');
  cs.classList.toggle('off', !active);
  cs.querySelector('span').textContent = active ? 'Активен' : 'Неактивен';
  const typeNames = { gemini:'Gemini', openai:'OpenAI', cartesia:'Cartesia', yandex:'Yandex', cascade:'Cascade', fish:'Fish' };
  document.getElementById('agent-type-badge').textContent = typeNames[agentData.assistant_type] || 'Voice';
  const id = agentData.id || '';
  document.getElementById('agent-id').textContent = 'ID агента: ' + (id.length>16 ? id.slice(-16) : id);
}

const DOC_DEFS = [
  { key:'doc_who_am_i', title:'Кто мы', ic:'fa-building', color:'#7C3AED', bg:'#F3E8FF' },
  { key:'doc_who_we_call', title:'Кому звоним', ic:'fa-bullseye', color:'#2563EB', bg:'#DBEAFE' },
  { key:'doc_how_we_talk', title:'Как говорим', ic:'fa-comments', color:'#059669', bg:'#D1FAE5' },
  { key:'doc_what_we_offer', title:'Что предлагаем', ic:'fa-box-open', color:'#EA580C', bg:'#FFEDD5' },
  { key:'doc_rules_and_goals', title:'Правила и цели', ic:'fa-flag', color:'#DC2626', bg:'#FEE2E2' },
];
function renderDocsGrid(){
  const grid = document.getElementById('docs-grid');
  grid.innerHTML = DOC_DEFS.map((d,i) => {
    const v = (agentData[d.key]||'').trim();
    const prev = v ? esc(v.slice(0,80)) : 'Нажмите чтобы заполнить';
    return `<div class="doc-card ${i===4?'wide':''}" onclick="openEditModal('${d.key}')">
      <div class="doc-card-ic" style="background:${d.bg};color:${d.color}"><i class="fas ${d.ic}"></i></div>
      <div class="doc-card-title">${d.title}</div>
      <div class="doc-card-prev">${prev}</div>
    </div>`;
  }).join('');
}

async function loadStats(){
  try{
    const r = await apiFetch(API + '/stats');
    if(!r || r.status!==200) return;
    const s = await r.json();
    document.getElementById('stat-calls').textContent = s.total_calls||0;
    document.getElementById('stat-contacts').textContent = s.total_contacts||0;
  }catch(e){}
}

async function loadRecentCalls(){
  try{
    const r = await apiFetch(API + '/calls?limit=5&offset=0');
    if(!r || r.status!==200) return;
    const data = await r.json();
    const el = document.getElementById('recent-calls');
    if(!data.calls || !data.calls.length){ el.innerHTML='<div class="empty">Нет звонков</div>'; return; }
    el.innerHTML = data.calls.slice(0,5).map(c => `
      <div class="call-item">
        <div class="avatar">${esc((c.contact_name||'?').trim().charAt(0).toUpperCase())}</div>
        <div class="call-info">
          <div class="call-name">${esc(c.contact_name||'—')}</div>
          <div class="call-meta"><span class="call-dot ${c.status==='answered'?'answered':'no_answer'}"></span> <i class="fas ${c.direction==='inbound'?'fa-arrow-down':'fa-arrow-up'}" title="${directionRu(c.direction)}" style="color:${c.direction==='inbound'?'#0891B2':'#7C3AED'}"></i> ${decisionRu(c.post_call_decision)} · ${fmtDate(c.started_at)}</div>
        </div>
        <div class="call-phone-ic"><i class="fas ${c.direction==='inbound'?'fa-phone-volume':'fa-phone'}"></i></div>
      </div>`).join('');
  }catch(e){}
}

async function loadTasks(){
  try{
    const r = await apiFetch(API + '/tasks?limit=5&status=scheduled');
    if(!r || r.status!==200){ return; }
    const data = await r.json();
    const el = document.getElementById('tasks-list');
    if(!data.tasks || !data.tasks.length){ el.innerHTML='<div class="empty">Нет запланированных задач</div>'; return; }
    el.innerHTML = data.tasks.map(t => {
      const parts = t.scheduled_time ? mskParts(t.scheduled_time) : null;
      const day = parts ? parts.day : '—';
      const mon = parts ? parts.mon : '';
      const tm = parts ? parts.time : '';
      const rel = relTime(t.scheduled_time);
      return `<div class="task-item">
        <div class="task-date" title="${tm} МСК"><div class="d">${day}</div><div class="m">${mon}</div><div class="t">${tm}</div></div>
        <div class="task-body">
          <div class="task-title">${esc(t.title||'Задача')} ${taskChannelBadge(t.channel)}</div>
          ${t.description?`<div class="task-desc">${esc(t.description)}</div>`:''}
          <div class="task-foot">
            <span class="task-contact">${esc(t.contact_name||'')}</span>
            <span class="task-when ${rel.soon?'soon':''}">${rel.text}</span>
          </div>
        </div>
      </div>`;
    }).join('');
  }catch(e){
    document.getElementById('tasks-list').innerHTML='<div class="empty">Нет задач</div>';
  }
}


