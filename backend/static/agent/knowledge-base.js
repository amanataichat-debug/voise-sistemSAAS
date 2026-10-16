/* ============================================================================
 * agent/knowledge-base.js — карточка «Знания и файлы» в шторке агента.
 *
 * Сами знания и библиотека файлов живут на отдельной странице
 * /static/agent-knowledge.html (ветка 1610-kb-calls): раньше это были две
 * маленькие модалки рядом, хотя это одно хозяйство — один и тот же файл агент
 * и отправляет клиенту, и использует как источник ответов. Здесь осталась
 * только сводка: сколько чего в базе и сколько агент знает наизусть на звонке.
 *
 * Сводка «наизусть» приходит из поля voice ответа /api/agent/knowledge —
 * это тот же расчёт, который уходит в промпт звонка
 * (backend/services/agent_voice_context.py), поэтому кабинет и трубка не
 * расходятся по построению.
 *
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

const KB_API = API + '/knowledge';

let knowledgeBaseState = null;   // сводка для карточки в шторке

async function loadKnowledgeBaseStatus(){
  try{
    const r = await apiFetch(KB_API);
    knowledgeBaseState = (r && r.status === 200) ? await r.json() : null;
  }catch(e){ knowledgeBaseState = null; }
  renderKnowledgeBaseBlock();
}

/** Открыть страницу знаний текущего агента. */
function openKnowledgePage(){
  const id = (typeof currentAgentId !== 'undefined' && currentAgentId) ? currentAgentId : '';
  window.location.href = '/static/agent-knowledge.html' + (id ? '?agent_id=' + encodeURIComponent(id) : '');
}

function renderKnowledgeBaseBlock(){
  const el = document.getElementById('kb-status-block');
  if(!el) return;
  const s = knowledgeBaseState;
  const counts = (s && s.counts) || {};
  const voice = (s && s.voice) || {};
  const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0);
  const filesCount = (typeof agentFilesState !== 'undefined' && agentFilesState && agentFilesState.library)
    ? agentFilesState.library.length : 0;

  if(!total && !filesCount){
    el.innerHTML = '<div class="empty">База пустая — на звонке агент не знает о компании ничего</div>';
    return;
  }

  const rows = [
    ['Вопрос — ответ', counts.qa || 0],
    ['О компании', counts.section || 0],
    ['Каталог', counts.product || 0],
    ['Файлы', filesCount],
  ].filter(([, n]) => n > 0);

  // Главное число карточки: сколько знаний агент получает мгновенно, без
  // паузы на поиск. Именно это владелец и хочет видеть одним взглядом.
  const instant = voice.chars || 0;
  el.innerHTML =
    rows.map(([label, n]) => `<div class="exp-row"><span class="lbl">${esc(label)}</span><span class="val">${n}</span></div>`).join('') +
    (instant
      ? `<div class="exp-row"><span class="lbl">Знает наизусть</span><span class="val">${instant} знаков</span></div>`
      : `<div class="empty" style="padding:8px 0">Ничего не уходит в инструкцию — агент будет всё искать</div>`);
}
