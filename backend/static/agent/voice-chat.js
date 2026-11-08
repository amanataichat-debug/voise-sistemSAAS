/* ============================================================================
 * agent/voice-chat.js — Голосовой разговор с агентом (оркестратором), ветка 0811-voice-jarvis.
 * Кнопка «Поговорить» в поле чата → окно с «шаром», стенограммой и статусом.
 * Сервер: /ws/agent-voice (backend/websockets/handler_owner_voice.py) — Gemini Live ведёт разговор,
 * знания и действия — тот же оркестратор, что в текстовом чате. Первым сообщением шлём JWT
 * ({type:'auth'}), дальше — микрофон PCM16 бинарными кадрами, назад — протокол виджета
 * (response.audio.delta 24 кГц, transcript.delta, conversation.interrupted, function_call.*).
 * Звук как в рабочем виджете (widget.js, full-duplex): один AudioContext на 24 кГц, ScriptProcessor,
 * воспроизведение встык с запасом 200 мс, перебивание — сразу останавливаем всё, что запланировано.
 * Классический скрипт (НЕ модуль): глобальные esc/getToken/currentAgentId/showToast из core.js.
 * ========================================================================== */

const VCHAT_VOICES = [
  { id:'Kore', label:'Kore · жен.' },
  { id:'Aoede', label:'Aoede · жен.' },
  { id:'Leda', label:'Leda · жен.' },
  { id:'Zephyr', label:'Zephyr · жен.' },
  { id:'Callirrhoe', label:'Callirrhoe · жен.' },
  { id:'Sulafat', label:'Sulafat · жен.' },
  { id:'Puck', label:'Puck · муж.' },
  { id:'Charon', label:'Charon · муж.' },
  { id:'Fenrir', label:'Fenrir · муж.' },
  { id:'Orus', label:'Orus · муж.' },
];
const VCHAT_CUSHION_SEC = 0.2;          // запас перед первым куском реплики (как у виджета)
const VCHAT_BARGE_MIN_LEFT = 0.35;      // перебивание по детектору речи — если осталось доиграть больше
const VCHAT_FN_STATUS = {
  ask_agent:'Смотрю в системе…',
  do_action:'Выполняю…',
  send_to_telegram:'Отправляю в Telegram…',
};

const vchat = {
  ws:null, ctx:null, stream:null, source:null, proc:null, outGain:null, analyser:null,
  nextPlay:0, sources:[], muted:false, open:false, connected:false, closing:false,
  lines:[], lastRole:null, fnRunning:0, wakeLock:null, raf:null, startedAt:0, error:null,
};

function vchatVoice(){
  try{ const v = localStorage.getItem('vf_owner_voice'); if(VCHAT_VOICES.some(x => x.id===v)) return v; }catch(e){}
  return 'Kore';
}

function vchatEnsureDom(){
  let ov = document.getElementById('vchat-overlay');
  if(ov) return ov;
  ov = document.createElement('div');
  ov.id = 'vchat-overlay';
  ov.className = 'vchat-overlay';
  ov.hidden = true;
  ov.setAttribute('role', 'dialog');
  ov.setAttribute('aria-modal', 'true');
  ov.setAttribute('aria-label', 'Разговор с агентом');
  const opts = VCHAT_VOICES.map(v => `<option value="${v.id}">${esc(v.label)}</option>`).join('');
  ov.innerHTML = `
    <div class="vchat-panel">
      <div class="vchat-head">
        <div class="vchat-title"><span class="vchat-dot" id="vchat-dot"></span>Разговор с агентом</div>
        <select class="vchat-voice" id="vchat-voice" title="Голос агента (со следующего разговора)">${opts}</select>
        <button class="vchat-x" type="button" onclick="closeVoiceChat()" aria-label="Закрыть">✕</button>
      </div>
      <div class="vchat-stage">
        <div class="vchat-orb" id="vchat-orb"><span class="vchat-orb-core"></span><span class="vchat-orb-ring"></span></div>
        <div class="vchat-status" id="vchat-status">Подключаюсь…</div>
      </div>
      <div class="vchat-transcript" id="vchat-transcript" aria-live="polite"></div>
      <div class="vchat-hint">Говорите свободно — можно перебивать. Писать клиентам, звонить и ставить задачи агент будет только после вашего «да».</div>
      <div class="vchat-foot">
        <button class="vchat-btn vchat-mute" id="vchat-mute" type="button" onclick="vchatToggleMute()" title="Выключить микрофон">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="17" x2="12" y2="22"/></svg>
          <span>Микрофон</span>
        </button>
        <button class="vchat-btn vchat-end" type="button" onclick="closeVoiceChat()" title="Закончить разговор">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
          <span>Закончить</span>
        </button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  const sel = ov.querySelector('#vchat-voice');
  sel.value = vchatVoice();
  sel.addEventListener('change', () => {
    try{ localStorage.setItem('vf_owner_voice', sel.value); }catch(e){}
    if(vchat.connected) showToast('Голос сменится со следующего разговора', 'info');
  });
  ov.addEventListener('keydown', e => { if(e.key === 'Escape') closeVoiceChat(); });
  return ov;
}

function vchatStatus(text, state){
  const el = document.getElementById('vchat-status');
  if(el) el.textContent = text;
  const orb = document.getElementById('vchat-orb');
  if(orb && state){ orb.dataset.state = state; }
  const dot = document.getElementById('vchat-dot');
  if(dot) dot.dataset.on = vchat.connected ? '1' : '0';
}

function vchatRenderTranscript(){
  const box = document.getElementById('vchat-transcript');
  if(!box) return;
  box.innerHTML = vchat.lines.slice(-12).map(l => {
    if(l.role === 'fn') return `<div class="vchat-line fn">${esc(l.text)}</div>`;
    return `<div class="vchat-line ${l.role}"><b>${l.role==='user'?'Вы':'Агент'}:</b> ${esc(l.text)}</div>`;
  }).join('');
  box.scrollTop = box.scrollHeight;
}

function vchatAddText(role, delta){
  if(!delta) return;
  const last = vchat.lines[vchat.lines.length - 1];
  if(last && last.role === role){ last.text += delta; }
  else { vchat.lines.push({ role, text: delta.replace(/^\s+/, '') }); }
  vchatRenderTranscript();
}

async function openVoiceChat(){
  if(typeof isReactiveMode === 'function' && isReactiveMode()){
    showToast('Голосовой разговор с агентом — на тарифе Profi', 'error'); return;
  }
  if(vchat.open) return;
  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    showToast('Этот браузер не даёт доступ к микрофону', 'error'); return;
  }
  const ov = vchatEnsureDom();
  Object.assign(vchat, { lines:[], lastRole:null, fnRunning:0, muted:false, closing:false, error:null,
                         connected:false, nextPlay:0, sources:[] });
  vchatRenderTranscript();
  ov.hidden = false;
  vchat.open = true;
  document.body.classList.add('vchat-on');
  vchatStatus('Подключаюсь…', 'connecting');
  const mute = document.getElementById('vchat-mute');
  if(mute) mute.classList.remove('on');
  try{
    await vchatStartAudio();
  }catch(e){
    vchatStatus('Нет доступа к микрофону — разрешите его в браузере', 'error');
    vchat.error = 'mic';
    return;
  }
  vchatConnect();
  try{ if(navigator.wakeLock) vchat.wakeLock = await navigator.wakeLock.request('screen'); }catch(e){}
  ov.querySelector('.vchat-end')?.focus();
}

async function vchatStartAudio(){
  const AC = window.AudioContext || window.webkitAudioContext;
  vchat.ctx = new AC({ sampleRate:24000, latencyHint:'interactive' });
  if(vchat.ctx.state === 'suspended') await vchat.ctx.resume();
  vchat.stream = await navigator.mediaDevices.getUserMedia({ audio:{
    echoCancellation:true, noiseSuppression:true, autoGainControl:true, channelCount:1, sampleRate:24000 } });
  vchat.outGain = vchat.ctx.createGain();
  vchat.analyser = vchat.ctx.createAnalyser();
  vchat.analyser.fftSize = 512;
  vchat.outGain.connect(vchat.analyser);
  vchat.analyser.connect(vchat.ctx.destination);
  vchat.source = vchat.ctx.createMediaStreamSource(vchat.stream);
  vchat.proc = vchat.ctx.createScriptProcessor(2048, 1, 1);
  vchat.micLevel = 0;
  vchat.proc.onaudioprocess = e => {
    const data = e.inputBuffer.getChannelData(0);
    let sum = 0;
    const pcm = new Int16Array(data.length);
    for(let i=0; i<data.length; i++){
      const s = Math.max(-1, Math.min(1, data[i]));
      sum += s*s;
      pcm[i] = s < 0 ? s*0x8000 : s*0x7fff;
    }
    vchat.micLevel = vchat.muted ? 0 : Math.sqrt(sum / data.length);
    if(vchat.muted || !vchat.connected || !vchat.ws || vchat.ws.readyState !== WebSocket.OPEN) return;
    vchat.ws.send(pcm.buffer);
  };
  vchat.source.connect(vchat.proc);
  // ScriptProcessor работает, только если подключён к выходу; тишина — через нулевое усиление
  const sink = vchat.ctx.createGain(); sink.gain.value = 0;
  vchat.proc.connect(sink); sink.connect(vchat.ctx.destination);
  vchatAnimate();
}

function vchatConnect(){
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const q = new URLSearchParams({ voice: vchatVoice(), rate: String(Math.round(vchat.ctx.sampleRate)) });
  if(typeof currentAgentId !== 'undefined' && currentAgentId) q.set('agent_id', currentAgentId);
  const ws = new WebSocket(`${proto}://${location.host}/ws/agent-voice?${q.toString()}`);
  ws.binaryType = 'arraybuffer';
  vchat.ws = ws;
  ws.onopen = () => { ws.send(JSON.stringify({ type:'auth', token:getToken() })); };
  ws.onmessage = ev => {
    let data;
    try{ data = JSON.parse(ev.data); }catch(e){ return; }
    vchatHandle(data);
  };
  ws.onclose = () => {
    const was = vchat.connected;
    vchat.connected = false;
    if(vchat.closing) return;
    if(!vchat.error) vchatStatus(was ? 'Разговор завершён' : 'Не удалось подключиться — попробуйте ещё раз', 'idle');
    vchatStopPlayback();
  };
  ws.onerror = () => {};
}

function vchatHandle(data){
  const t = data.type;
  if(t === 'connection_status' && data.status === 'connected'){
    vchat.connected = true; vchat.startedAt = Date.now();
    vchatStatus('Слушаю', 'listening');
  } else if(t === 'response.audio.delta'){
    vchatSchedule(data.delta);
  } else if(t === 'conversation.interrupted'){
    vchatStopPlayback();
  } else if(t === 'gemini.voice_activity'){
    if(data.activity === 'start' && vchat.ctx && vchat.nextPlay - vchat.ctx.currentTime >= VCHAT_BARGE_MIN_LEFT){
      vchatStopPlayback();   // владелец заговорил поверх ответа — замолкаем, как живой собеседник
    }
  } else if(t === 'transcript.delta'){
    vchatAddText(data.role === 'user' ? 'user' : 'assistant', data.delta || '');
  } else if(t === 'function_call.executing' || t === 'function_call.started'){
    if(t === 'function_call.executing'){
      vchat.fnRunning++;
      vchatStatus(VCHAT_FN_STATUS[data.function] || 'Работаю…', 'thinking');
    }
  } else if(t === 'function_call.completed' || t === 'function_call.delivery_error' || t === 'function_call.error'){
    vchat.fnRunning = Math.max(0, vchat.fnRunning - 1);
    const r = data.result || {};
    if(data.function === 'do_action' && r.ok && r.answer) vchat.lines.push({ role:'fn', text:'✓ ' + r.answer });
    else if(data.function === 'send_to_telegram' && r.ok) vchat.lines.push({ role:'fn', text:'✓ Отправлено в Telegram' });
    vchatRenderTranscript();
    if(!vchat.fnRunning) vchatStatus(vchat.sources.length ? 'Говорит' : 'Слушаю', vchat.sources.length ? 'speaking' : 'listening');
  } else if(t === 'session.limit'){
    showToast(data.message || 'Разговор длится слишком долго', 'info');
    closeVoiceChat();
  } else if(t === 'error'){
    const err = data.error || {};
    vchat.error = err.code || 'error';
    vchatStatus(err.message || 'Ошибка голосового режима', 'error');
    if(err.code === 'insufficient_credits' && typeof openCreditsModal === 'function') setTimeout(openCreditsModal, 600);
  }
}

function vchatSchedule(b64){
  const ctx = vchat.ctx;
  if(!ctx || !b64) return;
  let bin;
  try{ bin = atob(b64); }catch(e){ return; }
  const n = Math.floor(bin.length / 2);
  if(n < 1) return;
  const buffer = ctx.createBuffer(1, n, 24000);
  const ch = buffer.getChannelData(0);
  for(let i=0; i<n; i++){
    let v = bin.charCodeAt(2*i) | (bin.charCodeAt(2*i+1) << 8);
    if(v >= 0x8000) v -= 0x10000;
    ch[i] = v / 32768;
  }
  if(ctx.state === 'suspended') ctx.resume().catch(()=>{});
  const now = ctx.currentTime;
  if(vchat.nextPlay < now + 0.02) vchat.nextPlay = now + VCHAT_CUSHION_SEC;
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(vchat.outGain);
  vchat.sources.push(src);
  src.onended = () => {
    const i = vchat.sources.indexOf(src);
    if(i > -1) vchat.sources.splice(i, 1);
    if(!vchat.sources.length) vchatPlaybackIdle();
  };
  src.start(vchat.nextPlay);
  vchat.nextPlay += buffer.duration;
  if(!vchat.fnRunning) vchatStatus('Говорит', 'speaking');
}

function vchatPlaybackIdle(){
  if(vchat.ws && vchat.ws.readyState === WebSocket.OPEN){
    try{ vchat.ws.send(JSON.stringify({ type:'audio_playback.stopped' })); }catch(e){}
  }
  if(vchat.connected && !vchat.fnRunning) vchatStatus(vchat.muted ? 'Микрофон выключен' : 'Слушаю', 'listening');
}

function vchatStopPlayback(){
  const list = vchat.sources.slice();
  vchat.sources = [];
  list.forEach(s => { try{ s.onended = null; s.stop(); }catch(e){} });
  if(vchat.ctx) vchat.nextPlay = vchat.ctx.currentTime;
  if(list.length) vchatPlaybackIdle();
}

function vchatToggleMute(){
  vchat.muted = !vchat.muted;
  const b = document.getElementById('vchat-mute');
  if(b){ b.classList.toggle('on', vchat.muted); b.title = vchat.muted ? 'Включить микрофон' : 'Выключить микрофон'; }
  if(vchat.stream) vchat.stream.getAudioTracks().forEach(t => { t.enabled = !vchat.muted; });
  if(vchat.connected && !vchat.sources.length && !vchat.fnRunning) vchatStatus(vchat.muted ? 'Микрофон выключен' : 'Слушаю', 'listening');
}

function vchatAnimate(){
  const orb = document.getElementById('vchat-orb');
  const buf = new Uint8Array(256);
  const tick = () => {
    if(!vchat.open){ vchat.raf = null; return; }
    let lvl = 0;
    if(vchat.sources.length && vchat.analyser){
      vchat.analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for(let i=0; i<buf.length; i++){ const v = (buf[i]-128)/128; sum += v*v; }
      lvl = Math.min(1, Math.sqrt(sum/buf.length) * 4);
    } else {
      lvl = Math.min(1, (vchat.micLevel || 0) * 6);
    }
    if(orb){
      const prev = parseFloat(orb.style.getPropertyValue('--lvl')) || 0;
      orb.style.setProperty('--lvl', (prev*0.7 + lvl*0.3).toFixed(3));
    }
    vchat.raf = requestAnimationFrame(tick);
  };
  if(!vchat.raf) vchat.raf = requestAnimationFrame(tick);
}

function closeVoiceChat(){
  if(!vchat.open) return;
  vchat.open = false;
  vchat.closing = true;
  try{ if(vchat.ws && vchat.ws.readyState === WebSocket.OPEN) vchat.ws.send(JSON.stringify({ type:'session.end' })); }catch(e){}
  try{ vchat.ws && vchat.ws.close(); }catch(e){}
  vchat.ws = null; vchat.connected = false;
  vchatStopPlayback();
  try{ vchat.proc && (vchat.proc.onaudioprocess = null); vchat.proc && vchat.proc.disconnect(); }catch(e){}
  try{ vchat.source && vchat.source.disconnect(); }catch(e){}
  try{ vchat.stream && vchat.stream.getTracks().forEach(t => t.stop()); }catch(e){}
  try{ vchat.ctx && vchat.ctx.close(); }catch(e){}
  Object.assign(vchat, { ctx:null, stream:null, source:null, proc:null, outGain:null, analyser:null });
  try{ vchat.wakeLock && vchat.wakeLock.release(); }catch(e){}
  vchat.wakeLock = null;
  const ov = document.getElementById('vchat-overlay');
  if(ov) ov.hidden = true;
  document.body.classList.remove('vchat-on');
  vchatToChat();
  document.getElementById('chat-voice')?.focus();
}

// Разговор остаётся в чате: реплики владельца — чипами, ответы агента — пузырями с пометкой «голосом».
function vchatToChat(){
  const lines = vchat.lines.filter(l => (l.text || '').trim());
  if(!lines.length) return;
  if(typeof hideWelcome === 'function') hideWelcome();
  const msgs = document.getElementById('chat-messages');
  if(!msgs) return;
  const mark = document.createElement('div');
  mark.className = 'vchat-chat-mark';
  mark.textContent = '🎙 Разговор голосом';
  msgs.appendChild(mark);
  lines.forEach(l => {
    if(l.role === 'user') addBubble(l.text.trim(), 'user');
    else if(l.role === 'assistant') addBubble(l.text.trim(), 'assistant');
    else {
      const d = document.createElement('div');
      d.className = 'vchat-chat-fn';
      d.textContent = l.text;
      msgs.appendChild(d);
    }
  });
  msgs.scrollTop = msgs.scrollHeight;
  vchat.lines = [];
}

window.openVoiceChat = openVoiceChat;
window.closeVoiceChat = closeVoiceChat;
window.vchatToggleMute = vchatToggleMute;
