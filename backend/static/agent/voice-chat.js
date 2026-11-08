/* ============================================================================
 * agent/voice-chat.js — голосовой разговор с агентом (ветка 0811-voice-jarvis), window.VoksyVoice.
 *
 * Полноэкранная «живая» сцена: шар на canvas дышит в такт голосу (вашему — когда агент слушает, агента —
 * когда говорит), цвет по состоянию, субтитры, карточки (дашборд дня в начале + результаты функций: цифры
 * с графиком, клиент, списки, воронка), быстрые команды, расшифровка, голос и ОБРАЩЕНИЕ («по имени / шеф /
 * босс / своё», «на вы / на ты» — сохраняется на сервере и сразу применяется в разговоре). Тап по шару —
 * прервать агента. Стили — /static/css/voice-live.css. Один модуль на кабинет (agent.html → openVoiceChat)
 * и Telegram Mini App (voice-live.html → VoksyVoice.open).
 *
 * Сервер — /ws/agent-voice (backend/websockets/handler_owner_voice.py): первым сообщением {type:'auth'},
 * дальше микрофон PCM16 16 кГц бинарными кадрами; назад протокол виджета (response.audio.delta 24 кГц,
 * transcript.delta, conversation.interrupted, gemini.voice_activity, function_call.*) + owner.dashboard,
 * prefs.saved. Сюда: text.input (быстрая команда), prefs.save, audio_playback.stopped, session.end.
 *
 * Звук v2 (живой тест 08.11.2026: «запинки, голос прерывается, особенно в Telegram»):
 *  - AudioContext на родной частоте устройства (24 кГц насильно давал лишнюю пересборку звука на телефонах);
 *  - микрофон: фильтр 7 кГц → AudioWorklet «vv-mic» пересобирает в 16 кГц и отдаёт PCM16 по 40 мс;
 *  - воспроизведение: AudioWorklet «vv-out» с собственным буфером (не сотни запланированных кусочков):
 *    стартует, когда накопил запас (адаптивный 0,18 → 0,6 с), в конце ответа доигрывает остаток,
 *    при недоборе посреди ответа сам ставит паузу и копит запас заново — без щелчков и рваных слов;
 *  - эхо-ворота: пока звучит агент, тихие кадры микрофона уходят тишиной (эхо из динамиков);
 *  - нет AudioWorklet (старые браузеры) — прежний путь: ScriptProcessor и AudioBufferSourceNode.
 * Плавность: шар без перерисовки свечения (свечение — CSS), разрешение холста по устройству, автоснижение
 * качества при медленных кадрах; статус не дёргается между кусочками звука (задержка 0,45 с).
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
  const CHIPS = ['Что нового?', 'Кто ждёт ответа?', 'Сколько звонков вчера?', 'Как прошла неделя?', 'Что запланировано на завтра?'];
  const ADDRESS = [{ id: 'name', t: 'По имени' }, { id: 'chef', t: 'Шеф' }, { id: 'boss', t: 'Босс' }, { id: 'custom', t: 'Своё' }];
  const PALETTES = {
    connecting: ['#64748b', '#475569', '#94a3b8'], listening: ['#22d3ee', '#3b6dff', '#6366f1'],
    speaking: ['#7aa2ff', '#2a5ce8', '#a855f7'], thinking: ['#a78bfa', '#6366f1', '#22d3ee'],
    muted: ['#94a3b8', '#475569', '#334155'], error: ['#fb7185', '#e11d48', '#9f1239'], idle: ['#64748b', '#334155', '#475569'],
  };
  const MOTION = { connecting: [0.02, 0.5], listening: [0.03, 0.9], speaking: [0.045, 1.6], thinking: [0.05, 2.2],
    muted: [0.012, 0.4], error: [0.015, 0.4], idle: [0.015, 0.4] };
  const PREBUF_MIN = 0.18, PREBUF_MAX = 0.6, ECHO_TAIL_MS = 350, BARGE_MIN_LEFT = 0.35, DRAIN_AFTER_MS = 280;
  const IC = {
    logo: '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"><line x1="4" y1="10" x2="4" y2="14"/><line x1="8" y1="6" x2="8" y2="18"/><line x1="12" y1="3" x2="12" y2="21"/><line x1="16" y1="7" x2="16" y2="17"/><line x1="20" y1="10" x2="20" y2="14"/></svg>',
    cc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 10.5a2 2 0 1 0 0 3M16 10.5a2 2 0 1 0 0 3"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="17" x2="20" y2="17"/><circle cx="9" cy="7" r="2.4" fill="#0b1230"/><circle cx="15" cy="17" r="2.4" fill="#0b1230"/></svg>',
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
    funnel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5h18l-7 8v6l-4-2v-4z"/></svg>',
    done: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 12.5 10 17 19 7"/></svg>',
  };
  const CH_DOT = { whatsapp: 'wa', telegram: 'tg', instagram: 'ig' };

  // ---------------------------------------------------------------- аудиоворклеты (строки → Blob)
  const MIC_WORKLET = `
class VvMic extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.t = 0; this.prev = 0; this.out = new Int16Array(640); this.n = 0; this.sum = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || !ch.length) return true;
    const len = ch.length; let t = this.t;
    while (t < len - 1) {
      const i = Math.floor(t), f = t - i;
      const s0 = i < 0 ? this.prev : ch[i], s1 = ch[i + 1];
      let v = s0 + (s1 - s0) * f;
      this.sum += v * v;
      v = v < -1 ? -1 : v > 1 ? 1 : v;
      this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
      if (this.n === 640) {
        const rms = Math.sqrt(this.sum / 640);
        this.port.postMessage({ pcm: this.out.buffer, rms: rms }, [this.out.buffer]);
        this.out = new Int16Array(640); this.n = 0; this.sum = 0;
      }
      t += this.ratio;
    }
    this.t = t - len; this.prev = ch[len - 1];
    return true;
  }
}
registerProcessor('vv-mic', VvMic);`;
  const OUT_WORKLET = `
class VvOut extends AudioWorkletProcessor {
  constructor() {
    super(); this.q = []; this.cur = null; this.pos = 0; this.buffered = 0; this.prebuf = Math.round(sampleRate * ${PREBUF_MIN});
    this.playing = false; this.drain = false;
    this.port.onmessage = (e) => {
      const d = e.data;
      if (d.t === 'push') { this.q.push(d.s); this.buffered += d.s.length; this.drain = false; }
      else if (d.t === 'drain') { this.drain = true; }
      else if (d.t === 'prebuf') { this.prebuf = Math.round(sampleRate * d.v); }
      else if (d.t === 'clear') {
        const left = this.buffered / sampleRate;
        this.q = []; this.cur = null; this.pos = 0; this.buffered = 0; this.drain = false;
        if (this.playing) { this.playing = false; this.port.postMessage({ t: 'state', playing: false, cleared: true, left: left }); }
      }
    };
  }
  process(inputs, outputs) {
    const outs = outputs[0]; const out = outs[0];
    if (!out) return true;
    if (!this.playing && this.buffered > 0 && (this.buffered >= this.prebuf || this.drain)) {
      this.playing = true; this.port.postMessage({ t: 'state', playing: true });
    }
    if (this.playing) {
      for (let i = 0; i < out.length; i++) {
        if (!this.cur || this.pos >= this.cur.length) { this.cur = this.q.shift() || null; this.pos = 0; }
        if (!this.cur) {
          for (let j = i; j < out.length; j++) out[j] = 0;
          this.playing = false; this.buffered = 0;
          this.port.postMessage({ t: 'state', playing: false, underrun: !this.drain });
          break;
        }
        out[i] = this.cur[this.pos++]; this.buffered--;
      }
    } else { out.fill(0); }
    for (let c = 1; c < outs.length; c++) outs[c].set(out);
    return true;
  }
}
registerProcessor('vv-out', VvOut);`;

  const TG = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) ? window.Telegram.WebApp : null;

  const S = {
    opts: null, el: null, ws: null, ctx: null, stream: null, src: null, lp: null, mic: null, out: null, analyser: null, buf: null,
    worklet: false, sources: [], nextPlay: 0, rsT: 0, rsPrev: 0, micT: 0, micPrev: 0,
    agentPlaying: false, agentEndAt: 0, prebuf: PREBUF_MIN, inReply: false, underruns: 0, drainT: null, skipTurn: false,
    muted: false, open: false, live: false, closing: false, error: null, offline: false,
    lines: [], fnRunning: 0, wake: null, raf: null, startedAt: 0, timer: null, idleT: null,
    speechLvl: 0.06, holdUntil: 0, micLevel: 0, outLevel: 0, lvl: 0,
    lastRole: null, lastAgentAt: 0, agentText: '', ownerText: '', anyOwner: false,
    state: 'connecting', statusText: '', shimmer: false, pal: null, palTarget: null, quality: 2, glowEl: null,
    prefs: { address: 'name', formality: 'vy' }, ownerName: '', pendingPrefs: null,
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function toast(msg, type) { if (typeof window.showToast === 'function') window.showToast(msg, type || 'info'); }
  function $(sel) { return S.el ? S.el.querySelector(sel) : null; }
  function hex(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function rgba(c, a) { return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + a + ')'; }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
  function storedVoice() { const v = lsGet('vf_owner_voice'); return VOICES.some(x => x.id === v) ? v : ''; }
  function haptic(kind) {
    if (!TG || !TG.HapticFeedback) return;
    try { if (kind === 'success') TG.HapticFeedback.notificationOccurred('success'); else TG.HapticFeedback.impactOccurred(kind || 'light'); } catch (e) { }
  }
  function initials(name) {
    const p = String(name || '?').trim().split(/\s+/);
    return ((p[0] || '?')[0] + (p[1] ? p[1][0] : '')).toUpperCase();
  }

  function fnStatus(name, a) {
    a = a || {};
    const period = { today: 'сегодня', yesterday: 'вчера', week: 'неделю', month: 'месяц', this_month: 'этот месяц' }[a.period] || 'период';
    switch (name) {
      case 'get_stats': return 'Считаю цифры за ' + period + '…';
      case 'client_info': return a.query ? 'Открываю карточку: ' + a.query : 'Открываю карточку клиента…';
      case 'list_dialogs': return 'Смотрю переписку…';
      case 'get_schedule': return 'Смотрю расписание…';
      case 'ask_agent': return 'Думаю над вопросом…';
      case 'do_action': return 'Выполняю…';
      case 'send_to_telegram': return 'Отправляю в Telegram…';
      default: return 'Работаю…';
    }
  }

  // ---------------------------------------------------------------- разметка
  function buildDom() {
    const el = document.createElement('div');
    el.className = 'vv';
    el.hidden = true;
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Голосовой разговор с агентом');
    el.dataset.captions = '1';
    const voices = VOICES.map(v => '<button class="vv-voice" type="button" role="radio" data-voice="' + v.id + '">' + v.id + '<small>' + v.g + ', ' + v.d + '</small></button>').join('');
    const addr = ADDRESS.map(a => '<button type="button" role="radio" data-addr="' + a.id + '">' + a.t + '</button>').join('');
    const chips = CHIPS.map(c => '<button class="vv-chip" type="button" data-chip="' + esc(c) + '">' + esc(c) + '</button>').join('');
    el.innerHTML =
      '<div class="vv-aurora"><i></i><i></i></div>' +
      '<div class="vv-top"><div class="vv-brand"><span class="vv-logo">' + IC.logo + '</span>' +
        '<div class="vv-title"><b class="vv-name">Голосовой агент</b><span class="vv-pill"><i></i><span class="vv-pill-t">Подключаюсь…</span></span></div></div>' +
        '<button class="vv-icon-btn vv-cc" type="button" aria-pressed="true" title="Субтитры" aria-label="Субтитры">' + IC.cc + '</button>' +
        '<button class="vv-icon-btn vv-set" type="button" aria-expanded="false" title="Голос и обращение" aria-label="Голос и обращение">' + IC.gear + '</button>' +
        '<button class="vv-icon-btn vv-close" type="button" title="Закрыть (Esc)" aria-label="Закрыть">' + IC.x + '</button></div>' +
      '<div class="vv-menu" hidden>' +
        '<h4>Как обращаться ко мне</h4><div class="vv-seg vv-addr" role="radiogroup" aria-label="Обращение">' + addr + '</div>' +
        '<input class="vv-field vv-addr-text" type="text" maxlength="40" autocomplete="off" aria-label="Имя или обращение">' +
        '<div class="vv-seg vv-form" role="radiogroup" aria-label="На вы или на ты"><button type="button" role="radio" data-form="vy">На «вы»</button><button type="button" role="radio" data-form="ty">На «ты»</button></div>' +
        '<h4 style="margin-top:14px">Голос агента</h4><div class="vv-voices" role="radiogroup" aria-label="Голос агента">' + voices + '</div>' +
        '<button class="vv-save" type="button">Сохранить</button>' +
        '<p class="vv-menu-note">Обращение меняется сразу, голос — с нового разговора. Настройки общие для кабинета и Telegram.</p>' +
        '<p>В Telegram: кнопка «🎙 Поговорить с агентом» слева от поля ввода в боте агента.</p></div>' +
      '<div class="vv-alert" hidden></div>' +
      '<div class="vv-stage">' +
        '<button class="vv-orb-wrap" type="button" aria-label="Прервать агента"><span class="vv-glow"></span><canvas class="vv-orb" aria-hidden="true"></canvas></button>' +
        '<div class="vv-status" aria-live="polite">Подключаюсь…</div>' +
        '<div class="vv-captions"><div class="vv-cap-owner"></div><div class="vv-cap-agent"></div></div></div>' +
      '<div class="vv-cards" aria-live="polite"></div>' +
      '<div class="vv-chips">' + chips + '</div>' +
      '<div class="vv-controls">' +
        '<div class="vv-ctrl-w"><button class="vv-ctrl vv-mute" type="button" aria-label="Выключить микрофон" aria-pressed="false">' + IC.mic + '</button><span class="vv-ctrl-l vv-mute-l">Микрофон</span></div>' +
        '<div class="vv-ctrl-w"><button class="vv-ctrl vv-end" type="button" aria-label="Закончить разговор">' + IC.end + '</button><span class="vv-ctrl-l">Закончить</span></div>' +
        '<div class="vv-ctrl-w"><button class="vv-ctrl vv-logbtn" type="button" aria-label="Расшифровка разговора">' + IC.list + '</button><span class="vv-ctrl-l">Текст</span></div></div>' +
      '<div class="vv-log" aria-label="Расшифровка"><div class="vv-log-h">Расшифровка<button class="vv-icon-btn vv-log-x" type="button" aria-label="Закрыть расшифровку">' + IC.x + '</button></div><div class="vv-log-body"></div></div>';
    document.body.appendChild(el);
    S.glowEl = el.querySelector('.vv-glow');
    el.querySelector('.vv-close').addEventListener('click', () => close());
    el.querySelector('.vv-end').addEventListener('click', () => close());
    el.querySelector('.vv-mute').addEventListener('click', toggleMute);
    el.querySelector('.vv-orb-wrap').addEventListener('click', interruptAgent);
    el.querySelector('.vv-cc').addEventListener('click', e => {
      const on = el.dataset.captions !== '1';
      el.dataset.captions = on ? '1' : '0';
      e.currentTarget.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    const menu = el.querySelector('.vv-menu'), setBtn = el.querySelector('.vv-set');
    setBtn.addEventListener('click', () => { if (menu.hidden) openMenu(); else closeMenu(); });
    el.querySelectorAll('.vv-voice').forEach(b => b.addEventListener('click', () => markMenu({ voice: b.dataset.voice })));
    el.querySelectorAll('[data-addr]').forEach(b => b.addEventListener('click', () => markMenu({ address: b.dataset.addr })));
    el.querySelectorAll('[data-form]').forEach(b => b.addEventListener('click', () => markMenu({ formality: b.dataset.form })));
    el.querySelector('.vv-save').addEventListener('click', savePrefs);
    el.querySelectorAll('.vv-chip').forEach(b => b.addEventListener('click', () => sendText(b.dataset.chip)));
    el.querySelector('.vv-logbtn').addEventListener('click', () => toggleLog(true));
    el.querySelector('.vv-log-x').addEventListener('click', () => toggleLog(false));
    el.addEventListener('pointerdown', e => { if (!menu.hidden && !menu.contains(e.target) && !setBtn.contains(e.target)) closeMenu(); });
    return el;
  }

  // ---------------------------------------------------------------- настройки: обращение и голос
  let draft = null;
  function openMenu() {
    draft = Object.assign({ address: 'name', formality: 'vy', address_text: '' }, S.prefs || {});
    if (!draft.voice) draft.voice = storedVoice() || 'Kore';
    const field = $('.vv-addr-text');
    if (field) { field.hidden = true; field.value = draft.address_text || ''; }
    $('.vv-menu').hidden = false;
    $('.vv-set').setAttribute('aria-expanded', 'true');
    markMenu({});
  }
  function closeMenu() { $('.vv-menu').hidden = true; $('.vv-set').setAttribute('aria-expanded', 'false'); }
  function markMenu(patch) {
    if (!draft) return;
    const field = $('.vv-addr-text');
    if (field && !field.hidden) draft.address_text = field.value.trim();
    Object.assign(draft, patch);
    S.el.querySelectorAll('[data-addr]').forEach(b => b.setAttribute('aria-checked', b.dataset.addr === draft.address ? 'true' : 'false'));
    S.el.querySelectorAll('[data-form]').forEach(b => b.setAttribute('aria-checked', b.dataset.form === draft.formality ? 'true' : 'false'));
    S.el.querySelectorAll('.vv-voice').forEach(b => b.setAttribute('aria-checked', b.dataset.voice === draft.voice ? 'true' : 'false'));
    const showField = draft.address === 'name' || draft.address === 'custom';
    field.hidden = !showField;
    field.placeholder = draft.address === 'custom' ? 'Как обращаться: например, «Аман-ака»' : ('Имя' + (S.ownerName ? ' (сейчас: ' + S.ownerName + ')' : ''));
    if (patch.address) field.value = (draft.address === (S.prefs || {}).address ? (S.prefs.address_text || '') : '');
  }
  function savePrefs() {
    const field = $('.vv-addr-text');
    if (field && !field.hidden) draft.address_text = field.value.trim();
    if (draft.address === 'custom' && !draft.address_text) { field.focus(); return; }
    const prefs = { address: draft.address, formality: draft.formality, voice: draft.voice };
    if (draft.address_text && (draft.address === 'name' || draft.address === 'custom')) prefs.address_text = draft.address_text;
    lsSet('vf_owner_voice', draft.voice);
    S.prefs = prefs;
    const note = $('.vv-menu-note');
    if (S.live && S.ws && S.ws.readyState === WebSocket.OPEN) {
      S.ws.send(JSON.stringify({ type: 'prefs.save', prefs }));
      $('.vv-save').disabled = true;
    } else {
      S.pendingPrefs = prefs;    // уйдёт, как только подключимся
      if (note) note.textContent = 'Сохраню, как только подключимся.';
    }
  }

  function toggleLog(open) {
    const log = $('.vv-log');
    if (!log) return;
    log.classList.toggle('vv-open', open);
    if (open) renderLog();
  }

  // ---------------------------------------------------------------- состояние и подписи
  function setState(name) {
    if (!PALETTES[name]) name = 'idle';
    if (S.state === name && S.palTarget) return;
    S.state = name;
    S.palTarget = PALETTES[name].map(hex);
    if (!S.pal) S.pal = S.palTarget.map(c => c.slice());
    if (S.el) { S.el.style.setProperty('--vv-c1', PALETTES[name][1]); S.el.style.setProperty('--vv-c2', PALETTES[name][2]); }
  }
  function setStatus(text, state, shimmer) {
    shimmer = !!shimmer;
    if (state) setState(state);
    if (state === 'speaking' && performance.now() < (S.tipUntil || 0)) text += ' · нажмите на шар, чтобы прервать';
    if (text === S.statusText && shimmer === S.shimmer) return;
    S.statusText = text; S.shimmer = shimmer;
    const st = $('.vv-status');
    if (st) { st.textContent = text; st.classList.toggle('vv-shimmer', shimmer); }
  }
  function idleStatus() {
    if (!S.live || S.fnRunning) return;
    if (S.agentPlaying || S.sources.length) setStatus('Говорит', 'speaking');
    else if (S.muted) setStatus('Микрофон выключен', 'muted');
    else setStatus('Слушаю', 'listening');
  }
  function idleSoon() { clearTimeout(S.idleT); S.idleT = setTimeout(idleStatus, 450); }

  function tick() {
    const t = $('.vv-pill-t');
    if (!t) return;
    let text;
    if (S.live && S.startedAt) {
      const s = Math.floor((Date.now() - S.startedAt) / 1000);
      text = 'В эфире ' + String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
    } else text = S.error ? 'Нет связи' : (S.offline ? 'Нет связи' : 'Подключаюсь…');
    if (t.textContent !== text) t.textContent = text;
    const live = S.live ? '1' : '0';
    if (S.el.dataset.live !== live) S.el.dataset.live = live;
  }
  function showAlert(html) { const a = $('.vv-alert'); if (a) { a.innerHTML = html || ''; a.hidden = !html; } }

  // ---------------------------------------------------------------- субтитры и расшифровка
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
    body.innerHTML = lines.length ? lines.map(l => '<div class="vv-msg ' + l.role + '">' + esc(l.text.trim()) + '</div>').join('')
      : '<div class="vv-log-empty">Здесь появится текст разговора</div>';
    body.scrollTop = body.scrollHeight;
  }
  function tail(s, n) { return s.length > n ? '…' + s.slice(-n) : s; }
  function onTranscript(role, delta) {
    if (!delta) return;
    const now = Date.now();
    if (role === 'user') {
      if (S.lastRole !== 'user') S.ownerText = '';
      S.ownerText += delta;
      S.anyOwner = true;
      S.skipTurn = false;
      const c = $('.vv-chips'); if (c && !c.hidden) c.hidden = true;
      const o = $('.vv-cap-owner'); if (o) o.textContent = 'Вы: ' + tail(S.ownerText.trim(), 140);
    } else {
      const fresh = S.lastRole !== 'assistant' || now - S.lastAgentAt > 2500;
      if (fresh) S.agentText = '';
      S.agentText += delta;
      S.lastAgentAt = now;
      const box = $('.vv-cap-agent');
      if (box) {
        const narrow = window.innerWidth < 640, max = narrow ? (S.el.dataset.cards === '1' ? 80 : 150) : 300;
        box.textContent = tail(S.agentText.trim(), max);   // одна запись в DOM на кусочек — без сотен span
      }
    }
    S.lastRole = role;
    addLine(role, delta);
  }
  function sendText(text) {
    if (!S.ws || S.ws.readyState !== WebSocket.OPEN || !S.live) { toast('Ещё подключаюсь — секунду', 'info'); return; }
    S.ws.send(JSON.stringify({ type: 'text.input', text }));
    S.lastRole = null;
    onTranscript('user', text);
    S.lastRole = 'user';
    stopPlayback();
    haptic('light');
  }

  // ---------------------------------------------------------------- карточки
  function countUp(el, to) {
    const start = performance.now(), dur = 700;
    const step = now => {
      const k = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = String(Math.round(to * e));
      if (k < 1 && S.open) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function cardBody(card) {
    if (card.type === 'stats') {
      let h = '<div class="vv-tiles">' + (card.tiles || []).map(t =>
        '<div class="vv-tile' + (t.accent ? ' vv-accent' : '') + '"><em>' + esc(t.label) + '</em><b' + (typeof t.value === 'number' ? ' data-n="' + t.value + '">0' : '>' + esc(t.value)) +
        '</b><small>' + esc(t.sub || '') + '</small></div>').join('') + '</div>';
      const bars = card.bars || [];
      if (bars.length > 1) {
        const max = Math.max(1, ...bars.map(b => +b.value || 0)), labels = bars.length <= 10;
        if (card.bars_title) h += '<div class="vv-bars-t">' + esc(card.bars_title) + '</div>';
        h += '<div class="vv-bars' + (labels ? ' vv-has-labels' : '') + '">' + bars.map((b, i) =>
          '<div class="vv-bar' + (i === bars.length - 1 && card.title === 'Сегодня' ? ' vv-today' : '') + '" style="height:' + Math.max(6, Math.round((+b.value || 0) / max * 100)) +
          '%;animation-delay:' + (i * 35) + 'ms"' + (labels ? ' data-l="' + esc(b.label) + '" data-v="' + (+b.value || '') + '"' : '') + ' title="' + esc(b.label) + ': ' + esc(b.value) + '"></div>').join('') + '</div>';
      }
      return h;
    }
    if (card.type === 'client') {
      return '<div class="vv-client-h"><span class="vv-avatar">' + esc(initials(card.title)) + '</span><div><b>' + esc(card.title) + '</b><small>' + esc(card.subtitle || '') + '</small></div></div>' +
        '<div class="vv-feed">' + ((card.lines || []).map(l => '<div>' + esc(l) + '</div>').join('') || '<div>Переписки пока нет</div>') + '</div>';
    }
    if (card.type === 'list') {
      return '<div class="vv-rows">' + ((card.items || []).map(i => '<div class="vv-row"><span class="vv-dot ' + (CH_DOT[i.channel] || '') + '"></span><div><b>' + esc(i.title) + '</b>' +
        (i.meta ? ' · ' + esc(i.meta) : '') + (i.text ? '<small>' + esc(i.text) + '</small>' : '') + '</div></div>').join('') || '<div class="vv-row">Пусто</div>') + '</div>';
    }
    if (card.type === 'funnel') {
      const st = card.stages || [], max = Math.max(1, ...st.map(s => +s.value || 0));
      return '<div class="vv-funnel">' + st.map((s, i) => '<div class="vv-fn-row"><span>' + esc(s.label) + '</span><div class="vv-fn-bar"><i style="width:' +
        Math.max(3, Math.round((+s.value || 0) / max * 100)) + '%;animation-delay:' + (i * 60) + 'ms"></i></div><b>' + (+s.value || 0) + '</b></div>').join('') + '</div>';
    }
    return '<div class="vv-row">' + esc(card.text || '') + '</div>';
  }
  function renderCard(card, opts) {
    const box = $('.vv-cards');
    if (!box || !card || !card.type) return;
    opts = opts || {};
    const el = document.createElement('div');
    el.className = 'vv-card' + (card.type === 'done' ? ' vv-done' : '') + (opts.fresh ? ' vv-fresh' : '');
    el.innerHTML = '<div class="vv-card-h"><span>' + (CARD_IC[card.type] || CARD_IC.list) + '</span>' + esc(card.type === 'client' ? 'Клиент' : (card.title || '')) +
      (card.subtitle && card.type !== 'client' ? '<em>' + esc(card.subtitle) + '</em>' : '') + '</div>' + cardBody(card);
    if (opts.append) box.appendChild(el); else box.prepend(el);
    box.querySelectorAll('.vv-fresh').forEach(x => { if (x !== el) x.classList.remove('vv-fresh'); });
    while (box.children.length > 4) box.lastElementChild.remove();
    el.querySelectorAll('b[data-n]').forEach(b => countUp(b, +b.dataset.n));
    if (!opts.append) box.scrollTo({ left: 0, behavior: 'smooth' });
    if (S.el.dataset.cards !== '1') S.el.dataset.cards = '1';
  }

  // ---------------------------------------------------------------- звук: запуск
  async function startAudio() {
    const AC = window.AudioContext || window.webkitAudioContext;
    S.ctx = new AC({ latencyHint: 'interactive' });
    if (S.ctx.state === 'suspended') await S.ctx.resume();
    S.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    S.analyser = S.ctx.createAnalyser();
    S.analyser.fftSize = 512;
    S.buf = new Uint8Array(S.analyser.fftSize);
    S.analyser.connect(S.ctx.destination);
    S.src = S.ctx.createMediaStreamSource(S.stream);
    S.lp = S.ctx.createBiquadFilter();
    S.lp.type = 'lowpass'; S.lp.frequency.value = 7000; S.lp.Q.value = 0.707;   // против «зеркальных» призвуков при 16 кГц
    S.src.connect(S.lp);
    const sink = S.ctx.createGain(); sink.gain.value = 0; sink.connect(S.ctx.destination);
    S.worklet = false;
    if (S.ctx.audioWorklet && window.AudioWorkletNode) {
      try {
        await S.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([MIC_WORKLET], { type: 'application/javascript' })));
        await S.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([OUT_WORKLET], { type: 'application/javascript' })));
        S.mic = new AudioWorkletNode(S.ctx, 'vv-mic', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
        S.mic.port.onmessage = e => onMicFrame(new Int16Array(e.data.pcm), e.data.rms);
        S.out = new AudioWorkletNode(S.ctx, 'vv-out', { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [2] });
        S.out.port.onmessage = e => onOutState(e.data);
        S.out.connect(S.analyser);
        S.worklet = true;
      } catch (e) { S.worklet = false; }
    }
    if (!S.worklet) {
      S.mic = S.ctx.createScriptProcessor(2048, 1, 1);
      S.mic.onaudioprocess = e => fallbackMic(e.inputBuffer.getChannelData(0));
      S.out = null;
    }
    S.lp.connect(S.mic);
    S.mic.connect(sink);
  }

  // запасной путь микрофона: пересборка в 16 кГц на главном потоке
  function fallbackMic(data) {
    const ratio = S.ctx.sampleRate / 16000, len = data.length;
    const out = new Int16Array(Math.ceil(len / ratio) + 2);
    let t = S.micT, n = 0, sum = 0;
    while (t < len - 1) {
      const i = Math.floor(t), f = t - i, s0 = i < 0 ? S.micPrev : data[i];
      let v = s0 + (data[i + 1] - s0) * f;
      sum += v * v; v = v < -1 ? -1 : v > 1 ? 1 : v;
      out[n++] = v < 0 ? v * 32768 : v * 32767;
      t += ratio;
    }
    S.micT = t - len; S.micPrev = data[len - 1];
    if (n) onMicFrame(out.subarray(0, n), Math.sqrt(sum / n));
  }

  function agentActive(now) { return S.agentPlaying || S.sources.length > 0 || now < S.agentEndAt + ECHO_TAIL_MS; }

  function onMicFrame(pcm, rms) {
    const now = performance.now();
    const active = agentActive(now);
    if (!active && rms > 0.015) S.speechLvl = S.speechLvl * 0.94 + rms * 0.06;   // обычная громкость речи владельца
    const thr = Math.max(0.012, 0.35 * S.speechLvl);
    let pass = true;
    if (active) {
      if (rms >= thr) S.holdUntil = now + 600;
      else if (now > S.holdUntil) pass = false;     // тихо, пока говорит агент, — это эхо его голоса
    }
    S.micLevel = S.muted ? 0 : rms;
    if (S.muted || !S.live || !S.ws || S.ws.readyState !== WebSocket.OPEN) return;
    S.ws.send(pass ? pcm : new Int16Array(pcm.length));
  }

  // ---------------------------------------------------------------- звук: воспроизведение
  function decode24(b64) {
    let bin;
    try { bin = atob(b64); } catch (e) { return null; }
    const n = bin.length >> 1;
    if (n < 1) return null;
    const f = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
      if (v >= 0x8000) v -= 0x10000;
      f[i] = v / 32768;
    }
    return f;
  }
  function toCtxRate(f) {
    const rate = S.ctx.sampleRate;
    if (rate === 24000) return f;
    const ratio = 24000 / rate, len = f.length;
    const out = new Float32Array(Math.ceil((len - S.rsT) / ratio) + 2);
    let t = S.rsT, n = 0;
    while (t < len - 1) {
      const i = Math.floor(t), fr = t - i, s0 = i < 0 ? S.rsPrev : f[i];
      out[n++] = s0 + (f[i + 1] - s0) * fr;
      t += ratio;
    }
    S.rsT = t - len; S.rsPrev = f[len - 1];
    return out.subarray(0, n);
  }
  function playChunk(b64) {
    if (!S.ctx || !b64 || S.skipTurn) return;
    const f = decode24(b64);
    if (!f) return;
    if (S.ctx.state === 'suspended') S.ctx.resume().catch(() => { });
    S.inReply = true;
    clearTimeout(S.drainT);
    if (S.pendingUnderrun) {
      if (performance.now() - S.pendingUnderrun < 1500) {
        S.underruns++;                                     // поток продолжился после недобора — нужен запас побольше
        setPrebuf(Math.min(PREBUF_MAX, S.prebuf + 0.08));
      }
      S.pendingUnderrun = 0;
      clearTimeout(S.idleT);
    }
    if (S.worklet && S.out) {
      const s = toCtxRate(f);
      const copy = new Float32Array(s);   // подмассив → свой буфер, чтобы передать без копии в ворклете
      S.out.port.postMessage({ t: 'push', s: copy }, [copy.buffer]);
      S.drainT = setTimeout(() => { if (S.out) S.out.port.postMessage({ t: 'drain' }); }, DRAIN_AFTER_MS);
    } else {
      fallbackSchedule(f);
    }
  }
  function onOutState(d) {
    if (d.t !== 'state') return;
    if (d.playing) {
      if (!S.agentPlaying) {
        S.agentPlaying = true;
        clearTimeout(S.idleT);
        maybeTip();
        if (!S.fnRunning) setStatus('Говорит', 'speaking');
      }
      return;
    }
    S.agentPlaying = false;
    S.agentEndAt = performance.now();
    if (d.underrun && S.inReply) {
      // Буфер опустел, а конца ответа ещё не было: либо сеть рвёт поток (тогда звук сейчас продолжится
      // и запас вырастет — см. playChunk), либо ответ просто кончился раньше, чем мы это поняли.
      S.pendingUnderrun = performance.now();
      clearTimeout(S.idleT);
      S.idleT = setTimeout(() => { S.pendingUnderrun = 0; playbackDone(); }, 600);
      return;
    }
    if (d.cleared) { idleSoon(); return; }
    playbackDone();
  }
  function playbackDone() {
    if (S.ws && S.ws.readyState === WebSocket.OPEN) { try { S.ws.send(JSON.stringify({ type: 'audio_playback.stopped' })); } catch (e) { } }
    idleSoon();
  }
  function setPrebuf(v) {
    S.prebuf = v;
    if (S.out) S.out.port.postMessage({ t: 'prebuf', v });
  }
  function playbackLeft() {
    if (S.worklet) return S.agentPlaying ? 1 : 0;   // точный остаток знает ворклет; для перебивания хватает «играет»
    return S.ctx ? Math.max(0, S.nextPlay - S.ctx.currentTime) : 0;
  }
  function stopPlayback() {
    clearTimeout(S.drainT);
    S.inReply = false;
    S.rsT = 0; S.rsPrev = 0;
    if (S.worklet && S.out) S.out.port.postMessage({ t: 'clear' });
    const list = S.sources.slice();
    S.sources = [];
    list.forEach(s => { try { s.onended = null; s.stop(); } catch (e) { } });
    if (S.ctx) S.nextPlay = S.ctx.currentTime;
    if (S.agentPlaying || list.length) { S.agentPlaying = false; S.agentEndAt = performance.now(); idleSoon(); }
  }
  function interruptAgent() {
    if (!(S.agentPlaying || S.sources.length)) return;
    S.skipTurn = true;            // остаток этого ответа, который ещё придёт, не играем
    stopPlayback();
    haptic('medium');
  }
  function maybeTip() {
    if (lsGet('vv_tip_seen')) return;
    lsSet('vv_tip_seen', '1');
    S.tipUntil = performance.now() + 5000;                // подсказка — в строке статуса, один раз на устройство
    setTimeout(idleStatus, 5100);
  }

  // запасной путь воспроизведения (без AudioWorklet): кусочки встык с запасом
  function fallbackSchedule(f) {
    const ctx = S.ctx;
    const buffer = ctx.createBuffer(1, f.length, 24000);
    buffer.copyToChannel ? buffer.copyToChannel(f, 0) : buffer.getChannelData(0).set(f);
    const now = ctx.currentTime;
    if (S.nextPlay < now + 0.02) {
      if (S.inReply && S.sources.length === 0 && S.lastChunkAt && performance.now() - S.lastChunkAt < 900) setPrebuf(Math.min(PREBUF_MAX, S.prebuf + 0.08));
      S.nextPlay = now + S.prebuf;
    }
    S.lastChunkAt = performance.now();
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(S.analyser);
    S.sources.push(src);
    src.onended = () => {
      const i = S.sources.indexOf(src);
      if (i > -1) S.sources.splice(i, 1);
      if (!S.sources.length) { S.agentEndAt = performance.now(); idleSoon(); }
    };
    src.start(S.nextPlay);
    S.nextPlay += buffer.duration;
    clearTimeout(S.idleT);
    if (!S.fnRunning) setStatus('Говорит', 'speaking');
  }

  // ---------------------------------------------------------------- сеть
  async function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const q = new URLSearchParams({ rate: '16000' });
    const v = storedVoice();
    if (v) q.set('voice', v);
    if (S.opts.agentId) q.set('agent_id', S.opts.agentId);
    let token = '';
    try { token = typeof S.opts.token === 'function' ? await S.opts.token() : (S.opts.token || ''); } catch (e) { token = ''; }
    S.offline = false;
    const ws = new WebSocket(proto + '://' + location.host + '/ws/agent-voice?' + q.toString());
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
        showAlert((was ? 'Связь прервалась. ' : 'Не удалось подключиться к голосовому агенту. ') +
          '<button type="button" class="vv-retry">' + (was ? 'Продолжить разговор' : 'Попробовать ещё раз') + '</button>');
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
      if (d.prefs) S.prefs = d.prefs;
      if (d.prefs && d.prefs.voice && !storedVoice()) lsSet('vf_owner_voice', d.prefs.voice);
      S.ownerName = d.owner_name || '';
      showAlert('');
      setStatus('Слушаю', 'listening');
      haptic('light');
      tick();
      if (S.pendingPrefs && S.ws && S.ws.readyState === WebSocket.OPEN) { S.ws.send(JSON.stringify({ type: 'prefs.save', prefs: S.pendingPrefs })); S.pendingPrefs = null; }
    } else if (t === 'owner.dashboard') {
      (d.cards || []).forEach(c => renderCard(c, { append: true }));
      const box = $('.vv-cards'); if (box) box.scrollLeft = 0;
    } else if (t === 'response.audio.delta') {
      playChunk(d.delta);
    } else if (t === 'conversation.interrupted') {
      stopPlayback();
    } else if (t === 'gemini.voice_activity') {
      // Google услышал речь: обрываем доигрывание, только если ворота видели настоящую речь владельца
      if (d.activity === 'start' && performance.now() < S.holdUntil + 200 && playbackLeft() >= (S.worklet ? 1 : BARGE_MIN_LEFT)) stopPlayback();
    } else if (t === 'assistant.speech.started') {
      S.inReply = true;
    } else if (t === 'assistant.speech.ended' || t === 'turn.saved') {
      S.skipTurn = false;
      if (S.out && S.inReply) S.out.port.postMessage({ t: 'drain' });
      S.inReply = false;
      if (!S.underruns && S.prebuf > PREBUF_MIN) setPrebuf(Math.max(PREBUF_MIN, S.prebuf - 0.02));
      S.underruns = 0;
    } else if (t === 'transcript.delta') {
      onTranscript(d.role === 'user' ? 'user' : 'assistant', d.delta || '');
    } else if (t === 'function_call.executing') {
      S.fnRunning++;
      clearTimeout(S.idleT);
      setStatus(fnStatus(d.function, d.arguments), 'thinking', true);
    } else if (t === 'function_call.completed' || t === 'function_call.delivery_error' || t === 'function_call.error') {
      S.fnRunning = Math.max(0, S.fnRunning - 1);
      const r = d.result || {};
      if (r.card) { renderCard(r.card, { fresh: true }); haptic('success'); }
      if (d.function === 'do_action' && r.ok && r.answer) addLine('fn', '✓ ' + r.answer);
      else if (d.function === 'send_to_telegram' && r.ok) { addLine('fn', '✓ Отправлено в Telegram'); renderCard({ type: 'done', title: 'Отправлено в Telegram', text: 'Отчёт ждёт вас в чате с ботом агента' }, { fresh: true }); }
      if (!S.fnRunning) idleStatus();
    } else if (t === 'prefs.saved') {
      if (d.prefs) S.prefs = d.prefs;
      const btn = $('.vv-save'); if (btn) btn.disabled = false;
      const note = $('.vv-menu-note');
      if (note) { note.textContent = d.ok ? '✓ Сохранено. Обращение уже действует, голос — с нового разговора.' : 'Не удалось сохранить — попробуйте ещё раз.'; note.classList.toggle('vv-ok', !!d.ok); }
      if (d.ok) { haptic('success'); setTimeout(closeMenu, 900); }
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

  // ---------------------------------------------------------------- шар на canvas
  function animate() {
    const canvas = $('.vv-orb');
    if (!canvas) return;
    const c2 = canvas.getContext('2d', { alpha: true });
    const t0 = performance.now();
    let last = t0, slow = 0, fast = 0, frames = 0;
    const frame = now => {
      if (!S.open) { S.raf = null; return; }
      S.raf = requestAnimationFrame(frame);
      if (document.hidden) return;
      const dt = now - last; last = now;
      // автоснижение качества на слабых устройствах (и обратно, если всё плавно)
      if (frames++ > 30) {
        if (dt > 24) { slow++; fast = 0; } else { fast++; slow = Math.max(0, slow - 1); }
        if (slow > 45 && S.quality > 0) { S.quality--; slow = 0; }
        else if (fast > 600 && S.quality < 2) { S.quality++; fast = 0; }
      }
      const t = (now - t0) / 1000;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      const scale = S.quality === 2 ? Math.min(1.5, window.devicePixelRatio || 1) : S.quality === 1 ? 1 : 0.75;
      const W = Math.round(w * scale), H = Math.round(h * scale);
      if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
      c2.setTransform(scale, 0, 0, scale, 0, 0);
      c2.clearRect(0, 0, w, h);
      // уровни: голос агента (анализатор выхода) или владельца
      if (S.agentPlaying || S.sources.length) {
        S.analyser.getByteTimeDomainData(S.buf);
        let sum = 0;
        for (let i = 0; i < S.buf.length; i += 2) { const v = (S.buf[i] - 128) / 128; sum += v * v; }
        S.outLevel = Math.min(1, Math.sqrt(sum / (S.buf.length / 2)) * 4.5);
      } else S.outLevel *= 0.85;
      const target = S.state === 'speaking' ? S.outLevel : S.state === 'listening' ? Math.min(1, S.micLevel * 7) : 0;
      S.lvl += (target - S.lvl) * 0.2;
      if (S.glowEl && Math.abs((S.glowLvl || 0) - S.lvl) > 0.01) { S.glowLvl = S.lvl; S.glowEl.style.transform = 'scale(' + (0.92 + S.lvl * 0.32).toFixed(3) + ')'; }
      if (S.pal && S.palTarget) for (let k = 0; k < 3; k++) for (let j = 0; j < 3; j++) S.pal[k][j] += (S.palTarget[k][j] - S.pal[k][j]) * 0.06;
      const pal = S.pal || PALETTES.connecting.map(hex);
      const mo = MOTION[S.state] || MOTION.idle;
      const amp = mo[0] + S.lvl * 0.15, sp = mo[1];
      const cx = w / 2, cy = h / 2;
      const R = Math.min(w, h) * 0.39 * (1 + 0.08 * S.lvl + (S.state === 'thinking' ? 0.02 * Math.sin(t * 3) : 0));
      const layers = S.quality === 0 ? 2 : 3, N = S.quality === 0 ? 40 : S.quality === 1 ? 56 : 72;
      c2.globalCompositeOperation = 'lighter';
      for (let k = 0; k < layers; k++) {
        c2.beginPath();
        for (let i = 0; i <= N; i++) {
          const a = (i / N) * 6.2832;
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
        c2.globalAlpha = 0.52;
        c2.fillStyle = g;
        c2.fill();
      }
      c2.globalCompositeOperation = 'source-over';
      c2.globalAlpha = 1;
      const hl = c2.createRadialGradient(cx - R * 0.35, cy - R * 0.42, 0, cx - R * 0.35, cy - R * 0.42, R * 0.7);
      hl.addColorStop(0, 'rgba(255,255,255,.36)');
      hl.addColorStop(1, 'rgba(255,255,255,0)');
      c2.fillStyle = hl;
      c2.beginPath(); c2.arc(cx, cy, R * 0.98, 0, 6.2832); c2.fill();
      if (S.state === 'thinking' || S.state === 'connecting') {
        const rr = R * 1.18, a0 = t * 2.4;
        c2.lineWidth = 2.5; c2.lineCap = 'round';
        c2.strokeStyle = rgba(pal[0], 0.85);
        c2.beginPath(); c2.arc(cx, cy, rr, a0, a0 + 1.5); c2.stroke();
        c2.strokeStyle = rgba(pal[2], 0.35);
        c2.beginPath(); c2.arc(cx, cy, rr, a0 + 3.14, a0 + 3.94); c2.stroke();
        for (let i = 0; i < 3; i++) {
          const a = t * 1.6 + i * 2.094;
          c2.fillStyle = rgba(pal[i], 0.9);
          c2.beginPath(); c2.arc(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr, 2.6, 0, 6.2832); c2.fill();
        }
      } else if (S.state === 'listening') {
        c2.lineWidth = 1.5;
        c2.strokeStyle = rgba(pal[0], 0.12 + S.lvl * 0.45);
        c2.beginPath(); c2.arc(cx, cy, R * (1.12 + S.lvl * 0.1), 0, 6.2832); c2.stroke();
      }
    };
    if (!S.raf) S.raf = requestAnimationFrame(frame);
  }

  // ---------------------------------------------------------------- открыть / закрыть
  async function open(opts) {
    if (S.open) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast('Этот браузер не даёт доступ к микрофону', 'error'); return; }
    S.opts = Object.assign({ agentId: null, token: null, title: 'Голосовой агент', onClose: null, onCredits: null, onMicError: null }, opts || {});
    if (!S.el) S.el = buildDom();
    Object.assign(S, { lines: [], fnRunning: 0, muted: false, closing: false, error: null, live: false, nextPlay: 0, sources: [],
      prebuf: PREBUF_MIN, underruns: 0, inReply: false, lastRole: null, agentText: '', ownerText: '', anyOwner: false, skipTurn: false,
      startedAt: 0, lvl: 0, micLevel: 0, outLevel: 0, holdUntil: 0, pal: null, palTarget: null, agentPlaying: false, statusText: '',
      rsT: 0, rsPrev: 0, micT: 0, micPrev: 0, offline: false, state: '', pendingUnderrun: 0 });
    S.el.querySelector('.vv-name').textContent = S.opts.title || 'Голосовой агент';
    S.el.querySelector('.vv-cap-owner').textContent = '';
    S.el.querySelector('.vv-cap-agent').textContent = '';
    S.el.querySelector('.vv-cards').innerHTML = '';
    S.el.dataset.cards = '0';
    S.el.querySelector('.vv-chips').hidden = false;
    S.el.querySelector('.vv-log').classList.remove('vv-open');
    closeMenu();
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
      showAlert('Разрешите доступ к микрофону для этого сайта и откройте разговор снова.' +
        (S.opts.onMicError ? ' <button type="button" class="vv-mic-help">Открыть в браузере</button>' : ''));
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
    clearTimeout(S.idleT);
    try { if (S.mic) { if (S.mic.port) S.mic.port.onmessage = null; S.mic.onaudioprocess = null; S.mic.disconnect(); } } catch (e) { }
    try { S.out && S.out.disconnect(); } catch (e) { }
    try { S.src && S.src.disconnect(); } catch (e) { }
    try { S.stream && S.stream.getTracks().forEach(tr => tr.stop()); } catch (e) { }
    try { S.ctx && S.ctx.close(); } catch (e) { }
    Object.assign(S, { ctx: null, stream: null, src: null, lp: null, mic: null, out: null, analyser: null });
    try { S.wake && S.wake.release(); } catch (e) { }
    S.wake = null;
    clearInterval(S.timer);
    S.el.classList.remove('vv-in');
    setTimeout(() => { if (!S.open) S.el.hidden = true; }, 260);
    document.body.classList.remove('vv-open');
    const lines = S.lines.filter(l => (l.text || '').trim()).map(l => ({ role: l.role, text: l.text.trim() }));
    S.lines = [];
    if (typeof S.opts.onClose === 'function') { try { S.opts.onClose(lines); } catch (e) { } }
  }

  document.addEventListener('keydown', e => {
    const tag = (e.target && e.target.tagName) || '';
    const typing = /INPUT|TEXTAREA|SELECT/.test(tag) || (e.target && e.target.isContentEditable);
    if (!S.open) {
      // V — открыть разговор на странице агента (если не печатаем и нет открытых окон)
      if ((e.key === 'v' || e.key === 'V' || e.key === 'м' || e.key === 'М') && !e.metaKey && !e.ctrlKey && !e.altKey && !typing
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
      else if (!$('.vv-menu').hidden) closeMenu();
      else close();
    } else if (!typing && (e.key === 'm' || e.key === 'ь')) {
      toggleMute();
    } else if (!typing && e.key === ' ') {
      e.preventDefault(); interruptAgent();
    }
  });

  window.VoksyVoice = { open, close, isOpen: () => S.open, _handle: d => handle(d) };   // _handle — для проверок вёрстки

  // ---------------------------------------------------------------- кабинет: кнопка «Поговорить» на agent.html
  window.openVoiceChat = function () {
    if (typeof isReactiveMode === 'function' && isReactiveMode()) { toast('Голосовой разговор с агентом — на тарифе Profi', 'error'); return; }
    let title = 'Голосовой агент';
    try { if (typeof agentData !== 'undefined' && agentData && agentData.name && agentData.name !== 'Мой агент') title = agentData.name; } catch (e) { }
    open({
      agentId: (typeof currentAgentId !== 'undefined' && currentAgentId) || null,
      token: () => (typeof getToken === 'function' ? getToken() : lsGet('auth_token')),
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
