/* ============================================================================
 * agent/voice-messages.js — Голосовые сообщения агента в Telegram и WhatsApp
 *                            → /api/agent/voice-messages
 * Раздел «Голосовые сообщения» модалки настроек агента:
 *   • движок озвучки — ElevenLabs (лучший кыргызский) или OpenAI (голоса GPT-Live);
 *   • ElevenLabs: голос (по умолчанию как в звонках), язык, подача (по умолчанию Creative);
 *   • OpenAI: голос GPT-Live;
 *   • доля голосовых в режиме «агент решает сам»;
 *   • «Прослушать» (MP3 из /preview, списывает кредиты).
 * Сохраняется сразу. Когда отвечать голосом — в окнах WhatsApp и Telegram
 * (messengerSettingsHtml). Озвучка — backend/services/agent_voice_service.py.
 * Классический скрипт.
 * ========================================================================== */

const VOICE_MSG_API = API + '/voice-messages';
const VOICE_MODE_LABELS = { never: 'никогда', on_voice: 'в ответ на голосовое клиента', auto: 'агент решает сам' };
const VOICE_MSG_ENGINES = [
  { id: 'eleven', title: 'ElevenLabs', hint: 'Лучший кыргызский. Вздохи, паузы и спокойная подача — метками в тексте.' },
  { id: 'openai', title: 'OpenAI · голоса GPT-Live', hint: 'Очень живая манера речи, лучше всего на русском. Кыргызский слабее.' },
];
let voiceMsgState = null;
let voiceMsgAudio = null;

async function loadVoiceMessages(){
  const el = document.getElementById('i-voice-msg');
  if(!el) return;
  try{
    const r = await apiFetch(VOICE_MSG_API);
    voiceMsgState = (r && r.status === 200) ? await r.json() : null;
  }catch(e){ voiceMsgState = null; }
  renderVoiceMessages();
}

function _vmVoiceLang(){
  const s = voiceMsgState || {};
  return s.language || (s.call_voice && s.call_voice.language) || 'ky';
}

function renderVoiceMessages(){
  const el = document.getElementById('i-voice-msg');
  if(!el) return;
  const s = voiceMsgState;
  if(!s){ el.innerHTML = '<div class="empty">Не удалось загрузить настройки</div>'; return; }
  if(!s.configured){
    el.innerHTML = '<div class="note warn"><i class="fas fa-triangle-exclamation"></i><span>Озвучка не настроена на сервере — голосовые недоступны.</span></div>';
    return;
  }
  const engine = s.engine || 'eleven';
  const isOpenai = engine === 'openai';
  const callLang = s.call_voice && s.call_voice.language;
  const langTitle = c => (ELEVEN_LANGUAGES.find(l => l.code === c) || {}).title || c;
  const langOpts = `<option value="">Как в звонках${callLang ? ' (' + esc(langTitle(callLang)) + ')' : ''}</option>`
    + ELEVEN_LANGUAGES.map(l => `<option value="${l.code}" ${l.code === s.language ? 'selected' : ''}>${l.title}</option>`).join('');
  const stabOpts = `<option value="">Creative — живее, по умолчанию для голосовых</option>`
    + ELEVEN_STABILITY.filter(x => x.v !== 0).map(x => `<option value="${x.v}" ${s.stability != null && x.v === Number(s.stability) ? 'selected' : ''}>${esc(x.title)}</option>`).join('');
  const oaVoices = (s.openai_voices || []).map(v => `<option value="${esc(v)}" ${v === s.openai_voice ? 'selected' : ''}>${esc(v)}</option>`).join('');
  const shareOpts = (s.auto_share_choices || [30, 40, 50]).map(v => `<option value="${v}" ${v === s.auto_share ? 'selected' : ''}>${v}% ответов — голосом</option>`).join('');
  const engines = s.engines || {};
  const tiles = VOICE_MSG_ENGINES.map(e => `<button type="button" class="eng-tile${e.id === engine ? ' on' : ''}" ${engines[e.id] === false ? 'disabled title="Не настроен на сервере"' : ''} onclick="vmSetEngine('${e.id}')">
      <span class="eng-check"><i class="fas fa-check"></i></span>
      <span class="eng-head">${esc(e.title)}</span>
      <span class="eng-desc">${esc(e.hint)}</span>
    </button>`).join('');

  const ch = s.channels || {};
  const chRow = (key, label, icon, color, openFn) => {
    if(!(key in ch)) return `<div class="vm-ch"><i class="${icon}" style="color:${color}"></i> ${label}: <span style="color:var(--muted)">не подключён</span></div>`;
    const mode = ch[key];
    const on = mode !== 'never';
    return `<div class="vm-ch"><i class="${icon}" style="color:${color}"></i> ${label}: <b style="color:${on ? '#166534' : 'var(--muted)'}">${esc(VOICE_MODE_LABELS[mode] || mode)}</b>
      <span class="card-link" onclick="closeInstructionsModal(); ${openFn}()">изменить</span></div>`;
  };
  const noVoice = !s.effective;

  el.innerHTML = `
    <div class="form-group">
      <label class="form-label">Чем озвучивать</label>
      <div class="eng-grid">${tiles}</div>
    </div>
    ${noVoice ? `<div class="note warn"><i class="fas fa-triangle-exclamation"></i><span>${isOpenai ? 'OpenAI-озвучка не настроена на сервере.' : 'Голос не выбран ни здесь, ни для звонков — агент будет отвечать только текстом. Выберите голос ниже.'}</span></div>` : ''}
    <div id="vm-eleven" style="${isOpenai ? 'display:none' : ''}">
      <div class="form-group">
        <label class="form-label">Голос ElevenLabs <span class="hint" tabindex="0" data-hint="По умолчанию — тот же голос, что в звонках. Можно выбрать отдельный, в том числе клон голоса живого человека из вашего аккаунта ElevenLabs."><i class="far fa-circle-question"></i></span></label>
        <select class="form-select" id="vm-voice" disabled><option>Загрузка голосов…</option></select>
        <div class="voice-preview" id="vm-voice-desc" style="display:none"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label class="form-label">Язык озвучки</label><select class="form-select" id="vm-lang" onchange="vmLangChanged()">${langOpts}</select></div>
        <div class="form-group"><label class="form-label">Подача</label><select class="form-select" id="vm-stab" onchange="saveVoiceMessages({stability: _vmStab()})">${stabOpts}</select></div>
      </div>
    </div>
    <div id="vm-openai" style="${isOpenai ? '' : 'display:none'}">
      <div class="form-group">
        <label class="form-label">Голос GPT-Live</label>
        <select class="form-select" id="vm-oa-voice" onchange="saveVoiceMessages({openai_voice: this.value})">${oaVoices}</select>
        <div class="form-hint">Манера задаётся сама: спокойно, негромко, с паузами — как обычное голосовое.</div>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Прослушать</label>
      <textarea class="form-textarea" id="vm-preview-text" rows="2" style="min-height:56px" maxlength="400" placeholder="Свой текст для примера (необязательно). Можно метки: [softly] Так, смотрите… [short pause] доставка завтра."></textarea>
      <div style="display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap">
        <button class="btn btn-secondary btn-sm" id="vm-preview-btn" onclick="previewVoiceMessage()" ${noVoice ? 'disabled' : ''}><i class="fas fa-play"></i> Прослушать голосовое</button>
        <span class="form-hint" style="margin:0">Так клиент услышит ответ агента. Озвучка ≈ ${Number(s.credits_per_400_chars || 0)} кредитов за 30 секунд — и в переписке, и здесь.</span>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Когда агент отвечает голосом</label>
      <div class="vm-channels">
        ${chRow('telegram', 'Telegram', 'fas fa-paper-plane', '#229ED9', 'openTgAccountModal')}
        ${chRow('whatsapp', 'WhatsApp', 'fa-brands fa-whatsapp', '#25D366', 'openWaAccountModal')}
        <div class="vm-ch" style="display:block"><i class="fa-brands fa-instagram" style="color:#E1306C"></i> Instagram: <span style="color:var(--muted)">голосовые недоступны — Instagram через наш коннектор принимает только текст и фото</span></div>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Доля голосовых, когда агент решает сам</label>
      <select class="form-select" id="vm-share" style="max-width:280px" onchange="saveVoiceMessages({auto_share: parseInt(this.value, 10)})">${shareOpts}</select>
      <div class="form-hint">В режиме «агент решает сам» он чередует текст и голосовые примерно в этой пропорции. На голосовое клиента всегда отвечает голосом, короткие «да / минуту» — текстом. Цены, адреса и списки вопросов присылает отдельным текстом, не повторяя голосовое.</div>
    </div>`;
  if(!isOpenai) _vmLoadVoices();
}

async function vmSetEngine(engine){
  if(!voiceMsgState || voiceMsgState.engine === engine) return;
  await saveVoiceMessages({ engine });
  renderVoiceMessages();
}

function _vmStab(){
  const v = document.getElementById('vm-stab')?.value;
  return v === '' || v == null ? null : Number(v);
}

async function _vmLoadVoices(){
  const sel = document.getElementById('vm-voice');
  if(!sel) return;
  const s = voiceMsgState || {};
  const lang = _vmVoiceLang();
  let voices = elevenVoicesCache[lang];
  if(!voices){
    try{
      const r = await apiFetch('/api/eleven-assistants/voices?language=' + encodeURIComponent(lang));
      if(r && r.status === 200){ voices = ((await r.json()).voices) || []; elevenVoicesCache[lang] = voices; }
    }catch(e){}
  }
  voices = voices || [];
  const callId = s.call_voice && s.call_voice.voice_id;
  const callName = (voices.find(v => v.voice_id === callId) || {}).name || (agentData && agentData.eleven_voice_name) || '';
  const opt = v => `<option value="${esc(v.voice_id)}" data-name="${esc(v.name||'')}">${esc(v.name || v.voice_id)}${v.gender ? ' · ' + (v.gender==='female'?'женский':v.gender==='male'?'мужской':v.gender) : ''}</option>`;
  const rec = voices.filter(v => v.recommended), other = voices.filter(v => !v.recommended);
  let html = `<option value="">${callId ? 'Как в звонках' + (callName ? ' — ' + esc(callName) : '') : 'Как в звонках (голос не выбран)'}</option>`;
  if(s.voice_id && !voices.some(v => v.voice_id === s.voice_id)) html += `<option value="${esc(s.voice_id)}" data-name="${esc(s.voice_name||'')}">${esc(s.voice_name || s.voice_id)} · текущий</option>`;
  if(rec.length) html += `<optgroup label="Рекомендуем для языка">${rec.map(opt).join('')}</optgroup>`;
  if(other.length) html += `<optgroup label="${rec.length ? 'Другие голоса' : 'Голоса аккаунта'}">${other.map(opt).join('')}</optgroup>`;
  sel.innerHTML = html;
  sel.value = s.voice_id || '';
  sel.disabled = false;
  sel.onchange = () => {
    _vmShowVoice();
    const o = sel.selectedOptions && sel.selectedOptions[0];
    saveVoiceMessages({ voice_id: sel.value || null, voice_name: (sel.value && o) ? (o.dataset.name || null) : null });
  };
  _vmShowVoice();
}

function _vmShowVoice(){
  const sel = document.getElementById('vm-voice');
  const box = document.getElementById('vm-voice-desc');
  if(!sel || !box) return;
  const s = voiceMsgState || {};
  const id = sel.value || (s.call_voice && s.call_voice.voice_id);
  const v = (elevenVoicesCache[_vmVoiceLang()] || []).find(x => x.voice_id === id);
  if(!v){ box.style.display = 'none'; return; }
  const g = v.gender === 'female' ? GENDER_INFO.f : v.gender === 'male' ? GENDER_INFO.m : GENDER_INFO.n;
  const desc = [v.accent, v.age, v.description].filter(Boolean).join(' · ');
  box.style.display = 'flex';
  box.innerHTML = `<span class="voice-ava ${g.cls}"><i class="fas ${g.icon}"></i></span>
    <div class="voice-pv-txt"><b>${esc(v.name || '')} · ${g.label} голос${sel.value ? '' : ' · как в звонках'}</b><span>${esc(desc || (v.recommended ? 'Подтверждён для выбранного языка' : 'Голос из аккаунта ElevenLabs'))}</span></div>`;
}

async function vmLangChanged(){
  await saveVoiceMessages({ language: document.getElementById('vm-lang')?.value || null });
  _vmLoadVoices();
}

async function saveVoiceMessages(patch){
  try{
    const r = await apiFetch(VOICE_MSG_API, { method: 'PUT', body: JSON.stringify(patch || {}) });
    if(r && r.status === 200){
      voiceMsgState = await r.json();
      showToast('Сохранено', 'success');
      const btn = document.getElementById('vm-preview-btn');
      if(btn) btn.disabled = !voiceMsgState.effective;
      return true;
    }
    const err = await r?.json().catch(()=>({}));
    showToast(errText(err.detail ?? err.message), 'error');
  }catch(e){ showToast('Ошибка сети', 'error'); }
  return false;
}

async function previewVoiceMessage(){
  const s = voiceMsgState || {};
  const btn = document.getElementById('vm-preview-btn');
  if(btn){ btn.disabled = true; btn.innerHTML = '<div class="spinner" style="width:13px;height:13px;border-width:2px"></div> Озвучиваю…'; }
  const sel = document.getElementById('vm-voice');
  const body = {
    text: (document.getElementById('vm-preview-text')?.value || '').trim() || null,
    engine: s.engine || 'eleven',
    voice_id: sel?.value || null,
    language: document.getElementById('vm-lang')?.value || null,
    stability: _vmStab(),
    openai_voice: document.getElementById('vm-oa-voice')?.value || null,
  };
  try{
    const r = await apiFetch(VOICE_MSG_API + '/preview', { method: 'POST', body: JSON.stringify(body) });
    if(r && r.status === 200){
      const blob = await r.blob();
      if(voiceMsgAudio){ voiceMsgAudio.pause(); }
      voiceMsgAudio = new Audio(URL.createObjectURL(blob));
      voiceMsgAudio.play().catch(()=>{});
      if(typeof loadCredits === 'function') loadCredits();
    } else {
      const err = await r?.json().catch(()=>({}));
      const map = { voice_not_selected: 'Сначала выберите голос', tts_not_configured: 'Озвучка не настроена на сервере' };
      showToast(map[err.detail] || 'Не удалось озвучить, попробуйте ещё раз', 'error');
    }
  }catch(e){ showToast('Ошибка сети', 'error'); }
  if(btn){ btn.disabled = false; btn.innerHTML = '<i class="fas fa-play"></i> Прослушать голосовое'; }
}
