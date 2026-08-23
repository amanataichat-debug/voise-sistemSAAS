/* ============================================================================
 * agent/calls.js — История звонков (модалка) и карточка звонка → /api/agent/calls
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ── CALLS MODAL ──
async function openCallsModal(){
  document.getElementById('calls-modal-overlay').classList.remove('hidden');
  const tb = document.querySelector('#calls-modal-overlay .modal-body');
  tb.innerHTML = '<div style="text-align:center;padding:24px"><div class="spinner" style="margin:0 auto"></div></div>';
  try{
    const r = await apiFetch(API + '/calls?limit=50&offset=0');
    if(!r || r.status !== 200){ tb.innerHTML='<div class="empty">Ошибка</div>'; return; }
    const data = await r.json();
    if(!data.calls || !data.calls.length){
      tb.innerHTML = '<div class="empty">Звонков пока не было</div>';
      return;
    }
    tb.innerHTML = data.calls.map((c, i) => `
      <div style="margin-bottom:14px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <div class="avatar">${esc((c.contact_name||'?').trim().charAt(0).toUpperCase())}</div>
          <div style="flex:1">
            <div style="font-weight:600;font-size:13.5px">${esc(c.contact_name || '—')}</div>
            <div style="font-size:11.5px;color:var(--hint)">${esc(c.contact_phone || '')}</div>
          </div>
        </div>
        ${renderCallExpanded(c, 'mc-' + i)}
      </div>
    `).join('');
  }catch(e){
    tb.innerHTML = '<div class="empty">Ошибка сети</div>';
  }
}
function closeCallsModal(){ document.getElementById('calls-modal-overlay').classList.add('hidden'); }

// ── UNIVERSAL CALL CARD (used in calls history + contact details) ──
const POSTCALL_TOOL_LABELS = {
  update_contact_memory: { label: 'Обновил память о контакте', icon: 'fa-brain', color: '#7C3AED' },
  create_agent_task: { label: 'Запланировал перезвон', icon: 'fa-calendar-plus', color: '#2563EB' },
  send_telegram_notification: { label: 'Отправил уведомление в Telegram', icon: 'fa-paper-plane', color: '#0891B2' },
  send_sms: { label: 'Отправил SMS клиенту', icon: 'fa-comment-sms', color: '#16A34A' },
  move_contact_stage: { label: 'Сменил стадию воронки', icon: 'fa-arrows-turn-right', color: '#D97706' },
  update_contact_info: { label: 'Обновил данные контакта', icon: 'fa-user-pen', color: '#0EA5E9' },
  search_knowledge_base: { label: 'Искал в базе знаний', icon: 'fa-magnifying-glass', color: '#6366F1' },
  telegram_send_message: { label: 'Написал клиенту в Telegram', icon: 'fa-comment-dots', color: '#229ED9' },
  telegram_get_thread: { label: 'Прочитал Telegram-переписку', icon: 'fa-comments', color: '#0EA5E9' },
  schedule_telegram_message: { label: 'Запланировал сообщение в Telegram', icon: 'fa-calendar-plus', color: '#229ED9' },
  instagram_send_message: { label: 'Написал клиенту в Instagram', icon: 'fa-comment-dots', color: '#E1306C' },
  instagram_get_thread: { label: 'Прочитал Instagram-переписку', icon: 'fa-comments', color: '#E1306C' },
};

function renderCallExpanded(call, uid){
  const isSms = call.channel === 'sms';
  const isTg = call.channel === 'telegram';
  const isIg = call.channel === 'instagram';
  const isMsg = isSms || isTg || isIg; // текстовое событие (не звонок)
  // Запланированная отправка сообщения (schedule_telegram_message):
  // инициатива агента, «транскрипт» — инструкция, а не текст клиента.
  const isTgOut = isTg && (call.postcall_log || {}).call_direction === 'telegram_outbound';
  const dur = (!isMsg && call.duration_seconds) ? Math.floor(call.duration_seconds)+'с' : '—';
  const decisionBadgeHtml = decisionBadge(call.post_call_decision);
  let statusHtml;
  if(isTgOut){
    statusHtml = (call.postcall_log || {}).message_sent
      ? '<span class="status-badge badge-answered">Отправлено</span>'
      : '<span class="status-badge badge-no-answer">Без отправки</span>';
  } else if(isMsg){
    statusHtml = '<span class="status-badge badge-answered">Обработано</span>';
  } else {
    statusHtml = call.status==='answered'
      ? '<span class="status-badge badge-answered">Ответил</span>'
      : '<span class="status-badge badge-no-answer">Не ответил</span>';
  }
  // Бейдж канала: SMS / Telegram-обработка vs обычный звонок (входящий/исходящий).
  const channelBadge = isSms
    ? '<span class="status-badge" style="background:#DCFCE7;color:#15803D"><i class="fas fa-comment-sms"></i> SMS</span>'
    : isTg
    ? '<span class="status-badge" style="background:#E0F2FE;color:#0369A1"><i class="fas fa-paper-plane"></i> Telegram</span>'
    : isIg
    ? '<span class="status-badge" style="background:#FCE7F3;color:#BE185D"><i class="fa-brands fa-instagram"></i> Instagram</span>'
    : directionBadge(call.direction);

  const pre = call.precall_log || {};
  const post = call.postcall_log || {};

  const preBlock = (pre.first_phrase || pre.call_strategy) ? `
    <div style="background:#EEF2FF;border-left:3px solid #4338CA;padding:12px 14px;border-radius:8px;margin:10px 0">
      <div style="font-size:11px;font-weight:600;color:#4338CA;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px">
        <i class="fas fa-lightbulb"></i> Стратегия PreCall ${pre.model ? `· <span style="font-weight:400;color:var(--muted);text-transform:none">${esc(pre.model)}</span>` : ''}
      </div>
      ${pre.first_phrase ? `<div style="font-size:12px;margin-bottom:6px"><b>Первая фраза:</b> «${esc(pre.first_phrase)}»</div>` : ''}
      ${pre.call_strategy ? `<div style="font-size:12px;margin-bottom:6px"><b>Тактика:</b> ${esc(pre.call_strategy)}</div>` : ''}
      ${pre.tone ? `<div style="font-size:12px;margin-bottom:6px"><b>Тон:</b> ${esc(pre.tone)}</div>` : ''}
      ${pre.key_points && pre.key_points.length ? `<div style="font-size:12px"><b>Ключевые точки:</b><ul style="margin:4px 0 0 18px;color:var(--muted)">${pre.key_points.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
    </div>
  ` : '';

  const toolsHtml = (post.tool_calls && post.tool_calls.length) ? post.tool_calls.map(tc => {
    const meta = POSTCALL_TOOL_LABELS[tc.tool] || { label: tc.tool, icon: 'fa-cog', color: '#6b7280' };
    const ok = tc.result && tc.result.ok !== false;
    let detail = '';
    if(tc.tool === 'update_contact_memory'){
      const a = tc.args || {};
      detail = (a.summary || '') + (a.key_facts && a.key_facts.length ? ' · Факты: ' + a.key_facts.join(', ') : '');
    } else if(tc.tool === 'create_agent_task'){
      const a = tc.args || {};
      detail = `${a.title || ''} на ${a.scheduled_at ? fmtDate(a.scheduled_at) : '?'}`;
    } else if(tc.tool === 'send_telegram_notification'){
      detail = (tc.args || {}).message || '';
    } else if(tc.tool === 'send_sms'){
      detail = (tc.args || {}).text || (tc.args || {}).message || '';
    } else if(tc.tool === 'move_contact_stage'){
      const a = tc.args || {};
      detail = STAGE_META[a.stage] ? STAGE_META[a.stage].label : (a.stage || '');
    } else if(tc.tool === 'update_contact_info'){
      const a = tc.args || {};
      detail = ['name','company','position','notes']
        .filter(k => a[k]).map(k => a[k]).join(' · ');
    } else if(tc.tool === 'search_knowledge_base'){
      detail = (tc.args || {}).query || '';
    } else if(tc.tool === 'telegram_send_message' || tc.tool === 'instagram_send_message'){
      detail = (tc.args || {}).text || '';
    } else if(tc.tool === 'schedule_telegram_message'){
      const a = tc.args || {};
      detail = `${a.instruction || a.title || ''} — на ${a.scheduled_at ? fmtDate(a.scheduled_at) : (a.delay_minutes ? 'через ' + a.delay_minutes + ' мин' : '?')}`;
    }
    return `
      <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--border-soft)">
        <div style="width:26px;height:26px;border-radius:7px;background:${meta.color}20;color:${meta.color};display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:11px">
          <i class="fas ${meta.icon}"></i>
        </div>
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:600">${meta.label} ${ok ? '' : '<span style="color:var(--red);font-size:10px">· ошибка</span>'}</div>
          ${detail ? `<div style="font-size:11.5px;color:var(--muted);margin-top:2px;line-height:1.4">${esc(detail)}</div>` : ''}
        </div>
      </div>`;
  }).join('') : '';

  const postBlock = (post.tool_calls && post.tool_calls.length) || post.final_decision ? `
    <div style="background:#F0FDF4;border-left:3px solid #166534;padding:12px 14px;border-radius:8px;margin:10px 0">
      <div style="font-size:11px;font-weight:600;color:#166534;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px">
        <i class="fas fa-magnifying-glass-chart"></i> Анализ PostCall ${post.model ? `· <span style="font-weight:400;color:var(--muted);text-transform:none">${esc(post.model)}</span>` : ''}
      </div>
      ${post.final_decision ? `<div style="font-size:12px;margin-bottom:8px"><b>Решение:</b> ${decisionRu(post.final_decision)}</div>` : ''}
      ${toolsHtml ? `<div style="font-size:12px"><b>Действия агента:</b>${toolsHtml}</div>` : ''}
    </div>
  ` : '';

  const transcriptBlock = (call.transcript && call.transcript !== '(Транскрипт недоступен)') ? `
    <div style="font-size:11px;font-weight:600;color:var(--hint);text-transform:uppercase;letter-spacing:0.04em;margin:10px 0 4px">
      <i class="${isIg ? 'fa-brands fa-instagram' : isSms ? 'fas fa-comment-sms' : isTg ? 'fas fa-paper-plane' : 'fas fa-quote-left'}"></i> ${isSms ? 'Текст входящего SMS' : isTgOut ? 'Инструкция запланированного сообщения' : isTg ? 'Текст входящего сообщения Telegram' : isIg ? 'Текст входящего сообщения Instagram' : 'Транскрипт звонка'}
    </div>
    <div style="background:var(--bg);padding:12px 14px;border-radius:8px;margin:0 0 10px;font-size:12px;color:var(--muted);white-space:pre-wrap;line-height:1.6;max-height:300px;overflow-y:auto">
      ${esc(call.transcript)}
    </div>
  ` : '';

  const details = preBlock + transcriptBlock + postBlock;
  const hasDetails = !!details.trim();

  return `
    <div style="border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:10px">
      <div onclick="${hasDetails ? `toggleCallCard('${uid}')` : ''}" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;${hasDetails ? 'cursor:pointer' : ''}">
        <span style="font-weight:600;font-size:12.5px">${fmtDate(call.started_at || call.created_at)}</span>
        <span style="color:var(--muted);font-size:12px">${dur}</span>
        ${channelBadge}
        ${statusHtml}
        ${decisionBadgeHtml}
        ${hasDetails ? `<span style="margin-left:auto;font-size:11px;color:var(--blue);font-weight:600"><i class="fas fa-chevron-down" id="${uid}-chevron" style="transition:transform .2s"></i> Размышления</span>` : ''}
      </div>
      ${hasDetails ? `<div id="${uid}-details" style="display:none">${details}</div>` : ''}
    </div>`;
}

function toggleCallCard(uid){
  const el = document.getElementById(uid + '-details');
  const chev = document.getElementById(uid + '-chevron');
  if(!el) return;
  const open = el.style.display !== 'none';
  el.style.display = open ? 'none' : 'block';
  if(chev) chev.style.transform = open ? 'rotate(0deg)' : 'rotate(180deg)';
}


