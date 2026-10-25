/* ============================================================================
 * agent/analytics.js — Вкладка «Аналитика» страницы агента
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны из inline-onclick и из tabs.js.
 *
 * Фронт-only аналитика: берёт данные из /api/agent/stats, /contacts, /calls
 * и рисует карточки + CSS-бары. Никаких chart-библиотек. Данные кешируются
 * на 30 с чтобы повторный заход был мгновенным.
 * ========================================================================== */

let analyticsCache = null;
let analyticsCacheAt = 0;
const ANALYTICS_TTL_MS = 30000;

async function openAnalyticsView(){
  document.getElementById('analytics-view-overlay').classList.remove('hidden');
  await loadAnalytics();
}
function closeAnalyticsView(){
  document.getElementById('analytics-view-overlay').classList.add('hidden');
}

async function loadAnalytics(force){
  const body = document.getElementById('analytics-view-body');
  if(!body) return;
  const fresh = analyticsCache && (Date.now() - analyticsCacheAt < ANALYTICS_TTL_MS) && !force;
  if(!fresh) body.innerHTML = '<div class="alx-empty"><i class="fas fa-spinner fa-spin"></i> Собираю аналитику…</div>';

  try{
    let data = analyticsCache;
    if(!fresh){
      const [statsR, contactsR, callsR] = await Promise.all([
        apiFetch(API + '/stats'),
        apiFetch(API + '/contacts?limit=200&offset=0'),
        apiFetch(API + '/calls?limit=200'),
      ]);
      const stats = statsR && statsR.status === 200 ? await statsR.json() : {};
      const contacts = contactsR && contactsR.status === 200 ? (await contactsR.json()).contacts || [] : [];
      const calls = callsR && callsR.status === 200 ? (await callsR.json()).calls || [] : [];
      data = { stats, contacts, calls };
      analyticsCache = data;
      analyticsCacheAt = Date.now();
    }
    body.innerHTML = renderAnalytics(data);
  }catch(e){
    body.innerHTML = `<div class="alx-empty">Ошибка загрузки: ${esc(e && e.message || 'не удалось получить данные')}</div>`;
  }
}

function renderAnalytics(data){
  const { stats, contacts, calls } = data;
  const total_calls = stats.total_calls || 0;
  const success_calls = stats.success_calls || 0;
  const no_answer_calls = stats.no_answer_calls || 0;
  const followup_calls = stats.followup_calls || 0;
  const total_contacts = stats.total_contacts || 0;
  const active_contacts = stats.active_contacts || 0;
  const scheduled_tasks = stats.scheduled_tasks || 0;

  const answered_calls = total_calls - no_answer_calls;
  const answer_rate = total_calls ? Math.round(answered_calls / total_calls * 100) : 0;
  const success_rate = total_contacts ? Math.round(success_calls / total_contacts * 100) : 0;
  const conversion_rate = total_contacts ? Math.round(
    (contacts.filter(c => c.status === 'success').length / total_contacts) * 100
  ) : 0;

  // ── Распределение по стадиям воронки
  const stageCounts = {};
  STAGE_ORDER.forEach(s => stageCounts[s] = 0);
  contacts.forEach(c => {
    const key = STAGE_ORDER.includes(c.status) ? c.status : 'active';
    stageCounts[key]++;
  });
  const stageMax = Math.max(1, ...Object.values(stageCounts));

  // ── Решения по звонкам (последние 200)
  const decisions = { SUCCESS: 0, REJECTED: 0, NO_ANSWER: 0, FOLLOWUP: 0, OTHER: 0 };
  const decisionLabels = { SUCCESS: 'Успех', REJECTED: 'Отказ', NO_ANSWER: 'Не ответил', FOLLOWUP: 'Нужен ответ', OTHER: 'Другое' };
  const decisionColors = { SUCCESS: '#10B981', REJECTED: '#EF4444', NO_ANSWER: '#F59E0B', FOLLOWUP: '#3B82F6', OTHER: '#94a3b8' };
  calls.forEach(c => {
    const d = (c.post_call_decision || c.decision || 'OTHER').toUpperCase();
    if(decisions[d] !== undefined) decisions[d]++; else decisions.OTHER++;
  });
  const decMax = Math.max(1, ...Object.values(decisions));

  // ── Звонки по дням за последние 14 дней
  const days = [];
  const now = new Date();
  for(let i = 13; i >= 0; i--){
    const d = new Date(now); d.setHours(0,0,0,0); d.setDate(d.getDate() - i);
    days.push({ date: d, iso: d.toISOString().slice(0,10), count: 0 });
  }
  const dayByIso = Object.fromEntries(days.map(d => [d.iso, d]));
  calls.forEach(c => {
    const t = c.started_at || c.created_at;
    if(!t) return;
    const iso = String(t).slice(0, 10);
    if(dayByIso[iso]) dayByIso[iso].count++;
  });
  const callsToday = (dayByIso[days[days.length - 1].iso] || {}).count || 0;
  const callsWeek = days.slice(-7).reduce((a, d) => a + d.count, 0);
  const dayMax = Math.max(1, ...days.map(d => d.count));

  // ── Рендер
  return `
    <div class="alx-head">
      <div>
        <h3>Аналитика агента</h3>
        <div class="alx-hint">Данные из последних 200 звонков и всей базы контактов. Обновляются раз в 30 секунд.</div>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="loadAnalytics(true)"><i class="fas fa-rotate"></i> Обновить</button>
    </div>

    <!-- Главные метрики: 4 карточки -->
    <div class="alx-grid">
      <div class="alx-card">
        <h4><i class="fas fa-phone-volume"></i> Звонки</h4>
        <div class="alx-big">${total_calls}</div>
        <div class="alx-sub">Сегодня: <b>${callsToday}</b> · За неделю: <b>${callsWeek}</b></div>
      </div>
      <div class="alx-card">
        <h4><i class="fas fa-users"></i> Клиенты</h4>
        <div class="alx-big">${total_contacts}</div>
        <div class="alx-sub">В работе: <b>${active_contacts}</b> · В очереди звонков: <b>${scheduled_tasks}</b></div>
      </div>
      <div class="alx-card">
        <h4><i class="fas fa-circle-check"></i> Дозвон</h4>
        <div class="alx-big">${answer_rate}%</div>
        <div class="alx-sub">Ответили: <b>${answered_calls}</b> из <b>${total_calls}</b></div>
      </div>
      <div class="alx-card">
        <h4><i class="fas fa-trophy"></i> Конверсия</h4>
        <div class="alx-big">${conversion_rate}%</div>
        <div class="alx-sub">Успешно закрытых: <b>${contacts.filter(c=>c.status==='success').length}</b> из <b>${total_contacts}</b></div>
      </div>
    </div>

    <!-- Два больших блока: воронка и решения -->
    <div class="alx-grid" style="grid-template-columns: repeat(auto-fit, minmax(320px, 1fr))">
      <div class="alx-card">
        <h4><i class="fas fa-stream"></i> Распределение по стадиям</h4>
        <div class="alx-bars" style="margin-top:4px">
          ${STAGE_ORDER.map(s => {
            const m = STAGE_META[s] || { label: s, color: '#94a3b8' };
            const n = stageCounts[s];
            const pct = Math.round(n / stageMax * 100);
            return `
              <div class="alx-bar">
                <div class="alx-bar-head"><span class="alx-bar-label">${esc(m.label)}</span><span class="alx-bar-value">${n}</span></div>
                <div class="alx-bar-track"><div class="alx-bar-fill" style="width:${pct}%;background:${m.color}"></div></div>
              </div>
            `;
          }).join('')}
        </div>
      </div>

      <div class="alx-card">
        <h4><i class="fas fa-shuffle"></i> Исходы последних звонков</h4>
        <div class="alx-bars" style="margin-top:4px">
          ${Object.keys(decisions).map(d => {
            const n = decisions[d]; if(n === 0 && d === 'OTHER') return '';
            const pct = Math.round(n / decMax * 100);
            return `
              <div class="alx-bar">
                <div class="alx-bar-head"><span class="alx-bar-label">${esc(decisionLabels[d])}</span><span class="alx-bar-value">${n}</span></div>
                <div class="alx-bar-track"><div class="alx-bar-fill" style="width:${pct}%;background:${decisionColors[d]}"></div></div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    </div>

    <!-- Звонки по дням -->
    <div class="alx-card">
      <h4><i class="far fa-calendar-days"></i> Звонки по дням (14 дней)</h4>
      <div class="alx-cols">
        ${days.map(d => {
          const h = Math.round(d.count / dayMax * 140);
          const label = d.date.toLocaleDateString('ru-RU', { day:'2-digit', month:'2-digit' });
          return `
            <div class="alx-col" title="${label}: ${d.count} звонков">
              <div class="alx-col-value">${d.count || ''}</div>
              <div class="alx-col-bar" style="height:${Math.max(2, h)}px"></div>
              <div class="alx-col-label">${label}</div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}
