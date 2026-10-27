/* ============================================================================
 * agent/tasks-calendar.js — Календарь задач агента (модалка) → /api/agent/tasks
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

/* ── TASKS CALENDAR ── */
const TCAL_MONTHS = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
const TCAL_DAY_LIMIT = 5;            // порог "много задач" на один день
let _tcalByDay = {};                 // 'YYYY-MM-DD' -> [task, ...]
let _tcalYear = 0, _tcalMonth = 0;   // отображаемый месяц (0-based)
let _tcalSel = null;                 // выбранный день 'YYYY-MM-DD'
let _tcalExpanded = false;           // развёрнут ли список дня с >5 задачами

function _pad2(n){ return String(n).padStart(2,'0'); }
// Русский плюрал: pluralRu(2,'задача','задачи','задач') -> 'задачи'
function pluralRu(n, one, few, many){
  const m10 = n % 10, m100 = n % 100;
  if(m10 === 1 && m100 !== 11) return one;
  if(m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}
// 'YYYY-MM-DD' для даты задачи в Бишкеке. Имя сохранено ради 1:1-совместимости.
function mskDateKey(s){
  const d = new Date(s);
  if(isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Bishkek',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);
}
function _mskTodayKey(){
  return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Bishkek',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
}

async function openTasksCalendar(){
  document.getElementById('tasks-cal-modal-overlay').classList.remove('hidden');
  const grid = document.getElementById('tcal-grid');
  grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:30px"><div class="spinner" style="margin:0 auto"></div></div>';
  _tcalByDay = {}; _tcalSel = null; _tcalExpanded = false;
  try{
    const r = await apiFetch(API + '/tasks?status=scheduled&limit=500');
    const data = (r && r.status===200) ? await r.json() : {tasks:[]};
    (data.tasks || []).forEach(t => {
      const k = t.scheduled_time ? mskDateKey(t.scheduled_time) : null;
      if(!k) return;
      (_tcalByDay[k] = _tcalByDay[k] || []).push(t);
    });
    // сортируем задачи внутри дня по времени
    Object.values(_tcalByDay).forEach(arr => arr.sort((a,b)=> new Date(a.scheduled_time)-new Date(b.scheduled_time)));
  }catch(e){ _tcalByDay = {}; }

  // Отображаемый месяц = текущий, выбранный день = СЕГОДНЯ (не прыгаем на
  // первую задачу — пользователь хочет видеть «что сегодня», а задачи
  // других дней уже видны по бейджам в клетках).
  const today = _mskTodayKey();
  _tcalYear = parseInt(today.slice(0,4),10);
  _tcalMonth = parseInt(today.slice(5,7),10) - 1;
  _tcalSel = today;
  _tcalRenderGrid();
  _tcalRenderList(_tcalSel);
}
function closeTasksCalendar(){ document.getElementById('tasks-cal-modal-overlay').classList.add('hidden'); }

function tcalShiftMonth(delta){
  _tcalMonth += delta;
  if(_tcalMonth < 0){ _tcalMonth = 11; _tcalYear--; }
  else if(_tcalMonth > 11){ _tcalMonth = 0; _tcalYear++; }
  _tcalRenderGrid();
}

function _tcalRenderGrid(){
  document.getElementById('tcal-month').textContent = TCAL_MONTHS[_tcalMonth] + ' ' + _tcalYear;
  const today = _mskTodayKey();
  const firstDow = (new Date(Date.UTC(_tcalYear,_tcalMonth,1)).getUTCDay() + 6) % 7; // Пн=0
  const daysInMonth = new Date(Date.UTC(_tcalYear,_tcalMonth+1,0)).getUTCDate();
  let html = '';
  for(let i=0;i<firstDow;i++) html += '<div class="tcal-day empty"></div>';
  for(let day=1; day<=daysInMonth; day++){
    const key = `${_tcalYear}-${_pad2(_tcalMonth+1)}-${_pad2(day)}`;
    const tasks = _tcalByDay[key];
    const cls = ['tcal-day'];
    let badge = '';
    let preview = '';
    if(tasks && tasks.length){
      cls.push('has');
      const cnt = tasks.length > 9 ? '9+' : tasks.length;
      badge = `<span class="tcal-count" title="${tasks.length} ${pluralRu(tasks.length,'задача','задачи','задач')}">${cnt}</span>`;
      // Превью первых 2-х задач прямо в клетке — чтобы было видно, что это за задачи
      preview = '<div class="tcal-day-tasks">' +
        tasks.slice(0, 2).map(t => {
          const tm = t.scheduled_time ? ((mskParts(t.scheduled_time) || {}).time || '') : '';
          const title = (t.title || 'Задача').slice(0, 24);
          return `<div class="tcal-day-task" title="${esc(t.title || '')}">${tm ? `<b>${tm}</b> ` : ''}${esc(title)}</div>`;
        }).join('') +
        (tasks.length > 2 ? `<div class="tcal-day-more">+${tasks.length - 2}</div>` : '') +
        '</div>';
    }
    if(key === today) cls.push('today');
    if(key === _tcalSel) cls.push('sel');
    html += `<div class="${cls.join(' ')}" onclick="_tcalSelectDay('${key}')"><span class="tcal-day-num">${day}</span>${badge}${preview}</div>`;
  }
  document.getElementById('tcal-grid').innerHTML = html;
}

function _tcalSelectDay(key){
  _tcalSel = key;
  _tcalExpanded = false;
  _tcalRenderGrid();
  _tcalRenderList(key);
}

function _tcalDateLabel(key){
  // 'YYYY-MM-DD' -> '5 июня 2026, пятница'
  const [y,m,d] = key.split('-').map(Number);
  const dt = new Date(Date.UTC(y,m-1,d,12,0,0));
  return new Intl.DateTimeFormat('ru-RU',{timeZone:'UTC',day:'numeric',month:'long',year:'numeric',weekday:'long'}).format(dt);
}

function _tcalTaskRow(t, key){
  const tm = t.scheduled_time ? (mskParts(t.scheduled_time)||{}).time || '' : '';
  const rel = relTime(t.scheduled_time);
  const contactId = t.agent_contact_id || '';
  const dateFull = t.scheduled_time ? fmtDate(t.scheduled_time) : '';
  return `<div class="task-item" data-id="${t.id}">
    <div class="task-date"><div class="t" style="margin-top:0;font-size:13px">${tm}</div><div class="m">Бишкек</div></div>
    <div class="task-body">
      <div class="cd-task-clickable" onclick="_tcalToggleDetails('${t.id}')" title="Нажмите для подробностей">
        <div class="task-title">${esc(t.title||'Задача')} ${taskChannelBadge(t.channel)}</div>
        ${t.description?`<div class="task-desc">${esc(t.description)}</div>`:''}
        <div class="task-foot">
          <span class="task-contact">${esc(t.contact_name||'')}</span>
          <span class="task-when ${rel.soon?'soon':''}">${rel.text}</span>
        </div>
      </div>

      <!-- Подробности задачи — раскрываются по клику на тело -->
      <div class="task-details" id="tcal-task-det-${t.id}" style="display:none">
        <div class="td-grid">
          <div class="td-cell"><span class="td-k">Время</span><span class="td-v">${esc(dateFull || '—')}</span></div>
          <div class="td-cell"><span class="td-k">Канал</span><span class="td-v">${taskChannelBadge(t.channel) || (t.channel === 'telegram' ? 'Telegram' : 'Звонок')}</span></div>
          ${t.contact_name ? `<div class="td-cell"><span class="td-k">Клиент</span><span class="td-v">${esc(t.contact_name)}</span></div>` : ''}
          ${t.contact_phone ? `<div class="td-cell"><span class="td-k">Телефон</span><span class="td-v">${esc(t.contact_phone)}</span></div>` : ''}
        </div>
        ${t.description ? `<div class="td-desc"><div class="td-k">Описание</div><div class="td-text">${esc(t.description)}</div></div>` : ''}
        ${t.custom_greeting ? `<div class="td-desc"><div class="td-k">Инструкция (${t.channel === 'telegram' ? 'текст сообщения' : 'приветствие'})</div><div class="td-text">${esc(t.custom_greeting)}</div></div>` : ''}
        <div class="td-actions">
          ${contactId ? `<button class="btn btn-primary btn-sm" onclick="_tcalOpenContact('${contactId}')"><i class="fas fa-arrow-up-right-from-square"></i> Открыть клиента</button>` : ''}
          <button class="btn btn-secondary btn-sm" onclick="_tcalStartEdit('${t.id}')"><i class="fas fa-pen"></i> Изменить</button>
          <button class="btn btn-secondary btn-sm" style="margin-left:auto;color:var(--vf-danger)" onclick="_tcalDeleteTask('${t.id}','${key}')"><i class="far fa-trash-can"></i> Удалить</button>
        </div>
      </div>

      <!-- Форма редактирования — показывается из details по «Изменить» -->
      <div class="task-ed" id="tcal-task-ed-${t.id}" style="display:none">
        <input type="text" class="form-input" id="tcal-title-${t.id}" value="${esc(t.title||'')}" placeholder="Название задачи">
        <input type="datetime-local" class="form-input" id="tcal-time-${t.id}" value="${utcToMskInput(t.scheduled_time)}">
        <div class="task-ed-btns">
          <button class="btn btn-secondary btn-sm" onclick="_tcalCancelEdit('${t.id}')">Отмена</button>
          <button class="btn btn-primary btn-sm" onclick="_tcalSaveTask('${t.id}','${key}')"><i class="fas fa-check"></i> Сохранить</button>
        </div>
      </div>
    </div>
  </div>`;
}

// Клик по телу задачи → раскрыть/свернуть панель подробностей.
// Повторный клик по тому же — свернуть. По другой — свернуть предыдущую, открыть новую.
function _tcalToggleDetails(id){
  const det = document.getElementById('tcal-task-det-' + id);
  if(!det) return;
  const willOpen = (det.style.display === 'none' || !det.style.display);
  // Свернуть все остальные details + все edit-формы
  document.querySelectorAll('.task-details').forEach(el => { el.style.display = 'none'; });
  document.querySelectorAll('.task-ed').forEach(el => { el.style.display = 'none'; });
  if(willOpen) det.style.display = 'flex';
}
// Переход на карточку клиента — используем вкладочную навигацию.
function _tcalOpenContact(contactId){
  if(!contactId) return;
  closeTasksCalendar();
  // открываем вкладку «Клиенты» и там подтягиваем карточку
  if(typeof switchTab === 'function') switchTab('clients');
  if(typeof openContactDetailsModal === 'function'){
    setTimeout(() => openContactDetailsModal(contactId), 120);
  }
}
// Переход details → edit (из details по «Изменить»)
function _tcalStartEdit(id){
  const det = document.getElementById('tcal-task-det-' + id);
  const ed = document.getElementById('tcal-task-ed-' + id);
  if(det) det.style.display = 'none';
  if(ed) ed.style.display = 'flex';
}
function _tcalCancelEdit(id){
  const ed = document.getElementById('tcal-task-ed-' + id);
  if(ed) ed.style.display = 'none';
}
// Старое имя оставлено для обратной совместимости (если кто-то зовёт извне)
function _tcalToggleTask(id){ _tcalToggleDetails(id); }

async function _tcalSaveTask(id, key){
  const title = (document.getElementById('tcal-title-' + id).value || '').trim();
  const timeV = document.getElementById('tcal-time-' + id).value;
  if(!title){ showToast('Введите название задачи', 'error'); return; }
  if(!timeV){ showToast('Укажите дату и время', 'error'); return; }
  const iso = mskInputToUtc(timeV);
  if(!iso){ showToast('Некорректная дата', 'error'); return; }
  try{
    const r = await apiFetch(API + '/tasks/' + id, { method:'PUT', body: JSON.stringify({ title, scheduled_time: iso }) });
    if(!r || r.status !== 200){ showToast('Не удалось сохранить задачу', 'error'); return; }
    const upd = await r.json();
    // вытащить задачу из старого дня (сохранив contact_name и пр.)
    let task = null;
    if(_tcalByDay[key]){
      const i = _tcalByDay[key].findIndex(x => String(x.id) === String(id));
      if(i >= 0){ task = _tcalByDay[key][i]; _tcalByDay[key].splice(i, 1); if(!_tcalByDay[key].length) delete _tcalByDay[key]; }
    }
    task = { ...(task || {}), ...upd };
    const newKey = mskDateKey(upd.scheduled_time) || key;
    (_tcalByDay[newKey] = _tcalByDay[newKey] || []).push(task);
    _tcalByDay[newKey].sort((a,b)=> new Date(a.scheduled_time)-new Date(b.scheduled_time));
    // если день сменился — переходим на него
    _tcalSel = newKey;
    _tcalExpanded = false;
    _tcalYear = parseInt(newKey.slice(0,4),10);
    _tcalMonth = parseInt(newKey.slice(5,7),10) - 1;
    _tcalRenderGrid();
    _tcalRenderList(_tcalSel);
    loadTasks();
    showToast(newKey !== key ? 'Задача перенесена' : 'Задача обновлена', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

async function _tcalDeleteTask(id, key){
  if(!confirm('Удалить эту задачу? Запланированный звонок не будет выполнен.')) return;
  try{
    const r = await apiFetch(API + '/tasks/' + id, { method:'DELETE' });
    if(!r || (r.status!==200 && r.status!==204)){ showToast('Не удалось удалить задачу','error'); return; }
    // убираем из локального кэша и перерисовываем
    if(_tcalByDay[key]){
      _tcalByDay[key] = _tcalByDay[key].filter(t => String(t.id) !== String(id));
      if(!_tcalByDay[key].length) delete _tcalByDay[key];
    }
    _tcalRenderGrid();
    _tcalRenderList(key);
    loadTasks();   // обновляем виджет "Ближайшие задачи"
    showToast('Задача удалена','success');
  }catch(e){ showToast('Ошибка сети','error'); }
}

// Всего задач в кэше календаря
function _tcalTotalCount(){
  return Object.values(_tcalByDay).reduce((s, arr) => s + arr.length, 0);
}

// Удалить ВСЕ запланированные задачи агента
async function tcalDeleteAllTasks(){
  const n = _tcalTotalCount();
  if(!n){ showToast('Нет запланированных задач', 'error'); return; }
  const msg = `Удалить все запланированные задачи (${n} ${pluralRu(n,'задача','задачи','задач')})? Запланированные звонки не будут выполнены.`;
  if(!confirm(msg)) return;
  try{
    const r = await apiFetch(API + '/tasks', { method:'DELETE' });
    if(!r || r.status !== 200){ showToast('Не удалось удалить задачи', 'error'); return; }
    const data = await r.json();
    _tcalByDay = {}; _tcalExpanded = false;
    _tcalRenderGrid();
    _tcalRenderList(_tcalSel);
    loadTasks();   // обновляем виджет "Ближайшие задачи"
    const cnt = data.deleted || 0;
    showToast(`Удалено ${cnt} ${pluralRu(cnt,'задача','задачи','задач')}`, 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

// Удалить все задачи одного дня (key = 'YYYY-MM-DD' МСК)
async function tcalDeleteDayTasks(key){
  const tasks = _tcalByDay[key] || [];
  if(!tasks.length){ showToast('На этот день задач нет', 'error'); return; }
  const n = tasks.length;
  const msg = `Удалить все задачи на ${_tcalDateLabel(key)} (${n} ${pluralRu(n,'задача','задачи','задач')})? Запланированные звонки не будут выполнены.`;
  if(!confirm(msg)) return;
  try{
    const r = await apiFetch(API + '/tasks?date=' + encodeURIComponent(key), { method:'DELETE' });
    if(!r || r.status !== 200){ showToast('Не удалось удалить задачи', 'error'); return; }
    const data = await r.json();
    delete _tcalByDay[key];
    _tcalExpanded = false;
    _tcalRenderGrid();
    _tcalRenderList(key);
    loadTasks();   // обновляем виджет "Ближайшие задачи"
    const cnt = data.deleted || 0;
    showToast(`Удалено ${cnt} ${pluralRu(cnt,'задача','задачи','задач')}`, 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

// Строка задачи в едином списке (с меткой даты и подсветкой если выбрана).
function _tcalListRow(t, dayKey, selected){
  const tm = t.scheduled_time ? ((mskParts(t.scheduled_time) || {}).time || '') : '';
  const d = new Date(t.scheduled_time);
  const dayLabel = d.toLocaleDateString('ru-RU', { day:'2-digit', month:'short' });
  const rel = relTime(t.scheduled_time);
  const contactId = t.agent_contact_id || '';
  const overdue = t.scheduled_time ? (new Date(t.scheduled_time) < new Date() && dayKey < _mskTodayKey()) : false;
  const soon = rel.soon && !overdue;
  const whenText = overdue ? 'Просрочена' : rel.text;
  return `
    <div class="task-item tcal-list-row ${selected ? 'is-selected' : ''} ${overdue ? 'is-overdue' : ''}" data-id="${t.id}" data-day="${dayKey}">
      <div class="task-date tcal-list-date"><div class="t">${tm}</div><div class="m">${dayLabel}</div></div>
      <div class="task-body">
        <div class="cd-task-clickable" onclick="_tcalFocusTask('${t.id}', '${dayKey}')" title="Нажмите для подробностей">
          <div class="task-title">${esc(t.title || 'Задача')} ${taskChannelBadge(t.channel)}</div>
          ${t.contact_name ? `<div class="task-foot"><span class="task-contact">${esc(t.contact_name)}${t.contact_phone ? ` · ${esc(t.contact_phone)}` : ''}</span><span class="task-when ${soon ? 'soon' : ''} ${overdue ? 'overdue' : ''}">${whenText}</span></div>` : ''}
        </div>
        ${contactId ? `<button class="task-quick-goto" onclick="event.stopPropagation();_tcalOpenContact('${contactId}')" title="Открыть карточку клиента"><i class="fas fa-arrow-up-right-from-square"></i></button>` : ''}

        <!-- Подробности (та же разметка, что раньше внутри _tcalTaskRow) -->
        <div class="task-details" id="tcal-task-det-${t.id}" style="display:none">
          <div class="td-grid">
            <div class="td-cell"><span class="td-k">Время</span><span class="td-v">${esc(fmtDate(t.scheduled_time) || '—')}</span></div>
            <div class="td-cell"><span class="td-k">Канал</span><span class="td-v">${taskChannelBadge(t.channel) || 'Звонок'}</span></div>
            ${t.contact_name ? `<div class="td-cell"><span class="td-k">Клиент</span><span class="td-v">${esc(t.contact_name)}</span></div>` : ''}
            ${t.contact_phone ? `<div class="td-cell"><span class="td-k">Телефон</span><span class="td-v">${esc(t.contact_phone)}</span></div>` : ''}
          </div>
          ${t.description ? `<div class="td-desc"><div class="td-k">Описание</div><div class="td-text">${esc(t.description)}</div></div>` : ''}
          ${t.custom_greeting ? `<div class="td-desc"><div class="td-k">Инструкция</div><div class="td-text">${esc(t.custom_greeting)}</div></div>` : ''}
          <div class="td-actions">
            ${contactId ? `<button class="btn btn-primary btn-sm" onclick="_tcalOpenContact('${contactId}')"><i class="fas fa-arrow-up-right-from-square"></i> Открыть клиента</button>` : ''}
            <button class="btn btn-secondary btn-sm" onclick="_tcalStartEdit('${t.id}')"><i class="fas fa-pen"></i> Изменить</button>
            <button class="btn btn-secondary btn-sm" style="margin-left:auto;color:var(--vf-danger)" onclick="_tcalDeleteTask('${t.id}','${dayKey}')"><i class="far fa-trash-can"></i> Удалить</button>
          </div>
        </div>
        <!-- Форма редактирования -->
        <div class="task-ed" id="tcal-task-ed-${t.id}" style="display:none">
          <input type="text" class="form-input" id="tcal-title-${t.id}" value="${esc(t.title||'')}" placeholder="Название задачи">
          <input type="datetime-local" class="form-input" id="tcal-time-${t.id}" value="${utcToMskInput(t.scheduled_time)}">
          <div class="task-ed-btns">
            <button class="btn btn-secondary btn-sm" onclick="_tcalCancelEdit('${t.id}')">Отмена</button>
            <button class="btn btn-primary btn-sm" onclick="_tcalSaveTask('${t.id}','${dayKey}')"><i class="fas fa-check"></i> Сохранить</button>
          </div>
        </div>
      </div>
    </div>`;
}

// Клик по строке в списке: раскрыть подробности + переключить день в календаре.
function _tcalFocusTask(id, dayKey){
  _tcalToggleDetails(id);  // переключение panel как было
  // если открыли (не закрыли) — ведём календарь на этот день
  const det = document.getElementById('tcal-task-det-' + id);
  if(det && det.style.display !== 'none'){
    if(dayKey && _tcalSel !== dayKey){
      _tcalSel = dayKey;
      _tcalYear = parseInt(dayKey.slice(0,4),10);
      _tcalMonth = parseInt(dayKey.slice(5,7),10) - 1;
      _tcalRenderGrid();
    }
  }
}

function _tcalRenderList(key){
  const head = document.getElementById('tcal-side-head');
  const sub  = document.getElementById('tcal-side-sub');
  const body = document.getElementById('tcal-side-body');

  // Собираем ВСЕ задачи с today и позже + просроченные (<today ∧ scheduled)
  const today = _mskTodayKey();
  const allTasks = [];
  Object.keys(_tcalByDay).sort().forEach(k => {
    _tcalByDay[k].forEach(t => allTasks.push({ ...t, _dayKey: k }));
  });
  allTasks.sort((a, b) => new Date(a.scheduled_time) - new Date(b.scheduled_time));

  // Группируем по дню (today и позже + просроченные отдельной группой сверху)
  const overdue = allTasks.filter(t => t._dayKey < today);
  const future = allTasks.filter(t => t._dayKey >= today);
  const byDay = {};
  future.forEach(t => { (byDay[t._dayKey] = byDay[t._dayKey] || []).push(t); });

  head.textContent = 'Все задачи';
  const totalFuture = future.length;
  const totalOverdue = overdue.length;
  sub.textContent = totalFuture + totalOverdue === 0
    ? 'Запланированных задач нет'
    : `Актуальных: ${totalFuture}${totalOverdue ? ` · просрочено: ${totalOverdue}` : ''}`;

  if(!totalFuture && !totalOverdue){
    body.innerHTML = `<div class="tcal-empty">Запланированных задач пока нет.<br><span style="color:var(--vf-text-4);font-size:12px">Поставить новую — через чат («Позвонить Айко завтра в 15:00») или кнопку в карточке клиента.</span></div>`;
    return;
  }

  let html = '';
  // Просроченные сверху — чтобы владелец сразу их видел
  if(overdue.length){
    html += `<div class="tcal-group tcal-group-overdue"><div class="tcal-group-head"><i class="fas fa-triangle-exclamation"></i> Просрочено <span class="tcal-group-n">${overdue.length}</span></div>${overdue.map(t => _tcalListRow(t, t._dayKey, key === t._dayKey)).join('')}</div>`;
  }
  // Сгруппированный по дням актуальный список
  Object.keys(byDay).sort().forEach(k => {
    const label = _tcalDayGroupLabel(k, today);
    const n = byDay[k].length;
    const sel = (key === k) ? ' is-active' : '';
    html += `<div class="tcal-group${sel}"><div class="tcal-group-head" onclick="_tcalSelectDay('${k}')" title="Показать в календаре">${esc(label)} <span class="tcal-group-n">${n}</span></div>${byDay[k].map(t => _tcalListRow(t, k, key === k)).join('')}</div>`;
  });
  body.innerHTML = html;
}

// Удобное название группы: Сегодня / Завтра / <день месяца>
function _tcalDayGroupLabel(key, todayKey){
  if(key === todayKey) return 'Сегодня';
  const [y, m, d] = key.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m-1, d, 12));
  const tdt = new Date(Date.UTC(parseInt(todayKey.slice(0,4),10), parseInt(todayKey.slice(5,7),10)-1, parseInt(todayKey.slice(8,10),10), 12));
  const diff = Math.round((dt - tdt) / (24 * 3600 * 1000));
  if(diff === 1) return 'Завтра';
  return new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', day: 'numeric', month: 'long', weekday: 'long' }).format(dt);
}

function _tcalToggleExpand(){
  _tcalExpanded = !_tcalExpanded;
  _tcalRenderList(_tcalSel);
}


