/* ============================================================================
 * agent/import.js — Импорт контактов (xlsx/csv, 3 шага) → /api/agent/contacts/import/*
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ── CONTACTS IMPORT ──
let importState = { token:null, preview:null, file:null };

async function downloadAuthedFile(url, filename){
  try{
    const token = getToken();
    const resp = await fetch(url, { headers:{ 'Authorization':'Bearer '+token } });
    if(!resp.ok){ showToast('Не удалось скачать файл','error'); return; }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }catch(e){ showToast('Ошибка сети','error'); }
}

function openImportModal(){
  importState = { token:null, preview:null, file:null };
  document.getElementById('import-modal-overlay').classList.remove('hidden');
  importGotoStep(1);
  document.getElementById('import-file-input').value = '';
  document.getElementById('import-file-label').textContent = 'Перетащите файл сюда или нажмите для выбора';
}
function closeImportModal(){ document.getElementById('import-modal-overlay').classList.add('hidden'); }

function importGotoStep(step){
  for(let i=1;i<=3;i++){
    const el = document.getElementById('import-step-'+i);
    if(el) el.style.display = (i===step) ? '' : 'none';
  }
  // footer buttons
  const show = (id, on) => { const e=document.getElementById(id); if(e) e.style.display = on ? '' : 'none'; };
  show('import-back-btn', step===2);
  show('import-proceed-btn', step===2);
  show('import-errors-btn', false);
  show('import-cancel-btn', step!==3);
  show('import-close-btn', false);
}

function importBack(){ importGotoStep(1); }

async function downloadImportTemplate(){
  await downloadAuthedFile(withAgentId(API + '/contacts/import/template'), 'contacts_template.xlsx');
}

async function downloadImportErrors(){
  if(!importState.token) return;
  await downloadAuthedFile(withAgentId(API + '/contacts/import/errors/' + importState.token), 'import_errors.xlsx');
}

// File input + drag&drop wiring
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('import-file-input');
  const dz = document.getElementById('import-dropzone');
  if(input) input.addEventListener('change', e => { if(e.target.files && e.target.files[0]) handleImportFile(e.target.files[0]); });
  if(dz){
    ['dragover','dragenter'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.style.borderColor='var(--blue)'; }));
    ['dragleave','drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.style.borderColor='var(--border)'; }));
    dz.addEventListener('drop', e => { if(e.dataTransfer.files && e.dataTransfer.files[0]) handleImportFile(e.dataTransfer.files[0]); });
  }
});

async function handleImportFile(file){
  const nameLc = (file.name||'').toLowerCase();
  if(!nameLc.endsWith('.xlsx') && !nameLc.endsWith('.csv')){
    showToast('Поддерживаются только .xlsx и .csv','error'); return;
  }
  importState.file = file;
  document.getElementById('import-file-label').textContent = file.name;
  // go to step 2 with spinner
  importGotoStep(2);
  document.getElementById('import-preview-summary').innerHTML = '<div style="text-align:center;padding:24px"><div class="spinner" style="margin:0 auto"></div></div>';
  document.getElementById('import-proceed-btn').style.display = 'none';
  try{
    const fd = new FormData();
    fd.append('file', file);
    if(currentAgentId) fd.append('agent_id', currentAgentId);
    const token = getToken();
    const resp = await fetch(API + '/contacts/import/preview', { method:'POST', headers:{ 'Authorization':'Bearer '+token }, body:fd });
    if(resp.status === 402){ await handle402(resp); }
    if(!resp.ok){
      const err = await resp.json().catch(()=>({}));
      document.getElementById('import-preview-summary').innerHTML = `<div class="empty">${esc(errText(err.detail))}</div>`;
      return;
    }
    const data = await resp.json();
    importState.token = data.preview_token;
    importState.preview = data;
    renderImportPreview(data);
  }catch(e){
    document.getElementById('import-preview-summary').innerHTML = '<div class="empty">Ошибка сети</div>';
  }
}

function renderImportPreview(d){
  const fmtN = n => (n||0).toLocaleString('ru');
  const enough = d.credits_required_estimate <= d.credits_available;
  const summary = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:13px;margin-bottom:14px">
      <div style="background:var(--bg);border-radius:8px;padding:10px"><b style="font-size:18px;color:var(--green,#16a34a)">${fmtN(d.valid_rows)}</b><div style="color:var(--muted);font-size:11.5px">контактов будет создано</div></div>
      <div style="background:var(--bg);border-radius:8px;padding:10px"><b style="font-size:18px">${fmtN(d.total_rows)}</b><div style="color:var(--muted);font-size:11.5px">строк распознано</div></div>
    </div>
    <div style="font-size:12.5px;line-height:1.8;margin-bottom:12px">
      ${d.errors && d.errors.length ? `<div style="color:var(--red,#dc2626)"><i class="fas fa-circle-exclamation"></i> Ошибок: <b>${d.errors.length}</b></div>` : ''}
      ${d.duplicates && d.duplicates.length ? `<div style="color:var(--amber,#d97706)"><i class="fas fa-clone"></i> Дубликатов (будут пропущены): <b>${d.duplicates.length}</b></div>` : ''}
      ${d.shifted_to_working_hours ? `<div style="color:var(--blue)"><i class="far fa-clock"></i> Задач сдвинуто на след. рабочий день: <b>${d.shifted_to_working_hours}</b> (рабочие часы по МСК)</div>` : ''}
      <div style="${enough?'':'color:var(--red,#dc2626)'}"><i class="fas fa-coins"></i> Примерная стоимость: <b>${fmtN(d.credits_required_estimate)}</b> кредитов из ${fmtN(d.credits_available)}</div>
    </div>
    ${!enough ? `<div style="background:var(--amber-light);padding:10px 12px;border-radius:8px;font-size:12.5px;margin-bottom:8px">Недостаточно кредитов. <a href="#" onclick="closeImportModal();openCreditsModal();return false" style="color:var(--blue);font-weight:600">Пополнить баланс →</a></div>` : ''}
  `;
  document.getElementById('import-preview-summary').innerHTML = summary;

  // shift confirmation checkbox
  const shiftBox = document.getElementById('import-shift-confirm');
  if(d.shifted_to_working_hours > 0){
    shiftBox.style.display = '';
    document.getElementById('import-shift-checkbox').checked = false;
  } else {
    shiftBox.style.display = 'none';
  }

  // errors download button
  document.getElementById('import-errors-btn').style.display = (d.errors && d.errors.length) ? '' : 'none';

  // auto-tasks toggle (по умолчанию включён — поведение не меняется)
  const toggle = document.getElementById('import-tasks-toggle');
  if(d.valid_rows > 0){
    toggle.style.display = '';
    document.getElementById('import-tasks-checkbox').checked = true;
    onImportTasksToggle();
  } else {
    toggle.style.display = 'none';
  }

  // proceed button
  document.getElementById('import-proceed-btn').style.display = '';
  updateImportProceedBtn();
}

// Переключатель авто-задач: меняет только пояснение (превью не пересчитываем).
function onImportTasksToggle(){
  const on = document.getElementById('import-tasks-checkbox').checked;
  const hint = document.getElementById('import-tasks-hint');
  if(!hint) return;
  hint.innerHTML = on
    ? 'Агент запланирует звонок по каждому контакту: возьмёт «Задачу» и «Когда звонить» из файла, а если их нет — назначит время сам (в рабочие часы по МСК).'
    : 'Контакты просто сохранятся в базу. Задачи создадутся только для строк, где в файле заполнены «Задача» и/или «Когда звонить» — их ставим напрямую, без оркестратора. По остальным звонки не планируются, пока вы не поставите задачи вручную или через чат с оркестратором.';
}

function updateImportProceedBtn(){
  const d = importState.preview || {};
  const btn = document.getElementById('import-proceed-btn');
  let ok = !!d.can_proceed;
  if(d.shifted_to_working_hours > 0){
    const cb = document.getElementById('import-shift-checkbox');
    if(cb && !cb.checked) ok = false;
  }
  btn.disabled = !ok;
  btn.style.opacity = ok ? '1' : '0.5';
  btn.style.cursor = ok ? 'pointer' : 'not-allowed';
}

async function executeImport(){
  if(!importState.token) return;
  const total = (importState.preview && importState.preview.valid_rows) || 0;
  const createTasks = document.getElementById('import-tasks-checkbox').checked;
  importGotoStep(3);
  document.getElementById('import-progress-block').style.display = '';
  document.getElementById('import-result-block').style.display = 'none';
  document.getElementById('import-progress-text').textContent = `Импортируем ${total} контактов…`;
  try{
    const r = await apiFetch(API + '/contacts/import/execute', { method:'POST', body:JSON.stringify({ preview_token: importState.token, agent_id: currentAgentId || undefined, create_tasks: createTasks }) });
    if(!r || (r.status !== 200)){
      const err = await r?.json().catch(()=>({}));
      document.getElementById('import-progress-text').textContent = errText(err.detail);
      return;
    }
    const data = await r.json();
    const wait = Math.min(60, Math.max(5, data.estimated_seconds || 10)) * 1000;
    // Импорт идёт в фоне — ждём оценочное время, затем показываем результат и обновляем списки.
    setTimeout(() => {
      document.getElementById('import-progress-block').style.display = 'none';
      document.getElementById('import-result-block').style.display = '';
      document.getElementById('import-result-text').textContent = createTasks
        ? `Готово! Создаётся ${data.total} контактов и задач.`
        : `Готово! Создаётся ${data.total} контактов (авто-задачи не проставлялись).`;
      document.getElementById('import-close-btn').style.display = '';
      document.getElementById('import-cancel-btn').style.display = 'none';
      loadStats(); loadTasks();
    }, wait);
  }catch(e){
    document.getElementById('import-progress-text').textContent = 'Ошибка сети';
  }
}

function finishImport(){
  closeImportModal();
  loadStats(); loadTasks();
  const search = document.getElementById('contacts-search');
  if(search && !document.getElementById('contacts-list-modal-overlay').classList.contains('hidden')){
    loadContactsList(search.value);
  }
}


