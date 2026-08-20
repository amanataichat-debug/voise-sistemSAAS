/* ============================================================================
 * agent/knowledge-base.js — База данных агента (векторная БД) → /api/agent/knowledge-base
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ════════════════ БАЗА ДАННЫХ (Pinecone) ════════════════

let knowledgeBaseState = null;

// Лимит размера базы знаний (символов) — зеркалит MAX_KB_CHARS в backend/api/agent.py.
const KB_MAX_CHARS = 200000;

function updateKbCharCounter(){
  const el = document.getElementById('kb-char-counter');
  if(!el) return;
  const len = (document.getElementById('kb-content').value || '').length;
  el.textContent = `${len.toLocaleString('ru-RU')} / ${KB_MAX_CHARS.toLocaleString('ru-RU')}`;
  el.style.color = len > KB_MAX_CHARS ? 'var(--red,#dc2626)' : '';
}

async function loadKnowledgeBaseStatus(){
  try{
    const r = await apiFetch(API + '/knowledge-base');
    if(!r || r.status !== 200){ knowledgeBaseState = null; renderKnowledgeBaseBlock(); return; }
    knowledgeBaseState = await r.json();
  }catch(e){ knowledgeBaseState = null; }
  renderKnowledgeBaseBlock();
}

function kbFormatChars(n){
  n = n || 0;
  if(n >= 1000) return (n/1000).toFixed(n >= 10000 ? 0 : 1) + 'k символов';
  return n + ' символов';
}

function renderKnowledgeBaseBlock(){
  const el = document.getElementById('kb-status-block');
  if(!el) return;
  const s = knowledgeBaseState;
  if(!s || !s.has_knowledge_base){
    el.innerHTML = '<div class="empty">База не создана</div>';
    return;
  }
  const name = s.name ? esc(s.name) : 'База знаний';
  el.innerHTML = `<div style="font-size:13px;color:var(--green-dark,#166534)">`
    + `<i class="fas fa-circle-check"></i> ${name} · ${kbFormatChars(s.char_count)}</div>`;
}

function openKnowledgeBaseModal(){
  document.getElementById('kb-modal-overlay').classList.remove('hidden');
  // Заполняем форму из текущего состояния (если уже загружено).
  const s = knowledgeBaseState;
  const nameEl = document.getElementById('kb-name');
  const contentEl = document.getElementById('kb-content');
  const delBtn = document.getElementById('kb-delete-btn');
  const meta = document.getElementById('kb-modal-meta');
  if(s && s.has_knowledge_base){
    nameEl.value = s.name || '';
    contentEl.value = s.content || '';
    delBtn.style.display = '';
    meta.innerHTML = `<i class="fas fa-database"></i> База создана · ${kbFormatChars(s.char_count)}`
      + (s.updated_at ? ` · обновлено ${esc(fmtDate(s.updated_at))}` : '');
  } else {
    nameEl.value = '';
    contentEl.value = '';
    delBtn.style.display = 'none';
    meta.innerHTML = 'Загрузите текст — агент будет искать по нему во время звонка и в чате с вами.';
  }
  updateKbCharCounter();
}

function closeKnowledgeBaseModal(){
  document.getElementById('kb-modal-overlay').classList.add('hidden');
}

async function saveKnowledgeBase(){
  const btn = document.getElementById('kb-save-btn');
  const content = (document.getElementById('kb-content').value || '').trim();
  const name = (document.getElementById('kb-name').value || '').trim();

  if(!content){ showToast('Добавьте текст для базы знаний', 'error'); return; }
  if(content.length > KB_MAX_CHARS){
    showToast(`База знаний слишком большая: ${content.length.toLocaleString('ru-RU')} символов. Максимум 200 000.`, 'error');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:15px;height:15px;border-width:2px"></div> Сохранение…';
  try{
    const r = await apiFetch(API + '/knowledge-base', {
      method: 'POST',
      body: JSON.stringify({ content, name: name || null }),
    });
    if(r && r.status === 200){
      knowledgeBaseState = await r.json();
      renderKnowledgeBaseBlock();
      closeKnowledgeBaseModal();
      showToast('База знаний сохранена', 'success');
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(errText(err.detail) || 'Ошибка сохранения', 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }

  btn.disabled = false;
  btn.innerHTML = '<i class="fas fa-check"></i> Сохранить';
}

async function deleteKnowledgeBase(){
  if(!confirm('Удалить базу знаний? Это действие необратимо.')) return;

  const btn = document.getElementById('kb-delete-btn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:15px;height:15px;border-width:2px"></div> Удаление…';
  try{
    const r = await apiFetch(API + '/knowledge-base', { method: 'DELETE' });
    if(r && r.status === 200){
      knowledgeBaseState = await r.json();
      renderKnowledgeBaseBlock();
      closeKnowledgeBaseModal();
      showToast('База знаний удалена', 'success');
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(errText(err.detail) || 'Ошибка удаления', 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }

  btn.disabled = false;
  btn.innerHTML = '<i class="fas fa-trash"></i> Удалить базу';
}
