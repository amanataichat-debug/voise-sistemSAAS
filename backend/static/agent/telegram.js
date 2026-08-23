/* ============================================================================
 * agent/telegram.js — Telegram-бот агента → /api/agent/telegram/*
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ════════════════ TELEGRAM ════════════════
async function loadTelegramStatus(){
  try{
    const r = await apiFetch(TG_API);
    if(!r || r.status !== 200){ telegramState = null; renderTelegramStatusBlock(); return; }
    telegramState = await r.json();
  }catch(e){ telegramState = null; }
  renderTelegramStatusBlock();
}

function renderTelegramStatusBlock(){
  const el = document.getElementById('telegram-status-block');
  if(!el) return;
  const s = telegramState;
  if(!s || !s.is_configured){
    el.innerHTML = '<div class="empty">Не подключён</div>';
    return;
  }
  const uname = s.bot_username ? '@' + esc(s.bot_username) : 'бот';
  const n = (s.chat_ids || []).length;
  const chatsWord = n === 1 ? 'чат' : (n >= 2 && n <= 4 ? 'чата' : 'чатов');
  if(s.enabled){
    el.innerHTML = `<div style="font-size:13px;color:var(--green-dark,#166534)"><i class="fas fa-circle-check"></i> ${uname} · ${n} ${chatsWord}</div>`;
  } else {
    el.innerHTML = `<div style="font-size:13px;color:var(--hint,#6b7280)"><i class="fas fa-pause-circle"></i> ${uname} · отключён</div>`;
  }
}

function openTelegramModal(){
  document.getElementById('telegram-modal-overlay').classList.remove('hidden');
  renderTelegramModal();
}
function closeTelegramModal(){
  document.getElementById('telegram-modal-overlay').classList.add('hidden');
}

async function renderTelegramModal(){
  const body = document.getElementById('telegram-modal-body');
  body.innerHTML = '<div class="empty">Загрузка...</div>';
  try{
    const r = await apiFetch(TG_API);
    if(!r || r.status !== 200){ body.innerHTML = '<div class="empty">Ошибка загрузки</div>'; return; }
    telegramState = await r.json();
  }catch(e){ body.innerHTML = '<div class="empty">Ошибка сети</div>'; return; }
  renderTelegramStatusBlock();

  const s = telegramState;
  const agentLabel = `<div style="font-size:12.5px;color:var(--muted);margin-bottom:10px;padding:8px 10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)"><i class="fas fa-robot" style="margin-right:6px;color:var(--blue)"></i>Бот для агента <b>${esc(agentData?.name || 'Агент')}</b>. У каждого агента — свой отдельный Telegram-бот.</div>`;
  if(!s.is_configured){
    body.innerHTML = agentLabel + `
      <div style="font-size:13.5px;line-height:1.6;margin-bottom:14px">
        Подключите своего Telegram-бота, чтобы агент мог общаться из Telegram и присылать уведомления.
        <div class="form-hint" style="margin-top:8px">Создайте бота через <b>@BotFather</b> и вставьте его токен ниже.</div>
      </div>
      <div class="form-group">
        <label class="form-label">Токен бота</label>
        <input type="text" class="form-input" id="tg-token" placeholder="123456:ABC-DEF...">
      </div>
      <button class="btn btn-primary" id="tg-connect-btn" onclick="connectTelegramBot()"><i class="fab fa-telegram"></i> Подключить</button>`;
    return;
  }

  const chats = s.chat_ids || [];
  const chatRows = chats.length ? chats.map(c => `
    <tr>
      <td style="font-family:monospace">${esc(c.chat_id)}</td>
      <td>${esc(c.title || '—')}</td>
      <td>${esc(c.type || '—')}</td>
      <td style="color:var(--muted)">${c.added_at ? fmtDate(c.added_at) : '—'}</td>
      <td style="text-align:right"><span class="btn btn-secondary btn-sm" onclick="removeTelegramChat('${esc(c.chat_id)}')"><i class="fas fa-times"></i></span></td>
    </tr>`).join('') : '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:14px">Чатов пока нет</td></tr>';

  body.innerHTML = agentLabel + `
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:14px">
      <div style="font-size:14px;font-weight:600"><i class="fab fa-telegram" style="color:#229ED9"></i> @${esc(s.bot_username || '')}</div>
      <div style="display:flex;align-items:center;gap:8px;font-size:13px">
        <label class="switch"><input type="checkbox" id="tg-enabled" ${s.enabled ? 'checked' : ''} onchange="toggleTelegramEnabled(this.checked)"><span class="slider"></span></label>
        <span id="tg-enabled-label" class="${s.enabled ? 'toggle-label on' : 'toggle-label'}">${s.enabled ? 'Включён' : 'Выключен'}</span>
      </div>
    </div>

    <div style="margin:16px 0 8px;font-weight:600;font-size:13.5px">Чаты</div>
    <table class="calls-table" style="width:100%">
      <thead><tr><th>chat_id</th><th>Название</th><th>Тип</th><th>Добавлен</th><th></th></tr></thead>
      <tbody>${chatRows}</tbody>
    </table>
    <div class="form-hint" style="margin-top:6px">Чтобы узнать chat_id, напишите боту <b>@userinfobot</b> или используйте <b>getUpdates</b>.</div>

    <div class="form-row" style="margin-top:12px">
      <div class="form-group"><input class="form-input" id="tg-new-chat-id" placeholder="chat_id (например: -1001234567890)"></div>
      <div class="form-group"><input class="form-input" id="tg-new-chat-title" placeholder="Название (например: Отдел продаж)"></div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <select class="form-select" id="tg-new-chat-type">
          <option value="private">Личка</option>
          <option value="group">Группа</option>
          <option value="supergroup">Супергруппа</option>
          <option value="channel">Канал</option>
        </select>
      </div>
      <div class="form-group"><button class="btn btn-primary" style="width:100%" onclick="addTelegramChat()"><i class="fas fa-plus"></i> Добавить</button></div>
    </div>

    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px;padding-top:14px;border-top:1px solid var(--border-soft)">
      <button class="btn btn-secondary btn-sm" onclick="testTelegram()"><i class="fas fa-paper-plane"></i> Тест отправки</button>
      <button class="btn btn-danger btn-sm" style="margin-left:auto" onclick="disconnectTelegramBot()"><i class="fas fa-unlink"></i> Отключить бота</button>
    </div>`;
}

async function connectTelegramBot(){
  const token = document.getElementById('tg-token').value.trim();
  if(!token){ showToast('Введите токен бота','error'); return; }
  const btn = document.getElementById('tg-connect-btn');
  btn.disabled = true; btn.innerHTML = '<div class="spinner" style="width:15px;height:15px;border-width:2px"></div> Подключение...';
  try{
    const r = await apiFetch(TG_API, { method:'PUT', body:JSON.stringify({ bot_token: token }) });
    if(r && r.status === 200){
      telegramState = await r.json();
      showToast('Бот подключён', 'success');
      renderTelegramModal();
      renderTelegramStatusBlock();
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(tgErr(err.detail), 'error');
      btn.disabled = false; btn.innerHTML = '<i class="fab fa-telegram"></i> Подключить';
    }
  }catch(e){ showToast('Ошибка сети','error'); btn.disabled = false; btn.innerHTML = '<i class="fab fa-telegram"></i> Подключить'; }
}

async function toggleTelegramEnabled(enabled){
  const input = document.getElementById('tg-enabled');
  const label = document.getElementById('tg-enabled-label');
  if(input) input.disabled = true;
  try{
    const r = await apiFetch(TG_API + '/enabled', { method:'PATCH', body:JSON.stringify({ enabled }) });
    if(r && r.status === 200){
      telegramState = await r.json();
      const on = !!telegramState.enabled;
      if(input) input.checked = on;
      if(label){ label.textContent = on ? 'Включён' : 'Выключен'; label.className = on ? 'toggle-label on' : 'toggle-label'; }
      renderTelegramStatusBlock();
      showToast(on ? 'Бот включён' : 'Бот выключен', 'success');
    } else {
      if(input) input.checked = !enabled;
      showToast('Ошибка','error');
    }
  }catch(e){
    if(input) input.checked = !enabled;
    showToast('Ошибка сети','error');
  }finally{
    if(input) input.disabled = false;
  }
}

async function addTelegramChat(){
  const chat_id = document.getElementById('tg-new-chat-id').value.trim();
  const title = document.getElementById('tg-new-chat-title').value.trim();
  const type = document.getElementById('tg-new-chat-type').value;
  if(!chat_id){ showToast('Укажите chat_id','error'); return; }
  try{
    const r = await apiFetch(TG_API + '/chats', { method:'POST', body:JSON.stringify({ chat_id, title: title || null, type }) });
    if(r && r.status === 200){
      telegramState = await r.json();
      showToast('Чат добавлен', 'success');
      renderTelegramModal();
      renderTelegramStatusBlock();
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(tgErr(err.detail), 'error');
    }
  }catch(e){ showToast('Ошибка сети','error'); }
}

async function removeTelegramChat(chatId){
  try{
    const r = await apiFetch(TG_API + '/chats/' + encodeURIComponent(chatId), { method:'DELETE' });
    if(r && r.status === 200){
      telegramState = await r.json();
      renderTelegramModal();
      renderTelegramStatusBlock();
      showToast('Чат удалён', 'success');
    } else { showToast('Ошибка','error'); }
  }catch(e){ showToast('Ошибка сети','error'); }
}

async function testTelegram(){
  try{
    const r = await apiFetch(TG_API + '/test', { method:'POST' });
    if(r && r.status === 200){
      const d = await r.json();
      showToast(`Отправлено: ${d.sent}/${d.total}`, d.sent > 0 ? 'success' : 'error');
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(tgErr(err.detail), 'error');
    }
  }catch(e){ showToast('Ошибка сети','error'); }
}

async function disconnectTelegramBot(){
  if(!confirm('Отключить бота? Токен и список чатов будут удалены. История чатов сохранится.')) return;
  try{
    const r = await apiFetch(TG_API, { method:'DELETE' });
    if(r && r.status === 200){
      telegramState = await r.json();
      renderTelegramModal();
      renderTelegramStatusBlock();
      showToast('Бот отключён', 'success');
    } else { showToast('Ошибка','error'); }
  }catch(e){ showToast('Ошибка сети','error'); }
}

function tgErr(detail){
  const map = {
    invalid_token:'Невалидный токен бота. Проверьте токен от @BotFather.',
    telegram_bot_not_configured:'Бот не подключён.',
    no_chat_ids_configured:'Сначала добавьте хотя бы один чат.',
    already_exists:'Этот chat_id уже добавлен.',
    invalid_chat_type:'Неверный тип чата.',
    chat_id_required:'Укажите chat_id.',
    token_already_used:'Этот бот уже подключён к другому агенту. У каждого агента должен быть свой бот.',
    agent_not_found:'Агент не найден.',
  };
  if(typeof detail === 'string' && map[detail]) return map[detail];
  return 'Ошибка: ' + (detail || 'не удалось выполнить запрос');
}


