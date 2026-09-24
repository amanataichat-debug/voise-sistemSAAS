/* ============================================================================
 * agent/wizard.js — Мастер создания агента (9 шагов) → /api/agent/create
 * Часть страницы /static/agent.html (Voksy AI Agent).
 * Классический скрипт (НЕ ES-модуль): функции и состояние — глобальные,
 * доступны между всеми файлами agent/*.js и из inline-onclick в разметке.
 * Подключается из agent.html. Документация: backend/static/agent/CLAUDE.md
 * ========================================================================== */

// ════════════════ WIZARD ════════════════
const TOTAL_STEPS = 8; // 0..7 (8 = creation)
let wizardStep = 0;
let wizardData = {};
let wizardUser = {};
let wizardTele = {};

function showWizard(){
  // Вариант A: перед мастером — обязательный обучающий модуль (5 слайдов).
  // Показывается ВСЕГДА при создании; «Пропустить» → сразу к шагам мастера.
  if(typeof startOnboarding === 'function'){
    startOnboarding(openWizardSteps);
  } else {
    openWizardSteps();
  }
}
function openWizardSteps(){
  document.getElementById('loading-screen').classList.add('hidden');
  document.getElementById('wizard-overlay').classList.remove('hidden');
  try{ wizardData = JSON.parse(localStorage.getItem('agent_wizard_v3')||'{}'); }catch(e){ wizardData={}; }
  wizardStep = 0;
  renderWizard();
}
function renderProgress(){
  const el = document.getElementById('wizard-progress');
  let h='';
  for(let i=0;i<TOTAL_STEPS;i++){ const c = i<wizardStep?'done':i===wizardStep?'active':''; h+=`<div class="w-dot ${c}"></div>`; if(i<TOTAL_STEPS-1) h+=`<div class="w-line ${i<wizardStep?'done':''}"></div>`; }
  el.innerHTML=h;
}

async function renderWizard(){
  renderProgress();
  const c = document.getElementById('wizard-content');
  if(wizardStep===0){ await renderStep0(c); return; }
  if(wizardStep>=1 && wizardStep<=5){ renderDocStep(c); return; }
  if(wizardStep===6){ renderInstructionsStep(c); return; }
  if(wizardStep===7){ await renderModelStep(c); return; }
  if(wizardStep===8){ renderCreation(c); return; }
}

async function renderStep0(c){
  c.innerHTML = '<h2>Создание агента</h2><p class="hint">Загрузка...</p>';
  // Новые агенты говорят только голосом ElevenLabs (серверные ключи, свои не нужны).
  wizardData.assistant_type = 'eleven';
  let phones = { has_numbers:false, phone_numbers:[] };
  try{
    const r = await apiFetch(API + '/phone-numbers');
    if(r && r.status===200) phones = await r.json();
  }catch(e){}
  wizardTele = phones;
  drawStep0(c);
}

function drawStep0(c){
  const has = !!(wizardTele && wizardTele.has_numbers);
  const phoneBanner = has
    ? `<div class="tele-banner ok"><i class="fas fa-circle-check"></i> <span>В вашей телефонии есть номер — после создания выберите его в настройках агента («Звонки»).</span></div>`
    : `<div class="tele-banner info"><i class="fas fa-circle-info"></i> <span>Номера пока нет — агента можно создать и без него: чат, Telegram и Instagram работают сразу. Номер подключается позже в настройках агента.</span></div>`;
  c.innerHTML = `<h2>Создание агента</h2><p class="hint">Агент — это «мозг»-оркестратор, который ведёт вашу базу и планирует звонки, и голосовой ассистент, который разговаривает с клиентами.</p>
    <div class="type-card selected" style="cursor:default">
      <div class="type-radio"></div>
      <div class="type-info">
        <div class="type-name">Голос ElevenLabs</div>
        <div class="type-desc">Живые голоса Eleven v3, кыргызский и русский. Диалог ведёт OpenAI, озвучивает ElevenLabs — всё на ключах VoksiAI, свои ключи не нужны. Голос и язык выберете на последнем шаге.</div>
        <div style="margin-top:8px"><span class="key-pill ok"><i class="fas fa-check"></i> Готово к работе</span></div>
      </div>
    </div>
    ${phoneBanner}
    <div class="wizard-actions"><div></div><button class="btn btn-primary" onclick="wizardStep=1;renderWizard()">Далее <i class="fas fa-arrow-right"></i></button></div>`;
}

const WIZ_DOCS = [ null,
  { key:'doc_who_am_i', title:'Кто мы', hint:'Опишите компанию: название, сфера, УТП, средний чек.', ph:'Мы — компания «Ала-Тоо Сервис», продаём CRM для малого бизнеса в Бишкеке...' },
  { key:'doc_who_we_call', title:'Кому звоним', hint:'Портрет клиента: должность, боли, возражения.', ph:'Руководители отделов продаж в компаниях от 20 человек...' },
  { key:'doc_how_we_talk', title:'Как говорим', hint:'Стиль общения и имя агента.', ph:'Дружелюбно, кратко, без давления...', extra:true },
  { key:'doc_what_we_offer', title:'Что предлагаем', hint:'Продукты, цены, акции.', ph:'Тариф «Старт» — 2 990 сом/мес...' },
  { key:'doc_rules_and_goals', title:'Правила и цели', hint:'KPI, лимиты, что считать успехом.', ph:'Цель: назначить встречу. Макс 3 попытки...' },
];
function renderDocStep(c){
  const s = WIZ_DOCS[wizardStep];
  c.innerHTML = `<h2>Шаг ${wizardStep}: ${s.title}</h2><p class="hint">${s.hint}</p>
    ${s.extra?`<div class="form-group"><label class="form-label">Имя агента *</label><input type="text" class="form-input" id="w-name" value="${esc(wizardData.name||'')}" placeholder="Алина"></div>`:''}
    <div class="form-group"><textarea class="form-textarea" id="w-ta" rows="6" placeholder="${esc(s.ph)}">${esc(wizardData[s.key]||'')}</textarea></div>
    <div class="wizard-actions"><button class="btn btn-secondary" onclick="wizardBack()"><i class="fas fa-arrow-left"></i> Назад</button><button class="btn btn-primary" onclick="wizardNextDoc()">Далее <i class="fas fa-arrow-right"></i></button></div>`;
}
function saveDocStep(){
  const s = WIZ_DOCS[wizardStep];
  const ta = document.getElementById('w-ta'); if(ta) wizardData[s.key]=ta.value;
  const nm = document.getElementById('w-name'); if(nm) wizardData.name=nm.value;
  persistWizard();
}
function wizardNextDoc(){
  saveDocStep();
  const s = WIZ_DOCS[wizardStep];
  if(!(wizardData[s.key]||'').trim()){ showToast('Заполните поле','error'); return; }
  if(wizardStep===3 && !(wizardData.name||'').trim()){ showToast('Укажите имя агента','error'); return; }
  wizardStep++; renderWizard();
}
function wizardBack(){
  if(wizardStep>=1 && wizardStep<=5) saveDocStep();
  if(wizardStep===6){ const ta=document.getElementById('w-instr'); if(ta){ wizardData.additional_instructions=ta.value; persistWizard(); } }
  if(wizardStep>0){ wizardStep--; renderWizard(); }
}

function renderInstructionsStep(c){
  c.innerHTML = `<h2>Инструкции для оркестратора</h2><p class="hint">Опционально. Правила планирования звонков и работы с контактами для текстового мозга-оркестратора. В живом телефонном разговоре НЕ используются — для этого поле инструкций голосового агента на следующем шаге.</p>
    <div class="form-group"><textarea class="form-textarea" id="w-instr" rows="6" placeholder="Например: «перед обзвоном новых контактов проверяй дубли», «не планируй звонки в выходные».">${esc(wizardData.additional_instructions||'')}</textarea></div>
    <div class="wizard-actions"><button class="btn btn-secondary" onclick="wizardBack()"><i class="fas fa-arrow-left"></i> Назад</button><button class="btn btn-primary" onclick="wizardNextInstr()">Далее <i class="fas fa-arrow-right"></i></button></div>`;
}
function wizardNextInstr(){ const ta=document.getElementById('w-instr'); wizardData.additional_instructions=ta?ta.value:''; persistWizard(); wizardStep=7; renderWizard(); }

async function renderModelStep(c){
  if(!orchestratorModels.length){ const r=await apiFetch(API+'/orchestrator-models'); if(r&&r.status===200){ const d=await r.json(); orchestratorModels=d.models||[]; if(!wizardData.orchestrator_model) wizardData.orchestrator_model=d.default; } }
  if(!wizardData.orchestrator_model && orchestratorModels.length){ const def=orchestratorModels.find(m=>m.is_default); wizardData.orchestrator_model = def?def.slug:orchestratorModels[0].slug; }
  c.innerHTML = `<h2>Модель и голос</h2><p class="hint">Модель оркестратора управляет агентом: планирует звонки, анализирует результаты, отвечает в чате. Голос — то, чем агент говорит в звонке. Работа оркестратора оплачивается кредитами агента.</p>
    <div class="form-group"><label class="form-label">Модель</label><select class="form-select" id="w-model">${modelOptionsHtml(orchestratorModels, wizardData.orchestrator_model)}</select><div class="form-hint" id="w-model-desc"></div></div>
    ${voiceControlHtml('eleven', { eleven_voice_id: wizardData.eleven_voice_id, eleven_voice_name: wizardData.eleven_voice_name, eleven_language: wizardData.eleven_language, eleven_tts_model: wizardData.eleven_tts_model, eleven_stability: wizardData.eleven_stability }, W_VOICE_IDS)}
    <div class="form-group"><label class="form-label">Инструкции для голосового агента</label><textarea class="form-textarea" id="w-voice-instr" rows="4" placeholder="Например: «говори коротко, не дави», «если спросят про цену — назови диапазон».">${esc(wizardData.voice_additional_instructions||'')}</textarea><div class="form-hint">Правила поведения именно в живом разговоре по телефону. Опционально.</div></div>
    <div class="wizard-actions"><button class="btn btn-secondary" onclick="wizardBack()"><i class="fas fa-arrow-left"></i> Назад</button><button class="btn btn-primary" onclick="submitCreate()"><i class="fas fa-rocket"></i> Создать агента</button></div>`;
  elevenLoadVoices(W_VOICE_IDS, wizardData.eleven_voice_id || '');
  const ms=document.getElementById('w-model'); const upd=()=>{ const m=orchestratorModels.find(x=>x.slug===ms.value); document.getElementById('w-model-desc').innerHTML=modelHintHtml(m); wizardData.orchestrator_model=ms.value; persistWizard(); }; ms.onchange=upd; upd();
}

function submitCreate(){
  wizardData.assistant_type = 'eleven';
  Object.assign(wizardData, readVoiceBody('eleven', W_VOICE_IDS));
  const vi=document.getElementById('w-voice-instr'); if(vi) wizardData.voice_additional_instructions=vi.value;
  persistWizard();
  wizardStep=8; renderWizard();
}

async function renderCreation(c){
  const typeName = 'ElevenLabs';
  c.innerHTML = `<h2>Создание агента</h2><p class="hint">Настройка вашего агента...</p>
    <ul class="creation-list">
      <li class="creation-item pending" id="cr-docs"><div class="creation-icon"></div>Сохранение документов</li>
      <li class="creation-item pending" id="cr-voice"><div class="creation-icon"></div>Создание голосового агента (${typeName})</li>
      <li class="creation-item pending" id="cr-act"><div class="creation-icon"></div>Активация</li>
    </ul>
    <div id="cr-error" style="display:none;margin-top:16px;padding:12px;background:var(--red-light);color:var(--red);border-radius:10px;font-size:13px"></div>
    <div id="cr-success" style="display:none;margin-top:20px;text-align:center"><button class="btn btn-primary" onclick="location.reload()"><i class="fas fa-arrow-right"></i> Открыть агента</button></div>
    <div id="cr-back" style="display:none;margin-top:16px;text-align:center"><button class="btn btn-secondary" onclick="wizardStep=7;renderWizard()">Назад</button></div>`;
  await anim('cr-docs');
  const body = {
    name: wizardData.name||'Агент', assistant_type: 'eleven',
    doc_who_am_i: wizardData.doc_who_am_i||'', doc_who_we_call: wizardData.doc_who_we_call||'',
    doc_how_we_talk: wizardData.doc_how_we_talk||'', doc_what_we_offer: wizardData.doc_what_we_offer||'',
    doc_rules_and_goals: wizardData.doc_rules_and_goals||'', additional_instructions: wizardData.additional_instructions||null,
    voice_additional_instructions: wizardData.voice_additional_instructions||null,
    orchestrator_model: wizardData.orchestrator_model||null,
    eleven_voice_id: wizardData.eleven_voice_id||null,
    eleven_voice_name: wizardData.eleven_voice_name||null,
    eleven_language: wizardData.eleven_language||null,
    eleven_tts_model: wizardData.eleven_tts_model||null,
    eleven_stability: (wizardData.eleven_stability!=null && !isNaN(wizardData.eleven_stability)) ? wizardData.eleven_stability : null,
  };
  try{
    const r = await apiFetch(API + '/create', { method:'POST', body:JSON.stringify(body) });
    if(r && (r.status===200||r.status===201)){
      const created = await r.json().catch(()=>({}));
      if(created && created.id) localStorage.setItem('agent_current_id', created.id);
      done('cr-docs'); await anim('cr-voice'); done('cr-voice'); await anim('cr-act'); done('cr-act');
      localStorage.removeItem('agent_wizard_v3');
      const succ = document.getElementById('cr-success');
      succ.style.display='block';
      // ✅ Онбординг: сообщаем про тестовый период или необходимость тарифа
      const note = document.createElement('div');
      note.style.cssText = 'margin-top:12px;font-size:13.5px;color:var(--hint)';
      if(created.trial_activated){
        note.innerHTML = '🎉 Вам доступен бесплатный <b>тестовый период на 3 дня</b> и <b>1 500 кредитов</b> для теста оркестратора. После теста агент доступен на тарифе <b>Profi</b> (включает кредиты).';
      } else if(created.agent_trial_used){
        note.innerHTML = 'Тестовый период уже использован. Агент доступен на тарифе <b>Profi</b>. <button class="sub-action" onclick="location.href=\'/static/dashboard.html\'">Перейти к тарифам</button>';
      }
      succ.appendChild(note);
    } else {
      const err = await r?.json().catch(()=>({}));
      document.getElementById('cr-error').style.display='block';
      document.getElementById('cr-error').textContent = errText(err.detail ?? err.message);
      document.getElementById('cr-back').style.display='block';
    }
  }catch(e){
    document.getElementById('cr-error').style.display='block';
    document.getElementById('cr-error').textContent='Ошибка сети: '+e.message;
    document.getElementById('cr-back').style.display='block';
  }
}
function anim(id){ return new Promise(r=>{ const el=document.getElementById(id); if(el) el.querySelector('.creation-icon').innerHTML='<div class="spinner"></div>'; setTimeout(r,650); }); }
function done(id){ const el=document.getElementById(id); if(el){ el.classList.remove('pending'); el.classList.add('done'); el.querySelector('.creation-icon').innerHTML='<i class="fas fa-check"></i>'; } }

function persistWizard(){ localStorage.setItem('agent_wizard_v3', JSON.stringify(wizardData)); }


