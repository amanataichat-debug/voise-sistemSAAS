/* ============================================================================
 * agent/pipeline.js — Воронка-канбан с drag&drop → /api/agent/pipeline, PATCH /contacts/{id}/status
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 *
 * Поведение:
 * - Один раз грузим список контактов в память (plContacts).
 * - DnD оптимистичный: карточка переезжает в новую колонку мгновенно, PATCH
 *   уходит в фоне. На ошибке — карточка возвращается на место, тост.
 * - Поиск по имени / телефону / компании фильтрует колонки без запроса.
 * ========================================================================== */

// ── Состояние ──
let plContacts = [];        // массив контактов из бэка (один раз на открытие)
let plQuery = '';           // текст поиска
let plDragId = null;
let plDragFromStage = null; // откуда взяли карточку (для отката)

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
    if(!r){ wrap.innerHTML = '<div class="empty">Нет авторизации</div>'; return; }
    if(r.status !== 200){
      let msg = 'Ошибка ' + r.status;
      try{ const j = await r.clone().json(); msg += ': ' + (j.detail || j.message || ''); }catch(e){}
      wrap.innerHTML = `<div class="empty">${esc(msg)}</div>`;
      return;
    }
    const data = await r.json();
    plContacts = (data.contacts || []).map(c => ({ ...c }));
    renderPipeline();
  }catch(e){ wrap.innerHTML = '<div class="empty">Ошибка сети</div>'; }
}

function renderPipeline(){
  const wrap = document.getElementById('pipeline-board');
  if(!wrap) return;
  const q = plQuery.trim().toLowerCase();
  const match = c => {
    if(!q) return true;
    return (c.name || '').toLowerCase().includes(q)
        || (c.phone || '').toLowerCase().includes(q)
        || (c.company || '').toLowerCase().includes(q);
  };
  const groups = {};
  STAGE_ORDER.forEach(s => groups[s] = []);
  plContacts.forEach(c => {
    const key = STAGE_ORDER.includes(c.status) ? c.status : 'active';
    if(match(c)) groups[key].push(c);
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
          <span class="pl-count" id="pl-count-${s}">${groups[s].length}</span>
        </div>
        <div class="pl-col-body" id="pl-body-${s}">${cards}</div>
      </div>`;
  }).join('');
}

function pipelineCard(c){
  return `
    <div class="pl-card" draggable="true" data-id="${c.id}" data-stage="${c.status || 'new'}"
         ondragstart="plDragStart(event)" ondragend="plDragEnd(event)"
         onclick="openContactDetailsModal('${c.id}')">
      <div style="font-weight:600;font-size:12.5px">${esc(c.name || c.phone)}${aiPauseBadge(c)}</div>
      <div style="font-size:11px;color:var(--muted)">${esc(c.phone)}</div>
      ${c.company ? `<div style="font-size:11px;color:var(--muted)">${esc(c.company)}</div>` : ''}
      <div style="font-size:10.5px;color:var(--hint);margin-top:4px">Попыток: ${c.attempts_count || 0}${c.last_called_at ? ' · ' + fmtDate(c.last_called_at) : ''}</div>
    </div>`;
}

// ── Поиск ──
function plSearch(e){
  plQuery = e.target.value || '';
  renderPipeline();
}

// ── DnD ──
function plDragStart(e){
  plDragId = e.currentTarget.dataset.id;
  plDragFromStage = e.currentTarget.dataset.stage;
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
  const toStage = col.dataset.stage;
  const id = plDragId; const fromStage = plDragFromStage;
  plDragId = null; plDragFromStage = null;
  if(!id || !toStage || toStage === fromStage) return;

  // Оптимистичный перенос: карточка едет сразу
  const card = document.querySelector(`.pl-card[data-id="${id}"]`);
  const toBody = document.getElementById('pl-body-' + toStage);
  const fromBody = document.getElementById('pl-body-' + fromStage);
  if(card && toBody){
    const emptyInTarget = toBody.querySelector('.pl-empty');
    if(emptyInTarget) emptyInTarget.remove();
    toBody.appendChild(card);
    card.dataset.stage = toStage;
    bumpCount(toStage, +1);
    bumpCount(fromStage, -1);
    if(fromBody && !fromBody.querySelector('.pl-card')){
      fromBody.insertAdjacentHTML('beforeend', '<div class="pl-empty">Пусто</div>');
    }
    // Обновим состояние в памяти
    const c = plContacts.find(x => String(x.id) === String(id));
    if(c) c.status = toStage;
  }

  // Фоновый PATCH без ре-рендера всей доски
  const ok = await _patchContactStage(id, toStage);
  if(!ok){
    // Откатываем карточку обратно
    if(card && fromBody){
      const emptySrc = fromBody.querySelector('.pl-empty');
      if(emptySrc) emptySrc.remove();
      fromBody.appendChild(card);
      card.dataset.stage = fromStage;
      bumpCount(fromStage, +1);
      bumpCount(toStage, -1);
      if(toBody && !toBody.querySelector('.pl-card')){
        toBody.insertAdjacentHTML('beforeend', '<div class="pl-empty">Пусто</div>');
      }
      const c = plContacts.find(x => String(x.id) === String(id));
      if(c) c.status = fromStage;
    }
  } else {
    if(typeof loadStats === 'function') loadStats();
  }
}

function bumpCount(stage, delta){
  const el = document.getElementById('pl-count-' + stage);
  if(!el) return;
  const n = Math.max(0, (parseInt(el.textContent, 10) || 0) + delta);
  el.textContent = n;
}

// Прямой PATCH без тостов (ошибка покажется в UI через откат)
async function _patchContactStage(contactId, stage){
  try{
    const r = await apiFetch(API + '/contacts/' + contactId + '/status', {
      method:'PATCH', body: JSON.stringify({ status: stage }),
    });
    if(r && r.status === 200) return true;
    showToast('Не удалось сменить стадию', 'error');
    return false;
  }catch(e){
    showToast('Ошибка сети', 'error');
    return false;
  }
}
