/* ============================================================================
 * agent/agent-switcher.js — Мультиагент: список/переключение агентов, шапка агента, удаление → /api/agent (/list, /, /create)
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ════════════════ MULTI-AGENT (v3.1) ════════════════
let agentsMax = 3;

async function initAgents(){
  try{
    const r = await apiFetch(API + '/list');
    if(!r || r.status!==200){ showWizard(); return; }
    const data = await r.json();
    agentsList = data.agents || [];
    agentsMax = data.max_agents || 3;
    agentsCanCreateMore = !!data.can_create_more;
    if(agentsList.length === 0){ renderAgentSwitcher(); showWizard(); return; }
    const saved = localStorage.getItem('agent_current_id');
    currentAgentId = (saved && agentsList.some(a => String(a.id)===String(saved))) ? saved : agentsList[0].id;
    localStorage.setItem('agent_current_id', currentAgentId);
    renderAgentSwitcher();
    await loadCurrentAgent();
  }catch(e){ showWizard(); }
}

const AGENT_TYPE_NAMES = { gemini:'Gemini', openai:'OpenAI', cartesia:'Cartesia', yandex:'Yandex', cascade:'Cascade', fish:'Fish' };

function renderAgentSwitcher(){
  const sw = document.getElementById('agent-switch');
  const list = document.getElementById('agent-dropdown-list');
  const foot = document.getElementById('agent-dropdown-foot');
  const pill = document.getElementById('agent-dropdown-pill');
  if(!sw || !list) return;

  // Dropdown показываем, только если есть что переключать или можно создать ещё.
  const canShow = agentsList.length > 1 || agentsCanCreateMore;
  sw.classList.toggle('no-dropdown', !canShow);
  if(!canShow) closeAgentDropdown();

  // Pill с лимитом N/MAX
  const full = agentsList.length >= agentsMax;
  pill.textContent = `${agentsList.length}/${agentsMax}`;
  pill.classList.toggle('full', full);

  // Список агентов
  list.innerHTML = agentsList.map(a => {
    const active = String(a.id)===String(currentAgentId);
    const letter = esc((a.name||'А').trim().charAt(0).toUpperCase());
    const type = AGENT_TYPE_NAMES[a.assistant_type] || 'Voice';
    return `<div class="agent-dd-item ${active?'active':''}" data-id="${esc(String(a.id))}">
      <div class="agent-dd-avatar">${letter}</div>
      <div class="agent-dd-name">${esc(a.name||'Агент')}</div>
      <span class="type-badge">${esc(type)}</span>
      ${active?'<i class="fas fa-check agent-dd-check"></i>':''}
    </div>`;
  }).join('');
  list.querySelectorAll('.agent-dd-item').forEach(el => {
    el.addEventListener('click', () => { closeAgentDropdown(); selectAgent(el.dataset.id); });
  });

  // Подвал: кнопка создания или уведомление о лимите
  const canCreate = agentsCanCreateMore && agentsList.length < agentsMax;
  if(canCreate){
    foot.innerHTML = `<div class="agent-dd-create" id="agent-dd-create-btn">
      <div class="agent-dd-create-ic"><i class="fas fa-plus"></i></div>
      <span>Создать нового агента</span>
    </div>`;
    foot.querySelector('#agent-dd-create-btn').addEventListener('click', () => { closeAgentDropdown(); openNewAgentWizard(); });
  } else {
    foot.innerHTML = `<div class="agent-dd-limit">
      <i class="fas fa-circle-info"></i>
      <span>Лимит агентов исчерпан (${agentsList.length}/${agentsMax}). Удалите одного из существующих агентов, чтобы создать нового.</span>
    </div>`;
  }
}

function openAgentDropdown(){
  document.getElementById('agent-dropdown')?.classList.remove('hidden');
  document.getElementById('agent-switch')?.classList.add('open');
}
function closeAgentDropdown(){
  document.getElementById('agent-dropdown')?.classList.add('hidden');
  document.getElementById('agent-switch')?.classList.remove('open');
}
function toggleAgentDropdown(){
  const dd = document.getElementById('agent-dropdown');
  if(!dd) return;
  if(dd.classList.contains('hidden')) openAgentDropdown(); else closeAgentDropdown();
}


async function loadCurrentAgent(){
  const r = await apiFetch(API + '/');   // withAgentId подставит agent_id
  if(r && r.status===200){
    agentData = await r.json();
    // Каждый вход / перезагрузка страницы = новый диалог: чистим серверную
    // историю чата, чтобы агент не получал прошлый контекст (последние 20 сообщений).
    apiFetch(API + '/chat/clear', { method:'POST' }).catch(()=>{});
    showDashboard();
  }
  else { showWizard(); }
}

async function selectAgent(id){
  if(!id || String(id)===String(currentAgentId)) return;
  currentAgentId = id;
  localStorage.setItem('agent_current_id', id);
  const msgs = document.getElementById('chat-messages');
  if(msgs) msgs.innerHTML = '';   // чат привязан к конкретному агенту
  await loadCurrentAgent();
}

function openNewAgentWizard(){
  if(!agentsCanCreateMore || agentsList.length >= agentsMax){
    showToast(`Достигнут лимит агентов (${agentsMax}).`, 'error');
    return;
  }
  localStorage.removeItem('agent_wizard_v3');  // чистим черновик под нового агента
  showWizard();
}


async function deleteAgent(){
  const agentName = agentData.name || 'Агент';
  const promptMsg =
    `⚠️ Это удалит АГЕНТА И ВСЁ что с ним связано:\n\n` +
    `  • Все контакты агента\n` +
    `  • Всю историю звонков и транскрипты\n` +
    `  • Все запланированные задачи\n` +
    `  • Голосовых ассистентов агента и их диалоги\n\n` +
    `Телефонные номера останутся у вас, но будут отвязаны от агента —\n` +
    `входящие перестанут на него приходить.\n\n` +
    `Это действие необратимо.\n\n` +
    `Чтобы подтвердить — введите имя агента:\n"${agentName}"`;

  const confirmation = prompt(promptMsg);
  if(confirmation === null) return;
  if(confirmation.trim() !== agentName){
    showToast('Имя не совпадает, удаление отменено', 'error');
    return;
  }

  try{
    const r = await apiFetch(API + '/', { method:'DELETE' });
    if(r && r.status===200){
      const data = await r.json();
      const s = data.summary || {};
      const parts = [
        `${s.tasks||0} задач`,
        `${s.voice_assistants||0} голосовых ассистентов`,
      ];
      if(s.phone_numbers_unbound) parts.push(`отвязано номеров: ${s.phone_numbers_unbound}`);
      showToast(`Удалено: ${parts.join(', ')}`, 'success');
      setTimeout(() => location.reload(), 1200);
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(errText(err.detail), 'error');
    }
  }catch(e){
    showToast('Ошибка сети', 'error');
  }
}


