/* ============================================================================
 * agent/knowledge-base.js — База знаний агента v2 → /api/agent/knowledge
 * Часть страницы /static/agent.html (Voksy AI Agent).
 *
 * Пять разделов в одной модалке:
 *   • Вопрос — ответ: пары «что спрашивает клиент → что отвечать». Уходят
 *     прямо в инструкцию агента, он отвечает мгновенно, без поиска. Это
 *     главный рычаг скорости на звонке.
 *   • О компании: секции с заголовками (цены, адрес, доставка). Короткие тоже
 *     в инструкции, длинные — достаются поиском.
 *   • Каталог: позиции с ценой, характеристиками и фото/видео. Агент находит
 *     нужную модель и отправляет её фото клиенту.
 *   • Документы: файлы из библиотеки агента (PDF, Word, Excel) — векторный
 *     поиск по содержимому.
 *   • Чего не хватает: вопросы клиентов, на которые база не ответила.
 *
 * «Проверить» показывает, ответит ли агент мгновенно или пойдёт в поиск.
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные.
 * Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

const KB_API = API + '/knowledge';

// Подсказка под вкладками: владелец должен понимать, что быстро, а что нет.
const KB_TAB_HINTS = {
  qa: 'Пары «вопрос клиента — ответ». Самый быстрый раздел: эти ответы агент знает наизусть и отвечает без поиска. Держите здесь то, что спрашивают чаще всего — цены, адрес, часы работы. Оптимально до 50 записей.',
  section: 'Блоки о компании: условия, доставка, гарантия. Короткие агент тоже помнит наизусть; длинные достаёт поиском — это чуть дольше.',
  product: 'Товары и услуги с ценой и фото. Агент подбирает позицию по запросу клиента («наушники до 3000») и отправляет фото прямо в переписку.',
  document: 'Файлы из библиотеки агента: прайсы, договоры, презентации. Агент ищет ответ внутри них, когда в быстрых разделах его нет.',
  gaps: 'О чём спрашивали клиенты, а база промолчала. Добавьте ответы — и агент перестанет отправлять людей к менеджеру.',
};

const KB_KIND_TITLES = { qa: 'Вопрос — ответ', section: 'Секция', product: 'Позиция каталога', document: 'Документ' };

let knowledgeBaseState = null;   // сводка для карточки в шторке
let kbTab = 'qa';
let kbEntries = [];
let kbEditing = null;            // id редактируемой записи или 'new'

// ════════════════ КАРТОЧКА В ШТОРКЕ ════════════════

async function loadKnowledgeBaseStatus(){
  try{
    const r = await apiFetch(KB_API);
    knowledgeBaseState = (r && r.status === 200) ? await r.json() : null;
  }catch(e){ knowledgeBaseState = null; }
  renderKnowledgeBaseBlock();
}

function renderKnowledgeBaseBlock(){
  const el = document.getElementById('kb-status-block');
  if(!el) return;
  const s = knowledgeBaseState;
  const counts = (s && s.counts) || {};
  const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0);
  if(!total){
    el.innerHTML = '<div class="empty">База пустая — агент не знает о компании ничего</div>';
    return;
  }
  const parts = [];
  if(counts.qa) parts.push(`${counts.qa} ${pluralRu(counts.qa, 'ответ', 'ответа', 'ответов')}`);
  if(counts.section) parts.push(`${counts.section} ${pluralRu(counts.section, 'секция', 'секции', 'секций')}`);
  if(counts.product) parts.push(`${counts.product} ${pluralRu(counts.product, 'товар', 'товара', 'товаров')}`);
  if(counts.document) parts.push(`${counts.document} ${pluralRu(counts.document, 'документ', 'документа', 'документов')}`);
  el.innerHTML = `<div style="font-size:13px;color:var(--green-dark,#166534)">`
    + `<i class="fas fa-circle-check"></i> ${esc(parts.join(' · '))}</div>`;
}

// ════════════════ МОДАЛКА ════════════════

function openKnowledgeBaseModal(){
  document.getElementById('kb-modal-overlay').classList.remove('hidden');
  kbSwitchTab(kbTab);
}

function closeKnowledgeBaseModal(){
  document.getElementById('kb-modal-overlay').classList.add('hidden');
  kbEditing = null;
  document.getElementById('kb-editor').innerHTML = '';
  document.getElementById('kb-test-result').innerHTML = '';
  loadKnowledgeBaseStatus();
}

function kbSwitchTab(kind){
  kbTab = kind;
  document.querySelectorAll('#kb-tabs .files-tab').forEach(b =>
    b.classList.toggle('active', b.dataset.kind === kind));
  document.getElementById('kb-hint').textContent = KB_TAB_HINTS[kind] || '';
  // Проверка и кнопка «Добавить» для раздела пробелов не нужны.
  document.getElementById('kb-test-box').style.display = kind === 'gaps' ? 'none' : '';
  document.getElementById('kb-add-btn').style.display = kind === 'gaps' ? 'none' : '';
  document.getElementById('kb-test-result').innerHTML = '';
  document.getElementById('kb-editor').innerHTML = '';
  kbEditing = null;
  if(kind === 'gaps') loadKbGaps(); else loadKbEntries(kind);
}

async function loadKbEntries(kind){
  const list = document.getElementById('kb-list');
  list.innerHTML = '<div class="form-hint">Загрузка…</div>';
  try{
    const r = await apiFetch(KB_API + '?kind=' + encodeURIComponent(kind));
    if(!r || r.status !== 200){
      // Показываем причину: «Загрузка…» навсегда не объясняет владельцу ничего.
      const err = r ? await r.json().catch(() => ({})) : {};
      list.innerHTML = `<div class="empty">Не удалось загрузить базу знаний`
        + (r ? ` (ошибка ${r.status})` : '') + `.<br><span style="font-size:12px">`
        + esc(errText(err.detail ?? err.message ?? '')) + `</span></div>`;
      return;
    }
    const data = await r.json();
    kbEntries = data.entries || [];
    knowledgeBaseState = data;
    const meta = document.getElementById('kb-prompt-meta');
    if(meta){
      meta.textContent = data.prompt_chars
        ? `Агент помнит наизусть: ${data.prompt_chars.toLocaleString('ru-RU')} символов`
        : '';
    }
    renderKbList();
  }catch(e){
    list.innerHTML = '<div class="empty">Не удалось загрузить базу знаний: '
      + esc(String(e && e.message || e)) + '</div>';
  }
}

function kbEntryRow(e){
  const badge = e.in_prompt
    ? '<span class="kb-badge kb-badge-fast" title="Агент отвечает сразу, без поиска">мгновенно</span>'
    : '<span class="kb-badge" title="Агент найдёт поиском">поиск</span>';
  const off = e.is_active ? '' : '<span class="kb-badge kb-badge-off">выключено</span>';
  const hits = e.hit_count ? `<span class="kb-badge" title="Сколько раз пригодилось">${e.hit_count}×</span>` : '';
  let sub = '';
  if(e.kind === 'product'){
    const price = (e.price != null) ? `${e.price.toLocaleString('ru-RU')} ${esc(e.currency || 'KGS')}` : 'цена не указана';
    const media = (e.media_file_ids || []).length;
    sub = `${price}${media ? ' · ' + media + ' фото/видео' : ''}${e.in_stock === false ? ' · нет в наличии' : ''}`;
  }else if(e.kind === 'document'){
    const st = { ready: 'в поиске', pending: 'обрабатывается', failed: 'ошибка', skipped: 'нет текста' }[e.vector_status] || '';
    sub = `${st}${e.chunk_count ? ' · ' + e.chunk_count + ' фрагментов' : ''}`;
  }else{
    sub = (e.body || '').slice(0, 140);
  }
  return `<div class="kb-row">
      <div class="kb-row-main" onclick="kbOpenEditor('${e.id}')">
        <div class="kb-row-title">${esc(e.title || '(без заголовка)')} ${badge} ${off} ${hits}</div>
        <div class="kb-row-sub">${esc(sub)}</div>
      </div>
      <div class="kb-row-actions">
        <button class="btn btn-secondary btn-sm" onclick="kbOpenEditor('${e.id}')" title="Изменить"><i class="fas fa-pen"></i></button>
        <button class="btn btn-secondary btn-sm" onclick="kbDeleteEntry('${e.id}')" title="Удалить"><i class="fas fa-trash-can"></i></button>
      </div>
    </div>`;
}

function renderKbList(){
  const list = document.getElementById('kb-list');
  if(!kbEntries.length){
    const empty = {
      qa: 'Пока пусто. Добавьте частые вопросы клиентов — это самый быстрый способ научить агента.',
      section: 'Пока пусто. Добавьте блоки: о компании, цены, доставка, гарантия.',
      product: 'Каталог пуст. Добавьте товары с ценой и фото — агент будет подбирать их клиентам.',
      document: 'Документов нет. Загрузите файл в библиотеку агента и добавьте его сюда.',
    }[kbTab] || 'Пусто';
    list.innerHTML = `<div class="empty">${esc(empty)}</div>`;
    return;
  }
  list.innerHTML = kbEntries.map(kbEntryRow).join('');
}

// ════════════════ РЕДАКТОР ЗАПИСИ ════════════════

function kbOpenEditor(entryId){
  kbEditing = entryId || 'new';
  const e = entryId ? kbEntries.find(x => x.id === entryId) : null;
  const kind = e ? e.kind : kbTab;
  if(kind === 'document' && !e){ kbPickDocument(); return; }

  const isProduct = kind === 'product';
  const titleLabel = { qa: 'Вопрос клиента', section: 'Заголовок секции', product: 'Название товара', document: 'Название' }[kind];
  const bodyLabel = { qa: 'Ответ агента', section: 'Текст', product: 'Описание', document: 'Описание' }[kind];

  document.getElementById('kb-editor').innerHTML = `
    <div class="kb-editor">
      <div class="kb-editor-head">${e ? 'Изменить' : 'Новая запись'} · ${esc(KB_KIND_TITLES[kind] || kind)}</div>
      <div class="form-group">
        <label class="form-label">${esc(titleLabel)}</label>
        <input type="text" class="form-input" id="kb-f-title" maxlength="500" value="${esc((e && e.title) || '')}"
               placeholder="${kind === 'qa' ? 'Сколько стоит доставка?' : 'Например: Условия доставки'}">
      </div>
      <div class="form-group">
        <label class="form-label">${esc(bodyLabel)}</label>
        <textarea class="form-textarea" id="kb-f-body" rows="4">${esc((e && e.body) || '')}</textarea>
      </div>
      ${isProduct ? `
      <div class="kb-grid">
        <div class="form-group">
          <label class="form-label">Цена</label>
          <input type="number" class="form-input" id="kb-f-price" step="0.01" value="${(e && e.price != null) ? e.price : ''}">
        </div>
        <div class="form-group">
          <label class="form-label">Валюта</label>
          <input type="text" class="form-input" id="kb-f-currency" maxlength="8" value="${esc((e && e.currency) || 'KGS')}">
        </div>
        <div class="form-group">
          <label class="form-label">В наличии</label>
          <select class="form-input" id="kb-f-stock">
            <option value="1" ${(!e || e.in_stock !== false) ? 'selected' : ''}>Да</option>
            <option value="0" ${(e && e.in_stock === false) ? 'selected' : ''}>Нет</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Фото и видео товара</label>
        <div id="kb-media-list" class="kb-media"></div>
        <div class="form-hint">Выберите файлы из библиотеки агента — агент отправит их клиенту вместе с ответом.</div>
      </div>` : ''}
      <div class="form-group">
        <label class="form-label">Слова клиента (через запятую)</label>
        <input type="text" class="form-input" id="kb-f-keywords" value="${esc((e && e.keywords) || '')}"
               placeholder="почём, сколько стоит, прайс">
        <div class="form-hint">Синонимы, которых нет в заголовке. Помогают агенту найти запись, когда клиент спрашивает своими словами.</div>
      </div>
      <div class="kb-editor-actions">
        <label class="kb-check"><input type="checkbox" id="kb-f-active" ${(!e || e.is_active) ? 'checked' : ''}> Включено</label>
        ${(kind === 'qa' || kind === 'section') ? `<label class="kb-check"><input type="checkbox" id="kb-f-prompt" ${(!e || e.in_prompt) ? 'checked' : ''}> Агент помнит наизусть (без поиска)</label>` : ''}
        <span style="flex:1"></span>
        <button class="btn btn-secondary btn-sm" onclick="kbCloseEditor()">Отмена</button>
        <button class="btn btn-primary btn-sm" onclick="kbSaveEntry('${kind}')"><i class="fas fa-check"></i> Сохранить</button>
      </div>
    </div>`;

  if(isProduct) kbRenderMediaPicker((e && e.media_file_ids) || []);
  const el = document.getElementById('kb-f-title');
  if(el) el.focus();
}

function kbCloseEditor(){
  kbEditing = null;
  document.getElementById('kb-editor').innerHTML = '';
}

let kbMediaSelected = [];

async function kbRenderMediaPicker(selected){
  kbMediaSelected = (selected || []).slice();
  const box = document.getElementById('kb-media-list');
  if(!box) return;
  box.innerHTML = '<span class="form-hint">Загрузка файлов…</span>';
  let files = [];
  try{
    const r = await apiFetch(API + '/files?source=library');
    if(r && r.status === 200){
      const data = await r.json();
      files = (data.files || []).filter(f => /^(image|video)\//.test(f.mime_type || ''));
    }
  }catch(e){ /* показываем пустой список ниже */ }
  if(!files.length){
    box.innerHTML = '<span class="form-hint">В библиотеке нет фото и видео. Загрузите их в «Файлы агента».</span>';
    return;
  }
  box.innerHTML = files.map(f => `
    <label class="kb-media-item${kbMediaSelected.includes(f.id) ? ' on' : ''}" data-id="${f.id}">
      <input type="checkbox" ${kbMediaSelected.includes(f.id) ? 'checked' : ''} onchange="kbToggleMedia('${f.id}', this)">
      <i class="fas ${/^video\//.test(f.mime_type || '') ? 'fa-film' : 'fa-image'}"></i>
      <span>${esc(f.title || f.filename || 'файл')}</span>
    </label>`).join('');
}

function kbToggleMedia(fileId, input){
  const item = input.closest('.kb-media-item');
  if(input.checked){
    if(!kbMediaSelected.includes(fileId)) kbMediaSelected.push(fileId);
    if(item) item.classList.add('on');
  }else{
    kbMediaSelected = kbMediaSelected.filter(x => x !== fileId);
    if(item) item.classList.remove('on');
  }
}

async function kbSaveEntry(kind){
  const val = id => (document.getElementById(id) || {}).value;
  const checked = id => { const el = document.getElementById(id); return el ? el.checked : undefined; };
  const title = (val('kb-f-title') || '').trim();
  const bodyText = (val('kb-f-body') || '').trim();
  if(!title && !bodyText){ showToast('Заполните заголовок или текст', 'error'); return; }

  const payload = {
    kind,
    title,
    body: bodyText,
    keywords: (val('kb-f-keywords') || '').trim() || null,
    is_active: checked('kb-f-active') !== false,
  };
  const inPrompt = checked('kb-f-prompt');
  if(inPrompt !== undefined) payload.in_prompt = inPrompt;
  if(kind === 'product'){
    const price = val('kb-f-price');
    payload.price = price === '' || price == null ? null : Number(price);
    payload.currency = (val('kb-f-currency') || 'KGS').trim();
    payload.in_stock = val('kb-f-stock') !== '0';
    payload.media_file_ids = kbMediaSelected;
  }

  const isNew = kbEditing === 'new';
  try{
    const r = await apiFetch(isNew ? KB_API : `${KB_API}/${kbEditing}`, {
      method: isNew ? 'POST' : 'PUT',
      body: JSON.stringify(payload),
    });
    if(!r || (r.status !== 200 && r.status !== 201)){
      const err = await r.json().catch(() => ({}));
      showToast(errText(err.detail ?? err.message), 'error');
      return;
    }
    kbCloseEditor();
    showToast('Сохранено');
    loadKbEntries(kbTab);
  }catch(e){ showToast('Не удалось сохранить', 'error'); }
}

async function kbDeleteEntry(entryId){
  const e = kbEntries.find(x => x.id === entryId);
  if(!confirm(`Удалить «${(e && e.title) || 'запись'}»?`)) return;
  const url = (e && e.kind === 'document')
    ? `${KB_API}/document/${entryId}`   // плюс чистка векторов
    : `${KB_API}/${entryId}`;
  try{
    const r = await apiFetch(url, { method: 'DELETE' });
    if(!r || r.status !== 200){ showToast('Не удалось удалить', 'error'); return; }
    showToast('Удалено');
    loadKbEntries(kbTab);
  }catch(e2){ showToast('Не удалось удалить', 'error'); }
}

// ════════════════ ДОКУМЕНТЫ ════════════════

async function kbPickDocument(){
  // Берём файлы из библиотеки агента: база знаний и файлы — одно хранилище.
  const box = document.getElementById('kb-editor');
  box.innerHTML = '<div class="kb-editor"><div class="form-hint">Загрузка файлов библиотеки…</div></div>';
  let files = [];
  try{
    const r = await apiFetch(API + '/files?source=library');
    if(r && r.status === 200) files = (await r.json()).files || [];
  }catch(e){ /* ниже покажем пустое состояние */ }
  files = files.filter(f => f.has_text);
  if(!files.length){
    box.innerHTML = `<div class="kb-editor"><div class="form-hint">В библиотеке нет файлов с текстом.
      Загрузите прайс или договор в «Файлы агента», потом добавьте его сюда.</div>
      <div class="kb-editor-actions"><span style="flex:1"></span>
      <button class="btn btn-secondary btn-sm" onclick="kbCloseEditor()">Закрыть</button></div></div>`;
    return;
  }
  box.innerHTML = `<div class="kb-editor">
      <div class="kb-editor-head">Добавить документ в базу знаний</div>
      <div class="form-hint">Агент будет искать ответы внутри этого файла. Текст уже извлечён при загрузке.</div>
      <div class="kb-media" style="margin-top:8px">
        ${files.map(f => `<button type="button" class="kb-media-item" onclick="kbAddDocument('${f.id}')">
            <i class="fas fa-file-lines"></i><span>${esc(f.title || f.filename)}</span></button>`).join('')}
      </div>
      <div class="kb-editor-actions"><span style="flex:1"></span>
        <button class="btn btn-secondary btn-sm" onclick="kbCloseEditor()">Отмена</button></div>
    </div>`;
}

async function kbAddDocument(fileId){
  showToast('Обрабатываю документ…');
  try{
    const r = await apiFetch(KB_API + '/document', {
      method: 'POST', body: JSON.stringify({ file_id: fileId }),
    });
    if(!r || r.status !== 200){
      const err = await r.json().catch(() => ({}));
      const code = err.detail ?? err.message;
      showToast(code === 'file_has_no_text'
        ? 'В файле не нашлось текста — агент не сможет по нему отвечать'
        : errText(code), 'error');
      return;
    }
    kbCloseEditor();
    showToast('Документ добавлен в базу');
    loadKbEntries('document');
  }catch(e){ showToast('Не удалось добавить документ', 'error'); }
}

// ════════════════ ПРОВЕРКА ПОИСКА ════════════════

async function kbRunTest(){
  const q = (document.getElementById('kb-test-query').value || '').trim();
  const out = document.getElementById('kb-test-result');
  if(!q){ out.innerHTML = ''; return; }
  out.innerHTML = '<div class="form-hint">Ищу…</div>';
  try{
    const r = await apiFetch(KB_API + '/test', { method: 'POST', body: JSON.stringify({ query: q }) });
    if(!r || r.status !== 200){ out.innerHTML = '<div class="empty">Не удалось проверить</div>'; return; }
    const data = await r.json();
    const badge = data.answered_from_prompt
      ? '<span class="kb-badge kb-badge-fast">мгновенно</span>'
      : (data.results || []).length ? '<span class="kb-badge">через поиск</span>'
      : '<span class="kb-badge kb-badge-off">не найдено</span>';
    const rows = (data.results || []).map(r2 =>
      `<div class="kb-test-row"><b>${esc(r2.title || '')}</b><div>${esc((r2.body || '').slice(0, 200))}</div></div>`).join('');
    out.innerHTML = `<div class="kb-test-out">${badge} <span>${esc(data.note || '')}</span>${rows}</div>`;
  }catch(e){ out.innerHTML = '<div class="empty">Не удалось проверить</div>'; }
}

// ════════════════ ПРОБЕЛЫ БАЗЫ ════════════════

async function loadKbGaps(){
  const list = document.getElementById('kb-list');
  list.innerHTML = '<div class="form-hint">Загрузка…</div>';
  try{
    const r = await apiFetch(KB_API + '/gaps');
    if(!r || r.status !== 200){ list.innerHTML = '<div class="empty">Не удалось загрузить</div>'; return; }
    const gaps = (await r.json()).gaps || [];
    if(!gaps.length){
      list.innerHTML = '<div class="empty">Пока всё хорошо: агент находил ответы на все вопросы.</div>';
      return;
    }
    list.innerHTML = gaps.map(g => `<div class="kb-row">
        <div class="kb-row-main">
          <div class="kb-row-title">${esc(g.query)} <span class="kb-badge">${g.ask_count}×</span></div>
          <div class="kb-row-sub">${g.channel ? esc(g.channel) + ' · ' : ''}${g.last_asked_at ? relTime(g.last_asked_at) : ''}</div>
        </div>
        <div class="kb-row-actions">
          <button class="btn btn-primary btn-sm" onclick="kbAnswerGap('${g.id}', ${JSON.stringify(g.query).replace(/"/g, '&quot;')})">Ответить</button>
          <button class="btn btn-secondary btn-sm" onclick="kbHideGap('${g.id}')" title="Скрыть"><i class="fas fa-xmark"></i></button>
        </div>
      </div>`).join('');
  }catch(e){ list.innerHTML = '<div class="empty">Не удалось загрузить</div>'; }
}

function kbAnswerGap(gapId, query){
  // Открываем новый «вопрос — ответ» с уже подставленным вопросом клиента.
  kbSwitchTab('qa');
  setTimeout(() => {
    kbOpenEditor();
    const t = document.getElementById('kb-f-title');
    if(t) t.value = query;
    const b = document.getElementById('kb-f-body');
    if(b) b.focus();
    apiFetch(KB_API + '/gaps/' + gapId, { method: 'DELETE' }).catch(() => {});
  }, 50);
}

async function kbHideGap(gapId){
  try{
    await apiFetch(KB_API + '/gaps/' + gapId, { method: 'DELETE' });
    loadKbGaps();
  }catch(e){ showToast('Не удалось скрыть', 'error'); }
}
