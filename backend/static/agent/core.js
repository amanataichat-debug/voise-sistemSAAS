/* ============================================================================
 * agent/core.js — Ядро: константы, глобальное состояние, apiFetch/withAgentId/handle402, общие хелперы (esc, fmtDate, renderMarkdown, showToast…)
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

const API = '/api/agent';

// Стадии воронки — зеркало backend/core/pipeline_stages.py (фиксированный набор).
// 'calling' больше не стадия воронки, но оставлен в META для корректного показа
// старых записей контактов, у которых статус мог остаться "calling".
const STAGE_META = {
  new:         { label:'Новый',      color:'#6B7280' },
  active:      { label:'В работе',   color:'#3B82F6' },
  success:     { label:'Успех',      color:'#10B981' },
  rejected:    { label:'Отказ',      color:'#EF4444' },
  do_not_call: { label:'Не звонить', color:'#1F2937' },
  calling:     { label:'Дозвон',     color:'#F59E0B' },
};
const STAGE_ORDER = ['new','active','success','rejected','do_not_call'];
function stageBadge(status){
  const m = STAGE_META[status] || { label: status || '—', color:'#6B7280' };
  return `<span class="status-badge" style="background:${m.color}22;color:${m.color}">${esc(m.label)}</span>`;
}
const TG_API = '/api/agent/telegram';
let agentData = null;
let orchestratorModels = [];
let phoneNumbers = [];
let telegramState = null;

// ── Multi-agent (v3.1): выбранный агент и его список ──
let currentAgentId = null;
let agentsList = [];
let agentsCanCreateMore = false;

// Добавляет ?agent_id=<выбранный> к запросам по конкретному агенту.
// Не трогает /list и /create (там агент ещё не выбран / создаётся новый).
function withAgentId(url){
  if(!currentAgentId) return url;
  if(typeof url !== 'string' || !url.startsWith('/api/agent')) return url;
  if(url.includes('/api/agent/list') || url.includes('/api/agent/create')) return url;
  if(/[?&]agent_id=/.test(url)) return url;
  return url + (url.includes('?') ? '&' : '?') + 'agent_id=' + encodeURIComponent(currentAgentId);
}

const CREDITS_API = '/api/credits';
let creditsState = null;
let creditsTimer = null;

function getToken(){ return localStorage.getItem('auth_token'); }
async function apiFetch(url, options={}){
  const token = getToken();
  if(!token){ location.href='/static/login.html'; return; }
  url = withAgentId(url);
  const headers = { 'Authorization':'Bearer '+token, 'Content-Type':'application/json', ...options.headers };
  const resp = await fetch(url, { ...options, headers });
  if(resp.status===401){ localStorage.removeItem('auth_token'); location.href='/static/login.html'; return; }
  if(resp.status===402){ await handle402(resp); }
  return resp;
}

// ── Обработка 402 Payment Required ──
async function handle402(resp){
  let detail = null;
  try{ detail = (await resp.clone().json()).detail; }catch(e){}
  if(detail === 'subscription_expired'){
    loadCredits();
    if(confirm('Доступ к агенту закончился. Агент доступен на тарифе Profi. Перейти к тарифам?')){
      location.href = '/static/dashboard.html';
    }
  } else if(detail === 'subscription_required'){
    if(confirm('Тестовый период использован. Агент доступен на тарифе Profi. Перейти к тарифам?')){
      location.href = '/static/dashboard.html';
    }
  } else if(detail && detail.error === 'insufficient_credits'){
    openCreditsModal();
    alert(`Недостаточно кредитов: нужно ${detail.required}, доступно ${detail.available}.`);
  }
}


// ── HELPERS ──
function errText(detail){
  const map = {
    telephony_not_verified:'Телефония не верифицирована. Настройте её перед созданием агента.',
    api_key_required_gemini:'Нужен Google Gemini API ключ.',
    api_key_required_openai:'Нужен OpenAI API ключ.',
    api_key_required_cartesia:'Нужен Cartesia API ключ.',
    api_key_required_yandex:'Нужны API-ключ и Folder ID Yandex Cloud.',
    api_key_required_fish:'Нужен API-ключ Fish Audio.',
    invalid_assistant_type:'Неверный тип ассистента.',
    invalid_orchestrator_model:'Неверная модель оркестратора.',
    already_exists:'Агент уже существует.',
    agent_limit_reached:'Достигнут лимит агентов (максимум 3).',
    not_found:'Агент не найден.',
    openai_key_required:'Нужен OpenAI API ключ.',
    unsupported_format:'Неподдерживаемый формат. Используйте .xlsx или .csv.',
    parse_failed:'Не удалось разобрать файл. Проверьте формат.',
    exceed_limit:'Слишком много строк. Максимум 1000 за один импорт.',
    kb_too_large:'База знаний слишком большая. Максимум 200 000 символов.',
    preview_expired:'Время на подтверждение истекло. Загрузите файл заново.',
    no_valid_rows:'В файле нет валидных контактов.',
    forbidden:'Нет доступа.',
  };
  if(typeof detail === 'string' && map[detail]) return map[detail];
  if(detail && typeof detail === 'object' && detail.error === 'insufficient_credits'){
    return `Недостаточно кредитов: нужно ${detail.required}, доступно ${detail.available}.`;
  }
  return 'Ошибка: '+(detail||'не удалось выполнить запрос');
}
function decisionRu(d){ return ({ FOLLOWUP:'Перезвон', SUCCESS:'Успех', NO_ANSWER:'Не ответил' })[d] || d || '—'; }
function decisionBadge(d){ const cls={ FOLLOWUP:'badge-followup', SUCCESS:'badge-success', NO_ANSWER:'badge-no-answer' }; return `<span class="status-badge ${cls[d]||''}">${decisionRu(d)}</span>`; }
// Канал агентской задачи: call (звонок, дефолт) / telegram (отложенное сообщение).
// Для звонков бейдж не рисуем — это основной тип, шум не нужен.
function taskChannelBadge(channel){
  if(channel !== 'telegram') return '';
  return '<span class="status-badge" style="background:#E0F2FE;color:#0369A1;font-size:10px;padding:2px 7px"><i class="fas fa-paper-plane"></i> Telegram</span>';
}
// ── Цена моделей оркестратора ───────────────────────────────────────────────
// Только текст, без цветовой индикации: цветные маркеры («дорогая» красным)
// отталкивали от вполне рабочих моделей. Цену называет само число, а флаг
// `is_recommended` с бэкенда подсказывает, что выбрать по умолчанию.
// Оценку `credits_per_call` считает бэкенд (см. services/agent_models.py).

function fmtCredits(n){
  const v = Number(n) || 0;
  return v >= 100 ? String(Math.round(v)) : String(parseFloat(v.toFixed(2)));
}
// «~4.1 кр/звонок». До 10 кредитов показываем десятые — именно там модели
// сравнивают между собой, и округление до целого стёрло бы разницу.
function modelCallCost(m){
  const c = Number(m && m.credits_per_call) || 0;
  if(!c) return 'беспл.';
  return '~' + (c < 10 ? c.toFixed(1) : Math.round(c)) + ' кр/звонок';
}
function modelOptionsHtml(models, selectedSlug){
  return (models || []).map(m => {
    const sel = m.slug === selectedSlug ? ' selected' : '';
    const rec = m.is_recommended ? ' (рекомендуем)' : '';
    return `<option value="${esc(m.slug)}"${sel}>${esc(m.name + rec)} · ${esc(modelCallCost(m))}</option>`;
  }).join('');
}
function modelHintHtml(m){
  if(!m) return '';
  const price = Number(m.credits_per_call)
    ? `${fmtCredits(m.input_credits_per_1k)} кр / 1k входных · `
      + `${fmtCredits(m.output_credits_per_1k)} кр / 1k ответа · `
      + `≈${modelCallCost(m).replace('~','').replace(' кр/звонок','')} кр за типичный звонок`
    : 'Бесплатная модель OpenRouter · списывается минимум 1 кредит за вызов';
  const rec = m.is_recommended
    ? '<span class="model-recommended">Рекомендуем.</span> ' : '';
  return rec + `${esc(m.description || '')}<div class="model-price">${price}</div>`;
}

// Направление звонка: входящий (клиент позвонил) / исходящий (агент позвонил)
function directionRu(dir){ return dir === 'inbound' ? 'Входящий' : 'Исходящий'; }
function directionBadge(dir){
  const inbound = dir === 'inbound';
  const icon = inbound ? 'fa-arrow-down' : 'fa-arrow-up';
  const color = inbound ? '#0891B2' : '#7C3AED';
  return `<span class="status-badge" style="background:${color}22;color:${color}"><i class="fas ${icon}"></i> ${directionRu(dir)}</span>`;
}
// Все даты с бэка приходят в UTC (ISO с маркером). Отображаем в МСК (Europe/Moscow).
function fmtDate(s){
  if(!s) return '—';
  const d = new Date(s);
  if(isNaN(d.getTime())) return '—';
  const str = new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(d);
  return str + ' МСК';
}
// Разбор даты на части в МСК (для плиток задач)
function mskParts(s){
  const d = new Date(s);
  if(isNaN(d.getTime())) return null;
  const p = new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).formatToParts(d);
  const get = t => (p.find(x=>x.type===t)||{}).value || '';
  return { day: get('day'), mon: get('month').replace('.','').toUpperCase(), time: get('hour')+':'+get('minute') };
}
function relTime(s){ if(!s) return {text:'',soon:false}; const d=new Date(s), now=new Date(), diff=d-now; if(diff<=0) return {text:'сейчас',soon:true}; const min=Math.floor(diff/60000), h=Math.floor(min/60), days=Math.floor(h/24); if(min<60) return {text:`Через ${min} мин`,soon:true}; if(h<24) return {text:`Через ${h} ч ${min%60} мин`,soon:h<2}; return {text:`Через ${days} ${days===1?'день':days<5?'дня':'дней'}`,soon:false}; }
function esc(s){ if(s===null||s===undefined) return ''; const d=document.createElement('div'); d.textContent=String(s); return d.innerHTML; }

// МСК (UTC+3, без перехода на летнее время) для ввода/вывода datetime-local.
// value инпута трактуется как МСК; на бэк уходит UTC ISO, и обратно.
const MSK_OFFSET_MS = 3 * 60 * 60 * 1000;
// 'YYYY-MM-DDTHH:mm' (МСК) → UTC ISO-строка для API
function mskInputToUtc(v){
  if(!v) return null;
  const [datePart, timePart] = v.split('T');
  if(!datePart || !timePart) return null;
  const [y,m,d] = datePart.split('-').map(Number);
  const [hh,mm] = timePart.split(':').map(Number);
  if([y,m,d,hh,mm].some(n => Number.isNaN(n))) return null;
  return new Date(Date.UTC(y, m-1, d, hh, mm) - MSK_OFFSET_MS).toISOString();
}
// UTC ISO-строка → value для datetime-local в МСК ('YYYY-MM-DDTHH:mm')
function utcToMskInput(s){
  if(!s) return '';
  const d = new Date(s);
  if(isNaN(d.getTime())) return '';
  const m = new Date(d.getTime() + MSK_OFFSET_MS);
  const p = n => String(n).padStart(2,'0');
  return `${m.getUTCFullYear()}-${p(m.getUTCMonth()+1)}-${p(m.getUTCDate())}T${p(m.getUTCHours())}:${p(m.getUTCMinutes())}`;
}

// Markdown → безопасный HTML для пузырей чата (variant A+D).
// marked парсит GitHub-flavored markdown (таблицы, списки, код), DOMPurify вырезает любой XSS.
let _markedReady = false;
function _initMarked(){
  if(_markedReady || typeof marked === 'undefined') return;
  marked.setOptions({ gfm:true, breaks:true });
  _markedReady = true;
}
function renderMarkdown(text){
  if(text===null||text===undefined) return '';
  text = String(text);
  // Fallback, если CDN не загрузился: экранируем и сохраняем переносы строк.
  if(typeof marked === 'undefined' || typeof DOMPurify === 'undefined'){
    return esc(text).replace(/\n/g,'<br>');
  }
  _initMarked();
  let html = DOMPurify.sanitize(marked.parse(text), { ADD_ATTR:['target','rel'] });
  // Оборачиваем таблицы в скролл-контейнер, чтобы широкие таблицы не ломали вёрстку.
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  tmp.querySelectorAll('table').forEach(t => {
    const wrap = document.createElement('div');
    wrap.className = 'md-table-wrap';
    t.parentNode.insertBefore(wrap, t);
    wrap.appendChild(t);
  });
  // Внешние ссылки открываем в новой вкладке безопасно.
  tmp.querySelectorAll('a[href]').forEach(a => { a.target='_blank'; a.rel='noopener noreferrer'; });
  return tmp.innerHTML;
}
let toastTimer;
function showToast(msg,type='success'){
  let t=document.getElementById('toast');
  if(!t){ t=document.createElement('div'); t.id='toast'; t.style.cssText='position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);color:#fff;padding:11px 22px;border-radius:11px;font-size:13px;z-index:9999;opacity:0;transition:all .25s ease;max-width:90vw;text-align:center;box-shadow:0 6px 20px rgba(0,0,0,0.2)'; document.body.appendChild(t); }
  t.textContent=msg; t.style.background = type==='error'?'#991B1B':type==='success'?'#166534':'#1a1a2e';
  t.style.opacity='1'; t.style.transform='translateX(-50%) translateY(0)';
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>{ t.style.opacity='0'; t.style.transform='translateX(-50%) translateY(20px)'; },3500);
}

