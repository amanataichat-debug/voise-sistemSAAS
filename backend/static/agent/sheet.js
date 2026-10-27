/* ============================================================================
 * agent/sheet.js — Google Таблица агента → /api/agent/sheet*
 * Раздел «Google Таблица» модалки настроек агента:
 *   • подключение по ссылке (сервер сразу проверяет доступ);
 *   • новые клиенты пишутся в лист «Заявки» (backend/services/sheets_leads.py);
 *   • «Обновить каталог» — первый лист таблицы → позиции каталога базы знаний.
 * Классический скрипт.
 * ========================================================================== */

let sheetState = null;

async function loadAgentSheet(){
  const el = document.getElementById('i-sheet');
  if(!el) return;
  try{
    const r = await apiFetch(API + '/sheet');
    if(!r || r.status !== 200){ el.innerHTML = '<div class="empty">Не удалось загрузить</div>'; return; }
    sheetState = await r.json();
    renderAgentSheet();
  }catch(e){ el.innerHTML = '<div class="empty">Ошибка сети</div>'; }
}

function _sheetEmailHtml(){
  const email = sheetState && sheetState.service_email;
  if(!email) return '<div class="note warn"><i class="fas fa-triangle-exclamation"></i><span>Google Таблицы ещё не настроены на сервере. Напишите в поддержку.</span></div>';
  return `<div class="sheet-step">
      <span class="sheet-step-num">1</span>
      <div class="sheet-step-body">
        <b>Откройте доступ к таблице</b>
        <span>В таблице нажмите «Настройки доступа» и добавьте этот адрес с правом «Редактор»:</span>
        <div class="sheet-email">
          <code id="sheet-email">${esc(email)}</code>
          <button type="button" class="btn btn-secondary btn-sm" onclick="copySheetEmail()"><i class="fas fa-copy"></i> Скопировать</button>
        </div>
      </div>
    </div>`;
}

function renderAgentSheet(){
  const el = document.getElementById('i-sheet');
  const s = sheetState;
  if(!el || !s) return;
  if(!s.connected){
    el.innerHTML = _sheetEmailHtml() + `
      <div class="sheet-step">
        <span class="sheet-step-num">2</span>
        <div class="sheet-step-body">
          <b>Вставьте ссылку на таблицу</b>
          <div class="sheet-connect">
            <input class="form-input" id="sheet-url" placeholder="https://docs.google.com/spreadsheets/d/…" autocomplete="off">
            <button type="button" class="btn btn-primary" id="sheet-connect-btn" onclick="connectAgentSheet()"><i class="fas fa-link"></i> Подключить</button>
          </div>
        </div>
      </div>`;
    return;
  }
  el.innerHTML = `
    <div class="sheet-card">
      <div class="sheet-card-head">
        <span class="sheet-card-ic"><i class="fas fa-table"></i></span>
        <div class="sheet-card-title">
          <b>${esc(s.title || 'Google Таблица')}</b>
          <a href="${esc(s.url)}" target="_blank" rel="noopener">Открыть таблицу <i class="fas fa-arrow-up-right-from-square"></i></a>
        </div>
        <span class="sheet-ok"><i class="fas fa-circle-check"></i> Подключена</span>
      </div>
      <div class="sheet-row">
        <div><b>Заявки</b><span>Каждый новый клиент — строка на листе «${esc(s.leads_sheet)}»: дата, канал, имя, телефон, стадия, сообщение.</span></div>
        <span class="sheet-badge">Автоматически</span>
      </div>
      <div class="sheet-row">
        <div><b>Прайс в каталог</b><span>Первый лист: колонки «Название», «Цена», «Описание», «Наличие». Повторная загрузка обновит позиции из таблицы, ручные не тронет.</span></div>
        <button type="button" class="btn btn-primary btn-sm" id="sheet-import-btn" onclick="importSheetCatalog()"><i class="fas fa-rotate"></i> Обновить каталог</button>
      </div>
      <div class="sheet-foot">
        <button type="button" class="btn btn-secondary btn-sm" onclick="disconnectAgentSheet()"><i class="fas fa-unlink"></i> Отключить таблицу</button>
      </div>
    </div>`;
}

async function copySheetEmail(){
  const code = document.getElementById('sheet-email');
  if(!code) return;
  try{ await navigator.clipboard.writeText(code.textContent); showToast('Адрес скопирован', 'success'); }
  catch(e){
    const range = document.createRange(); range.selectNodeContents(code);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    showToast('Выделено — нажмите Ctrl+C', 'success');
  }
}

function _sheetBusy(id, busy, html){
  const btn = document.getElementById(id);
  if(!btn) return;
  btn.disabled = busy;
  if(html) btn.innerHTML = html;
}

async function connectAgentSheet(){
  const url = (document.getElementById('sheet-url').value || '').trim();
  if(!url){ showToast('Вставьте ссылку на таблицу', 'error'); return; }
  _sheetBusy('sheet-connect-btn', true, '<div class="spinner" style="width:15px;height:15px;border-width:2px"></div> Проверяем доступ…');
  try{
    const r = await apiFetch(API + '/sheet', { method:'POST', body: JSON.stringify({ url }) });
    const data = r ? await r.json().catch(() => ({})) : {};
    if(!r || r.status !== 200){
      showToast(typeof data.detail === 'string' ? data.detail : 'Не удалось подключить таблицу', 'error');
      _sheetBusy('sheet-connect-btn', false, '<i class="fas fa-link"></i> Подключить');
      return;
    }
    sheetState = data;
    renderAgentSheet();
    showToast('Таблица подключена', 'success');
  }catch(e){
    showToast('Ошибка сети', 'error');
    _sheetBusy('sheet-connect-btn', false, '<i class="fas fa-link"></i> Подключить');
  }
}

async function importSheetCatalog(){
  _sheetBusy('sheet-import-btn', true, '<div class="spinner" style="width:14px;height:14px;border-width:2px"></div> Загружаем…');
  try{
    const r = await apiFetch(API + '/sheet/import-catalog', { method:'POST' });
    const data = r ? await r.json().catch(() => ({})) : {};
    if(!r || r.status !== 200){
      showToast(typeof data.detail === 'string' ? data.detail : 'Не удалось загрузить прайс', 'error');
    } else {
      const parts = [`новых ${data.created}`, `обновлено ${data.updated}`];
      if(data.hidden) parts.push(`скрыто ${data.hidden}`);
      showToast(`Каталог обновлён: ${parts.join(', ')}`, 'success');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
  _sheetBusy('sheet-import-btn', false, '<i class="fas fa-rotate"></i> Обновить каталог');
}

async function disconnectAgentSheet(){
  try{
    const r = await apiFetch(API + '/sheet', { method:'DELETE' });
    if(!r || r.status !== 200){ showToast('Не удалось отключить', 'error'); return; }
    sheetState = await r.json();
    renderAgentSheet();
    showToast('Таблица отключена. Каталог остался в базе знаний.', 'success');
  }catch(e){ showToast('Ошибка сети', 'error'); }
}
