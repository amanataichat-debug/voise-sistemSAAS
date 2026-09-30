/* ============================================================================
 * agent/instructions-voice.js — Модалки настроек/инструкций агента + выбор голоса, модели оркестратора, телефоны, toggle активности
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

async function loadModels(){
  try{
    const r = await apiFetch(API + '/orchestrator-models');
    if(!r || r.status!==200) return;
    const data = await r.json();
    orchestratorModels = data.models || [];
  }catch(e){}
}

// Номера агента (собственная телефония) — agent/phone.js

// ── TOGGLE ──
async function toggleActive(e){
  const want = e.target.checked;
  try{
    const r = await apiFetch(API + '/', { method:'PUT', body:JSON.stringify({ is_active:want }) });
    if(r && r.status===200){ agentData = await r.json(); renderAgentHeader(); showToast(want?'Агент активирован':'Агент остановлен','success'); }
    else { e.target.checked=!want; showToast('Не удалось изменить статус','error'); }
  }catch(err){ e.target.checked=!want; showToast('Ошибка сети','error'); }
}


// ════════════════ EDIT MODAL ════════════════
function openEditModal(tab){
  fillEditForm();
  switchTab(tab && document.querySelector(`.doc-tab[data-tab="${tab}"]`) ? tab : 'doc_who_am_i');
  document.getElementById('edit-modal-overlay').classList.remove('hidden');
}
function closeEditModal(){ document.getElementById('edit-modal-overlay').classList.add('hidden'); }
function switchTab(tab){
  document.querySelectorAll('.doc-tab').forEach(t => t.classList.toggle('active', t.dataset.tab===tab));
  document.querySelectorAll('.doc-pane').forEach(p => p.classList.toggle('active', p.dataset.pane===tab));
}
function fillEditForm(){
  ['doc_who_am_i','doc_who_we_call','doc_how_we_talk','doc_what_we_offer','doc_rules_and_goals'].forEach(f => { document.getElementById('e-'+f).value = agentData[f]||''; });
}
async function saveEdit(){
  const btn = document.getElementById('edit-save-btn');
  btn.disabled = true; btn.innerHTML = '<div class="spinner" style="width:15px;height:15px;border-width:2px"></div> Сохранение...';
  const body = {
    doc_who_am_i: document.getElementById('e-doc_who_am_i').value,
    doc_who_we_call: document.getElementById('e-doc_who_we_call').value,
    doc_how_we_talk: document.getElementById('e-doc_how_we_talk').value,
    doc_what_we_offer: document.getElementById('e-doc_what_we_offer').value,
    doc_rules_and_goals: document.getElementById('e-doc_rules_and_goals').value,
  };
  try{
    const r = await apiFetch(API + '/', { method:'PUT', body:JSON.stringify(body) });
    if(r && r.status===200){
      agentData = await r.json();
      renderAgentHeader(); renderDocsGrid();
      closeEditModal();
      showToast('Документы сохранены','success');
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(errText(err.detail ?? err.message),'error');
    }
  }catch(e){ showToast('Ошибка сети','error'); }
  btn.disabled = false; btn.innerHTML = '<i class="fas fa-check"></i> Сохранить';
}

// ════════════════ VOICE SELECTION (shared) ════════════════
const OPENAI_VOICES = ['alloy','echo','marin','cedar','shimmer','ash','ballad','coral','sage','verse'];
const GEMINI_VOICES = ['Zephyr','Puck','Charon','Kore','Fenrir','Leda','Orus','Aoede','Callirrhoe','Autonoe','Enceladus','Iapetus','Umbriel','Algieba','Despina','Erinome','Algenib','Rasalgethi','Laomedeia','Achernar','Alnilam','Schedar','Gacrux','Pulcherrima','Achird','Zubenelgenubi','Vindemiatrix','Sadachbia','Sadaltager','Sulafat'];
// Должен совпадать с YANDEX_VOICES в backend/api/agent.py.
const YANDEX_VOICES = ['marina','dasha','alexander','julia','lera','masha','anton','kirill','filipp','ermil','jane','omazh','zahar','madi_ru','saule_ru'];
const VOICE_DEFAULTS = { gemini:'Kore', openai:'alloy', yandex:'marina', cascade:'Anna' };
// Голоса каскада — VoxTTS realtime (должны совпадать с CASCADE_VOICES в backend/api/agent.py).
const CASCADE_VOICES = ['Anna','Sergey'];
// Режимы синтеза Fish (должны совпадать с FISH_LATENCY_MODES в backend/models/fish_assistant.py).
const FISH_LATENCY_MODES = ['low','balanced','normal'];
const FISH_LATENCY_LABELS = { low:'Быстрый старт', balanced:'Сбалансированный', normal:'Качественный' };

// Пол + краткое описание голоса: [gender('m'|'f'|'n'), описание].
const VOICE_META = {
  gemini: {
    Zephyr:['f','Яркий, живой'], Puck:['m','Бодрый, энергичный'], Charon:['m','Информативный, чёткий'],
    Kore:['f','Уверенный, ровный'], Fenrir:['m','Энергичный, напористый'], Leda:['f','Молодой, лёгкий'],
    Orus:['m','Деловой, собранный'], Aoede:['f','Воздушный, мягкий'], Callirrhoe:['f','Спокойный, расслабленный'],
    Autonoe:['f','Светлый, приветливый'], Enceladus:['m','Мягкий, с придыханием'], Iapetus:['m','Чёткий, ясный'],
    Umbriel:['m','Спокойный, ровный'], Algieba:['m','Ровный, нейтральный'], Despina:['f','Гладкий, плавный'],
    Erinome:['f','Ясный, чистый'], Algenib:['m','Низкий, с хрипотцой'], Rasalgethi:['m','Информативный, внятный'],
    Laomedeia:['f','Позитивный, бодрый'], Achernar:['f','Нежный, мягкий'], Alnilam:['m','Твёрдый, уверенный'],
    Schedar:['m','Ровный, спокойный'], Gacrux:['f','Зрелый, насыщенный'], Pulcherrima:['f','Выразительный, яркий'],
    Achird:['m','Дружелюбный, тёплый'], Zubenelgenubi:['m','Непринуждённый, лёгкий'], Vindemiatrix:['f','Мягкий, деликатный'],
    Sadachbia:['m','Живой, оживлённый'], Sadaltager:['m','Уверенный, знающий'], Sulafat:['f','Тёплый, приятный'],
  },
  openai: {
    alloy:['n','Сбалансированный, нейтральный'], echo:['m','Чёткий, ясный'], marin:['f','Тёплый, приятный'],
    cedar:['m','Глубокий, низкий'], shimmer:['f','Мягкий, светлый'], ash:['m','Спокойный, ровный'],
    ballad:['m','Выразительный, эмоциональный'], coral:['f','Дружелюбный, тёплый'], sage:['f','Спокойный, мягкий'],
    verse:['m','Живой, динамичный'],
  },
  yandex: {
    marina:['f','Тёплый, дружелюбный'], dasha:['f','Живой, современный'], alexander:['m','Уверенный, деловой'],
    julia:['f','Ясный, приветливый'], lera:['f','Мягкий, спокойный'], masha:['f','Лёгкий, молодой'],
    anton:['m','Энергичный, бодрый'], kirill:['m','Ровный, нейтральный'], filipp:['m','Классический, чёткий'],
    ermil:['m','Спокойный, размеренный'], jane:['f','Выразительный, яркий'], omazh:['f','Зрелый, насыщенный'],
    zahar:['m','Низкий, основательный'], madi_ru:['m','Дружелюбный, тёплый'], saule_ru:['f','Мягкий, деликатный'],
  },
  cascade: {
    Anna:['f','Тёплый, дружелюбный'], Sergey:['m','Уверенный, деловой'],
  },
};
const GENDER_INFO = {
  m: { label:'Мужской',     icon:'fa-mars',             cls:'g-m' },
  f: { label:'Женский',     icon:'fa-venus',            cls:'g-f' },
  n: { label:'Нейтральный', icon:'fa-circle-half-stroke', cls:'g-n' },
};
function voiceMeta(type, name){ const m=(VOICE_META[type]||{})[name]; return m ? {gender:m[0], desc:m[1]} : {gender:'n', desc:''}; }
function voicePreviewHtml(type, name){
  const {gender, desc} = voiceMeta(type, name);
  const g = GENDER_INFO[gender] || GENDER_INFO.n;
  return `<span class="voice-ava ${g.cls}"><i class="fas ${g.icon}"></i></span>
    <div class="voice-pv-txt"><b>${esc(name)} · ${g.label} голос</b><span>${esc(desc)}</span></div>`;
}
function updateVoicePreview(sel, descId, type){
  const el = document.getElementById(descId);
  if(el) el.innerHTML = voicePreviewHtml(type, sel.value);
}

// Рендер контрола выбора голоса по типу ассистента.
// ids: { voice, vid, spd, spdv, desc, fvid, flat } — id элементов (модалка и визард используют разные).
function voiceControlHtml(type, cur, ids){
  if(type==='eleven') return elevenVoiceControlHtml(cur, ids);
  if(type==='fish'){
    // У Fish голос — reference_id из библиотеки fish.audio (в т.ч. свой клон),
    // фиксированного списка голосов нет. Модель синтеза не показываем: пока
    // обкатывается только одна (см. FISH_SELECTABLE_MODELS на бэке).
    const spd = (cur.voice_speed!=null) ? cur.voice_speed : 1.0;
    const lat = cur.fish_latency || 'balanced';
    const latOpts = FISH_LATENCY_MODES.map(m =>
      `<option value="${m}" ${m===lat?'selected':''}>${FISH_LATENCY_LABELS[m]||m}</option>`).join('');
    return `<div class="form-group">
        <label class="form-label">Fish Voice ID</label>
        <input type="text" class="form-input" id="${ids.fvid}" value="${esc(cur.fish_voice_id||'')}" placeholder="e58b0d7efca34eb38d5c4985e378abcb">
        <div class="form-hint">ID голоса из <a href="https://fish.audio/" target="_blank">fish.audio</a> (можно свой клон). Пустое поле — голос по умолчанию.</div>
      </div>
      <div class="form-group">
        <label class="form-label">Скорость голоса: <span id="${ids.spdv}">${spd}</span></label>
        <input type="range" id="${ids.spd}" min="0.5" max="1.5" step="0.1" value="${spd}" style="width:100%" oninput="document.getElementById('${ids.spdv}').textContent=this.value">
      </div>
      <div class="form-group">
        <label class="form-label">Режим синтеза</label>
        <select class="form-select" id="${ids.flat}">${latOpts}</select>
        <div class="form-hint">Быстрее — раньше начинает говорить, качественнее — ровнее интонация.</div>
      </div>`;
  }
  if(type==='cartesia'){
    const spd = (cur.voice_speed!=null) ? cur.voice_speed : 1.0;
    return `<div class="form-group">
        <label class="form-label">Cartesia Voice ID</label>
        <input type="text" class="form-input" id="${ids.vid}" value="${esc(cur.cartesia_voice_id||'')}" placeholder="a0e99841-438c-4a64-b679-ae501e7d6091">
        <div class="form-hint">ID голоса из <a href="https://play.cartesia.ai/" target="_blank">play.cartesia.ai</a></div>
      </div>
      <div class="form-group">
        <label class="form-label">Скорость голоса: <span id="${ids.spdv}">${spd}</span></label>
        <input type="range" id="${ids.spd}" min="0.5" max="1.5" step="0.1" value="${spd}" style="width:100%" oninput="document.getElementById('${ids.spdv}').textContent=this.value">
      </div>`;
  }
  const voices = (type==='gemini') ? GEMINI_VOICES : (type==='yandex') ? YANDEX_VOICES : (type==='cascade') ? CASCADE_VOICES : OPENAI_VOICES;
  const v = cur.voice || VOICE_DEFAULTS[type] || voices[0];
  const opts = voices.map(x=>{
    const g = GENDER_INFO[voiceMeta(type,x).gender] || GENDER_INFO.n;
    return `<option value="${x}" ${x===v?'selected':''}>${x} · ${g.label.toLowerCase()}</option>`;
  }).join('');
  return `<div class="form-group">
      <label class="form-label">Голос</label>
      <select class="form-select" id="${ids.voice}" onchange="updateVoicePreview(this,'${ids.desc}','${type}')">${opts}</select>
      <div class="voice-preview" id="${ids.desc}">${voicePreviewHtml(type, v)}</div>
    </div>`;
}

// Считать выбранный голос в объект для тела запроса.
function readVoiceBody(type, ids){
  if(type==='eleven'){
    const vEl = document.getElementById(ids.voice);
    const opt = vEl && vEl.selectedOptions ? vEl.selectedOptions[0] : null;
    const body = {
      eleven_language: document.getElementById(ids.elang)?.value || null,
      eleven_tts_model: document.getElementById(ids.emodel)?.value || null,
      eleven_stability: parseFloat(document.getElementById(ids.estab)?.value),
    };
    if(isNaN(body.eleven_stability)) delete body.eleven_stability;
    // Голос отправляем, только когда список загрузился (иначе не затираем текущий)
    if(vEl && !vEl.disabled && vEl.value){ body.eleven_voice_id = vEl.value; body.eleven_voice_name = opt ? (opt.dataset.name || opt.textContent) : null; }
    return body;
  }
  if(type==='fish'){
    const vidEl = document.getElementById(ids.fvid);
    const spdEl = document.getElementById(ids.spd);
    const latEl = document.getElementById(ids.flat);
    return {
      fish_voice_id: vidEl ? (vidEl.value.trim() || null) : null,
      voice_speed: spdEl ? (parseFloat(spdEl.value) || 1.0) : 1.0,
      fish_latency: latEl ? latEl.value : null,
    };
  }
  if(type==='cartesia'){
    const vidEl = document.getElementById(ids.vid);
    const spdEl = document.getElementById(ids.spd);
    return {
      cartesia_voice_id: vidEl ? (vidEl.value.trim() || null) : null,
      voice_speed: spdEl ? (parseFloat(spdEl.value) || 1.0) : 1.0,
    };
  }
  const vEl = document.getElementById(ids.voice);
  return { voice: vEl ? vEl.value : null };
}

const I_VOICE_IDS = { voice:'i-voice', vid:'i-cartesia-voice-id', spd:'i-voice-speed', spdv:'i-voice-speed-val', desc:'i-voice-desc', fvid:'i-fish-voice-id', flat:'i-fish-latency', elang:'i-eleven-lang', emodel:'i-eleven-model', estab:'i-eleven-stab' };
const W_VOICE_IDS = { voice:'w-voice', vid:'w-cartesia-voice-id', spd:'w-voice-speed', spdv:'w-voice-speed-val', desc:'w-voice-desc', fvid:'w-fish-voice-id', flat:'w-fish-latency', elang:'w-eleven-lang', emodel:'w-eleven-model', estab:'w-eleven-stab' };

// ════════════════ ELEVENLABS VOICE (единственный голос новых агентов) ════════════════
// Зеркала справочников backend/models/eleven_assistant.py (ELEVEN_TTS_MODELS, ELEVEN_LANGUAGES,
// ELEVEN_STABILITY_LEVELS). Голоса — из аккаунта ElevenLabs: /api/eleven-assistants/voices.
const ELEVEN_TTS_MODELS = [
  { id:'eleven_v3_conversational', title:'Eleven v3 Conversational · быстрее, $0.05 / 1000 символов' },
  { id:'eleven_v3', title:'Eleven v3 · выразительнее, $0.10 / 1000 символов' },
];
const ELEVEN_LANGUAGES = [
  { code:'ky', title:'Кыргызский' }, { code:'ru', title:'Русский' }, { code:'kk', title:'Казахский' },
  { code:'uz', title:'Узбекский' }, { code:'en', title:'Английский' }, { code:'tr', title:'Турецкий' },
];
const ELEVEN_STABILITY = [
  { v:0, title:'Creative — эмоциональнее' }, { v:0.5, title:'Natural — естественно' }, { v:1, title:'Robust — ровно' },
];
let elevenVoicesCache = {};   // language → [voices]
let elevenPreviewAudio = null;

function elevenVoiceControlHtml(cur, ids){
  const lang = cur.eleven_language || 'ky';
  const model = cur.eleven_tts_model || 'eleven_v3_conversational';
  const stab = (cur.eleven_stability != null) ? Number(cur.eleven_stability) : 0.5;
  const langOpts = ELEVEN_LANGUAGES.map(l => `<option value="${l.code}" ${l.code===lang?'selected':''}>${l.title}</option>`).join('');
  const modelOpts = ELEVEN_TTS_MODELS.map(m => `<option value="${m.id}" ${m.id===model?'selected':''}>${esc(m.title)}</option>`).join('');
  const stabOpts = ELEVEN_STABILITY.map(x => `<option value="${x.v}" ${x.v===stab?'selected':''}>${esc(x.title)}</option>`).join('');
  return `<div class="form-group">
      <label class="form-label">Язык разговора <span class="hint" tabindex="0" data-hint="На этом языке агент говорит в звонке. Для кыргызского лучше голоса, отмеченные как рекомендуемые."><i class="far fa-circle-question"></i></span></label>
      <select class="form-select" id="${ids.elang}" onchange="elevenLoadVoices(${esc(JSON.stringify(ids))}, null)">${langOpts}</select>
    </div>
    <div class="form-group">
      <label class="form-label">Голос ElevenLabs</label>
      <select class="form-select" id="${ids.voice}" disabled data-current="${esc(cur.eleven_voice_id||'')}" data-current-name="${esc(cur.eleven_voice_name||'')}"><option value="">Загрузка голосов…</option></select>
      <div class="voice-preview" id="${ids.desc}" style="display:none"></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label class="form-label">Модель синтеза</label><select class="form-select" id="${ids.emodel}">${modelOpts}</select></div>
      <div class="form-group"><label class="form-label">Подача</label><select class="form-select" id="${ids.estab}">${stabOpts}</select></div>
    </div>`;
}

// Загрузить голоса аккаунта ElevenLabs для выбранного языка и заполнить селект.
async function elevenLoadVoices(ids, current){
  const sel = document.getElementById(ids.voice);
  const lang = document.getElementById(ids.elang)?.value || 'ky';
  if(!sel) return;
  const keep = current != null ? current : (sel.value || sel.dataset.current || '');
  const keepName = sel.dataset.currentName || '';
  sel.disabled = true;
  sel.innerHTML = '<option value="">Загрузка голосов…</option>';
  let voices = elevenVoicesCache[lang];
  if(!voices){
    try{
      const r = await apiFetch('/api/eleven-assistants/voices?language=' + encodeURIComponent(lang));
      if(r && r.status === 200){ voices = ((await r.json()).voices) || []; elevenVoicesCache[lang] = voices; }
    }catch(e){}
  }
  if(!voices){
    sel.innerHTML = keep ? `<option value="${esc(keep)}" data-name="${esc(keepName)}">${esc(keepName || keep)}</option>` : '<option value="">Не удалось загрузить голоса</option>';
    sel.disabled = !keep;
    return;
  }
  const opt = v => `<option value="${esc(v.voice_id)}" data-name="${esc(v.name||'')}">${esc(v.name || v.voice_id)}${v.gender ? ' · ' + (v.gender==='female'?'женский':v.gender==='male'?'мужской':v.gender) : ''}</option>`;
  const rec = voices.filter(v => v.recommended), other = voices.filter(v => !v.recommended);
  let html = '';
  if(keep && !voices.some(v => v.voice_id === keep)) html += `<option value="${esc(keep)}" data-name="${esc(keepName)}">${esc(keepName || keep)} · текущий</option>`;
  if(rec.length) html += `<optgroup label="Рекомендуем для языка">${rec.map(opt).join('')}</optgroup>`;
  if(other.length) html += `<optgroup label="${rec.length ? 'Другие голоса' : 'Голоса аккаунта'}">${other.map(opt).join('')}</optgroup>`;
  sel.innerHTML = html || '<option value="">В аккаунте нет голосов</option>';
  if(keep) sel.value = keep;
  sel.disabled = !voices.length && !keep;
  sel.onchange = () => elevenShowPreview(ids);
  elevenShowPreview(ids);
}

function elevenShowPreview(ids){
  const sel = document.getElementById(ids.voice);
  const box = document.getElementById(ids.desc);
  if(!sel || !box) return;
  const lang = document.getElementById(ids.elang)?.value || 'ky';
  const v = (elevenVoicesCache[lang] || []).find(x => x.voice_id === sel.value);
  if(!v){ box.style.display = 'none'; return; }
  const g = v.gender === 'female' ? GENDER_INFO.f : v.gender === 'male' ? GENDER_INFO.m : GENDER_INFO.n;
  const desc = [v.accent, v.age, v.description].filter(Boolean).join(' · ');
  box.style.display = 'flex';
  box.innerHTML = `<span class="voice-ava ${g.cls}"><i class="fas ${g.icon}"></i></span>
    <div class="voice-pv-txt"><b>${esc(v.name || '')} · ${g.label} голос</b><span>${esc(desc || (v.recommended ? 'Подтверждён для выбранного языка' : 'Голос из аккаунта ElevenLabs'))}</span></div>
    ${v.preview_url ? `<button class="voice-play" type="button" title="Прослушать" onclick="elevenPlayPreview('${esc(v.preview_url)}')"><i class="fas fa-play"></i></button>` : ''}`;
}

function elevenPlayPreview(url){
  try{
    if(elevenPreviewAudio){ elevenPreviewAudio.pause(); }
    elevenPreviewAudio = new Audio(url);
    elevenPreviewAudio.play().catch(()=>{});
  }catch(e){}
}

// ════════════════ INSTRUCTIONS MODAL ════════════════
function openSettingsSection(name){
  document.querySelectorAll('#settings-nav .settings-nav-item').forEach(b => b.classList.toggle('active', b.dataset.section === name));
  document.querySelectorAll('#instructions-modal-overlay .settings-section').forEach(s => s.classList.toggle('active', s.dataset.section === name));
}

function openInstructionsModal(section){
  openSettingsSection(typeof section === 'string' ? section : 'main');
  document.getElementById('i-name').value = agentData.name || '';
  document.getElementById('i-additional_instructions').value = agentData.additional_instructions || '';
  document.getElementById('i-webhook_url').value = agentData.webhook_url || '';
  document.getElementById('i-voice_additional_instructions').value = agentData.voice_additional_instructions || '';
  document.getElementById('i-inbound_first_phrase').value = agentData.inbound_first_phrase || '';
  const vType = agentData.assistant_type || 'eleven';
  const legacy = vType !== 'eleven'
    ? `<div class="note warn"><i class="fas fa-triangle-exclamation"></i><span>Агент создан на старом голосовом движке (${esc(AGENT_TYPE_NAMES[vType] || vType)}). Новые агенты работают на ElevenLabs — перевести этого можно, создав нового агента.</span></div>`
    : '';
  document.getElementById('i-voice-group').innerHTML = legacy + voiceControlHtml(
    vType,
    {
      voice: agentData.voice,
      cartesia_voice_id: agentData.cartesia_voice_id,
      voice_speed: agentData.voice_speed,
      fish_voice_id: agentData.fish_voice_id,
      fish_latency: agentData.fish_latency,
      eleven_voice_id: agentData.eleven_voice_id,
      eleven_voice_name: agentData.eleven_voice_name,
      eleven_language: agentData.eleven_language,
      eleven_tts_model: agentData.eleven_tts_model,
      eleven_stability: agentData.eleven_stability,
    },
    I_VOICE_IDS
  );
  if(vType === 'eleven') elevenLoadVoices(I_VOICE_IDS, agentData.eleven_voice_id || '');

  const ms = document.getElementById('i-orchestrator_model');
  ms.innerHTML = modelOptionsHtml(orchestratorModels);
  ms.value = agentData.orchestrator_model || (orchestratorModels[0]?.slug || '');
  ms.onchange = () => {
    const m = orchestratorModels.find(x => x.slug === ms.value);
    document.getElementById('i-model-desc').innerHTML = modelHintHtml(m);
  };
  ms.onchange();

  fillPhoneSelect('i-phone-number');
  loadPhoneNumbers().then(() => fillPhoneSelect('i-phone-number'));

  // Публичный API — статус подгружаем асинхронно (не блокируем открытие модалки)
  const pubWrap = document.getElementById('i-public-access');
  if(pubWrap) pubWrap.innerHTML = '<div class="form-hint">Загрузка…</div>';
  loadPublicAccess();

  document.getElementById('instructions-modal-overlay').classList.remove('hidden');
}

function closeInstructionsModal(){
  document.getElementById('instructions-modal-overlay').classList.add('hidden');
}

async function saveInstructions(){
  const btn = document.getElementById('instructions-save-btn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:15px;height:15px;border-width:2px"></div> Сохранение...';

  const body = {
    name: document.getElementById('i-name').value || agentData.name,
    additional_instructions: document.getElementById('i-additional_instructions').value,
    webhook_url: document.getElementById('i-webhook_url').value.trim() || null,
    voice_additional_instructions: document.getElementById('i-voice_additional_instructions').value,
    inbound_first_phrase: document.getElementById('i-inbound_first_phrase').value.trim() || null,
    orchestrator_model: document.getElementById('i-orchestrator_model').value,
    ...readVoiceBody(agentData.assistant_type || 'eleven', I_VOICE_IDS),
  };
  try {
    // Номер агента — отдельным запросом (привязка входящих + номер исходящих)
    if(!(await savePhoneSelection('i-phone-number'))){
      btn.disabled = false; btn.innerHTML = '<i class="fas fa-check"></i> Сохранить';
      return;
    }
    const r = await apiFetch(API + '/', { method: 'PUT', body: JSON.stringify(body) });
    if(r && r.status === 200){
      agentData = await r.json();
      renderAgentHeader();
      closeInstructionsModal();
      showToast('Настройки сохранены', 'success');
    } else {
      const err = await r?.json().catch(() => ({}));
      showToast(errText(err.detail ?? err.message), 'error');
    }
  } catch(e) {
    showToast('Ошибка сети', 'error');
  }
  btn.disabled = false;
  btn.innerHTML = '<i class="fas fa-check"></i> Сохранить';
}


// ════════════════ PUBLIC API ACCESS ════════════════
// Публичный HTTP-канал приёма заявок «сервер-к-серверу».
// Backend: GET/PUT /api/agent/public-access, POST /public-access/regenerate.
let publicAccessState = null;

async function loadPublicAccess(){
  try{
    const r = await apiFetch(API + '/public-access');
    if(!r || r.status !== 200){ renderPublicAccess(); return; }
    publicAccessState = await r.json();
  }catch(e){ publicAccessState = null; }
  renderPublicAccess();
}

function renderPublicAccess(){
  const wrap = document.getElementById('i-public-access');
  if(!wrap) return;
  const s = publicAccessState || {};
  const enabled = !!s.enabled;

  const toggleRow = `<div class="pub-row">
      <label class="form-label" style="margin:0">Приём заявок включён</label>
      <label class="switch"><input type="checkbox" ${enabled?'checked':''} onchange="togglePublicAccess(this.checked)"><span class="slider"></span></label>
    </div>`;

  if(!enabled){
    wrap.innerHTML = toggleRow +
      '<div class="form-hint">Выключено. Включите тумблер — появятся ссылка, секретный ключ и инструкция.</div>';
    return;
  }

  const url = s.endpoint_url || '';
  const key = s.api_key || '';
  const curl =
`curl -X POST "${url}" \\
  -H "X-Api-Key: ${key}" \\
  -H "Content-Type: application/json" \\
  -d '{"name":"Иван","phone":"+79991234567","comment":"хочу демо"}'`;

  wrap.innerHTML = toggleRow + `
    <div class="form-group">
      <label class="form-label">Ссылка для обращения</label>
      <div class="pub-copy-row">
        <input type="text" class="form-input" id="i-pub-url" value="${esc(url)}" readonly>
        <button class="btn btn-secondary" type="button" title="Скопировать" onclick="copyPublicField('i-pub-url')"><i class="fas fa-copy"></i></button>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Секретный ключ</label>
      <div class="pub-copy-row">
        <input type="password" class="form-input" id="i-pub-key" value="${esc(key)}" readonly>
        <button class="btn btn-secondary" type="button" title="Показать/скрыть" onclick="togglePubKeyVisible()"><i class="fas fa-eye" id="i-pub-key-eye"></i></button>
        <button class="btn btn-secondary" type="button" title="Скопировать" onclick="copyPublicField('i-pub-key')"><i class="fas fa-copy"></i></button>
      </div>
      <div class="form-hint">Держите ключ в секрете — он даёт доступ к вашему агенту. <a href="#" onclick="regeneratePublicKey();return false;">Перевыпустить ключ</a> (старый сразу перестанет работать).</div>
    </div>
    <div class="form-group">
      <label class="form-label">Как обращаться</label>
      <div class="form-hint" style="margin-bottom:6px">POST-запрос на ссылку выше. Ключ — в заголовке <code>X-Api-Key</code> (или <code>Authorization: Bearer</code>). Тело — любой JSON или текст: агент сам разберёт, что в нём, и решит, что делать. Пример:</div>
      <pre class="pub-code">${esc(curl)}</pre>
    </div>`;
}

async function togglePublicAccess(want){
  try{
    const r = await apiFetch(API + '/public-access', { method:'PUT', body: JSON.stringify({ enabled: !!want }) });
    if(r && r.status === 200){
      publicAccessState = await r.json();
      renderPublicAccess();
      showToast(want ? 'Публичный приём включён' : 'Публичный приём выключен', 'success');
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(errText(err.detail ?? err.message), 'error');
      renderPublicAccess(); // откатить тумблер к фактическому состоянию
    }
  }catch(e){ showToast('Ошибка сети','error'); renderPublicAccess(); }
}

async function regeneratePublicKey(){
  if(!confirm('Перевыпустить секретный ключ? Старый ключ сразу перестанет работать.')) return;
  try{
    const r = await apiFetch(API + '/public-access/regenerate', { method:'POST' });
    if(r && r.status === 200){
      publicAccessState = await r.json();
      renderPublicAccess();
      showToast('Ключ перевыпущен','success');
    } else {
      const err = await r?.json().catch(()=>({}));
      showToast(errText(err.detail ?? err.message), 'error');
    }
  }catch(e){ showToast('Ошибка сети','error'); }
}

function togglePubKeyVisible(){
  const inp = document.getElementById('i-pub-key');
  const eye = document.getElementById('i-pub-key-eye');
  if(!inp) return;
  const show = inp.type === 'password';
  inp.type = show ? 'text' : 'password';
  if(eye) eye.className = show ? 'fas fa-eye-slash' : 'fas fa-eye';
}

async function copyPublicField(id){
  const inp = document.getElementById(id);
  if(!inp) return;
  const val = inp.value || '';
  try{
    if(navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(val);
    } else {
      const prevType = inp.type; inp.type = 'text';
      inp.select(); inp.setSelectionRange(0, 99999);
      document.execCommand('copy');
      inp.type = prevType;
    }
    showToast('Скопировано','success');
  }catch(e){ showToast('Не удалось скопировать','error'); }
}


