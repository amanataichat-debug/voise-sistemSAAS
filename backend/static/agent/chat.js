/* ============================================================================
 * agent/chat.js — Чат с оркестратором (стриминг ответов и tool-status) → /api/agent/chat[/stream]
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ════════════════ CHAT ════════════════
// Пул из 25 подсказок. При каждом новом чате показываем случайные 10.
// icon — FontAwesome, label — текст на плитке, prompt — что уйдёт агенту.
const CHAT_SUGGESTIONS = [
  { icon:'fa-chart-pie',        label:'Статистика агента',     prompt:'Покажи мою статистику за всё время' },
  { icon:'fa-compass',          label:'Воронка контактов',     prompt:'Покажи разбивку контактов по стадиям воронки' },
  { icon:'fa-person-walking',   label:'Кто в работе',          prompt:'Покажи контакты в стадии «в работе»' },
  { icon:'fa-circle-check',     label:'Успешные',              prompt:'Покажи контакты со стадией «успех»' },
  { icon:'fa-ban',              label:'Отказы',                prompt:'Покажи контакты, которые отказались' },
  { icon:'fa-user-plus',        label:'Последние контакты',    prompt:'Покажи последние добавленные контакты' },
  { icon:'fa-calendar-day',     label:'Расписание на сегодня', prompt:'Какие звонки запланированы на сегодня?' },
  { icon:'fa-calendar-week',    label:'Расписание на неделю',  prompt:'Покажи расписание звонков на ближайшую неделю' },
  { icon:'fa-forward',          label:'Ближайшие звонки',      prompt:'Покажи мои ближайшие запланированные звонки' },
  { icon:'fa-calendar-plus',    label:'Запланировать звонок',  prompt:'Помоги запланировать звонок контакту' },
  { icon:'fa-phone-slash',      label:'Недозвоны',             prompt:'Покажи, кому не дозвонились — очередь на перезвон' },
  { icon:'fa-chart-line',       label:'Отчёт за неделю',       prompt:'Сделай отчёт по звонкам за последнюю неделю' },
  { icon:'fa-calendar-check',   label:'Отчёт за месяц',        prompt:'Сделай отчёт по звонкам за последний месяц' },
  { icon:'fa-clock-rotate-left',label:'Последние звонки',      prompt:'Покажи историю последних звонков' },
  { icon:'fa-bullseye',         label:'Конверсия',             prompt:'Какая у меня конверсия звонков за неделю?' },
  { icon:'fa-user',             label:'Добавить контакт',      prompt:'Хочу добавить новый контакт в базу обзвона' },
  { icon:'fa-file-import',      label:'Импорт списка',         prompt:'Помоги загрузить список контактов для обзвона' },
  { icon:'fa-rocket',           label:'Обзвонить новых',       prompt:'Запланируй обзвон всех новых контактов' },
  { icon:'fa-phone-volume',     label:'Позвонить сейчас',      prompt:'Позвони контакту прямо сейчас' },
  { icon:'fa-circle-pause',     label:'Пауза по контакту',     prompt:'Поставь контакт на паузу — не звонить какое-то время' },
  { icon:'fa-lightbulb',        label:'Что ты умеешь',         prompt:'Что ты умеешь? Расскажи о своих возможностях' },
  { icon:'fa-mug-hot',          label:'Итоги дня',             prompt:'Подведи итоги звонков за сегодня' },
  { icon:'fa-clock',            label:'Лучшее время звонка',   prompt:'В какое время лучше звонить моим контактам?' },
  { icon:'fa-rotate',           label:'Кого перезвонить',      prompt:'Кому стоит перезвонить в первую очередь?' },
  { icon:'fa-receipt',          label:'Сводка по агенту',      prompt:'Дай краткую сводку: контакты, задачи, звонки' },
];

function pickSuggestions(n){
  const arr = CHAT_SUGGESTIONS.slice();
  for(let i=arr.length-1; i>0; i--){ const j=Math.floor(Math.random()*(i+1)); [arr[i],arr[j]]=[arr[j],arr[i]]; }
  return arr.slice(0, n);
}

// Рисует приветственный экран с 10 случайными плитками в окно чата.
function renderWelcome(){
  const msgs = document.getElementById('chat-messages');
  if(!msgs) return;
  msgs.innerHTML = '';
  const tiles = pickSuggestions(10).map(s =>
    `<button class="chat-tile" onclick="suggestionClick(this)" data-prompt="${esc(s.prompt)}">
       <span class="chat-tile-ic"><i class="fas ${s.icon}"></i></span>
       <span class="chat-tile-label">${esc(s.label)}</span>
     </button>`
  ).join('');
  const wrap = document.createElement('div');
  wrap.className = 'chat-welcome';
  wrap.id = 'chat-welcome';
  wrap.innerHTML =
    `<img class="chat-welcome-img" src="/static/images/IMG_2820.PNG" alt="Voicyfy">
     <div class="chat-welcome-title">Привет!<br>Я ваш агент Voicyfy</div>
     <div class="chat-welcome-sub">Помогаю управлять обзвоном: контакты, задачи, звонки и аналитика.<br>Выберите подсказку или напишите запрос сами.</div>
     <div class="chat-tiles">${tiles}</div>`;
  msgs.appendChild(wrap);
}

// Убирает приветственный экран (когда начинается диалог).
function hideWelcome(){
  const w = document.getElementById('chat-welcome');
  if(w) w.remove();
}

// Клик по плитке → отправляем её текст как сообщение агенту.
function suggestionClick(el){
  const prompt = el.getAttribute('data-prompt') || '';
  if(!prompt) return;
  const inp = document.getElementById('chat-input');
  if(inp) inp.value = prompt;
  sendMessage();
}

// Начать новый диалог: чистим серверную историю + показываем welcome заново.
async function newChat(){
  const btn = document.getElementById('chat-new-btn');
  if(btn) btn.disabled = true;
  try{ await apiFetch(API + '/chat/clear', { method:'POST' }); }catch(e){}
  renderWelcome();
  if(btn) btn.disabled = false;
  const inp = document.getElementById('chat-input');
  if(inp) inp.focus();
}

const TOOL_LABELS = {
  get_contact_call_history:'Изучаю историю звонков', get_agent_contacts:'Загружаю контакты',
  get_agent_stats:'Считаю статистику', get_agent_tasks:'Проверяю задачи',
  create_agent_contact:'Создаю контакт', create_agent_task:'Планирую звонок',
  update_contact_memory:'Обновляю память контакта', send_telegram_notification:'Отправляю уведомление',
  update_contact_info:'Обновляю данные контакта', move_contact_stage:'Меняю стадию воронки',
  delete_agent_task:'Удаляю задачу',
  search_contacts:'Ищу контакты', get_contact_details:'Открываю карточку контакта',
  get_contacts_by_stage:'Считаю воронку', bulk_create_contacts:'Добавляю контакты',
  delete_agent_contact:'Удаляю контакт', append_contact_note:'Дописываю заметку',
  update_agent_task:'Переношу звонок', get_upcoming_schedule:'Смотрю расписание',
  bulk_schedule_calls:'Планирую серию звонков', trigger_immediate_call:'Звоню прямо сейчас',
  snooze_contact:'Ставлю контакт на паузу', get_call_transcript:'Открываю транскрипт',
  get_period_report:'Готовлю отчёт за период', get_failed_calls:'Собираю недозвоны',
};

// Иконки инструментов для процесс-трейса (Фаза 4). Ключи — имена tools оркестратора.
const TOOL_ICONS = {
  get_contact_call_history:'fa-clock-rotate-left', get_agent_contacts:'fa-users',
  get_agent_stats:'fa-chart-pie', get_agent_tasks:'fa-calendar-check',
  create_agent_contact:'fa-user-plus', create_agent_task:'fa-calendar-plus',
  update_contact_memory:'fa-brain', send_telegram_notification:'fa-paper-plane',
  update_contact_info:'fa-user-pen', move_contact_stage:'fa-shuffle',
  delete_agent_task:'fa-trash-can',
  search_contacts:'fa-magnifying-glass', get_contact_details:'fa-id-card',
  get_contacts_by_stage:'fa-filter', bulk_create_contacts:'fa-users-rectangle',
  delete_agent_contact:'fa-user-minus', append_contact_note:'fa-note-sticky',
  update_agent_task:'fa-pen-to-square', get_upcoming_schedule:'fa-calendar-days',
  bulk_schedule_calls:'fa-calendar-plus', trigger_immediate_call:'fa-phone-volume',
  snooze_contact:'fa-circle-pause', get_call_transcript:'fa-file-lines',
  get_period_report:'fa-chart-line', get_failed_calls:'fa-phone-slash',
};

async function sendMessage(){
  const inp = document.getElementById('chat-input');
  const text = inp.value.trim();
  if(!text) return;
  inp.value=''; inp.style.height='auto';
  hideWelcome();
  addBubble(text,'user');
  document.getElementById('chat-send').disabled = true;
  const t0 = Date.now();

  // Fallback на старый /chat (legacy v2-агенты или сбой стрима).
  async function fallbackToPlainChat(stream){
    if(stream) stream.remove();
    const tId = showTyping();
    try{
      const r = await apiFetch(API + '/chat', { method:'POST', body:JSON.stringify({ message:text }) });
      removeTyping(tId);
      if(r && r.status===200){
        const d = await r.json();
        const el = ((Date.now()-t0)/1000).toFixed(1);
        addAgentBubble(d.reply, d.debug_log||[], el);
        loadStats(); loadRecentCalls(); loadTasks();
      } else {
        const err = await r?.json().catch(()=>({}));
        addBubble(errText(err.detail),'assistant');
      }
    }catch(e){ removeTyping(tId); addBubble('Ошибка сети.','assistant'); }
  }

  const stream = createStreamingBubble();
  let st = { acc:'', steps:[], t0, raf:null, dirty:false, done:false,
             wrap:stream.wrap, activity:stream.activity, working:stream.working,
             stepsEl:stream.stepsEl, answer:stream.answer };
  try{
    const token = getToken();
    if(!token){ location.href='/static/login.html'; return; }
    const resp = await fetch(withAgentId(API + '/chat/stream'), {
      method:'POST',
      headers:{ 'Authorization':'Bearer '+token, 'Content-Type':'application/json' },
      body: JSON.stringify({ message:text }),
    });

    if(resp.status===401){ localStorage.removeItem('auth_token'); location.href='/static/login.html'; return; }
    if(!resp.ok || !resp.body){
      // 409 (legacy v2) или сетевой сбой → fallback на обычный /chat.
      if(resp.status===409){ await fallbackToPlainChat(stream.wrap); document.getElementById('chat-send').disabled=false; return; }
      if(resp.status===402){ await handle402(resp); stream.wrap.remove(); document.getElementById('chat-send').disabled=false; return; }
      if(resp.status===400 || resp.status===404){
        const err = await resp.json().catch(()=>({}));
        stream.wrap.remove(); addBubble(errText(err.detail),'assistant');
        document.getElementById('chat-send').disabled=false; return;
      }
      await fallbackToPlainChat(stream.wrap); document.getElementById('chat-send').disabled=false; return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while(true){
      const { value, done } = await reader.read();
      if(done) break;
      buf += decoder.decode(value, { stream:true });
      let nl;
      while((nl = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl+1);
        if(line) handleStreamEvent(JSON.parse(line), st);
      }
    }
    if(buf.trim()){ try{ handleStreamEvent(JSON.parse(buf.trim()), st); }catch(e){} }

    if(!st.done){
      // Поток оборвался без события done — fallback, чтобы не виснуть.
      await fallbackToPlainChat(stream.wrap);
    } else {
      loadStats(); loadRecentCalls(); loadTasks();
    }
  }catch(e){
    if(st.raf) cancelAnimationFrame(st.raf);
    if(!st.done){ await fallbackToPlainChat(stream.wrap); }
  }finally{
    document.getElementById('chat-send').disabled = false;
  }
}

// Создаёт пустой пузырь ассистента с индикатором «думаю» и строкой статуса.
function createStreamingBubble(){
  const msgs = document.getElementById('chat-messages');
  const div = document.createElement('div'); div.className='msg assistant';
  div.innerHTML =
    `<div class="msg-avatar"><img src="/static/images/IMG_2820.PNG" alt="Voicyfy"></div>`+
    `<div class="msg-body">`+
      `<div class="activity" data-activity>`+
        `<div class="activity-working" data-working><span class="aw-dot"></span><span class="aw-text">Думаю…</span></div>`+
        `<div class="activity-steps" data-steps></div>`+
      `</div>`+
      `<div class="msg-bubble md answer" data-answer></div>`+
    `</div>`;
  msgs.appendChild(div); msgs.scrollTop = msgs.scrollHeight;
  return {
    wrap: div,
    activity: div.querySelector('[data-activity]'),
    working: div.querySelector('[data-working]'),
    stepsEl: div.querySelector('[data-steps]'),
    answer: div.querySelector('[data-answer]'),
  };
}

function _streamRender(st){
  st.dirty = false;
  st.answer.innerHTML = renderMarkdown(st.acc);
  const msgs = document.getElementById('chat-messages');
  msgs.scrollTop = msgs.scrollHeight;
}

// Краткий итог результата инструмента для строки трейса (число найденного / ошибка).
function _stepMeta(res){
  if(res && typeof res==='object'){
    if(res.count!==undefined) return String(res.count);
    if(res.ok===false) return 'ошибка';
  }
  return '';
}

// HTML одной строки процесс-трейса (иконка инструмента + название + статус + итог).
function stepRowHTML(s){
  const icon = TOOL_ICONS[s.tool] || 'fa-gear';
  const label = TOOL_LABELS[s.tool] || s.tool || 'Инструмент';
  let trail;
  if(s.status==='running') trail = '<span class="step-spin"></span>';
  else if(s.status==='error') trail = '<span class="step-state err">✕</span>';
  else trail = '<span class="step-state ok">✓</span>';
  const meta = s.meta ? `<span class="step-meta">${esc(s.meta)}</span>` : '';
  const cls = s.status==='ok' ? 'ok' : (s.status==='error' ? 'error' : 'running');
  return `<div class="step ${cls}"><span class="step-ic"><i class="fas ${icon}"></i></span><span class="step-label">${esc(label)}</span>${meta}${trail}</div>`;
}

function _renderSteps(st){
  if(st.stepsEl) st.stepsEl.innerHTML = st.steps.map(stepRowHTML).join('');
}

// Свёрнутый трейс после завершения ответа: «N шагов · Xс» + раскрываемый список.
function buildActivityCollapsed(steps, elapsed){
  const n = steps.length;
  const word = pluralRu(n, 'шаг', 'шага', 'шагов');
  const rows = steps.map(stepRowHTML).join('');
  return `<button class="trace-summary" type="button" onclick="toggleTrace(this)">`+
    `<i class="fas fa-bolt"></i><span>${n} ${word} · ${esc(String(elapsed))}с</span>`+
    `<i class="fas fa-chevron-down trace-chev"></i></button>`+
    `<div class="trace-steps" hidden>${rows}</div>`;
}
function toggleTrace(btn){
  const steps = btn.nextElementSibling;
  if(!steps) return;
  if(steps.hasAttribute('hidden')){ steps.removeAttribute('hidden'); btn.classList.add('open'); }
  else { steps.setAttribute('hidden',''); btn.classList.remove('open'); }
}

// Финализация трейса: были вызовы — схлопнуть в сводку, не было — убрать совсем.
function _finalizeActivity(st, elapsed){
  if(!st.activity) return;
  if(!st.steps.length){ st.activity.remove(); st.activity=null; return; }
  st.activity.classList.add('done');
  st.activity.innerHTML = buildActivityCollapsed(st.steps, elapsed);
}

function _appendTime(body){
  if(!body || body.querySelector('.msg-time')) return;
  const t = document.createElement('div'); t.className='msg-time';
  t.textContent = new Date().toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'});
  body.appendChild(t);
}

function handleStreamEvent(ev, st){
  const msgs = document.getElementById('chat-messages');
  if(ev.type==='start') return;

  if(ev.type==='tool_call'){
    if(st.working) st.working.style.display='inline-flex';
    st.steps.push({ tool:ev.tool, status:'running', meta:'' });
    _renderSteps(st);
    msgs.scrollTop = msgs.scrollHeight;
    return;
  }
  if(ev.type==='tool_result' || ev.type==='tool_error'){
    // Разрешаем последний ещё «бегущий» шаг (вызовы и результаты идут парами по порядку).
    for(let i=st.steps.length-1; i>=0; i--){
      if(st.steps[i].status==='running'){
        st.steps[i].status = ev.type==='tool_error' ? 'error' : 'ok';
        st.steps[i].meta   = ev.type==='tool_error' ? '' : _stepMeta(ev.result);
        break;
      }
    }
    _renderSteps(st);
    return;
  }
  if(ev.type==='token'){
    if(st.working) st.working.style.display='none';   // ответ пошёл — гасим индикатор «Думаю…»
    st.acc += ev.text;
    if(!st.dirty){ st.dirty=true; st.raf=requestAnimationFrame(()=>_streamRender(st)); }
    return;
  }
  if(ev.type==='clear_partial'){
    st.acc='';
    if(st.answer) st.answer.innerHTML='';
    return;
  }
  if(ev.type==='done'){
    st.done = true;
    if(st.raf) cancelAnimationFrame(st.raf);
    st.answer.innerHTML = renderMarkdown(ev.reply);
    const el = ((Date.now()-st.t0)/1000).toFixed(1);
    _finalizeActivity(st, el);
    _appendTime(st.answer.parentElement);
    msgs.scrollTop = msgs.scrollHeight;
    return;
  }
  if(ev.type==='error'){
    st.done = true;
    if(st.raf) cancelAnimationFrame(st.raf);
    if(st.working) st.working.style.display='none';
    st.answer.innerHTML = renderMarkdown(st.acc || 'Ошибка обработки запроса.');
    _finalizeActivity(st, ((Date.now()-st.t0)/1000).toFixed(1));
    _appendTime(st.answer.parentElement);
    return;
  }
}

// Сообщение юзера — деликатный чип справа; простой ответ ассистента (ошибки) — без пузыря.
function addBubble(text, role){
  const msgs = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'msg '+role;
  if(role==='user'){
    div.innerHTML = `<div class="msg-body"><div class="msg-bubble user-chip">${esc(text).replace(/\n/g,'<br>')}</div></div>`;
  } else {
    const now = new Date().toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'});
    div.innerHTML = `<div class="msg-avatar"><img src="/static/images/IMG_2820.PNG" alt="Voicyfy"></div><div class="msg-body"><div class="msg-bubble md answer">${renderMarkdown(text)}</div><div class="msg-time">${now}</div></div>`;
  }
  msgs.appendChild(div); msgs.scrollTop = msgs.scrollHeight;
}

// Собирает шаги трейса из debug_log (fallback-путь /chat без стрима).
function _stepsFromDebug(debugLog){
  const steps = [];
  (debugLog||[]).forEach(e => {
    if(e.type==='tool_call'){ steps.push({ tool:e.data&&e.data.tool, status:'ok', meta:'' }); }
    else if(e.type==='tool_result'){ const s=steps[steps.length-1]; if(s){ s.status='ok'; s.meta=_stepMeta(e.data&&e.data.result); } }
    else if(e.type==='tool_error'){ const s=steps[steps.length-1]; if(s){ s.status='error'; s.meta=''; } }
  });
  return steps;
}

function addAgentBubble(text, debugLog, elapsed){
  const msgs = document.getElementById('chat-messages');
  const now = new Date().toLocaleTimeString('ru',{hour:'2-digit',minute:'2-digit'});
  const div = document.createElement('div'); div.className='msg assistant';
  const steps = _stepsFromDebug(debugLog);
  const trace = steps.length ? `<div class="activity done">${buildActivityCollapsed(steps, elapsed)}</div>` : '';
  div.innerHTML = `<div class="msg-avatar"><img src="/static/images/IMG_2820.PNG" alt="Voicyfy"></div><div class="msg-body">${trace}<div class="msg-bubble md answer">${renderMarkdown(text)}</div><div class="msg-time">${now}</div></div>`;
  msgs.appendChild(div); msgs.scrollTop = msgs.scrollHeight;
}

function showTyping(){ const msgs=document.getElementById('chat-messages'); const id='typ-'+Date.now(); const div=document.createElement('div'); div.className='msg assistant'; div.id=id; div.innerHTML=`<div class="msg-avatar"><img src="/static/images/IMG_2820.PNG" alt="Voicyfy"></div><div class="msg-body"><div class="activity-working"><span class="aw-dot"></span><span class="aw-text">Думаю…</span></div></div>`; msgs.appendChild(div); msgs.scrollTop=msgs.scrollHeight; return id; }
function removeTyping(id){ document.getElementById(id)?.remove(); }


// ════════════════ ГОЛОСОВОЙ ВВОД (STT) ════════════════
// Запись с микрофона через MediaRecorder → POST /api/agent/transcribe →
// распознанный текст вставляем в поле ввода (пользователь правит и сам отправляет).
let _mediaRecorder = null, _audioChunks = [], _recording = false, _recStream = null;

async function toggleRecording(){
  if(_recording){ stopRecording(); return; }
  const btn = document.getElementById('chat-mic');
  if(!navigator.mediaDevices || !window.MediaRecorder){
    showToast('Запись не поддерживается этим браузером'); return;
  }
  try{
    _recStream = await navigator.mediaDevices.getUserMedia({ audio:true });
  }catch(e){ showToast('Нет доступа к микрофону'); return; }

  _audioChunks = [];
  const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
             : (MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '');
  try{
    _mediaRecorder = mime ? new MediaRecorder(_recStream, { mimeType:mime }) : new MediaRecorder(_recStream);
  }catch(e){ _mediaRecorder = new MediaRecorder(_recStream); }
  _mediaRecorder.ondataavailable = e => { if(e.data && e.data.size>0) _audioChunks.push(e.data); };
  _mediaRecorder.onstop = onRecordingStop;
  _mediaRecorder.start();
  _recording = true;
  if(btn){ btn.classList.add('recording'); btn.title='Остановить запись'; }
}

function stopRecording(){
  if(_mediaRecorder && _recording){ try{ _mediaRecorder.stop(); }catch(e){} }
  _recording = false;
  const btn = document.getElementById('chat-mic');
  if(btn){ btn.classList.remove('recording'); btn.title='Записать голосом'; }
}

async function onRecordingStop(){
  if(_recStream){ _recStream.getTracks().forEach(t=>t.stop()); _recStream=null; }
  const type = (_mediaRecorder && _mediaRecorder.mimeType) || 'audio/webm';
  const blob = new Blob(_audioChunks, { type });
  _audioChunks = [];
  if(!blob.size) return;

  const btn = document.getElementById('chat-mic');
  const inp = document.getElementById('chat-input');
  if(btn){ btn.classList.add('loading'); btn.disabled=true; }
  try{
    const token = getToken();
    if(!token){ location.href='/static/login.html'; return; }
    const ext = type.includes('mp4') ? 'mp4' : 'webm';
    const fd = new FormData();
    fd.append('file', blob, 'voice.'+ext);
    const resp = await fetch(withAgentId(API + '/transcribe'), {
      method:'POST', headers:{ 'Authorization':'Bearer '+token }, body: fd,
    });
    if(resp.status===401){ localStorage.removeItem('auth_token'); location.href='/static/login.html'; return; }
    if(resp.ok){
      const d = await resp.json();
      if(d && d.text){
        if(inp){
          inp.value = (inp.value ? inp.value.trim()+' ' : '') + d.text;
          inp.focus();
          inp.dispatchEvent(new Event('input'));  // авто-resize textarea
        }
      } else { showToast('Речь не распознана'); }
    } else {
      showToast('Не удалось распознать запись');
    }
  }catch(e){ showToast('Ошибка сети при распознавании'); }
  finally{ if(btn){ btn.classList.remove('loading'); btn.disabled=false; } }
}


