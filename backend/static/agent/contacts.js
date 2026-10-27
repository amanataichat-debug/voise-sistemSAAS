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
    else { const err=await r?.json().catch(()=>({})); showToast(errText(err.detail ?? err.message),'error'); }
  }catch(e){ showToast('Ошибка сети','error'); }
}


// ── CONTACTS LIST MODAL ──
let contactsSearchTimer = null;

// Активные фильтры во вкладке «Клиенты» (одна активная вкладка списка)
let contactsFilters = { stage: 'all', channel: 'all', tasks: 'all', tag: null };
let contactsListCache = []; // последняя порция с бэка, для живой фильтрации

async function openContactsListModal(){
  document.getElementById('contacts-list-modal-overlay').classList.remove('hidden');
  document.getElementById('contacts-search').value = '';
  contactsFilters = { stage: 'all', channel: 'all', tasks: 'all', tag: null };
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

// Переключение фильтра (вызывается из chip-кнопок)
function cfSet(key, value){
  contactsFilters[key] = value;
  _renderContactsListFromCache();
}
function cfSetTag(tag){
  contactsFilters.tag = contactsFilters.tag === tag ? null : tag;
  _renderContactsListFromCache();
}

async function loadContactsList(search){
  const body = document.getElementById('contacts-list-body');
  body.innerHTML = '<div class="empty">Загрузка...</div>';
  try{
    const qs = new URLSearchParams({ limit: 200, offset: 0 });
    if(search) qs.set('search', search);
    // Стадия — на бэке (/contacts поддерживает status)
    if(contactsFilters.stage && contactsFilters.stage !== 'all') qs.set('status', contactsFilters.stage);
    const r = await apiFetch(API + '/contacts?' + qs);
    if(!r || r.status !== 200){ body.innerHTML='<div class="empty">Ошибка</div>'; return; }
    const data = await r.json();
    contactsListCache = { contacts: data.contacts || [], total: data.total || 0, search: search || '' };
    _renderContactsListFromCache();
  }catch(e){
    body.innerHTML = '<div class="empty">Ошибка сети</div>';
  }
}

// Собирает уникальные теги из загруженных контактов — для chip-строки
function _collectTags(contacts){
  const map = {};
  (contacts || []).forEach(c => (c.tags || []).forEach(t => { map[t] = (map[t] || 0) + 1; }));
  return Object.keys(map).sort((a, b) => map[b] - map[a]).slice(0, 10); // top-10
}

function _renderContactsListFromCache(){
  const body = document.getElementById('contacts-list-body');
  if(!body || !contactsListCache || !contactsListCache.contacts) return;
  const all = contactsListCache.contacts;
  // Канал + задачи + тег — клиентские фильтры (поверх серверного status/search)
  const list = all.filter(c => {
    if(contactsFilters.channel !== 'all'){
      const ch = c.channels || {};
      if(contactsFilters.channel === 'none'){
        if(ch.whatsapp || ch.telegram || ch.instagram) return false;
      } else if(!ch[contactsFilters.channel]) return false;
    }
    if(contactsFilters.tasks === 'with' && !(c.pending_tasks > 0)) return false;
    if(contactsFilters.tasks === 'without' && (c.pending_tasks > 0)) return false;
    if(contactsFilters.tag && !(c.tags || []).includes(contactsFilters.tag)) return false;
    return true;
  });
  document.getElementById('contacts-list-count').textContent =
    (list.length === all.length ? `· всего ${contactsListCache.total}` : `· показано ${list.length} из ${contactsListCache.total}`);
  const tags = _collectTags(all);
  // Строка фильтров сверху списка
  const chip = (active, label, onclick) => `<button class="cf-chip ${active ? 'is-active' : ''}" onclick="${onclick}">${esc(label)}</button>`;
  const stageChips = ['all'].concat(STAGE_ORDER).map(k => chip(
    contactsFilters.stage === k,
    k === 'all' ? 'Все стадии' : (STAGE_META[k] ? STAGE_META[k].label : k),
    `cfSet('stage','${k}')`
  )).join('');
  const chChips = [['all','Все каналы'],['whatsapp','WhatsApp'],['telegram','Telegram'],['instagram','Instagram'],['none','Без переписки']]
    .map(([k, l]) => chip(contactsFilters.channel === k, l, `cfSet('channel','${k}')`)).join('');
  const tsChips = [['all','Все'],['with','С задачей'],['without','Без задачи']]
    .map(([k, l]) => chip(contactsFilters.tasks === k, l, `cfSet('tasks','${k}')`)).join('');
  const tagChips = tags.length ? tags.map(t => `<button class="cf-tag ${contactsFilters.tag === t ? 'is-active' : ''}" onclick="cfSetTag('${esc(t).replace(/'/g,"\\'")}')">${esc(t)}</button>`).join('') : '';

  const filtersBar = `
    <div class="cf-filters">
      <div class="cf-row"><span class="cf-label">Стадия</span><div class="cf-chips">${stageChips}</div></div>
      <div class="cf-row"><span class="cf-label">Канал</span><div class="cf-chips">${chChips}</div></div>
      <div class="cf-row"><span class="cf-label">Задачи</span><div class="cf-chips">${tsChips}</div></div>
      ${tags.length ? `<div class="cf-row"><span class="cf-label">Теги</span><div class="cf-chips">${tagChips}</div></div>` : ''}
    </div>`;

  if(!list.length){
    body.innerHTML = filtersBar + `<div class="empty" style="margin-top:14px">${contactsListCache.search ? 'Ничего не найдено' : 'Под фильтр ничего не попало'}</div>`;
    return;
  }
  const table = `
    <table class="calls-table">
      <thead><tr><th>Имя</th><th>Телефон</th><th>Компания</th><th>Стадия</th><th>Каналы</th><th>Теги</th><th>Задачи</th><th>Последний звонок</th><th></th></tr></thead>
      <tbody>
        ${list.map(c => {
          const ch = c.channels || {};
          const chBadges = [
            ch.whatsapp  ? '<i class="fab fa-whatsapp"  style="color:#25D366"></i>' : '',
            ch.telegram  ? '<i class="fab fa-telegram"  style="color:#229ED9"></i>' : '',
            ch.instagram ? '<i class="fab fa-instagram" style="color:#E1306C"></i>' : '',
          ].filter(Boolean).join(' ') || '<span style="color:var(--vf-text-4);font-size:11px">—</span>';
          const tagPills = (c.tags || []).slice(0, 2).map(t => `<span class="cf-tag-mini">${esc(t)}</span>`).join('');
          const taskCell = c.pending_tasks > 0
            ? `<span class="cf-task-badge"><i class="far fa-calendar-check"></i>${c.pending_tasks}</span>`
            : '<span style="color:var(--vf-text-4);font-size:11px">—</span>';
          return `
            <tr style="cursor:pointer" onclick="openContactDetailsModal('${c.id}')">
              <td style="font-weight:600">${esc(c.name || '—')}${aiPauseBadge(c)}</td>
              <td style="color:var(--muted)">${esc(c.phone)}</td>
              <td style="color:var(--muted)">${esc(c.company || '—')}</td>
              <td>${stageBadge(c.status)}</td>
              <td style="font-size:14px;white-space:nowrap">${chBadges}</td>
              <td>${tagPills || '<span style="color:var(--vf-text-4);font-size:11px">—</span>'}</td>
              <td style="white-space:nowrap">${taskCell}</td>
              <td style="color:var(--muted)">${c.last_called_at ? fmtDate(c.last_called_at) : '—'}</td>
              <td style="text-align:right;white-space:nowrap"><span class="btn btn-secondary btn-sm" style="pointer-events:none"><i class="fas fa-eye"></i> Открыть</span></td>
            </tr>`;
        }).join('')}
      </tbody>
    </table>`;
  body.innerHTML = filtersBar + table;
}

let currentContactDetailId = null;
let _cdTasks = [];   // запланированные задачи текущего контакта (для блока «Задачи»)
async function openContactDetailsModal(contactId){
  currentContactDetailId = contactId;
  // URL state: ?client=<id> — F5 возвращает открытую карточку, ссылка открывает её же
  try {
    const url = new URL(location.href);
    if(url.searchParams.get('client') !== String(contactId)){
      url.searchParams.set('client', contactId);
      history.replaceState({ ...(history.state || {}), client: contactId }, '', url.toString());
    }
  } catch(e) {}
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
  currentContactDetailId = null;
  // Убираем ?client=… из URL, не трогая ?tab=…
  try {
    const url = new URL(location.href);
    if(url.searchParams.has('client')){
      url.searchParams.delete('client');
      history.replaceState({ ...(history.state || {}), client: null }, '', url.toString());
    }
  } catch(e) {}
}

// Иконка канала для бейджа в объединённой ленте переписки
const CD_CHANNEL_META = {
  sms:       { icon: 'fas fa-comment-sms',    color: '#64748b', label: 'SMS' },
  telegram:  { icon: 'fas fa-paper-plane',    color: '#229ED9', label: 'Telegram' },
  instagram: { icon: 'fa-brands fa-instagram',color: '#E1306C', label: 'Instagram' },
  whatsapp:  { icon: 'fa-brands fa-whatsapp', color: '#25D366', label: 'WhatsApp' },
};

// Единая лента переписки: мерджим все каналы и сортируем по времени
function _cdMergedThread(c){
  const items = [];
  const push = (arr, channel) => (arr || []).forEach(m => items.push({ ...m, _channel: channel }));
  push(c.sms, 'sms');
  push(c.telegram, 'telegram');
  push(c.instagram, 'instagram');
  push(c.whatsapp, 'whatsapp');
  items.sort((a, b) => new Date(a.ts || 0) - new Date(b.ts || 0));
  return items;
}

// Пузырь переписки с бейджем канала (расширенная версия _cdSmsBubble)
function _cdChannelBubble(m){
  const out = (m.direction === 'outbound');
  const who = !out ? 'Клиент' : (m.sent_by === 'owner' ? 'Вы' : 'Агент');
  const tm = m.ts ? fmtDate(m.ts) : '';
  const body = (m.body || '').replace(/\s*\[file_id=[0-9a-f-]+\]/g, '');
  const file = m.attachment_id
    ? `<div style="margin-top:4px"><span class="card-link" onclick="downloadAgentFile('${esc(m.attachment_id)}')"><i class="fas fa-download"></i> Файл</span></div>`
    : '';
  const ch = CD_CHANNEL_META[m._channel];
  const channelBadge = ch ? `<span class="cd-ch" style="color:${ch.color}"><i class="${ch.icon}"></i> ${ch.label}</span>` : '';
  return `<div class="cd-sms ${out ? 'cd-sms-out' : 'cd-sms-in'}">
    <div class="cd-sms-meta">${channelBadge}${who}${tm ? ' · ' + tm : ''}</div>
    <div class="cd-sms-body">${esc(body)}</div>${file}
  </div>`;
}

// Переключение вкладки внутри карточки клиента
function cdSwitchSection(name){
  const body = document.getElementById('contact-details-body');
  if(!body) return;
  body.querySelectorAll('.cd-nav .cd-nav-btn').forEach(b => b.classList.toggle('is-active', b.dataset.section === name));
  body.querySelectorAll('.cd-section').forEach(s => s.classList.toggle('is-active', s.dataset.section === name));
  // Скроллим верх при смене раздела
  body.scrollTop = 0;
}

function renderContactDetails(c){
  document.getElementById('contact-details-title').textContent = c.name || c.phone;
  const mem = c.memory || {};
  const callsCount = (c.calls || []).length;
  const chatItems = _cdMergedThread(c);
  const chatCount = chatItems.length;
  const tasksCount = (c.tasks || []).length;

  // ── Шапка карточки: ключевые факты, стадия, основные действия
  const headerBlock = `
    <div class="cd-header">
      <div class="cd-header-main">
        <div class="cd-header-row">
          <div class="cd-fact"><i class="fas fa-phone"></i>${esc(c.phone)}</div>
          ${c.company ? `<div class="cd-fact"><i class="fas fa-building"></i>${esc(c.company)}</div>` : ''}
          <div class="cd-fact"><i class="fas fa-repeat"></i>Попыток: ${c.attempts_count || 0}</div>
          ${c.last_called_at ? `<div class="cd-fact"><i class="fas fa-clock"></i>Был звонок: ${esc(fmtDate(c.last_called_at))}</div>` : ''}
        </div>
        ${(c.alt_phones || []).length ? `<div class="cd-sub">Другие номера: ${c.alt_phones.map(esc).join(', ')}${c.call_phone ? ` · звоним на: <b>${esc(c.call_phone)}</b>` : ''}</div>` : ''}
      </div>
      <div class="cd-header-right">
        <label class="cd-stage-lbl">Стадия:</label>
        <select id="cd-stage" onchange="changeContactStage('${c.id}', this.value)" class="form-input cd-stage-select">
          ${STAGE_ORDER.map(s => `<option value="${s}" ${s === c.status ? 'selected' : ''}>${STAGE_META[s].label}</option>`).join('')}
        </select>
      </div>
    </div>`;

  // ── Внутренние вкладки
  const navBlock = `
    <div class="cd-nav">
      <button class="cd-nav-btn is-active" type="button" data-section="chat" onclick="cdSwitchSection('chat')">
        <i class="fas fa-comments"></i><span>Переписка</span>${chatCount ? `<span class="cd-nav-num">${chatCount}</span>` : ''}
      </button>
      <button class="cd-nav-btn" type="button" data-section="calls" onclick="cdSwitchSection('calls')">
        <i class="fas fa-phone-volume"></i><span>Звонки</span>${callsCount ? `<span class="cd-nav-num">${callsCount}</span>` : ''}
      </button>
      <button class="cd-nav-btn" type="button" data-section="memory" onclick="cdSwitchSection('memory')">
        <i class="fas fa-brain"></i><span>Размышления</span>
      </button>
      <button class="cd-nav-btn" type="button" data-section="tasks" onclick="cdSwitchSection('tasks')">
        <i class="far fa-calendar-check"></i><span>Задачи</span>${tasksCount ? `<span class="cd-nav-num">${tasksCount}</span>` : ''}
      </button>
      <button class="cd-nav-btn" type="button" data-section="info" onclick="cdSwitchSection('info')">
        <i class="fas fa-id-card"></i><span>Данные</span>
      </button>
    </div>`;

  // ── Разделы ───────────────────────────────────────────────────────────
  // 1. Переписка (default) — объединённая лента всех каналов хронологически
  const chatSection = `
    <div class="cd-section is-active" data-section="chat">
      ${chatCount
        ? `<div class="cd-sms-thread cd-chat-thread">${chatItems.map(_cdChannelBubble).join('')}</div>`
        : '<div class="empty">Переписки пока не было. Клиент пишет в WhatsApp / Telegram / Instagram — здесь появится вся история.</div>'}
    </div>`;

  // 2. Звонки — карточки звонков с аудио (renderCallExpanded умеет audio)
  const callsSection = `
    <div class="cd-section" data-section="calls">
      ${callsCount
        ? `<div style="display:flex;flex-direction:column;gap:10px">${c.calls.map((call, i) => renderCallExpanded(call, 'cc-' + i)).join('')}</div>`
        : '<div class="empty">Звонков пока не было</div>'}
    </div>`;

  // 3. Размышления — память агента о клиенте
  const memorySection = `
    <div class="cd-section" data-section="memory">
      <div class="cd-memory-card">
        <div class="cd-memory-head"><i class="fas fa-brain"></i> Память агента об этом клиенте</div>
        ${mem.summary
          ? `<div class="cd-memory-summary">${esc(mem.summary)}</div>`
          : '<div class="empty" style="text-align:left;padding:12px 0">Память ещё не накоплена — агент заполнит её после первого разговора.</div>'}
        ${mem.key_facts && mem.key_facts.length ? `
          <div class="cd-memory-section"><b>Ключевые факты:</b>
            <ul>${mem.key_facts.map(f => `<li>${esc(f)}</li>`).join('')}</ul>
          </div>` : ''}
        ${mem.best_time ? `<div class="cd-memory-section"><b>Лучшее время связи:</b> ${esc(mem.best_time)}</div>` : ''}
        ${mem.tone_history && mem.tone_history.length ? `<div class="cd-memory-section"><b>Тон последних разговоров:</b> ${mem.tone_history.slice(-5).map(esc).join(' → ')}</div>` : ''}
      </div>
    </div>`;

  // 4. Задачи
  const tasksSection = `
    <div class="cd-section" data-section="tasks">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <div style="font-size:14px;font-weight:600">Запланированное</div>
        <button class="btn btn-secondary btn-sm" onclick="cdTaskAddToggle()"><i class="fas fa-plus"></i> Добавить задачу</button>
      </div>
      <div class="cd-task-new" id="cd-task-new" style="display:none">
        <input type="text" class="form-input" id="cdt-new-title" placeholder="Название задачи">
        <div>
          <label class="form-label" style="margin-bottom:4px">Дата и время <span style="color:var(--muted);font-weight:400">🕐 Бишкек</span></label>
          <input type="datetime-local" class="form-input" id="cdt-new-time">
        </div>
        <div class="task-ed-btns">
          <button class="btn btn-secondary btn-sm" onclick="cdTaskAddToggle()">Отмена</button>
          <button class="btn btn-primary btn-sm" onclick="cdTaskCreate()"><i class="fas fa-check"></i> Создать</button>
        </div>
      </div>
      <div id="cd-tasks-list"></div>
    </div>`;

  // 5. Данные — форма редактирования, включая CRM v2 поля (сделка, теги, следующий шаг)
  const nsAt = c.next_step_at
    ? new Date(c.next_step_at).toISOString().slice(0, 16) // local-form datetime-local
    : '';
  const tagsVal = (c.tags || []).join(', ');
  const infoSection = `
    <div class="cd-section" data-section="info">
      <div class="form-row">
        <div class="form-group"><label class="form-label">Имя</label><input type="text" class="form-input" id="cd-name" value="${esc(c.name||'')}"></div>
        <div class="form-group"><label class="form-label">Компания</label><input type="text" class="form-input" id="cd-company" value="${esc(c.company||'')}"></div>
      </div>
      <div class="form-group"><label class="form-label">Должность</label><input type="text" class="form-input" id="cd-position" value="${esc(c.position||'')}"></div>
      <div class="form-group"><label class="form-label">Теги <span style="color:var(--vf-text-4);font-weight:400;font-size:11px">· через запятую: vip, повтор, розница</span></label><input type="text" class="form-input" id="cd-tags" value="${esc(tagsVal)}" placeholder="vip, hot, розница"></div>

      <div class="cd-deal-card">
        <div class="cd-deal-head"><i class="fas fa-coins"></i> Сделка</div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Сумма, сом</label><input type="number" min="0" step="100" class="form-input" id="cd-deal-amount" value="${c.deal_amount != null ? esc(c.deal_amount) : ''}" placeholder="например, 25000"></div>
          <div class="form-group"><label class="form-label">Следующий шаг — когда <span style="color:var(--vf-text-4);font-weight:400;font-size:11px">🕐 Бишкек</span></label><input type="datetime-local" class="form-input" id="cd-next-at" value="${esc(nsAt)}"></div>
        </div>
        <div class="form-group"><label class="form-label">Следующий шаг — что сделать</label><input type="text" class="form-input" id="cd-next-text" value="${esc(c.next_step_text||'')}" placeholder="Прислать КП в WhatsApp"></div>
        ${c.status === 'rejected' || c.status === 'do_not_call' ? `
          <div class="form-group"><label class="form-label">Причина отказа</label><input type="text" class="form-input" id="cd-lost" value="${esc(c.lost_reason||'')}" placeholder="Дорого / не актуально / конкурент"></div>
        ` : ''}
      </div>

      <div class="form-group"><label class="form-label">Заметки о клиенте</label><textarea class="form-textarea" id="cd-notes" rows="4" style="min-height:90px">${esc(c.notes||'')}</textarea></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn btn-secondary btn-sm" onclick="resetContactEdit()">Отмена</button>
        <button class="btn btn-primary btn-sm" onclick="saveContactInfo()"><i class="fas fa-check"></i> Сохранить</button>
      </div>
    </div>`;

  _cdTasks = (c.tasks || []).slice().sort((a,b)=> new Date(a.scheduled_time)-new Date(b.scheduled_time));
  document.getElementById('contact-details-body').innerHTML =
    '<div id="cd-ai-pause">' + aiPauseBlockHtml(c) + '</div>' +
    headerBlock +
    navBlock +
    chatSection +
    callsSection +
    memorySection +
    tasksSection +
    infoSection;
  renderContactTasksSection();
}

// ── Пауза агента в переписке с клиентом (все мессенджеры) ──
// Ставится сама, когда владелец пишет клиенту, или кнопкой/командой в чате.
function aiPauseText(c){
  const p = contactAiPause(c);
  if(!p) return '';
  if(p.forever) return 'Агент отключён в этом чате навсегда';
  const why = p.reason === 'owner_message' ? ' — вы написали сами'
            : p.reason === 'command' ? ' — по вашей команде' : '';
  const parts = mskParts(p.until);
  const today = mskParts(new Date().toISOString());
  const sameDay = parts && today && parts.day === today.day && parts.mon === today.mon;
  const when = sameDay ? parts.time : fmtDate(p.until);
  return `Агент на паузе до ${when}${why}`;
}

function aiPauseBlockHtml(c){
  const p = contactAiPause(c);
  const status = p
    ? `<div style="display:flex;align-items:center;gap:8px;font-weight:600;color:#b45309"><i class="fas fa-circle-pause"></i> ${esc(aiPauseText(c))}</div>
       <div style="font-size:12px;color:var(--muted);margin-top:2px">Сообщения клиента сохраняются, агент на них не отвечает. Звонки идут как обычно.</div>`
    : `<div style="display:flex;align-items:center;gap:8px;font-weight:600;color:#166534"><i class="fas fa-circle-check"></i> Агент работает</div>
       <div style="font-size:12px;color:var(--muted);margin-top:2px">Отвечает клиенту в Telegram, WhatsApp и Instagram.</div>`;
  const resume = p
    ? `<button class="btn btn-primary btn-sm" onclick="resumeContactAgent('${c.id}')"><i class="fas fa-circle-play"></i> Вернуть агента</button>`
    : '';
  const pauseLabel = p ? 'Изменить паузу' : 'Остановить агента';
  return `<div class="cd-ai-pause" style="border:1px solid ${p ? '#FCD34D' : 'var(--border)'};background:${p ? '#FFFBEB' : 'transparent'};border-radius:10px;padding:12px;margin-bottom:14px">
    ${status}
    <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:10px">
      ${resume}
      <select class="form-input" id="cd-ai-pause-dur" style="width:auto;padding:4px 8px;font-size:12px">
        <option value="10">10 минут</option>
        <option value="30">30 минут</option>
        <option value="60" selected>1 час</option>
        <option value="1440">1 день</option>
        <option value="-1">навсегда</option>
      </select>
      <button class="btn btn-secondary btn-sm" onclick="pauseContactAgent('${c.id}')"><i class="fas fa-circle-pause"></i> ${pauseLabel}</button>
    </div>
  </div>`;
}

async function pauseContactAgent(contactId){
  const sel = document.getElementById('cd-ai-pause-dur');
  const v = parseInt(sel ? sel.value : '60', 10);
  const body = v < 0 ? { forever: true } : { minutes: v };
  await _aiPauseRequest(contactId, 'POST', body, 'Агент остановлен для этого клиента');
}

async function resumeContactAgent(contactId){
  await _aiPauseRequest(contactId, 'DELETE', null, 'Агент снова отвечает клиенту');
}

async function _aiPauseRequest(contactId, method, body, okText){
  try{
    const opts = { method };
    if(body) opts.body = JSON.stringify(body);
    const r = await apiFetch(API + '/contacts/' + contactId + '/ai-pause', opts);
    if(r && r.status === 200){
      const c = await r.json();
      const wrap = document.getElementById('cd-ai-pause');
      if(wrap) wrap.innerHTML = aiPauseBlockHtml(c);
      showToast(okText, 'success');
      const search = document.getElementById('contacts-search');
      const listOpen = !document.getElementById('contacts-list-modal-overlay')?.classList.contains('hidden');
      if(search && listOpen) loadContactsList(search.value);
      return;
    }
    const err = await r?.json().catch(()=>({}));
    showToast(errText(err.detail ?? err.message), 'error');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

// Значок «пауза» в списке клиентов и в карточке воронки.
function aiPauseBadge(c){
  if(!contactAiPause(c)) return '';
  return ` <span class="status-badge" title="${esc(aiPauseText(c))}" style="background:#FEF3C7;color:#92400E;font-size:10.5px;padding:1px 6px"><i class="fas fa-circle-pause"></i> пауза</span>`;
}

// ── SMS-переписка в карточке контакта ──
// outbound = от агента (вправо), inbound = от клиента (влево). Тред резолвится
// сервером по номеру (см. /api/agent/contacts/{id} → поле sms).
function _cdSmsBubble(m){
  const out = (m.direction === 'outbound');
  const who = !out ? 'Клиент' : (m.sent_by === 'owner' ? 'Вы' : 'Агент');
  const tm = m.ts ? fmtDate(m.ts) : '';
  // Вложения (голосовые, фото, документы, отправленные файлы): служебный
  // [file_id=…] нужен оркестратору, человеку показываем кнопку «Файл».
  const body = (m.body || '').replace(/\s*\[file_id=[0-9a-f-]+\]/g, '');
  const file = m.attachment_id
    ? `<div style="margin-top:4px"><span class="card-link" onclick="downloadAgentFile('${esc(m.attachment_id)}')"><i class="fas fa-download"></i> Файл</span></div>`
    : '';
  return `<div class="cd-sms ${out ? 'cd-sms-out' : 'cd-sms-in'}">
    <div class="cd-sms-meta">${who}${tm ? ' · ' + tm : ''}</div>
    <div class="cd-sms-body">${esc(body)}</div>${file}
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
  const val = id => (document.getElementById(id) || {}).value || '';
  const body = {
    name: val('cd-name').trim() || null,
    company: val('cd-company').trim() || null,
    position: val('cd-position').trim() || null,
    notes: val('cd-notes').trim() || null,
    // CRM v2: теги + сделка
    tags: val('cd-tags').split(',').map(t => t.trim()).filter(Boolean),
    deal_amount: val('cd-deal-amount') ? Number(val('cd-deal-amount')) : null,
    next_step_text: val('cd-next-text').trim() || null,
    next_step_at: val('cd-next-at') || null,  // datetime-local → ISO-ish
    lost_reason: val('cd-lost').trim() || null,
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
      showToast(errText(err.detail ?? err.message), 'error');
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


