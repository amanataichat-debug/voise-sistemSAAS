/* ============================================================================
 * agent/stages.js — Настраиваемые стадии воронки (CRM v2)
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): всё, что нужно разметке и другим
 * модулям, выставлено на window.
 *
 * Как работает:
 * - На старте (DOMContentLoaded) загружаем стадии с бэка (/pipeline/stages).
 *   Если у агента есть кастомный набор — заменяем ПО МЕСТУ (мутируем, не
 *   перезаписываем ссылку) STAGE_META и STAGE_ORDER из core.js. Этого
 *   достаточно, чтобы pipeline / contacts / analytics их увидели — они
 *   читают эти переменные в runtime.
 * - Редактор стадий (window.openStagesEditor): модалка с перетаскиванием
 *   стадий, цветом и типом (open/won/lost). «Сохранить» → PUT, «Сбросить
 *   на дефолт» → DELETE.
 * ========================================================================== */

(function(){
  'use strict';
  const STAGES_API = API + '/pipeline/stages';
  // Запоминаем KIND каждого ключа (нужно для редактора: open/won/lost).
  window.STAGE_KINDS = window.STAGE_KINDS || {};
  // Текущий набор стадий (мутируем после fetch — ссылка STAGE_* не меняется).
  window.CURRENT_STAGES = window.CURRENT_STAGES || [];

  function applyStages(stages){
    if(!Array.isArray(stages) || !stages.length) return;
    // Полностью заменяем содержимое, сохраняя ссылки
    STAGE_ORDER.length = 0;
    Object.keys(STAGE_META).forEach(k => {
      // Оставляем "calling" (легаси) — чтобы старые статусы не терялись.
      if(k !== 'calling') delete STAGE_META[k];
    });
    Object.keys(window.STAGE_KINDS).forEach(k => delete window.STAGE_KINDS[k]);
    stages.forEach(s => {
      STAGE_ORDER.push(s.key);
      STAGE_META[s.key] = { label: s.label, color: s.color };
      window.STAGE_KINDS[s.key] = s.kind || 'open';
    });
    window.CURRENT_STAGES = stages.slice();
  }

  window.loadAgentStages = async function loadAgentStages(){
    try{
      const r = await apiFetch(STAGES_API);
      if(!r || r.status !== 200) return;
      const data = await r.json();
      applyStages(data.stages || []);
      // Если воронка открыта — обновим доску, чтобы колонки сразу встали
      if(typeof renderPipeline === 'function' && document.getElementById('pipeline-board')){
        try { renderPipeline(); } catch(e){}
      }
    }catch(e){ /* silent */ }
  };

  // ── Редактор стадий ─────────────────────────────────────────────────
  const PRESET_COLORS = ['#6B7280','#3B82F6','#10B981','#EF4444','#F59E0B','#8B5CF6','#EC4899','#06B6D4','#F97316','#14B8A6','#1F2937'];
  let stagesDraft = []; // работаем с копией, применяем на «Сохранить»

  window.openStagesEditor = function openStagesEditor(){
    stagesDraft = (window.CURRENT_STAGES.length ? window.CURRENT_STAGES : STAGE_ORDER.map(k => ({
      key: k, label: STAGE_META[k].label, color: STAGE_META[k].color, kind: window.STAGE_KINDS[k] || 'open',
    }))).map(s => ({ ...s }));
    renderStagesEditor();
    document.getElementById('stages-editor-overlay').classList.remove('hidden');
  };
  window.closeStagesEditor = function(){ document.getElementById('stages-editor-overlay').classList.add('hidden'); };

  function _slug(label){
    const base = (label || '').toLowerCase()
      .replace(/[^a-z0-9а-я_]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
    const translit = base
      .replace(/а/g,'a').replace(/б/g,'b').replace(/в/g,'v').replace(/г/g,'g').replace(/д/g,'d')
      .replace(/е/g,'e').replace(/ж/g,'zh').replace(/з/g,'z').replace(/и/g,'i').replace(/й/g,'y')
      .replace(/к/g,'k').replace(/л/g,'l').replace(/м/g,'m').replace(/н/g,'n').replace(/о/g,'o')
      .replace(/п/g,'p').replace(/р/g,'r').replace(/с/g,'s').replace(/т/g,'t').replace(/у/g,'u')
      .replace(/ф/g,'f').replace(/х/g,'h').replace(/ц/g,'c').replace(/ч/g,'ch').replace(/ш/g,'sh')
      .replace(/щ/g,'sch').replace(/ъ/g,'').replace(/ы/g,'y').replace(/ь/g,'').replace(/э/g,'e')
      .replace(/ю/g,'yu').replace(/я/g,'ya');
    return translit.slice(0, 40) || 'stage';
  }
  function _uniqueKey(base, existing){
    let k = _slug(base), i = 2;
    const set = new Set(existing);
    while(set.has(k)){ k = _slug(base) + '_' + i; i++; }
    return k;
  }

  function renderStagesEditor(){
    const body = document.getElementById('stages-editor-body');
    if(!body) return;
    body.innerHTML = `
      <div class="se-note">
        <i class="fas fa-info-circle"></i>
        Стадии показываются колонками в канбане. Тип стадии: <b>«Открыта»</b> — клиент в работе,
        <b>«Успех»</b> — закрытая сделка (сумма идёт в выручку), <b>«Отказ»</b> — потеряли.
      </div>
      <div class="se-list" id="se-list">
        ${stagesDraft.map((s, i) => _stageRow(s, i)).join('')}
      </div>
      <button class="btn btn-secondary btn-sm" style="margin-top:12px" onclick="seAddStage()">
        <i class="fas fa-plus"></i> Добавить стадию
      </button>`;
  }
  function _stageRow(s, i){
    const colors = PRESET_COLORS.map(c =>
      `<button class="se-color ${s.color.toLowerCase() === c.toLowerCase() ? 'is-active' : ''}" style="background:${c}" onclick="seSetColor(${i},'${c}')" title="${c}"></button>`
    ).join('');
    return `
      <div class="se-row" data-i="${i}">
        <div class="se-grab" title="Перетащить"><i class="fas fa-grip-vertical"></i></div>
        <input type="text" class="form-input se-label" value="${esc(s.label)}" oninput="seSetLabel(${i}, this.value)" placeholder="Название стадии">
        <select class="form-input se-kind" onchange="seSetKind(${i}, this.value)">
          <option value="open" ${s.kind === 'open' ? 'selected' : ''}>Открыта</option>
          <option value="won" ${s.kind === 'won' ? 'selected' : ''}>Успех</option>
          <option value="lost" ${s.kind === 'lost' ? 'selected' : ''}>Отказ</option>
        </select>
        <div class="se-colors">${colors}</div>
        <div class="se-move">
          <button class="icon-btn" onclick="seMove(${i}, -1)" title="Выше"><i class="fas fa-chevron-up"></i></button>
          <button class="icon-btn" onclick="seMove(${i}, +1)" title="Ниже"><i class="fas fa-chevron-down"></i></button>
        </div>
        <button class="icon-btn se-del" onclick="seDelete(${i})" title="Удалить" ${stagesDraft.length <= 2 ? 'disabled' : ''}><i class="far fa-trash-can"></i></button>
      </div>`;
  }
  window.seSetLabel = function(i, v){ stagesDraft[i].label = v; };
  window.seSetKind  = function(i, v){ stagesDraft[i].kind = v; };
  window.seSetColor = function(i, c){ stagesDraft[i].color = c; renderStagesEditor(); };
  window.seMove = function(i, dir){
    const j = i + dir;
    if(j < 0 || j >= stagesDraft.length) return;
    const tmp = stagesDraft[i]; stagesDraft[i] = stagesDraft[j]; stagesDraft[j] = tmp;
    renderStagesEditor();
  };
  window.seDelete = function(i){
    if(stagesDraft.length <= 2){ showToast('Минимум 2 стадии', 'error'); return; }
    stagesDraft.splice(i, 1);
    renderStagesEditor();
  };
  window.seAddStage = function(){
    const existing = stagesDraft.map(s => s.key);
    stagesDraft.push({ key: _uniqueKey('new_stage', existing), label: 'Новая стадия', color: PRESET_COLORS[stagesDraft.length % PRESET_COLORS.length], kind: 'open' });
    renderStagesEditor();
  };

  window.saveStagesEditor = async function(){
    // На сохранение генерируем недостающие ключи из labels; для существующих keys оставляем прежние.
    const existing = new Set();
    const payload = stagesDraft.map(s => {
      let k = (s.key || '').toString().trim().toLowerCase();
      if(!k || !/^[a-z0-9_]+$/.test(k)){
        k = _uniqueKey(s.label, Array.from(existing));
      } else if(existing.has(k)){
        k = _uniqueKey(s.label, Array.from(existing));
      }
      existing.add(k);
      return { key: k, label: s.label || k, color: s.color || '#6B7280', kind: s.kind || 'open' };
    });
    try{
      const r = await apiFetch(STAGES_API, { method: 'PUT', body: JSON.stringify({ stages: payload }) });
      if(!r || r.status !== 200){
        const err = await r?.json().catch(()=>({}));
        showToast(errText(err.detail ?? err.message ?? 'Не удалось сохранить'), 'error'); return;
      }
      const data = await r.json();
      applyStages(data.stages || []);
      showToast('Стадии сохранены', 'success');
      closeStagesEditor();
      if(typeof renderPipeline === 'function') renderPipeline();
    }catch(e){ showToast('Ошибка сети', 'error'); }
  };

  window.resetStagesToDefault = async function(){
    if(!confirm('Сбросить стадии к дефолтным (Новый, В работе, Успех, Отказ, Не звонить)?')) return;
    try{
      const r = await apiFetch(STAGES_API, { method: 'DELETE' });
      if(!r || r.status !== 200){ showToast('Ошибка сброса', 'error'); return; }
      const data = await r.json();
      applyStages(data.stages || []);
      showToast('Стадии сброшены к дефолтным', 'success');
      closeStagesEditor();
      if(typeof renderPipeline === 'function') renderPipeline();
    }catch(e){ showToast('Ошибка сети', 'error'); }
  };

  // Загружаем стадии после того, как агент выбран (currentAgentId есть)
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => window.loadAgentStages(), 1600);
  });
})();
