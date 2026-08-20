/* ============================================================================
 * agent/pipeline.js — Воронка-канбан с drag&drop → /api/agent/pipeline, PATCH /contacts/{id}/status
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ── Канбан воронки ──
async function openPipelineModal(){
  document.getElementById('pipeline-modal-overlay').classList.remove('hidden');
  await loadPipeline();
}
function closePipelineModal(){
  document.getElementById('pipeline-modal-overlay').classList.add('hidden');
}

async function loadPipeline(){
  const wrap = document.getElementById('pipeline-board');
  wrap.innerHTML = '<div class="empty">Загрузка...</div>';
  try{
    const r = await apiFetch(API + '/contacts?limit=200&offset=0');
    if(!r || r.status !== 200){ wrap.innerHTML = '<div class="empty">Ошибка</div>'; return; }
    const data = await r.json();
    const groups = {};
    STAGE_ORDER.forEach(s => groups[s] = []);
    // Неизвестные/легаси-статусы (напр. "calling") складываем в "В работе",
    // чтобы ни один контакт не выпал из доски.
    (data.contacts || []).forEach(c => {
      const key = STAGE_ORDER.includes(c.status) ? c.status : 'active';
      groups[key].push(c);
    });
    wrap.innerHTML = STAGE_ORDER.map(s => {
      const m = STAGE_META[s];
      const cards = groups[s].length
        ? groups[s].map(pipelineCard).join('')
        : '<div class="pl-empty">Пусто</div>';
      return `
        <div class="pl-col" data-stage="${s}" ondragover="plDragOver(event)" ondragleave="plDragLeave(event)" ondrop="plDrop(event)">
          <div class="pl-col-head" style="border-top:3px solid ${m.color}">
            <span style="font-weight:600">${m.label}</span>
            <span class="pl-count">${groups[s].length}</span>
          </div>
          <div class="pl-col-body">${cards}</div>
        </div>`;
    }).join('');
  }catch(e){ wrap.innerHTML = '<div class="empty">Ошибка сети</div>'; }
}

function pipelineCard(c){
  return `
    <div class="pl-card" draggable="true" data-id="${c.id}"
         ondragstart="plDragStart(event)" ondragend="plDragEnd(event)"
         onclick="openContactDetailsModal('${c.id}')">
      <div style="font-weight:600;font-size:12.5px">${esc(c.name || c.phone)}</div>
      <div style="font-size:11px;color:var(--muted)">${esc(c.phone)}</div>
      ${c.company ? `<div style="font-size:11px;color:var(--muted)">${esc(c.company)}</div>` : ''}
      <div style="font-size:10.5px;color:var(--hint);margin-top:4px">Попыток: ${c.attempts_count || 0}${c.last_called_at ? ' · ' + fmtDate(c.last_called_at) : ''}</div>
    </div>`;
}

let plDragId = null;
function plDragStart(e){
  plDragId = e.currentTarget.dataset.id;
  e.currentTarget.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
}
function plDragEnd(e){ e.currentTarget.classList.remove('dragging'); }
function plDragOver(e){ e.preventDefault(); e.currentTarget.classList.add('pl-over'); }
function plDragLeave(e){ e.currentTarget.classList.remove('pl-over'); }
async function plDrop(e){
  e.preventDefault();
  const col = e.currentTarget;
  col.classList.remove('pl-over');
  const stage = col.dataset.stage;
  const id = plDragId; plDragId = null;
  if(!id) return;
  const ok = await changeContactStage(id, stage);
  if(ok) loadPipeline();
}


