/* ============================================================================
 * agent/voice-chat.js — голосовой разговор с агентом (ветка 0811-voice-jarvis), window.VoksyVoice.
 *
 * Полноэкранная «живая» сцена: шар на canvas дышит в такт голосу (вашему — когда агент слушает, агента —
 * когда говорит), цвет по состоянию (слушает / говорит / думает), субтитры, карточки с цифрами и клиентами
 * (поле card в результате функции), быстрые команды, расшифровка, выбор голоса. Стили — /static/css/voice-live.css.
 * Один модуль на кабинет (agent.html → openVoiceChat) и Telegram Mini App (voice-live.html → VoksyVoice.open).
 *
 * Сервер — /ws/agent-voice (backend/websockets/handler_owner_voice.py). Первым сообщением {type:'auth'},
 * дальше микрофон PCM16 бинарными кадрами, назад протокол виджета (response.audio.delta 24 кГц, transcript.delta,
 * conversation.interrupted, gemini.voice_activity, function_call.*), {type:'text.input'} — быстрая команда.
 *
 * Звук (живой тест 08.11.2026: «запинки, как будто связь пропадает»):
 *  - микрофон через AudioWorklet (не зависит от отрисовки), запасной путь — ScriptProcessor;
 *  - эхо-ворота: пока звучит агент, тихие кадры микрофона (эхо из динамиков, которое не догасил браузер) уходят
 *    тишиной — иначе Gemini принимает свой же голос за перебивание и обрывает фразу на полуслове;
 *  - перебивание по детектору речи Google — только если ворота видели настоящую речь владельца;
 *  - адаптивный буфер: кусок пришёл позже, чем доиграл предыдущий, посреди ответа → запас растёт (0,2 → 0,6 с).
 * Классический скрипт, глобально только window.VoksyVoice и обёртки openVoiceChat/closeVoiceChat для agent.html.
 * ========================================================================== */
(function () {
  'use strict';

  const VOICES = [
    { id: 'Kore', g: 'жен.', d: 'уверенный' }, { id: 'Aoede', g: 'жен.', d: 'лёгкий' },
    { id: 'Leda', g: 'жен.', d: 'мягкий' }, { id: 'Zephyr', g: 'жен.', d: 'яркий' },
    { id: 'Callirrhoe', g: 'жен.', d: 'спокойный' }, { id: 'Sulafat', g: 'жен.', d: 'тёплый' },
    { id: 'Puck', g: 'муж.', d: 'бодрый' }, { id: 'Charon', g: 'муж.', d: 'деловой' },
    { id: 'Fenrir', g: 'муж.', d: 'энергичный' }, { id: 'Orus', g: 'муж.', d: 'низкий' },
  ];
  const CHIPS = ['Отчёт за сегодня', 'Кто ждёт ответа?', 'Сколько звонков вчера?', 'Как прошла неделя?', 'Что запланировано на завтра?'];
  const PALETTES = {
    connecting: ['#64748b', '#475569', '#94a3b8'],
    listening: ['#22d3ee', '#3b6dff', '#6366f1'],
    speaking: ['#7aa2ff', '#2a5ce8', '#a855f7'],
    thinking: ['#a78bfa', '#6366f1', '#22d3ee'],
    muted: ['#94a3b8', '#475569', '#334155'],
    error: ['#fb7185', '#e11d48', '#9f1239'],
    idle: ['#64748b', '#334155', '#475569'],
  };
  const MOTION = { // амплитуда «дыхания», скорость
    connecting: [0.02, 0.5], listening: [0.03, 0.9], speaking: [0.045, 1.6], thinking: [0.05, 2.2],
    muted: [0.012, 0.4], error: [0.015, 0.4], idle: [0.015, 0.4],
  };
  const CUSHION_MIN = 0.2, CUSHION_MAX = 0.6, ECHO_TAIL = 0.35, BARGE_MIN_LEFT = 0.35;
  const IC = {
    logo: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"><line x1="4" y1="10" x2="4" y2="14"/><line x1="8" y1="6" x2="8" y2="18"/><line x1="12" y1="3" x2="12" y2="21"/><line x1="16" y1="7" x2="16" y2="17"/><line x1="20" y1="10" x2="20" y2="14"/></svg>',
    cc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 10.5a2 2 0 1 0 0 3M16 10.5a2 2 0 1 0 0 3"/></svg>',
    sliders: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="17" x2="20" y2="17"/><circle cx="9" cy="7" r="2.4" fill="#0b1230"/><circle cx="15" cy="17" r="2.4" fill="#0b1230"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2.5" width="6" height="12" rx="3"/><path d="M5 10.5a7 7 0 0 0 14 0"/><line x1="12" y1="17.5" x2="12" y2="21.5"/></svg>',
    micOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="3" x2="21" y2="21"/><path d="M9 9v1.5a3 3 0 0 0 5.1 2.1M15 9.3V5.5a3 3 0 0 0-5.7-1.3"/><path d="M5 10.5a7 7 0 0 0 11.6 5.3M19 10.5a7 7 0 0 1-.6 2.8"/><line x1="12" y1="17.5" x2="12" y2="21.5"/></svg>',
    end: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 9c-2.6 0-5 .5-7.2 1.4-.7.3-1.1 1-1 1.8l.3 2.1c.1.8.9 1.3 1.7 1.1l2.6-.7c.6-.2 1-.7 1-1.3v-1.6c1.7-.5 3.5-.5 5.2 0v1.6c0 .6.4 1.1 1 1.3l2.6.7c.8.2 1.6-.3 1.7-1.1l.3-2.1c.1-.8-.3-1.5-1-1.8C17 9.5 14.6 9 12 9z"/></svg>',
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="7" x2="20" y2="7"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="17" x2="16" y2="17"/><circle cx="4" cy="7" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="17" r="1"/></svg>',
  };
  const CARD_IC = {
    stats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="6" y1="20" x2="6" y2="12"/><line x1="12" y1="20" x2="12" y2="5"/><line x1="18" y1="20" x2="18" y2="9"/></svg>',
    client: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="8" y1="7" x2="20" y2="7"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="17" x2="16" y2="17"/><circle cx="4" cy="7" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="17" r="1"/></svg>',
    done: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 12.5 10 17 19 7"/></svg>',
  };
  const WORKLET = 'class VvMic extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array(2048);this.n=0}' +
    'process(i){const c=i[0]&&i[0][0];if(c){for(let k=0;k<c.length;k++){this.b[this.n++]=c[k];if(this.n===2048){this.port.postMessage(this.b.slice(0));this.n=0}}}return true}}' +
    "registerProcessor('vv-mic',VvMic);";

  const S = {
    opts: null, el: null, ws: null, ctx: null, stream: null, src: null, node: null, outGain: null, analyser: null,
    nextPlay: 0, sources: [], muted: false, open: false, live: false, closing: false, error: null,
    lines: [], fnRunning: 0, wake: null, raf: null, startedAt: 0, timer: null,
    cushion: CUSHION_MIN, lastChunkAt: 0, inReply: false, underruns: 0,
    speechLvl: 0.06, holdUntil: 0, micLevel: 0, outLevel: 0, lvl: 0,
    lastRole: null, lastAgentAt: 0, agentText: '', ownerText: '', anyOwner: false,
    state: 'connecting', pal: null, palTarget: null, buf: null,
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function toast(msg, type) { if (typeof window.showToast === 'function') window.showToast(msg, type || 'info'); }
  function $(sel) { return S.el ? S.el.querySelector(sel) : null; }
  function hex(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function rgba(c, a) { return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`; }
  function storedVoice() {
    try { const v = localStorage.getItem('vf_owner_voice'); if (VOICES.some(x => x.id === v)) return v; } catch (e) { }
    return 'Kore';
  }

  function fnStatus(name, a) {
    a = a || {};
    const period = { today: 'сегодня', yesterday: 'вчера', week: 'неделю', month: 'месяц', this_month: 'этот месяц' }[a.period] || 'период';
    switch (name) {
      case 'get_stats': return `Считаю цифры за ${period}…`;
      case 'client_info': return a.query ? `Открываю карточку: ${a.query}` : 'Открываю карточку клиента…';
      case 'list_dialogs': return 'Смотрю переписку…';
      case 'get_schedule': return 'Смотрю расписание…';
      case 'ask_agent': return 'Думаю над вопросом…';
      case 'do_action': return 'Выполняю…';
      case 'send_to_telegram': return 'Отправляю в Telegram…';
      default: return 'Работаю…';
    }
  }

  // ------------------------------------------------------------------ разметка
  function buildDom() {
    const el = document.createElement('div');
    el.className = 'vv';
    el.hidden = true;
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Голосовой разговор с агентом');
    el.dataset.captions = '1';
    const voices = VOICES.map(v => `<button class="vv-voice" type="button" role="radio" data-voice="${v.id}">${v.id}<small>${v.g}, ${v.d}</small></button>`).join('');
    const chips = CHIPS.map(c => `<button class="vv-chip" type="button" data-chip="${esc(c)}">${esc(c)}</button>`).join('');
    el.innerHTML = `
      <div class="vv-aurora"></div><div class="vv-grain"></div>
      <div class="vv-top">
        <div class="vv-brand"><span class="vv-logo">${IC.logo}</span>
          <div class="vv-title"><b class="vv-name">Голосовой агент</b><span class="vv-pill"><i></i><span class="vv-pill-t">Подключаюсь…</span></span></div></div>
        <button class="vv-icon-btn vv-cc" type="button" aria-pressed="true" title="Субтитры" aria-label="Субтитры">${IC.cc}</button>
        <button class="vv-icon-btn vv-set" type="button" aria-expanded="false" title="Голос агента" aria-label="Голос агента">${IC.sliders}</button>
        <button class="vv-icon-btn vv-close" type="button" title="Закрыть (Esc)" aria-label="Закрыть">${IC.x}</button>
      </div>
      <div class="vv-menu" hidden role="radiogroup" aria-label="Голос агента"><h4>Голос агента</h4><div class="vv-voices">${voices}</div>
        <p class="vv-menu-note">Голос меняется для нового разговора.</p><p>Тот же разговор есть в Telegram: напишите боту агента /voice — появится кнопка «🎙 Голос».</p></div>
      <div class="vv-alert" hidden></div>
      <div class="vv-stage">
        <div class="vv-orb-wrap"><canvas class="vv-orb" aria-hidden="true"></canvas></div>
        <div class="vv-status" aria-live="polite">Подключаюсь…</div>
        <div class="vv-captions"><div class="vv-cap-owner"></div><div class="vv-cap-agent"></div></div>
      </div>
      <div class="vv-cards" aria-live="polite"></div>
      <div class="vv-chips">${chips}</div>
      <div class="vv-controls">
        <div class="vv-ctrl-w"><button class="vv-ctrl vv-mute" type="button" aria-label="Выключить микрофон" aria-pressed="false">${IC.mic}</button><span class="vv-ctrl-l vv-mute-l">Микрофон</span></div>
        <div class="vv-ctrl-w"><button class="vv-ctrl vv-end" type="button" aria-label="Закончить разговор">${IC.end}</button><span class="vv-ctrl-l">Закончить</span></div>
        <div class="vv-ctrl-w"><button class="vv-ctrl vv-logbtn" type="button" aria-label="Расшифровка разговора">${IC.list}</button><span class="vv-ctrl-l">Текст</span></div>
      </div>
      <div class="vv-log" aria-label="Расшифровка"><div class="vv-log-h">Расшифровка<button class="vv-icon-btn vv-log-x" type="button" aria-label="Закрыть расшифровку">${IC.x}</button></div>
        <div class="vv-log-body"></div></div>`;
    document.body.appendChild(el);
    el.querySelector('.vv-close').addEventListener('click', () => close());
    el.querySelector('.vv-end').addEventListener('click', () => close());
    el.querySelector('.vv-mute').addEventListener('click', toggleMute);
    el.querySelector('.vv-cc').addEventListener('click', e => {
      const on = el.dataset.captions !== '1';
      el.dataset.captions = on ? '1' : '0';
      e.currentTarget.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    const menu = el.querySelector('.vv-menu'), setBtn = el.querySelector('.vv-set');
    setBtn.addEventListener('click', () => { menu.hidden = !menu.hidden; setBtn.setAttribute('aria-expanded', String(!menu.hidden)); markVoice(); });
    el.querySelectorAll('.vv-voice').forEach(b => b.addEventListener('click', () => pickVoice(b.dataset.voice)));
    el.querySelectorAll('.vv-chip').forEach(b => b.addEventListener('click', () => sendText(b.dataset.chip)));
    el.querySelector('.vv-logbtn').addEventListener('click', () => toggleLog(true));
    el.querySelector('.vv-log-x').addEventListener('click', () => toggleLog(false));
    el.addEventListener('click', e => {
      if (!menu.hidden && !menu.contains(e.target) && !setBtn.contains(e.target)) { menu.hidden = true; setBtn.setAttribute('aria-expanded', 'false'); }
    });
    return el;
  }

  function markVoice() {
    const cur = storedVoice();
    S.el.querySelectorAll('.vv-voice').forEach(b => b.setAttribute('aria-checked', b.dataset.voice === cur ? 'true' : 'false'));
  }

  function pickVoice(v) {
    try { localStorage.setItem('vf_owner_voice', v); } catch (e) { }
    markVoice();
    if (S.live && !S.anyOwner) { reconnect(); return; }   // разговор ещё не начат — сразу новым голосом
    const note = $('.vv-menu-note');
    if (note) note.textContent = `Голос ${v} — со следующего разговора.`;
  }

  function toggleLog(open) {
    const log = $('.vv-log');
    if (!log) return;
    log.classList.toggle('vv-open', open);
    if (open) renderLog();
  }

  // ------------------------------------------------------------------ состояние и подписи
  function setState(name) {
    if (!PALETTES[name]) name = 'idle';
    S.state = name;
    S.palTarget = PALETTES[name].map(hex);
    if (!S.pal) S.pal = S.palTarget.map(c => c.slice());
    if (S.el) {
      S.el.style.setProperty('--vv-c1', PALETTES[name][1]);
      S.el.style.setProperty('--vv-c2', PALETTES[name][2]);
    }
  }

  function setStatus(text, state, shimmer) {
    const st = $('.vv-status');
    if (st) { st.textContent = text; st.classList.toggle('vv-shimmer', !!shimmer); }
    if (state) setState(state);
  }

  function idleStatus() {
    if (!S.live) return;
    if (S.fnRunning) return;
    if (S.sources.length) setStatus('Говорит', 'speaking');
    else if (S.muted) setStatus('Микрофон выключен', 'muted');
    else setStatus('Слушаю', 'listening');
  }

  function tick() {
    const t = $('.vv-pill-t');
    if (!t) return;
    if (S.live && S.startedAt) {
      const s = Math.floor((Date.now() - S.startedAt) / 1000);
      t.textContent = `В эфире ${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    } else if (!S.error) t.textContent = S.offline ? 'Нет связи' : 'Подключаюсь…';
    S.el.dataset.live = S.live ? '1' : '0';
  }

  function showAlert(html) {
    const a = $('.vv-alert');
    if (!a) return;
    a.innerHTML = html || '';
    a.hidden = !html;
  }

  // ------------------------------------------------------------------ субтитры и расшифровка
  function addLine(role, delta) {
    const last = S.lines[S.lines.length - 1];
    if (last && last.role === role && role !== 'fn') last.text += delta;
    else S.lines.push({ role, text: role === 'fn' ? delta : delta.replace(/^\s+/, '') });
    if ($('.vv-log.vv-open')) renderLog();
  }

  function renderLog() {
    const body = $('.vv-log-body');
    if (!body) return;
    const lines = S.lines.filter(l => (l.text || '').trim());
    body.innerHTML = lines.length
      ? lines.map(l => `<div class="vv-msg ${l.role}">${esc(l.text.trim())}</div>`).join('')
      : '<div class="vv-log-empty">Здесь появится текст разговора</div>';
    body.scrollTop = body.scrollHeight;
  }

  function onTranscript(role, delta) {
    if (!delta) return;
    const now = Date.now();
    if (role === 'user') {
      if (S.lastRole !== 'user') S.ownerText = '';
      S.ownerText += delta;
      S.anyOwner = true;
      hideChips();
      const o = $('.vv-cap-owner');
      if (o) o.textContent = 'Вы: ' + tail(S.ownerText.trim(), 140);
    } else {
      const fresh = S.lastRole !== 'assistant' || now - S.lastAgentAt > 2500;
      const box = $('.vv-cap-agent');
      if (fresh) { S.agentText = ''; if (box) box.innerHTML = ''; }
      S.agentText += delta;
      S.lastAgentAt = now;
      if (box) {
        const narrow = window.innerWidth < 640, max = narrow ? (S.el.dataset.cards === '1' ? 80 : 150) : 300;
        if (S.agentText.length > max) box.textContent = tail(S.agentText.trim(), max);
        else { const sp = document.createElement('span'); sp.className = 'vv-new'; sp.textContent = fresh ? delta.replace(/^\s+/, '') : delta; box.appendChild(sp); }
      }
    }
    S.lastRole = role;
    addLine(role, delta);
  }

  function tail(s, n) { return s.length > n ? '…' + s.slice(-n) : s; }

  function hideChips() { const c = $('.vv-chips'); if (c) c.hidden = true; }

  function sendText(text) {
    if (!S.ws || S.ws.readyState !== WebSocket.OPEN || !S.live) { toast('Ещё подключаюсь — секунду', 'info'); return; }
    S.ws.send(JSON.stringify({ type: 'text.input', text }));
    S.lastRole = null;
    onTranscript('user', text);
    S.lastRole = 'user';
    stopPlayback();
  }

  // ------------------------------------------------------------------ карточки результатов
  function renderCard(card) {
    const box = $('.vv-cards');
    if (!box || !card || !card.type) return;
    const el = document.createElement('div');
    el.className = 'vv-card' + (card.type === 'done' ? ' vv-done' : '');
    const icon = CARD_IC[card.type] || CARD_IC.list;
    let body = '';
    if (card.type === 'stats') {
      body = `<div class="vv-tiles">${(card.tiles || []).map(t => `<div class="vv-tile"><em>${esc(t.label)}</em><b>${esc(t.value)}</b><small>${esc(t.sub || '')}</small></div>`).join('')}</div>`;
      const bars = card.bars || [];
      if (bars.length > 1) {
        const max = Math.max(1, ...bars.map(b => +b.value || 0));
        const labels = bars.length <= 8;
        body += `<div class="vv-bars${labels ? ' vv-has-labels' : ''}">${bars.map((b, i) =>
          `<div class="vv-bar" style="height:${Math.max(6, Math.round((+b.value || 0) / max * 100))}%;animation-delay:${i * 30}ms" ${labels ? `data-l="${esc(b.label)}"` : ''} title="${esc(b.label)}: ${esc(b.value)}"></div>`).join('')}</div>`;
      }
    } else if (card.type === 'client') {
      body = `${card.subtitle ? `<div class="vv-card-sub">${esc(card.subtitle)}</div>` : ''}<div class="vv-rows">${(card.lines || []).map(l => `<div class="vv-row">${esc(l)}</div>`).join('') || '<div class="vv-row">Переписки пока нет</div>'}</div>`;
    } else if (card.type === 'list') {
      body = `<div class="vv-rows">${(card.items || []).map(i => `<div class="vv-row"><b>${esc(i.title)}</b>${i.meta ? ` · ${esc(i.meta)}` : ''}${i.text ? `<small>${esc(i.text)}</small>` : ''}</div>`).join('') || '<div class="vv-row">Пусто</div>'}</div>`;
    } else {
      body = `<div class="vv-row">${esc(card.text || '')}</div>`;
    }
    el.innerHTML = `<div class="vv-card-h"><span>${icon}</span>${esc(card.title || '')}</div>${body}`;
    box.prepend(el);
    while (box.children.length > 3) box.lastElementChild.remove();
    S.el.dataset.cards = '1';
    box.scrollLeft = 0;
  }

  // ------------------------------------------------------------------ звук: микрофон
  async function startAudio() {
    const AC = window.AudioContext || window.webkitAudioContext;
    S.ctx = new AC({ sampleRate: 24000, latencyHint: 'interactive' });
    if (S.ctx.state === 'suspended') await S.ctx.resume();
    S.stream = await navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    S.outGain = S.ctx.createGain();
    S.analyser = S.ctx.createAnalyser();
    S.analyser.fftSize = 512;
    S.buf = new Uint8Array(S.analyser.fftSize);
    S.outGain.connect(S.analyser);
    S.analyser.connect(S.ctx.destination);
    S.src = S.ctx.createMediaStreamSource(S.stream);
    const sink = S.ctx.createGain(); sink.gain.value = 0; sink.connect(S.ctx.destination);
    let worklet = false;
    if (S.ctx.audioWorklet && window.AudioWorkletNode) {
      try {
        const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
        await S.ctx.audioWorklet.addModule(url);
        S.node = new AudioWorkletNode(S.ctx, 'vv-mic');
        S.node.port.onmessage = e => onMic(e.data);
        worklet = true;
      } catch (e) { worklet = false; }
    }
    if (!worklet) {
      S.node = S.ctx.createScriptProcessor(2048, 1, 1);
      S.node.onaudioprocess = e => onMic(e.inputBuffer.getChannelData(0));
    }
    S.src.connect(S.node);
    S.node.connect(sink);
  }

  function onMic(data) {
    if (!data || !data.length || !S.ctx) return;
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    const now = performance.now();
    const agentActive = S.sources.length > 0 || S.ctx.currentTime < S.nextPlay + ECHO_TAIL;
    if (!agentActive && rms > 0.015) S.speechLvl = S.speechLvl * 0.94 + rms * 0.06;   // обычная громкость речи владельца
    const thr = Math.max(0.012, 0.35 * S.speechLvl);
    let pass = true;
    if (agentActive) {
      if (rms >= thr) S.holdUntil = now + 600;
      else if (now > S.holdUntil) pass = false;    // тихо, пока говорит агент, — это эхо его голоса
    }
    S.micLevel = S.muted ? 0 : rms;
    if (S.muted || !S.live || !S.ws || S.ws.readyState !== WebSocket.OPEN) return;
    const pcm = new Int16Array(data.length);
    if (pass) for (let i = 0; i < data.length; i++) { const s = Math.max(-1, Math.min(1, data[i])); pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
    S.ws.send(pcm.buffer);
  }

  // ------------------------------------------------------------------ звук: воспроизведение
  function schedule(b64) {
    const ctx = S.ctx;
    if (!ctx || !b64) return;
    let bin;
    try { bin = atob(b64); } catch (e) { return; }
    const n = bin.length >> 1;
    if (n < 1) return;
    const buffer = ctx.createBuffer(1, n, 24000);
    const ch = buffer.getChannelData(0);
    for (let i = 0; i < n; i++) {
      let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
      if (v >= 0x8000) v -= 0x10000;
      ch[i] = v / 32768;
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => { });
    const now = ctx.currentTime;
    const t = performance.now();
    if (S.nextPlay < now + 0.02) {
      if (S.inReply && S.lastChunkAt && t - S.lastChunkAt < 900) {
        S.underruns++;                                          // буфер опустел посреди ответа — сеть рвёт поток
        S.cushion = Math.min(CUSHION_MAX, S.cushion + 0.08);
      }
      S.nextPlay = now + S.cushion;
    }
    S.lastChunkAt = t;
    S.inReply = true;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(S.outGain);
    S.sources.push(src);
    src.onended = () => {
      const i = S.sources.indexOf(src);
      if (i > -1) S.sources.splice(i, 1);
      if (!S.sources.length) playbackIdle();
    };
    src.start(S.nextPlay);
    S.nextPlay += buffer.duration;
    if (!S.fnRunning) setStatus('Говорит', 'speaking');
  }

  function playbackIdle() {
    if (S.ws && S.ws.readyState === WebSocket.OPEN) {
      try { S.ws.send(JSON.stringify({ type: 'audio_playback.stopped' })); } catch (e) { }
    }
    idleStatus();
  }

  function stopPlayback() {
    const list = S.sources.slice();
    S.sources = [];
    list.forEach(s => { try { s.onended = null; s.stop(); } catch (e) { } });
    if (S.ctx) S.nextPlay = S.ctx.currentTime;
    S.inReply = false;
    if (list.length) playbackIdle();
  }

  // ------------------------------------------------------------------ сеть
  async function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const q = new URLSearchParams({ voice: storedVoice(), rate: String(Math.round(S.ctx.sampleRate)) });
    if (S.opts.agentId) q.set('agent_id', S.opts.agentId);
    let token = '';
    try { token = typeof S.opts.token === 'function' ? await S.opts.token() : (S.opts.token || ''); } catch (e) { token = ''; }
    S.offline = false;
    const ws = new WebSocket(`${proto}://${location.host}/ws/agent-voice?${q.toString()}`);
    S.ws = ws;
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }));
    ws.onmessage = ev => { let d; try { d = JSON.parse(ev.data); } catch (e) { return; } handle(d); };
    ws.onclose = () => {
      if (S.ws !== ws) return;
      const was = S.live;
      S.live = false;
      S.offline = !S.closing;
      stopPlayback();
      if (S.closing) return;
      if (!S.error) {
        setStatus(was ? 'Разговор завершён' : 'Не удалось подключиться', 'idle');
        if (!was) showAlert('Не удалось подключиться к голосовому агенту. <button type="button" class="vv-retry">Попробовать ещё раз</button>');
        else showAlert('Связь прервалась. <button type="button" class="vv-retry">Продолжить разговор</button>');
        const r = $('.vv-retry');
        if (r) r.addEventListener('click', () => { showAlert(''); reconnect(); });
      }
      tick();
    };
  }

  function reconnect() {
    S.error = null;
    if (S.ws) { const old = S.ws; S.ws = null; try { old.close(); } catch (e) { } }
    S.live = false;
    stopPlayback();
    setStatus('Подключаюсь…', 'connecting');
    connect();
  }

  function handle(d) {
    const t = d.type;
    if (t === 'connection_status' && d.status === 'connected') {
      S.live = true;
      S.startedAt = S.startedAt || Date.now();
      showAlert('');
      setStatus('Слушаю', 'listening');
      tick();
    } else if (t === 'response.audio.delta') {
      schedule(d.delta);
    } else if (t === 'conversation.interrupted') {
      stopPlayback();
    } else if (t === 'gemini.voice_activity') {
      // Google услышал речь: перебиваем доигрывание, только если ворота видели настоящую речь владельца
      if (d.activity === 'start' && S.ctx && performance.now() < S.holdUntil + 200 && S.nextPlay - S.ctx.currentTime >= BARGE_MIN_LEFT) stopPlayback();
    } else if (t === 'assistant.speech.started') {
      S.inReply = true;
    } else if (t === 'assistant.speech.ended' || t === 'turn.saved') {
      S.inReply = false;
      if (!S.underruns && S.cushion > CUSHION_MIN) S.cushion = Math.max(CUSHION_MIN, S.cushion - 0.02);
      S.underruns = 0;
    } else if (t === 'transcript.delta') {
      onTranscript(d.role === 'user' ? 'user' : 'assistant', d.delta || '');
    } else if (t === 'function_call.executing') {
      S.fnRunning++;
      setStatus(fnStatus(d.function, d.arguments), 'thinking', true);
    } else if (t === 'function_call.completed' || t === 'function_call.delivery_error' || t === 'function_call.error') {
      S.fnRunning = Math.max(0, S.fnRunning - 1);
      const r = d.result || {};
      if (r.card) renderCard(r.card);
      if (d.function === 'do_action' && r.ok && r.answer) addLine('fn', '✓ ' + r.answer);
      else if (d.function === 'send_to_telegram' && r.ok) { addLine('fn', '✓ Отправлено в Telegram'); renderCard({ type: 'done', title: 'Отправлено в Telegram', text: 'Отчёт ждёт вас в чате с ботом агента' }); }
      if (!S.fnRunning) { const st = $('.vv-status'); if (st) st.classList.remove('vv-shimmer'); idleStatus(); }
    } else if (t === 'session.limit') {
      toast(d.message || 'Разговор длится слишком долго', 'info');
      close();
    } else if (t === 'error') {
      const err = d.error || {};
      S.error = err.code || 'error';
      setStatus(err.message || 'Ошибка голосового режима', 'error');
      showAlert(esc(err.message || 'Ошибка голосового режима') +
        (err.code === 'insufficient_credits' && S.opts.onCredits ? ' <button type="button" class="vv-credits">Пополнить</button>' : ''));
      const b = $('.vv-credits');
      if (b) b.addEventListener('click', () => { close(); S.opts.onCredits(); });
    }
  }

  // ------------------------------------------------------------------ шар на canvas
  function animate() {
    const canvas = $('.vv-orb');
    if (!canvas) return;
    const c2 = canvas.getContext('2d');
    const t0 = performance.now();
    const frame = () => {
      if (!S.open) { S.raf = null; return; }
      S.raf = requestAnimationFrame(frame);
      if (document.hidden) return;
      const t = (performance.now() - t0) / 1000;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
      c2.setTransform(dpr, 0, 0, dpr, 0, 0);
      c2.clearRect(0, 0, w, h);
      // уровни
      if (S.sources.length && S.analyser) {
        S.analyser.getByteTimeDomainData(S.buf);
        let sum = 0;
        for (let i = 0; i < S.buf.length; i++) { const v = (S.buf[i] - 128) / 128; sum += v * v; }
        S.outLevel = Math.min(1, Math.sqrt(sum / S.buf.length) * 4.5);
      } else S.outLevel *= 0.85;
      const target = S.state === 'speaking' ? S.outLevel : S.state === 'listening' ? Math.min(1, S.micLevel * 7) : 0;
      S.lvl += (target - S.lvl) * 0.2;
      if (S.pal && S.palTarget) for (let k = 0; k < 3; k++) for (let j = 0; j < 3; j++) S.pal[k][j] += (S.palTarget[k][j] - S.pal[k][j]) * 0.06;
      const pal = S.pal || PALETTES.connecting.map(hex);
      const [amp0, sp] = MOTION[S.state] || MOTION.idle;
      const amp = amp0 + S.lvl * 0.15;
      const cx = w / 2, cy = h / 2;
      const R = Math.min(w, h) * 0.3 * (1 + 0.09 * S.lvl + (S.state === 'thinking' ? 0.02 * Math.sin(t * 3) : 0));
      // свечение
      const glow = c2.createRadialGradient(cx, cy, R * 0.3, cx, cy, R * 2.1);
      glow.addColorStop(0, rgba(pal[1], 0.42 + S.lvl * 0.2));
      glow.addColorStop(0.5, rgba(pal[2], 0.12));
      glow.addColorStop(1, rgba(pal[2], 0));
      c2.fillStyle = glow;
      c2.fillRect(0, 0, w, h);
      // слои «жидкого» шара
      c2.globalCompositeOperation = 'lighter';
      for (let k = 0; k < 3; k++) {
        c2.beginPath();
        const N = 96;
        for (let i = 0; i <= N; i++) {
          const a = (i / N) * Math.PI * 2;
          const n = Math.sin(a * 3 + t * sp * (1 + k * 0.3) + k * 1.7) * 0.5 + Math.sin(a * 5 - t * sp * 1.3 + k) * 0.3 + Math.sin(a * 7 + t * sp * 0.7 + k * 2.3) * 0.2;
          const r = R * (0.95 - k * 0.07) * (1 + amp * n);
          const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
          if (i === 0) c2.moveTo(x, y); else c2.lineTo(x, y);
        }
        c2.closePath();
        const ox = Math.cos(t * 0.6 + k * 2.1) * R * 0.25, oy = Math.sin(t * 0.5 + k * 1.3) * R * 0.25;
        const g = c2.createRadialGradient(cx + ox - R * 0.2, cy + oy - R * 0.25, R * 0.05, cx, cy, R * 1.15);
        g.addColorStop(0, rgba(pal[k], 0.95));
        g.addColorStop(0.6, rgba(pal[(k + 1) % 3], 0.7));
        g.addColorStop(1, rgba(pal[(k + 2) % 3], 0.35));
        c2.globalAlpha = 0.5;
        c2.fillStyle = g;
        c2.fill();
      }
      c2.globalCompositeOperation = 'source-over';
      c2.globalAlpha = 1;
      // блик
      const hl = c2.createRadialGradient(cx - R * 0.35, cy - R * 0.42, 0, cx - R * 0.35, cy - R * 0.42, R * 0.7);
      hl.addColorStop(0, 'rgba(255,255,255,.38)');
      hl.addColorStop(1, 'rgba(255,255,255,0)');
      c2.fillStyle = hl;
      c2.beginPath(); c2.arc(cx, cy, R * 0.98, 0, Math.PI * 2); c2.fill();
      // кольца
      if (S.state === 'thinking' || S.state === 'connecting') {
        const rr = R * 1.25, a0 = t * 2.4;
        c2.lineWidth = 2.5; c2.lineCap = 'round';
        c2.strokeStyle = rgba(pal[0], 0.85);
        c2.beginPath(); c2.arc(cx, cy, rr, a0, a0 + 1.5); c2.stroke();
        c2.strokeStyle = rgba(pal[2], 0.35);
        c2.beginPath(); c2.arc(cx, cy, rr, a0 + Math.PI, a0 + Math.PI + 0.8); c2.stroke();
        for (let i = 0; i < 3; i++) {
          const a = t * 1.6 + i * 2.094;
          c2.fillStyle = rgba(pal[i], 0.9);
          c2.beginPath(); c2.arc(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr, 2.6, 0, Math.PI * 2); c2.fill();
        }
      } else if (S.state === 'listening') {
        c2.lineWidth = 1.5;
        c2.strokeStyle = rgba(pal[0], 0.12 + S.lvl * 0.45);
        c2.beginPath(); c2.arc(cx, cy, R * (1.16 + S.lvl * 0.14), 0, Math.PI * 2); c2.stroke();
      }
    };
    if (!S.raf) S.raf = requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ открыть / закрыть
  async function open(opts) {
    if (S.open) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast('Этот браузер не даёт доступ к микрофону', 'error'); return; }
    S.opts = Object.assign({ agentId: null, token: null, title: 'Голосовой агент', onClose: null, onCredits: null, onMicError: null }, opts || {});
    if (!S.el) S.el = buildDom();
    Object.assign(S, { lines: [], fnRunning: 0, muted: false, closing: false, error: null, live: false, nextPlay: 0, sources: [],
      cushion: CUSHION_MIN, underruns: 0, inReply: false, lastRole: null, agentText: '', ownerText: '', anyOwner: false,
      startedAt: 0, lvl: 0, micLevel: 0, outLevel: 0, holdUntil: 0, pal: null });
    S.el.querySelector('.vv-name').textContent = S.opts.title || 'Голосовой агент';
    S.el.querySelector('.vv-cap-owner').textContent = '';
    S.el.querySelector('.vv-cap-agent').textContent = '';
    S.el.querySelector('.vv-cards').innerHTML = '';
    S.el.dataset.cards = '0';
    S.el.querySelector('.vv-chips').hidden = false;
    S.el.querySelector('.vv-log').classList.remove('vv-open');
    const mute = S.el.querySelector('.vv-mute');
    mute.classList.remove('vv-off'); mute.innerHTML = IC.mic; mute.setAttribute('aria-pressed', 'false');
    S.el.querySelector('.vv-mute-l').textContent = 'Микрофон';
    showAlert('');
    S.el.hidden = false;
    requestAnimationFrame(() => S.el.classList.add('vv-in'));
    document.body.classList.add('vv-open');
    S.open = true;
    setStatus('Подключаюсь…', 'connecting');
    animate();
    tick();
    S.timer = setInterval(tick, 1000);
    S.el.querySelector('.vv-end').focus();
    try {
      await startAudio();
    } catch (e) {
      S.error = 'mic';
      setStatus('Нет доступа к микрофону', 'error');
      const extra = S.opts.onMicError ? ' <button type="button" class="vv-mic-help">Открыть в браузере</button>' : '';
      showAlert('Разрешите доступ к микрофону для этого сайта и откройте разговор снова.' + extra);
      const b = $('.vv-mic-help');
      if (b) b.addEventListener('click', () => S.opts.onMicError());
      return;
    }
    connect();
    try { if (navigator.wakeLock) S.wake = await navigator.wakeLock.request('screen'); } catch (e) { }
  }

  function toggleMute() {
    S.muted = !S.muted;
    const b = $('.vv-mute');
    if (b) {
      b.classList.toggle('vv-off', S.muted);
      b.innerHTML = S.muted ? IC.micOff : IC.mic;
      b.setAttribute('aria-pressed', S.muted ? 'true' : 'false');
      b.setAttribute('aria-label', S.muted ? 'Включить микрофон' : 'Выключить микрофон');
    }
    const l = $('.vv-mute-l');
    if (l) l.textContent = S.muted ? 'Выключен' : 'Микрофон';
    if (S.stream) S.stream.getAudioTracks().forEach(tr => { tr.enabled = !S.muted; });
    idleStatus();
  }

  function close() {
    if (!S.open) return;
    S.open = false;
    S.closing = true;
    try { if (S.ws && S.ws.readyState === WebSocket.OPEN) S.ws.send(JSON.stringify({ type: 'session.end' })); } catch (e) { }
    try { S.ws && S.ws.close(); } catch (e) { }
    S.ws = null; S.live = false;
    stopPlayback();
    try { if (S.node) { S.node.onaudioprocess = null; if (S.node.port) S.node.port.onmessage = null; S.node.disconnect(); } } catch (e) { }
    try { S.src && S.src.disconnect(); } catch (e) { }
    try { S.stream && S.stream.getTracks().forEach(tr => tr.stop()); } catch (e) { }
    try { S.ctx && S.ctx.close(); } catch (e) { }
    Object.assign(S, { ctx: null, stream: null, src: null, node: null, outGain: null, analyser: null });
    try { S.wake && S.wake.release(); } catch (e) { }
    S.wake = null;
    clearInterval(S.timer);
    S.el.classList.remove('vv-in');
    setTimeout(() => { if (!S.open) S.el.hidden = true; }, 280);
    document.body.classList.remove('vv-open');
    const lines = S.lines.filter(l => (l.text || '').trim()).map(l => ({ role: l.role, text: l.text.trim() }));
    S.lines = [];
    if (typeof S.opts.onClose === 'function') { try { S.opts.onClose(lines); } catch (e) { } }
  }

  document.addEventListener('keydown', e => {
    if (!S.open) {
      // V — открыть разговор на странице агента (если не печатаем и нет открытых окон)
      const tag = (e.target && e.target.tagName) || '';
      if ((e.key === 'v' || e.key === 'V' || e.key === 'м' || e.key === 'М') && !e.metaKey && !e.ctrlKey && !e.altKey
        && !/INPUT|TEXTAREA|SELECT/.test(tag) && !(e.target && e.target.isContentEditable)
        && document.getElementById('chat-voice') && !document.querySelector('.modal-overlay:not(.hidden)')
        && !document.querySelector('.inbox-overlay.open')) {
        e.preventDefault();
        window.openVoiceChat && window.openVoiceChat();
      }
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      if ($('.vv-log.vv-open')) toggleLog(false);
      else if (!$('.vv-menu').hidden) $('.vv-menu').hidden = true;
      else close();
    } else if ((e.key === 'm' || e.key === 'ь') && !/INPUT|TEXTAREA/.test((e.target && e.target.tagName) || '')) {
      toggleMute();
    }
  });

  window.VoksyVoice = { open, close, isOpen: () => S.open, _handle: d => handle(d) };   // _handle — для проверок вёрстки

  // ------------------------------------------------------------------ кабинет: кнопка «Поговорить» на agent.html
  window.openVoiceChat = function () {
    if (typeof isReactiveMode === 'function' && isReactiveMode()) { toast('Голосовой разговор с агентом — на тарифе Profi', 'error'); return; }
    let title = 'Голосовой агент';
    try { if (typeof agentData !== 'undefined' && agentData && agentData.name && agentData.name !== 'Мой агент') title = agentData.name; } catch (e) { }
    open({
      agentId: (typeof currentAgentId !== 'undefined' && currentAgentId) || null,
      token: () => (typeof getToken === 'function' ? getToken() : localStorage.getItem('auth_token')),
      title,
      onCredits: () => { if (typeof openCreditsModal === 'function') openCreditsModal(); },
      onClose: lines => { toChat(lines); const b = document.getElementById('chat-voice'); if (b) b.focus(); },
    });
  };
  window.closeVoiceChat = close;

  // Разговор остаётся в чате: реплики владельца — чипами, ответы агента — пузырями, сделанное — зелёной строкой.
  function toChat(lines) {
    if (!lines || !lines.length || typeof addBubble !== 'function') return;
    if (typeof hideWelcome === 'function') hideWelcome();
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    const mark = document.createElement('div');
    mark.className = 'vchat-chat-mark';
    mark.textContent = '🎙 Разговор голосом';
    msgs.appendChild(mark);
    lines.forEach(l => {
      if (l.role === 'user' || l.role === 'assistant') addBubble(l.text, l.role);
      else { const d = document.createElement('div'); d.className = 'vchat-chat-fn'; d.textContent = l.text; msgs.appendChild(d); }
    });
    msgs.scrollTop = msgs.scrollHeight;
  }
})();
