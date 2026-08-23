/* ============================================================================
 * agent/contacts.js — CRM-контакты: добавление, список, карточка, задачи контакта, смена стадии → /api/agent/contacts
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ── CONTACT ──
function openAddContactModal(){ document.getElementById('contact-modal-overlay').classList.remove('hidden'); }
function closeContactModal(){ document.getElementById('contact-modal-overlay').classList.add('hidden'); ['c-phone','c-name','c-company','c-position','c-notes'].forEach(id=>document.getElementById(id).value=''); }
async function saveContact(){
  const phone = document.getElementById('c-phone').value.trim();
  if(!phone){ showToast('Укажите телефон','error'); return; }
  const body = { phone, name:document.getElementById('c-name').value.trim()||null, company:document.getElementById('c-company').value.trim()||null, position:document.getElementById('c-position').value.trim()||null, notes:document.getElementById('c-notes').value.trim()||null };
  try{
    const r = await apiFetch(API + '/contacts', { method:'POST', body:JSON.stringify(body) });
    if(r && r.status===200){ closeContactModal(); loadStats(); loadTasks(); showToast('Контакт добавлен. Первый звонок через 1 час.','success'); }
    else { const err=await r?.json().catch(()=>({})); showToast(errText(err.detail),'error'); }
  }catch(e){ showToast('Ошибка сети','error'); }
}


// ── CONTACTS LIST MODAL ──
let contactsSearchTimer = null;

async function openContactsListModal(){
  document.getElementById('contacts-list-modal-overlay').classList.remove('hidden');
  document.getElementById('contacts-search').value = '';
  await loadContactsList('');
  // debounced search
  document.getElementById('contacts-search').oninput = (e) => {
    clearTimeout(contactsSearchTimer);
    contactsSearchTimer = setTimeout(() => loadContactsList(e.target.value), 250);
  };
}
function closeContactsListModal(){
  document.getElementById('contacts-list-modal-overlay').classList.add('hidden');
}

async function loadContactsList(search){
  const body = document.getElementById('contacts-list-body');
  body.innerHTML = '<div class="empty">Загрузка...</div>';
  try{
    const qs = new URLSearchParams({ limit: 100, offset: 0 });
    if(search) qs.set('search', search);
    const r = await apiFetch(API + '/contacts?' + qs);
    if(!r || r.status !== 200){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
    const data = await r.json();
    document.getElementById('contacts-list-count').textContent =
      `· всего ${data.total}`;
    if(!data.contacts.length){
      body.innerHTML = `<div class="empty">${search ? 'Ничего не найдено' : 'Контактов пока нет'}</div>`;
      return;
    }
    body.innerHTML = `
      <table class="calls-table">
        <thead><tr><th>Имя</th><th>Телефон</th><th>Компания</th><th>Стадия</th><th>Попыток</th><th>Последний звонок</th><th></th></tr></thead>
        <tbody>
          ${data.contacts.map(c => `
            <tr style="cursor:pointer" onclick="openContactDetailsModal('${c.id}')">
              <td style="font-weight:600">${esc(c.name || '—')}</td>
              <td style="color:var(--muted)">${esc(c.phone)}</td>
              <td style="color:var(--muted)">${esc(c.company || '—')}</td>
              <td>${stageBadge(c.status)}</td>
              <td style="color:var(--muted)">${c.attempts_count || 0}</td>
              <td style="color:var(--muted)">${c.last_called_at ? fmtDate(c.last_called_at) : '—'}</td>
              <td style="text-align:right;white-space:nowrap"><span class="btn btn-secondary btn-sm" style="pointer-events:none"><i class="fas fa-eye"></i> Смотреть инфо</span></td>
            </tr>
          `).join('')}
        </tbody>
      </table>`;
  }catch(e){
    body.innerHTML = '<div class="empty">Ошибка сети</div>';
  }
}

let currentContactDetailId = null;
let _cdTasks = [];   // запланированные задачи текущего контакта (для блока «Задачи»)
async function openContactDetailsModal(contactId){
  currentContactDetailId = contactId;
  document.getElementById('contact-details-modal-overlay').classList.remove('hidden');
  const body = document.getElementById('contact-details-body');
  body.innerHTML = '<div class="empty">Загрузка...</div>';
  try{
    const r = await apiFetch(API + '/contacts/' + contactId);
    if(!r || r.status !== 200){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
    const c = await r.json();
    renderContactDetails(c);
    document.getElementById('contact-delete-btn').onclick = () => deleteContactFromModal(contactId, c.name || c.phone);
  }catch(e){
    body.innerHTML = '<div class="empty">Ошибка сети</div>';
  }
}
function closeContactDetailsModal(){
  document.getElementById('contact-details-modal-overlay').classList.add('hidden');
}

function renderContactDetails(c){
  document.getElementById('contact-details-title').textContent = c.name || c.phone;
  const mem = c.memory || {};
  const memBlock = `
    <div style="background:var(--bg);border-radius:10px;padding:14px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--blue-dark)">
        <i class="fas fa-brain"></i> Память агента
      </div>
      ${mem.summary ? `<div style="font-size:12.5px;margin-bottom:8px;line-height:1.5">${esc(mem.summary)}</div>` : '<div class="empty" style="text-align:left;padding:0">Память ещё не накоплена</div>'}
      ${mem.key_facts && mem.key_facts.length ? `
        <div style="font-size:11px;color:var(--muted);margin-top:8px"><b>Ключевые факты:</b></div>
        <ul style="font-size:12px;color:var(--muted);padding-left:18px;margin-top:4px">
          ${mem.key_facts.map(f => `<li>${esc(f)}</li>`).join('')}
        </ul>
      ` : ''}
      ${mem.best_time ? `<div style="font-size:12px;color:var(--muted);margin-top:6px"><b>Лучшее время:</b> ${esc(mem.best_time)}</div>` : ''}
      ${mem.tone_history && mem.tone_history.length ? `<div style="font-size:12px;color:var(--muted);margin-top:6px"><b>Тон последних разговоров:</b> ${mem.tone_history.slice(-5).map(esc).join(' → ')}</div>` : ''}
    </div>`;

  const infoBlock = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px;font-size:12.5px">
      <div><b>Телефон:</b> ${esc(c.phone)}</div>
      <div><b>Попыток:</b> ${c.attempts_count || 0}</div>
      <div style="display:flex;align-items:center;gap:8px"><b>Стадия:</b>
        <select id="cd-stage" onchange="changeContactStage('${c.id}', this.value)" class="form-input" style="display:inline-block;width:auto;padding:2px 8px;font-size:12px">
          ${STAGE_ORDER.map(s => `<option value="${s}" ${s === c.status ? 'selected' : ''}>${STAGE_META[s].label}</option>`).join('')}
        </select>
      </div>
      <div><b>Последний звонок:</b> ${c.last_called_at ? fmtDate(c.last_called_at) : '—'}</div>
    </div>
    <div style="border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:16px">
      <div style="font-size:12px;font-weight:600;margin-bottom:10px;color:var(--blue-dark)"><i class="fas fa-id-card"></i> Данные контакта</div>
      <div class="form-row">
        <div class="form-group"><label class="form-label">Имя</label><input type="text" class="form-input" id="cd-name" value="${esc(c.name||'')}"></div>
        <div class="form-group"><label class="form-label">Компания</label><input type="text" class="form-input" id="cd-company" value="${esc(c.company||'')}"></div>
      </div>
      <div class="form-group"><label class="form-label">Должность</label><input type="text" class="form-input" id="cd-position" value="${esc(c.position||'')}"></div>
      <div class="form-group"><label class="form-label">Информация о клиенте</label><textarea class="form-textarea" id="cd-notes" rows="3" style="min-height:70px">${esc(c.notes||'')}</textarea></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn btn-secondary btn-sm" onclick="resetContactEdit()">Отмена</button>
        <button class="btn btn-primary btn-sm" onclick="saveContactInfo()"><i class="fas fa-check"></i> Сохранить</button>
      </div>
    </div>`;

  const tasksBlock = `
    <div style="border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="font-size:12px;font-weight:600;color:var(--blue-dark)"><i class="far fa-calendar-check"></i> Задачи</div>
        <button class="btn btn-secondary btn-sm" onclick="cdTaskAddToggle()"><i class="fas fa-plus"></i> Добавить</button>
      </div>
      <div class="cd-task-new" id="cd-task-new" style="display:none">
        <input type="text" class="form-input" id="cdt-new-title" placeholder="Название задачи">
        <div>
          <label class="form-label" style="margin-bottom:4px">Дата и время <span style="color:var(--muted);font-weight:400">🕐 МСК</span></label>
          <input type="datetime-local" class="form-input" id="cdt-new-time">
        </div>
        <div class="task-ed-btns">
          <button class="btn btn-secondary btn-sm" onclick="cdTaskAddToggle()">Отмена</button>
          <button class="btn btn-primary btn-sm" onclick="cdTaskCreate()"><i class="fas fa-check"></i> Создать</button>
        </div>
      </div>
      <div id="cd-tasks-list"></div>
    </div>`;

  const callsBlock = (c.calls && c.calls.length) ? `
    <div style="font-size:13px;font-weight:600;margin-bottom:8px"><i class="fas fa-phone-volume"></i> История звонков (${c.calls.length})</div>
    ${c.calls.map((call, i) => renderCallExpanded(call, 'cc-' + i)).join('')}
  ` : '<div class="empty">Звонков пока не было</div>';

  const smsBlock = (c.sms && c.sms.length) ? `
    <div style="font-size:13px;font-weight:600;margin:16px 0 8px"><i class="fas fa-comment-sms"></i> SMS-переписка (${c.sms.length})</div>
    <div class="cd-sms-thread">${c.sms.map(_cdSmsBubble).join('')}</div>
  ` : '';

  // Telegram-переписка (личный аккаунт владельца) — те же пузыри, что SMS.
  const tgBlock = (c.telegram && c.telegram.length) ? `
    <div style="font-size:13px;font-weight:600;margin:16px 0 8px"><i class="fas fa-paper-plane" style="color:#229ED9"></i> Telegram-переписка (${c.telegram.length})</div>
    <div class="cd-sms-thread">${c.telegram.map(_cdSmsBubble).join('')}</div>
  ` : '';

  // Instagram-переписка (DM бизнес-аккаунта владельца) — те же пузыри.
  const igBlock = (c.instagram && c.instagram.length) ? `
    <div style="font-size:13px;font-weight:600;margin:16px 0 8px"><i class="fa-brands fa-instagram" style="color:#E1306C"></i> Instagram-переписка (${c.instagram.length})</div>
    <div class="cd-sms-thread">${c.instagram.map(_cdSmsBubble).join('')}</div>
  ` : '';

  _cdTasks = (c.tasks || []).slice().sort((a,b)=> new Date(a.scheduled_time)-new Date(b.scheduled_time));
  document.getElementById('contact-details-body').innerHTML = infoBlock + tasksBlock + memBlock + callsBlock + smsBlock + tgBlock + igBlock;
  renderContactTasksSection();
}

// ── SMS-переписка в карточке контакта ──
// outbound = от агента (вправо), inbound = от клиента (влево). Тред резолвится
// сервером по номеру (см. /api/agent/contacts/{id} → поле sms).
function _cdSmsBubble(m){
  const out = (m.direction === 'outbound');
  const who = out ? 'Агент' : 'Клиент';
  const tm = m.ts ? fmtDate(m.ts) : '';
  return `<div class="cd-sms ${out ? 'cd-sms-out' : 'cd-sms-in'}">
    <div class="cd-sms-meta">${who}${tm ? ' · ' + tm : ''}</div>
    <div class="cd-sms-body">${esc(m.body || '')}</div>
  </div>`;
}

// ── Задачи в карточке контакта ──
function renderContactTasksSection(){
  const wrap = document.getElementById('cd-tasks-list');
  if(!wrap) return;
  if(!_cdTasks.length){
    wrap.innerHTML = '<div class="empty" style="text-align:left;padding:4px 0">Запланированных задач нет</div>';
    return;
  }
  wrap.innerHTML = _cdTasks.map(t => _cdTaskRow(t)).join('');
}

function _cdTaskRow(t){
  const tm = t.scheduled_time ? fmtDate(t.scheduled_time) : '—';
  return `<div class="cd-task" data-id="${t.id}">
    <div class="cd-task-row">
      <div class="cd-task-main cd-task-clickable" onclick="cdTaskEdit('${t.id}')">
        <div class="cd-task-title">${esc(t.title||'Задача')} ${taskChannelBadge(t.channel)}</div>
        <div class="cd-task-time"><i class="far fa-clock"></i> ${tm}</div>
      </div>
      <div class="cd-task-acts">
        <button class="task-edit-btn" title="Изменить" onclick="cdTaskEdit('${t.id}')"><i class="fas fa-pen"></i></button>
        <button class="task-del" title="Удалить задачу" onclick="cdTaskDelete('${t.id}')"><i class="fas fa-trash-alt"></i></button>
      </div>
    </div>
    <div class="task-ed" id="cd-task-ed-${t.id}" style="display:none">
      <input type="text" class="form-input" id="cdt-title-${t.id}" value="${esc(t.title||'')}" placeholder="Название задачи">
      <input type="datetime-local" class="form-input" id="cdt-time-${t.id}" value="${utcToMskInput(t.scheduled_time)}">
      <div class="task-ed-btns">
        <button class="btn btn-secondary btn-sm" onclick="cdTaskEditCancel('${t.id}')">Отмена</button>
        <button class="btn btn-primary btn-sm" onclick="cdTaskSave('${t.id}')"><i class="fas fa-check"></i> Сохранить</button>
      </div>
    </div>
  </div>`;
}

function cdTaskEdit(id){
  const ed = document.getElementById('cd-task-ed-' + id);
  if(ed) ed.style.display = 'flex';
}
function cdTaskEditCancel(id){
  const ed = document.getElementById('cd-task-ed-' + id);
  if(ed) ed.style.display = 'none';
}

function cdTaskAddToggle(){
  const f = document.getElementById('cd-task-new');
  if(!f) return;
  f.style.display = (f.style.display === 'none' || !f.style.display) ? 'flex' : 'none';
}

async function cdTaskSave(id){
  const title = (document.getElementById('cdt-title-' + id).value || '').trim();
  const timeV = document.getElementById('cdt-time-' + id).value;
  if(!title){ showToast('Введите название задачи', 'error'); return; }
  if(!timeV){ showToast('Укажите дату и время', 'error'); return; }
  const iso = mskInputToUtc(timeV);
  if(!iso){ showToast('Некорректная дата', 'error'); return; }
  try{
    const r = await apiFetch(API + '/tasks/' + id, { method:'PUT', body: JSON.stringify({ title, scheduled_time: iso }) });
    if(!r || r.status !== 200){ showToast('Не удалось сохранить задачу', 'error'); return; }
    const upd = await r.json();
    const i = _cdTasks.findIndex(x => String(x.id) === String(id));
    if(i >= 0) _cdTasks[i] = { ..._cdTasks[i], ...upd };
    _cdTasks.sort((a,b)=> new Date(a.scheduled_time)-new Date(b.scheduled_time));
    renderContactTasksSection();
    loadTasks();
    showToast('Задача обновлена', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

async function cdTaskDelete(id){
  if(!confirm('Удалить эту задачу? Запланированный звонок не будет выполнен.')) return;
  try{
    const r = await apiFetch(API + '/tasks/' + id, { method:'DELETE' });
    if(!r || (r.status !== 200 && r.status !== 204)){ showToast('Не удалось удалить задачу', 'error'); return; }
    _cdTasks = _cdTasks.filter(x => String(x.id) !== String(id));
    renderContactTasksSection();
    loadTasks();
    showToast('Задача удалена', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

async function cdTaskCreate(){
  if(!currentContactDetailId) return;
  const title = (document.getElementById('cdt-new-title').value || '').trim();
  const timeV = document.getElementById('cdt-new-time').value;
  if(!title){ showToast('Введите название задачи', 'error'); return; }
  if(!timeV){ showToast('Укажите дату и время', 'error'); return; }
  const iso = mskInputToUtc(timeV);
  if(!iso){ showToast('Некорректная дата', 'error'); return; }
  try{
    const r = await apiFetch(API + '/contacts/' + currentContactDetailId + '/tasks', {
      method:'POST', body: JSON.stringify({ title, scheduled_time: iso }),
    });
    if(!r || (r.status !== 200 && r.status !== 201)){ showToast('Не удалось создать задачу', 'error'); return; }
    const t = await r.json();
    _cdTasks.push(t);
    _cdTasks.sort((a,b)=> new Date(a.scheduled_time)-new Date(b.scheduled_time));
    cdTaskAddToggle();
    document.getElementById('cdt-new-title').value = '';
    document.getElementById('cdt-new-time').value = '';
    renderContactTasksSection();
    loadTasks();
    showToast('Задача создана', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

function resetContactEdit(){
  if(currentContactDetailId) openContactDetailsModal(currentContactDetailId);
}

async function saveContactInfo(){
  if(!currentContactDetailId) return;
  const body = {
    name: document.getElementById('cd-name').value.trim() || null,
    company: document.getElementById('cd-company').value.trim() || null,
    position: document.getElementById('cd-position').value.trim() || null,
    notes: document.getElementById('cd-notes').value.trim() || null,
  };
  try{
    const r = await apiFetch(API + '/contacts/' + currentContactDetailId, { method:'PUT', body:JSON.stringify(body) });
    if(r && r.status === 200){
      showToast('Данные контакта сохранены', 'success');
      const c = await r.json();
      document.getElementById('contact-details-title').textContent = c.name || c.phone;
      // обновим список под модалкой, если он открыт
      const search = document.getElementById('contacts-search');
      if(search) loadContactsList(search.value);
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(errText(err.detail), 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

async function deleteContactFromModal(contactId, label){
  if(!confirm(`Удалить контакт "${label}"? Это удалит всю историю звонков с ним.`)) return;
  try{
    const r = await apiFetch(API + '/contacts/' + contactId, { method:'DELETE' });
    if(r && r.status === 200){
      closeContactDetailsModal();
      loadContactsList(document.getElementById('contacts-search').value);
      loadStats();
      showToast('Контакт удалён', 'success');
    } else {
      showToast('Ошибка удаления', 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

// ── Смена стадии контакта (PATCH /contacts/{id}/status) ──
async function changeContactStage(contactId, stage){
  try{
    const r = await apiFetch(API + '/contacts/' + contactId + '/status', {
      method:'PATCH', body: JSON.stringify({ status: stage }),
    });
    if(r && r.status === 200){
      showToast('Стадия обновлена', 'success');
      if(typeof loadStats === 'function') loadStats();
      return true;
    }
    showToast('Не удалось обновить стадию', 'error');
  }catch(e){ showToast('Ошибка сети', 'error'); }
  return false;
}


