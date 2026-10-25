/* ============================================================================
 * agent/analytics.js — Вкладка «Аналитика» страницы агента
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны из inline-onclick и из tabs.js.
 *
 * Фронт-only аналитика: берёт /stats, /contacts, /calls и рисует карточки +
 * SVG-графики (donut, area, horizontal bars). Никаких chart-библиотек.
 * Данные кешируются на 30 с чтобы повторный заход был мгновенным.
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

// ── Утилиты для SVG ──────────────────────────────────────────────────────
function _polarToCart(cx, cy, r, deg){
  const rad = (deg - 90) * Math.PI / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}
function _arcPath(cx, cy, rOuter, rInner, startDeg, endDeg){
  // Защита от 360° (SVG arc не рисует полный круг)
  if(endDeg - startDeg >= 359.999) endDeg = startDeg + 359.999;
  const p1 = _polarToCart(cx, cy, rOuter, endDeg);
  const p2 = _polarToCart(cx, cy, rOuter, startDeg);
  const p3 = _polarToCart(cx, cy, rInner, startDeg);
  const p4 = _polarToCart(cx, cy, rInner, endDeg);
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${p1.x} ${p1.y} A ${rOuter} ${rOuter} 0 ${large} 0 ${p2.x} ${p2.y} L ${p3.x} ${p3.y} A ${rInner} ${rInner} 0 ${large} 1 ${p4.x} ${p4.y} Z`;
}

// ── SVG-графики ──────────────────────────────────────────────────────────
function _donutChart(items, totalLabel){
  // items: [{label, value, color}]
  const total = items.reduce((a, i) => a + i.value, 0);
  const size = 180, cx = size/2, cy = size/2, rO = 78, rI = 50;
  if(!total){
    return `
      <svg viewBox="0 0 ${size} ${size}" class="alx-donut">
        <circle cx="${cx}" cy="${cy}" r="${rO}" fill="none" stroke="var(--vf-surface-3)" stroke-width="${rO-rI}"/>
        <text x="${cx}" y="${cy-4}" text-anchor="middle" class="alx-donut-total">0</text>
        <text x="${cx}" y="${cy+14}" text-anchor="middle" class="alx-donut-sub">${esc(totalLabel||'')}</text>
      </svg>`;
  }
  let acc = 0;
  const slices = items.map(it => {
    if(it.value <= 0) return '';
    const start = acc / total * 360;
    acc += it.value;
    const end = acc / total * 360;
    return `<path d="${_arcPath(cx, cy, rO, rI, start, end)}" fill="${it.color}"><title>${esc(it.label)}: ${it.value}</title></path>`;
  }).join('');
  return `
    <svg viewBox="0 0 ${size} ${size}" class="alx-donut">
      <circle cx="${cx}" cy="${cy}" r="${(rO+rI)/2}" fill="none" stroke="var(--vf-surface-3)" stroke-width="${rO-rI}"/>
      ${slices}
      <text x="${cx}" y="${cy-4}" text-anchor="middle" class="alx-donut-total">${total}</text>
      <text x="${cx}" y="${cy+14}" text-anchor="middle" class="alx-donut-sub">${esc(totalLabel||'')}</text>
    </svg>`;
}
function _donutLegend(items){
  const total = Math.max(1, items.reduce((a, i) => a + i.value, 0));
  return `<div class="alx-legend">${items.map(it => {
    const pct = Math.round(it.value / total * 100);
    return `
      <div class="alx-legend-row">
        <span class="alx-legend-dot" style="background:${it.color}"></span>
        <span class="alx-legend-label">${esc(it.label)}</span>
        <span class="alx-legend-val"><b>${it.value}</b> · ${pct}%</span>
      </div>`;
  }).join('')}</div>`;
}

function _areaChart(points, opts){
  // points: [{x: Date|iso, y: number}]
  opts = opts || {};
  const w = 640, h = 160, padL = 32, padR = 8, padT = 10, padB = 24;
  const n = points.length;
  if(!n) return `<div class="alx-empty">Нет данных</div>`;
  const maxY = Math.max(1, ...points.map(p => p.y));
  const stepX = (w - padL - padR) / Math.max(1, n - 1);
  const scaleY = v => h - padB - (v / maxY) * (h - padT - padB);
  const scaleX = i => padL + i * stepX;
  const linePts = points.map((p, i) => `${scaleX(i)},${scaleY(p.y)}`).join(' ');
  const areaPts = `${padL},${h - padB} ${linePts} ${scaleX(n-1)},${h - padB}`;
  const gridY = [0, 0.25, 0.5, 0.75, 1].map(f => {
    const v = Math.round(maxY * f);
    const y = scaleY(v);
    return `
      <line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="var(--vf-border)" stroke-dasharray="2 3" opacity=".6"/>
      <text x="${padL - 6}" y="${y + 3}" text-anchor="end" class="alx-axis">${v}</text>`;
  }).join('');
  const xLabels = points.map((p, i) => {
    // Показываем каждую 2-ю метку, чтобы не перекрывались
    if(i % 2 !== 0 && i !== n - 1) return '';
    const d = p.x instanceof Date ? p.x : new Date(p.x);
    const label = d.toLocaleDateString('ru-RU', { day:'2-digit', month:'2-digit' });
    return `<text x="${scaleX(i)}" y="${h - 6}" text-anchor="middle" class="alx-axis">${label}</text>`;
  }).join('');
  const dots = points.map((p, i) => `<circle cx="${scaleX(i)}" cy="${scaleY(p.y)}" r="3" fill="var(--vf-accent)" stroke="var(--vf-surface)" stroke-width="2"><title>${p.x.toLocaleDateString ? p.x.toLocaleDateString('ru-RU') : p.x}: ${p.y}</title></circle>`).join('');
  const gradId = 'alx-grad-' + Math.random().toString(36).slice(2, 8);
  return `
    <svg viewBox="0 0 ${w} ${h}" class="alx-area" preserveAspectRatio="none">
      <defs>
        <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--vf-accent)" stop-opacity=".35"/>
          <stop offset="100%" stop-color="var(--vf-accent)" stop-opacity="0"/>
        </linearGradient>
      </defs>
      ${gridY}
      <polygon points="${areaPts}" fill="url(#${gradId})"/>
      <polyline points="${linePts}" fill="none" stroke="var(--vf-accent)" stroke-width="2.2" stroke-linejoin="round"/>
      ${dots}
      ${xLabels}
    </svg>`;
}

function _hBarRow(label, value, max, color, pctLabel){
  const pct = Math.round((value / Math.max(1, max)) * 100);
  return `
    <div class="alx-bar">
      <div class="alx-bar-head">
        <span class="alx-bar-label">${esc(label)}</span>
        <span class="alx-bar-value">${value}${pctLabel ? ' · ' + Math.round(value / Math.max(1, pctLabel) * 100) + '%' : ''}</span>
      </div>
      <div class="alx-bar-track"><div class="alx-bar-fill" style="width:${pct}%;background:${color}"></div></div>
    </div>`;
}

// ── Главный рендер ───────────────────────────────────────────────────────
function renderAnalytics(data){
  const { stats, contacts, calls } = data;
  const total_calls = stats.total_calls || 0;
  const success_calls = stats.success_calls || 0;
  const no_answer_calls = stats.no_answer_calls || 0;
  const total_contacts = stats.total_contacts || 0;
  const active_contacts = stats.active_contacts || 0;
  const scheduled_tasks = stats.scheduled_tasks || 0;

  const answered_calls = total_calls - no_answer_calls;
  const answer_rate = total_calls ? Math.round(answered_calls / total_calls * 100) : 0;
  const won_contacts = contacts.filter(c => c.status === 'success').length;
  const lost_contacts = contacts.filter(c => c.status === 'rejected' || c.status === 'do_not_call').length;
  const conversion_rate = total_contacts ? Math.round(won_contacts / total_contacts * 100) : 0;

  // ── Воронка — для donut
  const stageSlices = STAGE_ORDER.map(s => {
    const m = STAGE_META[s] || { label: s, color: '#94a3b8' };
    return { label: m.label, color: m.color, value: contacts.filter(c => (STAGE_ORDER.includes(c.status) ? c.status : 'active') === s).length };
  });

  // ── Исходы последних звонков
  const decMap = { SUCCESS: 'Успех', REJECTED: 'Отказ', NO_ANSWER: 'Не ответил', FOLLOWUP: 'Перезвон нужен', OTHER: 'Другое' };
  const decColor = { SUCCESS: '#10B981', REJECTED: '#EF4444', NO_ANSWER: '#F59E0B', FOLLOWUP: '#3B82F6', OTHER: '#94a3b8' };
  const decisions = { SUCCESS: 0, REJECTED: 0, NO_ANSWER: 0, FOLLOWUP: 0, OTHER: 0 };
  calls.forEach(c => {
    const d = (c.post_call_decision || c.decision || 'OTHER').toUpperCase();
    if(decisions[d] !== undefined) decisions[d]++; else decisions.OTHER++;
  });
  const decTotal = Math.max(1, Object.values(decisions).reduce((a, b) => a + b, 0));

  // ── Каналы: откуда идут клиенты. Берём канал из calls (sms/telegram/call/whatsapp/instagram)
  const chanMeta = {
    call:       { label: 'Голосовой звонок', color: '#2a5ce8', icon: 'fas fa-phone-volume' },
    sms:        { label: 'SMS',              color: '#64748b', icon: 'fas fa-comment-sms' },
    telegram:   { label: 'Telegram',         color: '#229ED9', icon: 'fab fa-telegram' },
    whatsapp:   { label: 'WhatsApp',         color: '#25D366', icon: 'fab fa-whatsapp' },
    instagram:  { label: 'Instagram',        color: '#E1306C', icon: 'fab fa-instagram' },
    other:      { label: 'Другое',           color: '#94a3b8', icon: 'fas fa-circle' },
  };
  const channels = { call: 0, sms: 0, telegram: 0, whatsapp: 0, instagram: 0, other: 0 };
  calls.forEach(c => {
    const ch = (c.channel || '').toLowerCase();
    if(channels[ch] !== undefined) channels[ch]++;
    else if(!ch || ch === 'phone' || ch === 'voice') channels.call++;
    else channels.other++;
  });
  const chanTotal = Math.max(1, Object.values(channels).reduce((a, b) => a + b, 0));

  // ── Звонки по дням за 14 дней
  const days = [];
  const now = new Date();
  for(let i = 13; i >= 0; i--){
    const d = new Date(now); d.setHours(0,0,0,0); d.setDate(d.getDate() - i);
    days.push({ x: d, y: 0, iso: d.toISOString().slice(0,10) });
  }
  const dayByIso = Object.fromEntries(days.map(d => [d.iso, d]));
  calls.forEach(c => {
    const t = c.started_at || c.created_at;
    if(!t) return;
    const iso = String(t).slice(0, 10);
    if(dayByIso[iso]) dayByIso[iso].y++;
  });
  const callsToday = (dayByIso[days[days.length - 1].iso] || {}).y || 0;
  const callsYesterday = (dayByIso[days[days.length - 2].iso] || {}).y || 0;
  const callsWeek = days.slice(-7).reduce((a, d) => a + d.y, 0);
  const callsPrevWeek = days.slice(0, 7).reduce((a, d) => a + d.y, 0);
  const weekDelta = callsPrevWeek ? Math.round((callsWeek - callsPrevWeek) / callsPrevWeek * 100) : 0;

  // ── Деньги (нужны поля сделки — пока оцениваем на коленке)
  // TODO (следующий коммит, миграция БД): deal_amount у клиента → реальная выручка.
  // Пока показываем «данные появятся с полями сделки» в сомах.
  const avgCheck = null;
  const totalRevenue = null;

  // ── Рендер ────────────────────────────────────────────────────────────
  return `
    <div class="alx-head">
      <div>
        <h3>Аналитика агента</h3>
        <div class="alx-hint">Данные из последних 200 звонков и базы контактов. Обновляются раз в 30 секунд.</div>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="loadAnalytics(true)"><i class="fas fa-rotate"></i> Обновить</button>
    </div>

    <!-- 4 главные метрики: KPI-плитки -->
    <div class="alx-grid alx-kpi">
      <div class="alx-card alx-kpi-card">
        <div class="alx-kpi-icon" style="background:color-mix(in srgb, var(--vf-accent) 15%, transparent);color:var(--vf-accent)"><i class="fas fa-phone-volume"></i></div>
        <div class="alx-kpi-body">
          <div class="alx-kpi-label">Звонки</div>
          <div class="alx-kpi-big">${total_calls}</div>
          <div class="alx-kpi-trend">сегодня <b>${callsToday}</b> · неделя <b>${callsWeek}</b>${weekDelta !== 0 ? ` <span class="${weekDelta>0?'alx-up':'alx-down'}"><i class="fas fa-${weekDelta>0?'arrow-up':'arrow-down'}"></i>${Math.abs(weekDelta)}%</span>` : ''}</div>
        </div>
      </div>
      <div class="alx-card alx-kpi-card">
        <div class="alx-kpi-icon" style="background:color-mix(in srgb, var(--vf-success) 15%, transparent);color:var(--vf-success)"><i class="fas fa-users"></i></div>
        <div class="alx-kpi-body">
          <div class="alx-kpi-label">Клиенты</div>
          <div class="alx-kpi-big">${total_contacts}</div>
          <div class="alx-kpi-trend">в работе <b>${active_contacts}</b> · в очереди <b>${scheduled_tasks}</b></div>
        </div>
      </div>
      <div class="alx-card alx-kpi-card">
        <div class="alx-kpi-icon" style="background:color-mix(in srgb, var(--vf-warning) 15%, transparent);color:var(--vf-warning)"><i class="fas fa-circle-check"></i></div>
        <div class="alx-kpi-body">
          <div class="alx-kpi-label">Дозвон</div>
          <div class="alx-kpi-big">${answer_rate}%</div>
          <div class="alx-kpi-trend">ответили <b>${answered_calls}</b> из <b>${total_calls}</b></div>
        </div>
      </div>
      <div class="alx-card alx-kpi-card">
        <div class="alx-kpi-icon" style="background:color-mix(in srgb, var(--vf-danger) 15%, transparent);color:var(--vf-accent)"><i class="fas fa-trophy"></i></div>
        <div class="alx-kpi-body">
          <div class="alx-kpi-label">Конверсия</div>
          <div class="alx-kpi-big">${conversion_rate}%</div>
          <div class="alx-kpi-trend">успех <b>${won_contacts}</b> · потеряно <b>${lost_contacts}</b></div>
        </div>
      </div>
    </div>

    <!-- Звонки по дням (большая карта) -->
    <div class="alx-card alx-card-wide">
      <div class="alx-card-head">
        <h4><i class="far fa-calendar-days"></i> Звонки по дням · 14 дней</h4>
        <div class="alx-card-sub">Прошлая неделя: ${callsPrevWeek} · Эта: ${callsWeek}</div>
      </div>
      ${_areaChart(days)}
    </div>

    <!-- Воронка (donut + легенда) и Исходы (horiz bars) -->
    <div class="alx-grid alx-grid-2">
      <div class="alx-card">
        <div class="alx-card-head"><h4><i class="fas fa-stream"></i> Воронка · распределение клиентов</h4></div>
        <div class="alx-donut-wrap">
          ${_donutChart(stageSlices, 'клиентов')}
          ${_donutLegend(stageSlices)}
        </div>
      </div>
      <div class="alx-card">
        <div class="alx-card-head"><h4><i class="fas fa-shuffle"></i> Исходы последних звонков</h4></div>
        <div class="alx-bars">
          ${Object.keys(decisions).filter(d => decisions[d] > 0 || d !== 'OTHER').map(d =>
            _hBarRow(decMap[d], decisions[d], decTotal, decColor[d])
          ).join('')}
        </div>
      </div>
    </div>

    <!-- Каналы коммуникации -->
    <div class="alx-card">
      <div class="alx-card-head">
        <h4><i class="fas fa-tower-cell"></i> Каналы коммуникации</h4>
        <div class="alx-card-sub">Из каких каналов приходят разговоры и переписки</div>
      </div>
      <div class="alx-stack">
        ${Object.entries(channels).filter(([, v]) => v > 0).map(([k, v]) => {
          const m = chanMeta[k] || chanMeta.other;
          const pct = Math.round(v / chanTotal * 100);
          return `<div class="alx-stack-seg" style="flex:${v};background:${m.color}" title="${esc(m.label)}: ${v} (${pct}%)"></div>`;
        }).join('') || `<div class="alx-stack-seg alx-stack-empty">нет данных</div>`}
      </div>
      <div class="alx-chan-grid">
        ${Object.entries(channels).map(([k, v]) => {
          const m = chanMeta[k] || chanMeta.other;
          const pct = Math.round(v / chanTotal * 100);
          return `
            <div class="alx-chan">
              <div class="alx-chan-dot" style="background:${m.color}22;color:${m.color}"><i class="${m.icon}"></i></div>
              <div class="alx-chan-body">
                <div class="alx-chan-label">${esc(m.label)}</div>
                <div class="alx-chan-val"><b>${v}</b> <span class="alx-chan-pct">${pct}%</span></div>
              </div>
            </div>`;
        }).join('')}
      </div>
    </div>

    <!-- Деньги — заглушка до появления полей сделки у клиента -->
    <div class="alx-card alx-money-card">
      <div class="alx-card-head">
        <h4><i class="fas fa-coins"></i> Выручка и средний чек</h4>
        <div class="alx-card-sub">Валюта — сом (KGS)</div>
      </div>
      <div class="alx-money-row">
        <div class="alx-money-cell">
          <div class="alx-money-label">Выручка от успешных сделок</div>
          <div class="alx-money-big">${totalRevenue != null ? totalRevenue.toLocaleString('ru-RU') + ' сом' : '— сом'}</div>
        </div>
        <div class="alx-money-cell">
          <div class="alx-money-label">Средний чек клиента</div>
          <div class="alx-money-big">${avgCheck != null ? avgCheck.toLocaleString('ru-RU') + ' сом' : '— сом'}</div>
        </div>
        <div class="alx-money-cell">
          <div class="alx-money-label">Успешно закрыто клиентов</div>
          <div class="alx-money-big">${won_contacts}</div>
        </div>
      </div>
      <div class="alx-money-note"><i class="fas fa-info-circle"></i> Чтобы считать выручку автоматически, у карточки клиента должно быть поле «Сумма сделки». Включим в следующем обновлении — тогда эти цифры заполнятся без ручного ввода.</div>
    </div>
  `;
}
