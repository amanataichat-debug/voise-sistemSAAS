/* ============================================================================
 * agent/connectors.js — Внешние коннекторы агента (Google Календарь, Gmail)
 *                       через Composio → /api/agent/connectors
 * Часть страницы /static/agent.html (Voicyfy Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ════════════════ КОННЕКТОРЫ (Composio) ════════════════

let connectorsState = null;

const CONNECTOR_META = {
  google_calendar: { label: 'Google Календарь', icon: 'fa-calendar-days', color: '#4285F4' },
  gmail:           { label: 'Gmail',            icon: 'fa-envelope',      color: '#EA4335' },
};

async function loadConnectors(){
  try{
    const r = await apiFetch(API + '/connectors');
    if(!r || r.status !== 200){ connectorsState = null; renderConnectorsBlock(); return; }
    connectorsState = await r.json();
  }catch(e){ connectorsState = null; }
  renderConnectorsBlock();
  renderConnectorsList();
}

function renderConnectorsBlock(){
  const el = document.getElementById('connectors-block');
  if(!el) return;
  const s = connectorsState;
  const card = document.getElementById('connectors-card');

  // Личный Telegram (telegram-account.js) живёт в этой же карточке.
  const tgAvailable = (typeof tgAccountAvailable === 'function') && tgAccountAvailable();

  if((!s || !s.configured) && !tgAvailable){
    // Ни Composio, ни Telegram не настроены на сервере — прячем карточку целиком.
    if(card) card.style.display = 'none';
    return;
  }
  if(card) card.style.display = '';

  const connected = (s && s.configured) ? (s.connectors || []).filter(c => c.connected) : [];
  const tgSummary = (typeof tgAccountSummaryHtml === 'function') ? tgAccountSummaryHtml() : '';
  if(!connected.length && !tgSummary){
    el.innerHTML = '<div class="empty">Ничего не подключено</div>';
    return;
  }
  el.innerHTML = connected.map(c => {
    const m = CONNECTOR_META[c.toolkit] || { label: c.toolkit, icon: 'fa-plug', color: '#64748b' };
    const who = c.connected_email ? ' · ' + esc(c.connected_email) : '';
    return `<div style="font-size:13px;color:var(--green-dark,#166534);margin:2px 0">`
      + `<i class="fas fa-circle-check"></i> ${esc(m.label)}${who}</div>`;
  }).join('') + tgSummary;
}

function openConnectorsModal(){
  const ov = document.getElementById('connectors-modal-overlay');
  if(ov) ov.classList.remove('hidden');
  renderConnectorsList();
  // Подтянуть свежий статус на случай возврата из OAuth.
  loadConnectors();
  if(typeof loadTgAccount === 'function') loadTgAccount();
}

function closeConnectorsModal(){
  const ov = document.getElementById('connectors-modal-overlay');
  if(ov) ov.classList.add('hidden');
}

function renderConnectorsList(){
  const el = document.getElementById('connectors-list');
  if(!el) return;
  const s = connectorsState;
  // Строка личного Telegram (telegram-account.js) — в общем списке коннекторов.
  const tgRow = (typeof tgAccountConnectorRowHtml === 'function') ? tgAccountConnectorRowHtml() : '';
  if(!s || !s.configured){
    el.innerHTML = tgRow || '<div class="empty">Коннекторы недоступны</div>';
    return;
  }
  el.innerHTML = tgRow + (s.connectors || []).map(c => {
    const m = CONNECTOR_META[c.toolkit] || { label: c.toolkit, icon: 'fa-plug', color: '#64748b' };
    let right;
    if(!c.available){
      right = `<span style="font-size:12px;color:#94a3b8">Недоступен</span>`;
    } else if(c.connected){
      const who = c.connected_email ? `<div style="font-size:12px;color:#64748b">${esc(c.connected_email)}</div>` : '';
      right = `${who}<button class="btn btn-secondary btn-sm" onclick="disconnectConnector('${c.toolkit}')">`
        + `<i class="fas fa-link-slash"></i> Отключить</button>`;
    } else {
      const hint = c.status === 'pending' ? ' (ожидание…)'
                 : c.status === 'error' ? ' заново' : '';
      const label = c.status === 'error' ? 'Переподключить' : 'Подключить';
      right = `<button class="btn btn-primary btn-sm" id="connect-btn-${c.toolkit}" onclick="connectConnector('${c.toolkit}')">`
        + `<i class="fas fa-link"></i> ${label}${hint}</button>`;
    }
    // Подпись статуса: подключено / требует переподключения (error) / не подключено.
    const sub = c.connected ? 'Подключено'
              : c.status === 'error' ? 'Требует переподключения'
              : 'Не подключено';
    const subColor = c.connected ? '#166534' : c.status === 'error' ? '#b45309' : '#94a3b8';
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;`
      + `padding:12px 0;border-bottom:1px solid var(--border,#e2e8f0)">`
      + `<div style="display:flex;align-items:center;gap:10px">`
      + `<i class="fas ${m.icon}" style="color:${m.color};font-size:18px;width:22px;text-align:center"></i>`
      + `<div><div style="font-weight:600;font-size:14px">${esc(m.label)}</div>`
      + `<div style="font-size:12px;color:${subColor}">${sub}</div></div></div>`
      + `<div style="text-align:right">${right}</div></div>`;
  }).join('');
}

async function connectConnector(toolkit){
  // Открываем попап СИНХРОННО в обработчике клика (иначе блокировщик попапов).
  let popup = null;
  try{ popup = window.open('', 'composio_connect', 'width=520,height=720'); }catch(e){}

  const btn = document.getElementById('connect-btn-' + toolkit);
  if(btn){ btn.disabled = true; btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> Открываю…'; }

  try{
    const r = await apiFetch(API + '/connectors/' + toolkit + '/connect', { method: 'POST' });
    if(r && r.status === 200){
      const data = await r.json();
      if(data.redirect_url){
        if(popup && !popup.closed){ popup.location.href = data.redirect_url; }
        else { window.location.href = data.redirect_url; }
      } else if(data.connected){
        // Переиспользовано существующее подключение этого пользователя — без OAuth.
        if(popup) popup.close();
        const m = CONNECTOR_META[toolkit] || { label: toolkit };
        showToast(`${m.label} подключён`, 'success');
        await loadConnectors();
      } else {
        if(popup) popup.close();
        showToast('Не удалось получить ссылку авторизации', 'error');
      }
    } else {
      if(popup) popup.close();
      const err = await r?.json().catch(()=>({}));
      showToast(connectorErr(err.detail), 'error');
    }
  }catch(e){
    if(popup) popup.close();
    showToast('Ошибка сети', 'error');
  }
  renderConnectorsList();
}

async function disconnectConnector(toolkit){
  const m = CONNECTOR_META[toolkit] || { label: toolkit };
  if(!confirm(`Отключить ${m.label}? Агент перестанет использовать этот сервис.`)) return;
  try{
    const r = await apiFetch(API + '/connectors/' + toolkit, { method: 'DELETE' });
    if(r && r.status === 200){
      showToast(`${m.label} отключён`, 'success');
      await loadConnectors();
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(connectorErr(err.detail), 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

function connectorErr(detail){
  const map = {
    composio_not_configured: 'Коннекторы не настроены на сервере',
    toolkit_auth_config_missing: 'Для этого сервиса не настроен OAuth на сервере',
    public_base_url_not_set: 'Не задан публичный URL сервера',
    composio_initiate_failed: 'Composio недоступен, попробуйте позже',
    no_redirect_url: 'Composio не вернул ссылку авторизации',
    unknown_toolkit: 'Неизвестный коннектор',
  };
  if(typeof errText === 'function' && !map[detail]) return errText(detail) || 'Ошибка подключения';
  return map[detail] || 'Ошибка подключения';
}

// Возврат из OAuth-попапа: callback-страница шлёт postMessage.
window.addEventListener('message', function(ev){
  const d = ev && ev.data;
  if(!d || d.type !== 'connector_result') return;
  const m = CONNECTOR_META[d.toolkit] || { label: d.toolkit };
  if(d.status === 'success'){ showToast(`${m.label} подключён`, 'success'); }
  else { showToast(`Не удалось подключить ${m.label}`, 'error'); }
  loadConnectors();
});

// Если callback вернулся в этой же вкладке (попап заблокирован) — покажем тост.
(function(){
  try{
    const p = new URLSearchParams(window.location.search);
    const tk = p.get('connector');
    const st = p.get('status');
    if(tk && st){
      const m = CONNECTOR_META[tk] || { label: tk };
      setTimeout(function(){
        if(typeof showToast === 'function'){
          showToast(st === 'success' ? `${m.label} подключён` : `Не удалось подключить ${m.label}`,
                    st === 'success' ? 'success' : 'error');
        }
      }, 800);
      // Чистим query, чтобы тост не повторялся при перезагрузке.
      history.replaceState({}, '', window.location.pathname);
    }
  }catch(e){}
})();
