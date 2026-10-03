/* ============================================================================
 * agent/files.js — Файлы агента → /api/agent/files
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Библиотека владельца (прайсы, презентации — агент отправляет их клиентам),
 * документы, созданные агентом (create_document), и вложения клиентов
 * (голосовые/фото/документы из Telegram и Instagram — с расшифровкой).
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

let agentFilesState = { source: 'library', files: [], libraryCount: null, storage: true, maxMb: 20 };

const AGENT_FILE_ICONS = {
  voice: 'fa-microphone', audio: 'fa-microphone', video: 'fa-play',
  image: 'fa-image', document: 'fa-file-lines', other: 'fa-file',
};

const AGENT_FILE_ERRORS = {
  file_too_large: 'файл слишком большой',
  too_long: 'слишком длинное',
  insufficient_credits: 'не обработан — закончились кредиты',
  unsupported_format: 'формат не поддерживается',
  transcription_failed: 'не удалось распознать речь',
  no_text: 'текста не найдено',
  download_failed: 'не удалось скачать',
  storage_not_configured: 'хранилище файлов не настроено на сервере',
  library_limit_reached: 'в библиотеке уже 100 файлов',
  empty_file: 'пустой файл',
};

function agentFileSize(n){
  if(!n) return '';
  if(n < 1024) return n + ' Б';
  if(n < 1024*1024) return Math.round(n/1024) + ' КБ';
  return (n/1024/1024).toFixed(1) + ' МБ';
}

function agentFileDuration(s){
  if(!s) return '';
  s = Math.round(s);
  return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0');
}

async function loadAgentFiles(){
  try{
    const r = await apiFetch(API + '/files?source=library');
    if(!r || r.status !== 200){ agentFilesState.libraryCount = null; renderAgentFilesBlock(); return; }
    const data = await r.json();
    agentFilesState.libraryCount = (data.files || []).length;
    agentFilesState.storage = data.storage_configured !== false;
    agentFilesState.maxMb = data.max_mb || 20;
  }catch(e){ agentFilesState.libraryCount = null; }
  renderAgentFilesBlock();
}

function renderAgentFilesBlock(){
  const el = document.getElementById('files-block');
  if(!el) return;
  const n = agentFilesState.libraryCount;
  if(n === null){ el.innerHTML = '<div class="empty">Не удалось загрузить</div>'; return; }
  if(!n){ el.innerHTML = '<div class="empty">Библиотека пуста</div>'; return; }
  el.innerHTML = `<div style="font-size:13px;color:var(--green-dark,#166534)">`
    + `<i class="fas fa-circle-check"></i> В библиотеке ${n} ${n === 1 ? 'файл' : (n < 5 ? 'файла' : 'файлов')}</div>`;
}

function openAgentFilesModal(){
  document.getElementById('files-modal-overlay').classList.remove('hidden');
  document.getElementById('files-max-mb').textContent = agentFilesState.maxMb;
  switchAgentFilesTab(agentFilesState.source || 'library');
}

function closeAgentFilesModal(){
  document.getElementById('files-modal-overlay').classList.add('hidden');
  loadAgentFiles();
}

async function switchAgentFilesTab(source){
  agentFilesState.source = source;
  document.querySelectorAll('#files-modal-overlay .files-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.source === source);
  });
  document.getElementById('files-upload-form').style.display = source === 'library' ? '' : 'none';
  const list = document.getElementById('files-list');
  list.innerHTML = '<div class="empty">Загрузка...</div>';
  try{
    const r = await apiFetch(API + '/files?source=' + source);
    if(!r || r.status !== 200){ list.innerHTML = '<div class="empty">Не удалось загрузить</div>'; return; }
    const data = await r.json();
    agentFilesState.files = data.files || [];
    agentFilesState.storage = data.storage_configured !== false;
  }catch(e){ list.innerHTML = '<div class="empty">Не удалось загрузить</div>'; return; }
  renderAgentFilesList();
}

function renderAgentFilesList(){
  const list = document.getElementById('files-list');
  const files = agentFilesState.files || [];
  const src = agentFilesState.source;
  if(src === 'library' && !agentFilesState.storage){
    list.innerHTML = '<div class="empty">Хранилище файлов (R2) не настроено на сервере — загрузка недоступна.</div>';
    return;
  }
  if(!files.length){
    const empty = {
      library: 'Пока нет файлов. Загрузите прайс или презентацию — агент будет отправлять их клиентам.',
      generated: 'Агент ещё не создавал документов.',
      inbound: 'Клиенты ещё не присылали голосовых и файлов.',
    }[src];
    list.innerHTML = `<div class="empty">${empty}</div>`;
    return;
  }
  list.innerHTML = files.map(f => {
    const icon = AGENT_FILE_ICONS[f.kind] || 'fa-file';
    const name = esc(f.title || f.filename);
    const meta = [];
    if(f.title && f.title !== f.filename) meta.push(esc(f.filename));
    if(f.duration_seconds) meta.push(agentFileDuration(f.duration_seconds));
    if(f.size_bytes) meta.push(agentFileSize(f.size_bytes));
    if(f.channel) meta.push(f.channel === 'telegram' ? 'Telegram' : 'Instagram');
    if(f.credits_charged) meta.push(f.credits_charged + ' кр.');
    if(f.created_at) meta.push(esc(fmtDate(f.created_at)));
    let status = '';
    if(f.status === 'failed' || f.status === 'skipped'){
      status = `<div class="files-item-warn"><i class="fas fa-triangle-exclamation"></i> ${esc(AGENT_FILE_ERRORS[f.error] || f.error || 'не обработан')}</div>`;
    }
    const descr = f.description ? `<div class="files-item-descr">${esc(f.description)}</div>` : '';
    const actions = [
      f.has_text ? `<button class="btn btn-secondary btn-sm" title="Текст" onclick="showAgentFileText('${f.id}')"><i class="fas fa-file-lines"></i></button>` : '',
      `<button class="btn btn-secondary btn-sm" title="Скачать" onclick="downloadAgentFile('${f.id}')"><i class="fas fa-download"></i></button>`,
      src === 'library' ? `<button class="btn btn-secondary btn-sm" title="Описание" onclick="editAgentFile('${f.id}')"><i class="fas fa-pen"></i></button>` : '',
      `<button class="btn btn-danger btn-sm" title="Удалить" onclick="deleteAgentFile('${f.id}')"><i class="fas fa-trash-can"></i></button>`,
    ].join('');
    return `<div class="files-item" id="file-${f.id}">
      <div class="files-item-icon"><i class="fas ${icon}"></i></div>
      <div class="files-item-main">
        <div class="files-item-name">${name}</div>
        <div class="files-item-meta">${meta.join(' · ')}</div>
        ${descr}${status}
        <div class="files-item-text hidden"></div>
      </div>
      <div class="files-item-actions">${actions}</div>
    </div>`;
  }).join('');
  if(window.VF && VF.faSweep) VF.faSweep(list);
}

async function uploadAgentFile(){
  const input = document.getElementById('files-input');
  const file = input.files && input.files[0];
  if(!file){ alert('Выберите файл'); return; }
  if(file.size > agentFilesState.maxMb * 1024 * 1024){ alert(`Файл больше ${agentFilesState.maxMb} МБ`); return; }
  const btn = document.getElementById('files-upload-btn');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', document.getElementById('files-title').value.trim());
  fd.append('description', document.getElementById('files-description').value.trim());
  btn.disabled = true;
  const old = btn.innerHTML;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Загрузка и чтение…';
  try{
    // apiFetch ставит Content-Type: application/json — для multipart нужен голый fetch.
    const token = getToken();
    const r = await fetch(withAgentId(API + '/files'), {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + token }, body: fd,
    });
    if(!r.ok){
      const err = await r.json().catch(() => ({}));
      const code = err.detail ?? err.message;
      alert('Не удалось загрузить: ' + (AGENT_FILE_ERRORS[code] || errText(code)));
      return;
    }
    const data = await r.json();
    input.value = '';
    document.getElementById('files-title').value = '';
    document.getElementById('files-description').value = '';
    if(data.file && data.file.status === 'skipped' && data.file.error === 'insufficient_credits'){
      alert('Файл загружен, но текст не распознан: закончились кредиты. Агент сможет отправлять файл, но не знает, что внутри.');
    }
    await switchAgentFilesTab('library');
  }catch(e){
    alert('Ошибка сети при загрузке');
  }finally{
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

async function showAgentFileText(id){
  const box = document.querySelector(`#file-${id} .files-item-text`);
  if(!box) return;
  if(!box.classList.contains('hidden')){ box.classList.add('hidden'); return; }
  box.textContent = 'Загрузка...';
  box.classList.remove('hidden');
  try{
    const r = await apiFetch(API + '/files/' + id);
    const data = await r.json();
    box.textContent = data.extracted_text || '(текста нет)';
  }catch(e){ box.textContent = 'Не удалось загрузить текст'; }
}

async function downloadAgentFile(id){
  try{
    const r = await apiFetch(API + '/files/' + id + '/link');
    if(!r || r.status !== 200){ alert('Файл недоступен'); return; }
    const data = await r.json();
    window.open(data.url, '_blank', 'noopener');
  }catch(e){ alert('Файл недоступен'); }
}

async function editAgentFile(id){
  const f = (agentFilesState.files || []).find(x => x.id === id);
  if(!f) return;
  const title = prompt('Название для агента', f.title || f.filename);
  if(title === null) return;
  const description = prompt('Когда отправлять', f.description || '');
  if(description === null) return;
  const r = await apiFetch(API + '/files/' + id, {
    method: 'PATCH', body: JSON.stringify({ title, description }),
  });
  if(!r || !r.ok){ alert('Не удалось сохранить'); return; }
  switchAgentFilesTab(agentFilesState.source);
}

async function deleteAgentFile(id){
  if(!confirm('Удалить файл? Агент больше не сможет его отправлять.')) return;
  const r = await apiFetch(API + '/files/' + id, { method: 'DELETE' });
  if(!r || !r.ok){ alert('Не удалось удалить'); return; }
  switchAgentFilesTab(agentFilesState.source);
}
