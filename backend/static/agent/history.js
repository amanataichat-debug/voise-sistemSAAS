/* ============================================================================
 * agent/history.js — «История работы агента»: лента фоновых задач PreCall/PostCall
 * (звонки + обработка входящих SMS) → /api/agent/calls?limit=100
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 *
 * Каждая запись agent_calls несёт precall_log (что оркестратор спланировал) и
 * postcall_log.tool_calls (все фоновые действия: перезвон, Telegram, SMS, память,
 * стадия). Карточки раскрываются через общий renderCallExpanded (calls.js).
 * SMS-обработка приходит тем же эндпоинтом с channel="sms".
 * ========================================================================== */

// Всегда грузим последние 100 фоновых задач, чтобы не перегружать панель.
const AGENT_HISTORY_LIMIT = 100;

async function loadAgentHistory(){
  const el = document.getElementById('agent-history-list');
  if(!el) return;
  el.innerHTML = '<div style="text-align:center;padding:18px"><div class="spinner" style="margin:0 auto"></div></div>';
  try{
    const r = await apiFetch(API + '/calls?limit=' + AGENT_HISTORY_LIMIT + '&offset=0');
    if(!r || r.status !== 200){ el.innerHTML = '<div class="empty">Ошибка загрузки</div>'; return; }
    const data = await r.json();
    if(!data.calls || !data.calls.length){
      el.innerHTML = '<div class="empty">Агент ещё не выполнял задач</div>';
      return;
    }
    el.innerHTML = data.calls.map((c, i) => `
      <div class="hist-item">
        <div class="hist-item-head">
          <div class="avatar">${esc((c.contact_name || c.contact_phone || '?').trim().charAt(0).toUpperCase())}</div>
          <div style="flex:1;min-width:0">
            <div class="hist-item-name">${esc(c.contact_name || c.contact_phone || '—')}</div>
            ${c.contact_name && c.contact_phone ? `<div class="hist-item-phone">${esc(c.contact_phone)}</div>` : ''}
          </div>
        </div>
        ${renderCallExpanded(c, 'hist-' + i)}
      </div>
    `).join('');
  }catch(e){
    el.innerHTML = '<div class="empty">Ошибка сети</div>';
  }
}
