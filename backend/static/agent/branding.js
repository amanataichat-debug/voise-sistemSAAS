/* ============================================================================
 * agent/branding.js — Фирменный стиль документов агента
 *                      → PUT /api/agent/ (поля brand_*), POST /api/agent/files
 * Раздел «Документы» модалки настроек агента:
 *   • логотип (картинка; хранится обычным файлом агента, id → brand_logo_file_id);
 *   • фирменный цвет шапки (палитра + своё значение #RRGGBB);
 *   • название компании в шапке и контакты в подвале.
 * Этим оформляются PDF, которые агент собирает тулзой create_document
 * (backend/services/document_design.py). Пусто → нейтральный стиль VoksiAI
 * без логотипа. Сохраняется сразу, как голосовые сообщения.
 * Классический скрипт.
 * ========================================================================== */

// Палитра: безопасные тёмные цвета — на них белый текст шапки читается.
const BRAND_COLORS = [
  { v: '#2a5ce8', t: 'Синий VoksiAI' },
  { v: '#0f766e', t: 'Изумрудный' },
  { v: '#b91c1c', t: 'Красный' },
  { v: '#7c3aed', t: 'Фиолетовый' },
  { v: '#c2410c', t: 'Оранжевый' },
  { v: '#1f2937', t: 'Графитовый' },
];

let brandingState = null;

function loadBranding(){
  // agentData уже загружен (loadCurrentAgent) — отдельного запроса не нужно.
  brandingState = {
    company: agentData.brand_company_name || '',
    color: agentData.brand_color || '',
    contacts: agentData.brand_contacts || '',
    logoId: agentData.brand_logo_file_id || null,
  };
  renderBranding();
}

function _brandLogoHtml(){
  const s = brandingState;
  if(!s.logoId){
    return `<div class="form-hint">Логотип не выбран — в шапке будет только название компании.</div>`;
  }
  return `<div class="brand-logo-row">
      <span class="brand-logo-name"><i class="fas fa-image"></i> Логотип загружен</span>
      <button type="button" class="btn btn-secondary btn-sm" onclick="downloadAgentFile('${s.logoId}')">Посмотреть</button>
      <button type="button" class="btn btn-secondary btn-sm" onclick="brandRemoveLogo()">Убрать</button>
    </div>`;
}

function renderBranding(){
  const el = document.getElementById('i-branding');
  if(!el) return;
  const s = brandingState;
  if(!s){ el.innerHTML = '<div class="empty">Не удалось загрузить настройки</div>'; return; }
  const current = (s.color || '#2a5ce8').toLowerCase();
  const swatches = BRAND_COLORS.map(c => `
    <button type="button" class="brand-swatch${c.v === current ? ' on' : ''}" style="background:${c.v}"
            title="${esc(c.t)}" onclick="brandSetColor('${c.v}')"><i class="fas fa-check"></i></button>`).join('');

  el.innerHTML = `
    <div class="form-group">
      <label>Логотип</label>
      ${_brandLogoHtml()}
      <div class="brand-upload">
        <input type="file" id="brand-logo-input" accept="image/png,image/jpeg,image/webp">
        <button type="button" class="btn btn-secondary btn-sm" id="brand-logo-btn" onclick="brandUploadLogo()">Загрузить логотип</button>
      </div>
      <div class="form-hint">PNG с прозрачным фоном выглядит лучше всего. Логотип встанет в шапку документа слева.</div>
    </div>

    <div class="form-group">
      <label>Цвет шапки</label>
      <div class="brand-swatches">${swatches}</div>
      <div class="brand-custom">
        <input type="text" id="brand-color-input" maxlength="7" placeholder="#2a5ce8" value="${esc(s.color || '')}">
        <button type="button" class="btn btn-secondary btn-sm" onclick="brandSetColor(document.getElementById('brand-color-input').value)">Применить</button>
      </div>
      <div class="form-hint">Можно вписать свой цвет в формате #RRGGBB. Пусто — синий VoksiAI.</div>
    </div>

    <div class="form-group">
      <label>Название компании</label>
      <input type="text" id="brand-company-input" maxlength="255" placeholder="Например: ОсОО «Ваша компания»"
             value="${esc(s.company || '')}" onchange="saveBranding()">
      <div class="form-hint">Стоит в шапке рядом с логотипом и в подвале каждой страницы.</div>
    </div>

    <div class="form-group">
      <label>Контакты в подвале</label>
      <input type="text" id="brand-contacts-input" maxlength="500" placeholder="сайт · телефон · почта"
             value="${esc(s.contacts || '')}" onchange="saveBranding()">
      <div class="form-hint">Одной строкой — клиент увидит их внизу каждой страницы документа.</div>
    </div>`;
}

function brandSetColor(value){
  const raw = (value || '').trim();
  if(raw && !/^#?[0-9a-fA-F]{6}$/.test(raw)){
    showToast('Цвет нужен в формате #RRGGBB', 'error');
    return;
  }
  brandingState.color = raw ? (raw.startsWith('#') ? raw.toLowerCase() : '#' + raw.toLowerCase()) : '';
  renderBranding();
  saveBranding();
}

function brandRemoveLogo(){
  brandingState.logoId = null;
  renderBranding();
  saveBranding();
}

async function brandUploadLogo(){
  const input = document.getElementById('brand-logo-input');
  const file = input && input.files && input.files[0];
  if(!file){ showToast('Выберите картинку', 'error'); return; }
  if(!/^image\//.test(file.type)){ showToast('Логотип должен быть картинкой', 'error'); return; }
  const btn = document.getElementById('brand-logo-btn');
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Загрузка…';
  try{
    const fd = new FormData();
    fd.append('file', file);
    fd.append('title', 'Логотип компании');
    fd.append('description', 'Логотип для шапки документов агента');
    // apiFetch ставит Content-Type: application/json — для multipart нужен голый fetch.
    const r = await fetch(withAgentId(API + '/files'), {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + getToken() }, body: fd,
    });
    if(!r.ok){
      const err = await r.json().catch(() => ({}));
      showToast('Не удалось загрузить: ' + errText(err.detail ?? err.message), 'error');
      return;
    }
    const data = await r.json();
    const fileId = data && data.file && data.file.id;
    if(!fileId){ showToast('Файл загружен, но id не вернулся', 'error'); return; }
    brandingState.logoId = fileId;
    input.value = '';
    renderBranding();
    await saveBranding();
    if(typeof loadAgentFiles === 'function') loadAgentFiles();
  }catch(e){
    showToast('Не удалось загрузить логотип', 'error');
  }finally{
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

async function saveBranding(){
  const company = document.getElementById('brand-company-input');
  const contacts = document.getElementById('brand-contacts-input');
  if(company) brandingState.company = company.value.trim();
  if(contacts) brandingState.contacts = contacts.value.trim();
  const body = {
    brand_company_name: brandingState.company || null,
    brand_color: brandingState.color || null,
    brand_contacts: brandingState.contacts || null,
    brand_logo_file_id: brandingState.logoId || null,
  };
  try{
    const r = await apiFetch(API + '/', { method: 'PUT', body: JSON.stringify(body) });
    if(r.status !== 200){
      const err = await r.json().catch(() => ({}));
      const code = err.detail ?? err.message;
      showToast(code === 'invalid_brand_color' ? 'Цвет нужен в формате #RRGGBB' : errText(code), 'error');
      return;
    }
    Object.assign(agentData, body);
    showToast('Оформление документов сохранено');
  }catch(e){
    showToast('Не удалось сохранить', 'error');
  }
}
