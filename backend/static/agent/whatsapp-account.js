/* ============================================================================
 * agent/whatsapp-account.js — WhatsApp агента (Evolution API, неофициально)
 *                              → /api/agent/whatsapp
 * Номер подключается по QR-коду, как WhatsApp Web: «Подключить» создаёт
 * подключение на шлюзе wa.voksyai.online, модалка показывает QR и раз в 3 с
 * опрашивает /qr, пока номер не подключится. После этого агент отвечает на
 * входящие и может писать первым (инструменты whatsapp_*), звонков нет.
 * Строка рендерится внутри списка коннекторов (connectors.js вызывает
 * waAccountConnectorRowHtml / waAccountSummaryHtml), как личный Telegram.
 * Часть страницы /static/agent.html. Классический скрипт (НЕ ES-модуль).
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

const WA_ACC_API = '/api/agent/whatsapp';
let waAccState = null;   // ответ GET /whatsapp (или null)
let waAccBusy = false;
let waQrTimer = null;    // опрос /qr, пока открыта модалка и ждём сканирования

async function loadWaAccount(){
  try{
    const r = await apiFetch(WA_ACC_API);
    waAccState = (r && r.status === 200) ? await r.json() : null;
  }catch(e){ waAccState = null; }
  if(typeof renderConnectorsBlock === 'function') renderConnectorsBlock();
  if(typeof renderConnectorsList === 'function') renderConnectorsList();
  renderWaAccountModal();
}

function waAccountAvailable(){
  return !!(waAccState && waAccState.configured);
}

// ── Строка в списке коннекторов ──
function waAccountConnectorRowHtml(){
  const s = waAccState;
  if(!s || !s.configured) return '';
  const st = s.status || 'not_connected';
  let right, sub, subColor;
  if(st === 'connected'){
    right = `${s.phone_masked ? `<div style="font-size:12px;color:#64748b">${esc(s.phone_masked)}</div>` : ''}`
      + `<button class="btn btn-secondary btn-sm" onclick="openWaAccountModal()"><i class="fas fa-gear"></i> Настроить</button>`;
    sub = s.auto_reply_enabled ? 'Подключено · автоответ включён' : 'Подключено · автоответ выключен';
    subColor = '#166534';
  } else if(st === 'pending_qr'){
    right = `<button class="btn btn-primary btn-sm" onclick="openWaAccountModal()"><i class="fas fa-qrcode"></i> Показать QR</button>`;
    sub = 'Ждёт сканирования QR-кода';
    subColor = '#b45309';
  } else if(st === 'disconnected'){
    right = `<button class="btn btn-primary btn-sm" onclick="openWaAccountModal()"><i class="fas fa-link"></i> Переподключить</button>`;
    sub = 'Номер отключён с телефона — нужен новый QR';
    subColor = '#b45309';
  } else {
    right = `<button class="btn btn-primary btn-sm" onclick="openWaAccountModal()"><i class="fas fa-link"></i> Подключить</button>`;
    sub = 'Не подключено';
    subColor = '#94a3b8';
  }
  return `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;`
    + `padding:12px 0;border-bottom:1px solid var(--border,#e2e8f0)">`
    + `<div style="display:flex;align-items:center;gap:10px">`
    + `<i class="fa-brands fa-whatsapp" style="color:#25D366;font-size:20px;width:22px;text-align:center"></i>`
    + `<div><div style="font-weight:600;font-size:14px">WhatsApp</div>`
    + `<div style="font-size:12px;color:${subColor}">${sub}</div></div></div>`
    + `<div style="text-align:right">${right}</div></div>`;
}

function waAccountSummaryHtml(){
  const s = waAccState;
  if(!s || !s.configured || s.status !== 'connected') return '';
  const who = s.phone_masked ? ' · ' + esc(s.phone_masked) : '';
  return `<div style="font-size:13px;color:var(--green-dark,#166534);margin:2px 0">`
    + `<i class="fas fa-circle-check"></i> WhatsApp${who}</div>`;
}

// ── Модалка ──
function openWaAccountModal(){
  const ov = document.getElementById('wa-account-modal-overlay');
  if(ov) ov.classList.remove('hidden');
  renderWaAccountModal();
  if(waAccState && waAccState.status === 'pending_qr') _waStartPolling();
}

function closeWaAccountModal(){
  const ov = document.getElementById('wa-account-modal-overlay');
  if(ov) ov.classList.add('hidden');
  _waStopPolling();
}

function _waWarnHtml(){
  return `<div style="background:#FEF9C3;border-left:3px solid #CA8A04;padding:10px 12px;border-radius:8px;font-size:12px;color:#713F12;margin-bottom:14px">
      <b>Важно:</b> подключение неофициальное (как WhatsApp Web). WhatsApp может
      заблокировать номер за рассылки незнакомым людям — не используйте холодную
      базу, лучше отдельный рабочий номер. Агент пишет первым только в пределах
      суточного лимита новых чатов. Звонить через WhatsApp агент не умеет.
    </div>`;
}

function renderWaAccountModal(){
  const el = document.getElementById('wa-account-modal-body');
  if(!el) return;
  const s = waAccState;
  if(!s || !s.configured){
    el.innerHTML = '<div class="empty">WhatsApp-шлюз не настроен на сервере</div>';
    return;
  }
  const st = s.status || 'not_connected';

  if(st === 'not_connected' || st === 'disconnected'){
    el.innerHTML = `
      ${st === 'disconnected' ? `<div style="background:#FEE2E2;border-left:3px solid #DC2626;padding:10px 12px;border-radius:8px;font-size:12px;color:#7F1D1D;margin-bottom:14px">Номер вышел из WhatsApp Web (отключили на телефоне или WhatsApp завершил сессию). Подключите заново.</div>` : _waWarnHtml()}
      <div style="font-size:13px;margin-bottom:14px">Нажмите «Получить QR-код», затем на телефоне откройте WhatsApp → <b>Настройки → Связанные устройства → Привязка устройства</b> и отсканируйте код.</div>
      <button class="btn btn-primary" id="waacc-submit" onclick="waAccConnect()"><i class="fas fa-qrcode"></i> Получить QR-код</button>`;
  } else if(st === 'pending_qr'){
    const qr = s.qr
      ? `<img src="${esc(s.qr)}" alt="QR" style="width:260px;height:260px;image-rendering:pixelated;border:1px solid var(--border,#e2e8f0);border-radius:12px">`
      : `<div style="width:260px;height:260px;display:flex;align-items:center;justify-content:center;border:1px dashed var(--border,#e2e8f0);border-radius:12px"><div class="spinner"></div></div>`;
    el.innerHTML = `
      <div style="font-size:13px;margin-bottom:12px">На телефоне: WhatsApp → <b>Настройки → Связанные устройства → Привязка устройства</b>, наведите камеру на код. Код обновляется сам.</div>
      <div style="display:flex;justify-content:center;margin-bottom:12px">${qr}</div>
      <div style="font-size:12px;color:#64748b;text-align:center;margin-bottom:14px"><span class="spinner" style="width:10px;height:10px;border-width:2px;display:inline-block;vertical-align:middle"></span> Ждём сканирования…</div>
      <button class="btn btn-secondary" onclick="waAccDisconnect(true)">Отменить</button>`;
  } else { // connected
    const who = [s.wa_name, s.phone_masked].filter(Boolean).join(' · ');
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
        <i class="fa-brands fa-whatsapp" style="color:#25D366;font-size:22px"></i>
        <div><div style="font-weight:600;font-size:14px">Подключён${who ? ': ' + esc(who) : ''}</div></div>
      </div>
      ${_waWarnHtml()}
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--border,#e2e8f0)">
        <div>
          <div style="font-weight:600;font-size:13px">Автоответ на входящие</div>
          <div style="font-size:12px;color:#64748b">Агент сам отвечает на новые сообщения (через несколько секунд после последнего)</div>
        </div>
        <label class="switch"><input type="checkbox" ${s.auto_reply_enabled ? 'checked' : ''} onchange="waAccSave({auto_reply_enabled: this.checked})"><span class="slider"></span></label>
      </div>
      <div style="padding:10px 0;border-top:1px solid var(--border,#e2e8f0)">
        <div style="font-weight:600;font-size:13px;margin-bottom:6px">Кому отвечать</div>
        <select class="form-input" onchange="waAccSave({reply_scope: this.value})">
          <option value="contacts" ${s.reply_scope !== 'all' ? 'selected' : ''}>Только контактам агента (рекомендуется)</option>
          <option value="all" ${s.reply_scope === 'all' ? 'selected' : ''}>Всем, кто напишет (контакт создаётся сам)</option>
        </select>
      </div>
      <div style="padding:10px 0;border-top:1px solid var(--border,#e2e8f0)">
        <div style="font-weight:600;font-size:13px;margin-bottom:6px">Новых чатов в сутки (агент пишет первым)</div>
        <input type="number" class="form-input" min="0" max="100" value="${Number(s.daily_new_chats_limit || 0)}" onchange="waAccSave({daily_new_chats_limit: parseInt(this.value || '0', 10)})" style="max-width:120px">
        <div style="font-size:12px;color:#64748b;margin-top:6px">Для нового номера начинайте с 10–20 в сутки. 0 — агент только отвечает.</div>
      </div>
      <div style="padding-top:14px;border-top:1px solid var(--border,#e2e8f0)">
        <button class="btn btn-secondary" onclick="waAccDisconnect(false)"><i class="fas fa-link-slash"></i> Отключить номер</button>
      </div>`;
  }
}

function _waStartPolling(){
  _waStopPolling();
  waQrTimer = setInterval(waAccPoll, 3000);
}

function _waStopPolling(){
  if(waQrTimer){ clearInterval(waQrTimer); waQrTimer = null; }
}

async function waAccConnect(){
  if(waAccBusy) return;
  waAccBusy = true;
  const btn = document.getElementById('waacc-submit');
  if(btn){ btn.disabled = true; btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> Подождите…'; }
  try{
    const r = await apiFetch(WA_ACC_API + '/connect', { method: 'POST' });
    if(r && r.status === 200){
      waAccState = await r.json();
      if(waAccState.status === 'connected') showToast('WhatsApp подключён', 'success');
      else _waStartPolling();
    } else {
      const err = await r?.json().catch(() => ({}));
      showToast(waAccErr(err.detail), 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
  waAccBusy = false;
  renderWaAccountModal();
  if(typeof renderConnectorsList === 'function') renderConnectorsList();
}

async function waAccPoll(){
  const ov = document.getElementById('wa-account-modal-overlay');
  if(!ov || ov.classList.contains('hidden')){ _waStopPolling(); return; }
  try{
    const r = await apiFetch(WA_ACC_API + '/qr');
    if(r && r.status === 200){
      waAccState = await r.json();
      if(waAccState.status === 'connected'){
        _waStopPolling();
        showToast('WhatsApp подключён', 'success');
        if(typeof renderConnectorsBlock === 'function') renderConnectorsBlock();
        if(typeof renderConnectorsList === 'function') renderConnectorsList();
      }
      if(waAccState.status !== 'pending_qr') _waStopPolling();
      renderWaAccountModal();
    }
  }catch(e){}
}

async function waAccSave(patch){
  try{
    const r = await apiFetch(WA_ACC_API + '/settings', { method: 'PATCH', body: JSON.stringify(patch) });
    if(r && r.status === 200){
      waAccState = await r.json();
      showToast('Сохранено', 'success');
    } else {
      const err = await r?.json().catch(() => ({}));
      showToast(waAccErr(err.detail), 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
  if(typeof renderConnectorsList === 'function') renderConnectorsList();
  renderWaAccountModal();
}

async function waAccDisconnect(cancelOnly){
  if(!cancelOnly && !confirm('Отключить WhatsApp? Агент перестанет писать клиентам и отвечать на входящие. Переписка сохранится.')) return;
  _waStopPolling();
  try{
    const r = await apiFetch(WA_ACC_API, { method: 'DELETE' });
    if(r && r.status === 200 && !cancelOnly) showToast('WhatsApp отключён', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
  await loadWaAccount();
}

function waAccErr(detail){
  const map = {
    not_configured: 'WhatsApp-шлюз не настроен на сервере',
    already_connected: 'Номер уже подключён',
    gateway_unreachable: 'WhatsApp-шлюз недоступен, попробуйте позже',
    instance_not_found: 'Подключение не найдено на шлюзе — отключите и подключите заново',
    invalid_reply_scope: 'Неверное значение охвата',
    invalid_limit: 'Лимит — от 0 до 100',
    not_connected: 'WhatsApp не подключён',
    agent_not_found: 'Агент не найден',
  };
  return map[detail] || 'Ошибка WhatsApp, попробуйте ещё раз';
}
