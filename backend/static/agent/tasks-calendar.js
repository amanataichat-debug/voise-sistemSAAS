/* ============================================================================
 * agent/tasks-calendar.js — Календарь задач агента (модалка) → /api/agent/tasks
 * Часть страницы /static/agent.html (Voicyfy Agent).
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
// 'YYYY-MM-DD' для даты задачи в МСК
function mskDateKey(s){
  const d = new Date(s);
  if(isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);
}
function _mskTodayKey(){
  return new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
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

  // отображаемый месяц = текущий по МСК
  const today = _mskTodayKey();
  _tcalYear = parseInt(today.slice(0,4),10);
  _tcalMonth = parseInt(today.slice(5,7),10) - 1;

  // выбор по умолчанию: ближайший день с задачами (сегодня или позже), иначе первый
  const keys = Object.keys(_tcalByDay).sort();
  if(keys.length){
    _tcalSel = keys.find(k => k >= today) || keys[0];
    // показать месяц выбранного дня
    _tcalYear = parseInt(_tcalSel.slice(0,4),10);
    _tcalMonth = parseInt(_tcalSel.slice(5,7),10) - 1;
  }
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
    if(tasks && tasks.length){
      cls.push('has');
      const cnt = tasks.length > 9 ? '9+' : tasks.length;
      badge = `<span class="tcal-count" title="${tasks.length} ${pluralRu(tasks.length,'задача','задачи','задач')}">${cnt}</span>`;
    }
    if(key === today) cls.push('today');
    if(key === _tcalSel) cls.push('sel');
    html += `<div class="${cls.join(' ')}" onclick="_tcalSelectDay('${key}')"><span>${day}</span>${badge}</div>`;
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
  return `<div class="task-item" data-id="${t.id}">
    <div class="task-date"><div class="t" style="margin-top:0;font-size:13px">${tm}</div><div class="m">МСК</div></div>
    <div class="task-body">
      <div class="cd-task-clickable" onclick="_tcalToggleTask('${t.id}')">
        <div class="task-title">${esc(t.title||'Задача')} ${taskChannelBadge(t.channel)}</div>
        ${t.description?`<div class="task-desc">${esc(t.description)}</div>`:''}
        <div class="task-foot">
          <span class="task-contact">${esc(t.contact_name||'')}</span>
          <span class="task-when ${rel.soon?'soon':''}">${rel.text}</span>
        </div>
      </div>
      <div class="task-ed" id="tcal-task-ed-${t.id}" style="display:none">
        <input type="text" class="form-input" id="tcal-title-${t.id}" value="${esc(t.title||'')}" placeholder="Название задачи">
        <input type="datetime-local" class="form-input" id="tcal-time-${t.id}" value="${utcToMskInput(t.scheduled_time)}">
        <div class="task-ed-btns">
          <button class="btn btn-secondary btn-sm" onclick="_tcalToggleTask('${t.id}')">Отмена</button>
          <button class="btn btn-primary btn-sm" onclick="_tcalSaveTask('${t.id}','${key}')"><i class="fas fa-check"></i> Сохранить</button>
        </div>
      </div>
    </div>
    <button class="task-edit-btn" title="Изменить" onclick="_tcalToggleTask('${t.id}')"><i class="fas fa-pen"></i></button>
    <button class="task-del" title="Удалить задачу" onclick="_tcalDeleteTask('${t.id}','${key}')"><i class="fas fa-trash-alt"></i></button>
  </div>`;
}

function _tcalToggleTask(id){
  const ed = document.getElementById('tcal-task-ed-' + id);
  if(ed) ed.style.display = (ed.style.display === 'none' || !ed.style.display) ? 'flex' : 'none';
}

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

function _tcalRenderList(key){
  const head = document.getElementById('tcal-side-head');
  const sub  = document.getElementById('tcal-side-sub');
  const body = document.getElementById('tcal-side-body');
  const tasks = key ? _tcalByDay[key] : null;
  if(!key || !tasks || !tasks.length){
    head.textContent = key ? _tcalDateLabel(key) : 'Выберите день';
    sub.textContent  = 'Дни с задачами отмечены в календаре';
    body.innerHTML   = '<div class="tcal-empty">На этот день задач нет</div>';
    return;
  }
  head.textContent = _tcalDateLabel(key);
  sub.textContent  = `Задач: ${tasks.length}`;

  let html = '';
  if(tasks.length > TCAL_DAY_LIMIT){
    html += `<div class="tcal-warn"><i class="fas fa-circle-exclamation"></i> На этот день больше ${TCAL_DAY_LIMIT} задач (${tasks.length})</div>`;
    const shown = _tcalExpanded ? tasks : tasks.slice(0, TCAL_DAY_LIMIT);
    html += `<div class="tcal-list">${shown.map(t=>_tcalTaskRow(t,key)).join('')}</div>`;
    html += _tcalExpanded
      ? `<button class="tcal-more" onclick="_tcalToggleExpand()">Свернуть</button>`
      : `<button class="tcal-more" onclick="_tcalToggleExpand()">Показать все ${tasks.length}</button>`;
  } else {
    html += `<div class="tcal-list">${tasks.map(t=>_tcalTaskRow(t,key)).join('')}</div>`;
  }
  html += `<button class="tcal-del-day" onclick="tcalDeleteDayTasks('${key}')"><i class="fas fa-trash-alt"></i> Удалить задачи дня</button>`;
  body.innerHTML = html;
}

function _tcalToggleExpand(){
  _tcalExpanded = !_tcalExpanded;
  _tcalRenderList(_tcalSel);
}


