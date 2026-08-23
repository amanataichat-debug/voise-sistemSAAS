/* ============================================================================
 * agent/telegram-account.js — ЛИЧНЫЙ Telegram-аккаунт агента (MTProto)
 *                             → /api/agent/telegram-account
 * НЕ путать с telegram.js (Telegram-БОТ агента, Bot API). Здесь пользователь
 * авторизует свой личный аккаунт (телефон → код → пароль 2FA), после чего
 * агент пишет клиентам от его имени и отвечает на входящие (поллер бэкенда).
 * Строка коннектора рендерится внутри списка коннекторов (connectors.js
 * вызывает tgAccountConnectorRowHtml/tgAccountSummaryHtml).
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

const TG_ACC_API = '/api/agent/telegram-account';
let tgAccState = null;   // ответ GET /telegram-account (или null)
let tgAccBusy = false;   // защита от дабл-кликов на шагах авторизации

async function loadTgAccount(){
  try{
    const r = await apiFetch(TG_ACC_API);
    tgAccState = (r && r.status === 200) ? await r.json() : null;
  }catch(e){ tgAccState = null; }
  // Перерисовать блок/список коннекторов (они читают tgAccState) и модалку.
  if(typeof renderConnectorsBlock === 'function') renderConnectorsBlock();
  if(typeof renderConnectorsList === 'function') renderConnectorsList();
  renderTgAccountModal();
}

// ── Строка в списке коннекторов (вызывается из connectors.js) ──
function tgAccountConnectorRowHtml(){
  const s = tgAccState;
  if(!s || !s.configured) return '';
  const st = s.status || 'not_connected';
  let right, sub, subColor;
  if(st === 'connected'){
    const who = s.tg_username ? '@' + s.tg_username : (s.phone_masked || '');
    right = `${who ? `<div style="font-size:12px;color:#64748b">${esc(who)}</div>` : ''}`
      + `<button class="btn btn-secondary btn-sm" onclick="openTgAccountModal()"><i class="fas fa-gear"></i> Настроить</button>`;
    sub = s.auto_reply_enabled ? 'Подключено · автоответ включён' : 'Подключено · автоответ выключен';
    subColor = '#166534';
  } else if(st === 'pending_code' || st === 'pending_password'){
    right = `<button class="btn btn-primary btn-sm" onclick="openTgAccountModal()"><i class="fas fa-link"></i> Продолжить</button>`;
    sub = 'Авторизация не завершена';
    subColor = '#b45309';
  } else if(st === 'error'){
    right = `<button class="btn btn-primary btn-sm" onclick="openTgAccountModal()"><i class="fas fa-link"></i> Переподключить</button>`;
    sub = 'Требует переподключения';
    subColor = '#b45309';
  } else {
    right = `<button class="btn btn-primary btn-sm" onclick="openTgAccountModal()"><i class="fas fa-link"></i> Подключить</button>`;
    sub = 'Не подключено';
    subColor = '#94a3b8';
  }
  return `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;`
    + `padding:12px 0;border-bottom:1px solid var(--border,#e2e8f0)">`
    + `<div style="display:flex;align-items:center;gap:10px">`
    + `<i class="fas fa-paper-plane" style="color:#229ED9;font-size:18px;width:22px;text-align:center"></i>`
    + `<div><div style="font-weight:600;font-size:14px">Telegram (личный аккаунт)</div>`
    + `<div style="font-size:12px;color:${subColor}">${sub}</div></div></div>`
    + `<div style="text-align:right">${right}</div></div>`;
}

// ── Строка в свёрнутом блоке коннекторов на дашборде ──
function tgAccountSummaryHtml(){
  const s = tgAccState;
  if(!s || !s.configured || s.status !== 'connected') return '';
  const who = s.tg_username ? ' · @' + esc(s.tg_username) : '';
  return `<div style="font-size:13px;color:var(--green-dark,#166534);margin:2px 0">`
    + `<i class="fas fa-circle-check"></i> Telegram (личный)${who}</div>`;
}

// true, если карточку коннекторов стоит показывать ради Telegram
function tgAccountAvailable(){
  return !!(tgAccState && tgAccState.configured);
}

// ── Модалка ──
function openTgAccountModal(){
  const ov = document.getElementById('tg-account-modal-overlay');
  if(ov) ov.classList.remove('hidden');
  renderTgAccountModal();
}

function closeTgAccountModal(){
  const ov = document.getElementById('tg-account-modal-overlay');
  if(ov) ov.classList.add('hidden');
}

function renderTgAccountModal(){
  const el = document.getElementById('tg-account-modal-body');
  if(!el) return;
  const s = tgAccState;
  if(!s || !s.configured){
    el.innerHTML = '<div class="empty">Коннектор Telegram не настроен на сервере</div>';
    return;
  }
  const st = s.status || 'not_connected';

  const warn = `<div style="background:#FEF9C3;border-left:3px solid #CA8A04;padding:10px 12px;border-radius:8px;font-size:12px;color:#713F12;margin-bottom:14px">
      <b>Важно:</b> агент будет писать клиентам с вашего личного аккаунта. Telegram
      банит за сообщения незнакомым людям — не используйте рассылки по холодной базе.
      Отправка ограничена лимитами, писать первым по номеру телефона агент может лишь изредка.
    </div>`;

  if(st === 'not_connected' || st === 'error'){
    el.innerHTML = `
      ${st === 'error' ? `<div style="background:#FEE2E2;border-left:3px solid #DC2626;padding:10px 12px;border-radius:8px;font-size:12px;color:#7F1D1D;margin-bottom:14px">Сессия недействительна (${esc(s.last_error || 'отозвана')}). Подключите аккаунт заново.</div>` : warn}
      <label class="form-label">Номер телефона аккаунта Telegram</label>
      <input type="tel" class="form-input" id="tgacc-phone" placeholder="+79991234567" value="">
      <div style="font-size:12px;color:#64748b;margin:6px 0 14px">Telegram пришлёт код подтверждения в приложение (не по SMS).</div>
      <button class="btn btn-primary" id="tgacc-submit" onclick="tgAccStart()"><i class="fas fa-paper-plane"></i> Получить код</button>`;
  } else if(st === 'pending_code'){
    el.innerHTML = `
      <div style="font-size:13px;margin-bottom:12px">Код отправлен в Telegram на номер <b>${esc(s.phone_masked || '')}</b>. Откройте Telegram и введите код из сообщения от «Telegram».</div>
      <label class="form-label">Код подтверждения</label>
      <input type="text" class="form-input" id="tgacc-code" placeholder="12345" autocomplete="one-time-code">
      <div style="height:14px"></div>
      <button class="btn btn-primary" id="tgacc-submit" onclick="tgAccVerifyCode()"><i class="fas fa-check"></i> Подтвердить</button>
      <button class="btn btn-secondary" onclick="tgAccRestart()" style="margin-left:8px">Начать заново</button>`;
  } else if(st === 'pending_password'){
    el.innerHTML = `
      <div style="font-size:13px;margin-bottom:12px">На аккаунте включена двухэтапная аутентификация. Введите облачный пароль.</div>
      <label class="form-label">Облачный пароль (2FA)</label>
      <input type="password" class="form-input" id="tgacc-password" placeholder="Пароль">
      <div style="height:14px"></div>
      <button class="btn btn-primary" id="tgacc-submit" onclick="tgAccVerifyPassword()"><i class="fas fa-check"></i> Войти</button>
      <button class="btn btn-secondary" onclick="tgAccRestart()" style="margin-left:8px">Начать заново</button>`;
  } else { // connected
    const who = [s.tg_first_name, s.tg_username ? '@' + s.tg_username : null].filter(Boolean).join(' · ');
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
        <i class="fas fa-circle-check" style="color:#16A34A;font-size:20px"></i>
        <div>
          <div style="font-weight:600;font-size:14px">Подключён${who ? ': ' + esc(who) : ''}</div>
          <div style="font-size:12px;color:#64748b">${esc(s.phone_masked || '')}</div>
        </div>
      </div>
      ${warn}
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--border,#e2e8f0)">
        <div>
          <div style="font-weight:600;font-size:13px">Автоответ на входящие</div>
          <div style="font-size:12px;color:#64748b">Агент сам отвечает на новые сообщения в личке (проверка раз в минуту)</div>
        </div>
        <label class="switch"><input type="checkbox" id="tgacc-autoreply" ${s.auto_reply_enabled ? 'checked' : ''} onchange="tgAccToggleAutoReply(this.checked)"><span class="slider"></span></label>
      </div>
      <div style="padding:10px 0;border-top:1px solid var(--border,#e2e8f0)">
        <div style="font-weight:600;font-size:13px;margin-bottom:6px">Кому отвечать</div>
        <select class="form-input" id="tgacc-scope" onchange="tgAccSetScope(this.value)">
          <option value="contacts" ${s.reply_scope !== 'all' ? 'selected' : ''}>Только контактам агента (рекомендуется)</option>
          <option value="all" ${s.reply_scope === 'all' ? 'selected' : ''}>Всем новым личным сообщениям</option>
        </select>
        <div style="font-size:12px;color:#64748b;margin-top:6px">«Всем» — агент будет отвечать даже друзьям и родным, если они напишут. Используйте только для рабочего аккаунта.</div>
      </div>
      <div style="padding-top:14px;border-top:1px solid var(--border,#e2e8f0)">
        <button class="btn btn-secondary" onclick="tgAccDisconnect()"><i class="fas fa-link-slash"></i> Отключить аккаунт</button>
      </div>`;
  }
}

function _tgAccLock(lock){
  tgAccBusy = lock;
  const btn = document.getElementById('tgacc-submit');
  if(btn){
    btn.disabled = lock;
    if(lock) btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> Подождите…';
  }
}

async function _tgAccPost(path, body){
  const r = await apiFetch(TG_ACC_API + path, { method: 'POST', body: JSON.stringify(body) });
  if(r && r.status === 200){ tgAccState = await r.json(); return true; }
  const err = await r?.json().catch(() => ({}));
  showToast(tgAccErr(err.detail), 'error');
  return false;
}

async function tgAccStart(){
  if(tgAccBusy) return;
  const phone = (document.getElementById('tgacc-phone')?.value || '').trim();
  if(!phone){ showToast('Укажите номер телефона', 'error'); return; }
  _tgAccLock(true);
  try{ await _tgAccPost('/start', { phone }); }
  catch(e){ showToast('Ошибка сети', 'error'); }
  _tgAccLock(false);
  renderTgAccountModal();
}

async function tgAccVerifyCode(){
  if(tgAccBusy) return;
  const code = (document.getElementById('tgacc-code')?.value || '').trim();
  if(!code){ showToast('Введите код', 'error'); return; }
  _tgAccLock(true);
  try{
    const ok = await _tgAccPost('/verify-code', { code });
    if(ok && tgAccState.status === 'connected') showToast('Telegram подключён', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
  _tgAccLock(false);
  await loadTgAccount();
}

async function tgAccVerifyPassword(){
  if(tgAccBusy) return;
  const password = document.getElementById('tgacc-password')?.value || '';
  if(!password){ showToast('Введите пароль', 'error'); return; }
  _tgAccLock(true);
  try{
    const ok = await _tgAccPost('/verify-password', { password });
    if(ok && tgAccState.status === 'connected') showToast('Telegram подключён', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
  _tgAccLock(false);
  await loadTgAccount();
}

// «Начать заново» с шага кода/пароля: сбрасываем незавершённую авторизацию
// удалением строки и возвращаемся к вводу номера.
async function tgAccRestart(){
  try{ await apiFetch(TG_ACC_API, { method: 'DELETE' }); }catch(e){}
  await loadTgAccount();
}

async function tgAccToggleAutoReply(enabled){
  try{
    const r = await apiFetch(TG_ACC_API + '/settings', { method: 'PATCH', body: JSON.stringify({ auto_reply_enabled: enabled }) });
    if(r && r.status === 200){
      tgAccState = await r.json();
      showToast(enabled ? 'Автоответ включён' : 'Автоответ выключен', 'success');
    } else {
      const err = await r?.json().catch(() => ({}));
      showToast(tgAccErr(err.detail), 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
  if(typeof renderConnectorsList === 'function') renderConnectorsList();
  renderTgAccountModal();
}

async function tgAccSetScope(scope){
  try{
    const r = await apiFetch(TG_ACC_API + '/settings', { method: 'PATCH', body: JSON.stringify({ reply_scope: scope }) });
    if(r && r.status === 200) tgAccState = await r.json();
  }catch(e){ showToast('Ошибка сети', 'error'); }
}

async function tgAccDisconnect(){
  if(!confirm('Отключить личный Telegram? Агент перестанет писать клиентам и отвечать на входящие. Сессия будет разлогинена.')) return;
  try{
    const r = await apiFetch(TG_ACC_API, { method: 'DELETE' });
    if(r && r.status === 200){
      showToast('Telegram отключён', 'success');
      closeTgAccountModal();
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
  await loadTgAccount();
}

function tgAccErr(detail){
  const map = {
    not_configured: 'Коннектор Telegram не настроен на сервере',
    already_connected: 'Аккаунт уже подключён',
    auth_not_started: 'Авторизация не начата — введите номер телефона',
    password_not_expected: 'Пароль сейчас не требуется',
    invalid_reply_scope: 'Неверное значение охвата',
    not_connected: 'Telegram не подключён',
    phone_invalid: 'Неверный номер телефона',
    phone_banned: 'Этот номер заблокирован Telegram',
    code_invalid: 'Неверный код подтверждения',
    code_expired: 'Код истёк — начните заново',
    password_invalid: 'Неверный облачный пароль',
    session_revoked: 'Сессия отозвана — подключите заново',
    agent_not_found: 'Агент не найден',
  };
  if(typeof detail === 'string' && detail.startsWith('flood_wait:')){
    return 'Telegram просит подождать ' + detail.split(':')[1] + ' сек. и попробовать снова';
  }
  return map[detail] || 'Ошибка Telegram, попробуйте ещё раз';
}
