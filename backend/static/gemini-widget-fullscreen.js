/**
 * 🚀 Gemini Fullscreen Voice Widget v3.0.0 - PRODUCTION
 *
 * CHANGELOG v3.0.0:
 *   - NEW: Картинка — 82% модальная карточка с blurred backdrop вместо fullscreen
 *   - NEW: Spring-анимация входа карточки (scale 0.88 → 1 + translateY)
 *   - NEW: Клик вне карточки → minimize (назад к диалогу)
 *   - NEW: Sonar-rings — 3 концентрических кольца снаружи сферы (только при speaking)
 *   - NEW: Sphere speaking — непрерывный glow-pulse (зелёный breathing effect)
 *   - NEW: Sphere listening — blob-морфинг для органичного ощущения
 *   - NEW: Ambient glow-shadow под сферой (цвет по состоянию)
 *   - NEW: Wave-bars меняют цвет по состоянию сферы
 *   - NEW: Scrims (градиенты) внутри карточки для читаемости
 *   - FIX: Все исправления v2.0.0 сохранены (same-URL, z-index, STATE.mode)
 *
 * Supports:
 *   data-model="2.5"  → /ws/gemini/{id}
 *   data-model="3.1"  → /ws/gemini-31/{id}
 *
 * @version 3.0.0
 * @author WellcomeAI Team
 */

(function () {
    'use strict';

    // ============================================================================
    // CONFIGURATION
    // ============================================================================

    const CONFIG = {
        assistantId: null,
        serverUrl: null,
        model: '2.5',
        position: 'bottom-right',
        audio: {
            inputSampleRate: 16000,
            outputSampleRate: 24000,
            playbackSampleRate: 24000,
            actualSampleRate: null,
            needsResampling: false
        },
        vad: { enabled: true, speechThreshold: -38, visualSilenceThreshold: -45 },
        ws: { reconnectDelay: 2000, maxReconnectAttempts: 5, pingInterval: 30000 },
        setup: { waitAfterSetup: 800, maxSetupWait: 10000 }
    };

    // ============================================================================
    // STATE
    // ============================================================================

    const STATE = {
        ws: null,
        isConnected: false, isSetupComplete: false, readyToRecord: false,
        isRecording: false, isPlaying: false, isSpeaking: false,
        audioContext: null, mediaStream: null,
        audioWorkletNode: null, audioStreamNode: null,
        pingInterval: null, reconnectAttempts: 0, setupTimeout: null,
        audioWorkletReady: false, streamWorkletReady: false,
        isIOS: false, isAndroid: false, isMobile: false,
        lastInterruptionTime: 0, lastSpeechNotifyTime: 0,
        mode: 'button',   // 'button' | 'dialog' | 'image'
        currentImageUrl: null,
        ui: {}
    };

    // ============================================================================
    // AUDIOWORKLETS
    // ============================================================================

    const RECORDER_WORKLET_CODE = `
class RecorderWorkletProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.bufferSize = 512;
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferIndex = 0;
        this.resampleRatio = sampleRate / 16000;
    }
    downsample(inputData) {
        if (this.resampleRatio === 1) return inputData;
        const out = new Float32Array(Math.floor(inputData.length / this.resampleRatio));
        for (let i = 0; i < out.length; i++) {
            const s = i * this.resampleRatio, f = Math.floor(s), c = Math.min(f + 1, inputData.length - 1);
            out[i] = inputData[f] * (1 - (s - f)) + inputData[c] * (s - f);
        }
        return out;
    }
    process(inputs) {
        const input = inputs[0];
        if (!input || !input[0]) return true;
        const d = this.downsample(input[0]);
        for (let i = 0; i < d.length; i++) {
            this.buffer[this.bufferIndex++] = d[i];
            if (this.bufferIndex >= this.bufferSize) {
                this.port.postMessage({ type: 'audioData', data: this.buffer.slice(0, this.bufferIndex) });
                this.bufferIndex = 0;
            }
        }
        return true;
    }
}
registerProcessor('recorder-worklet', RecorderWorkletProcessor);
`;

    const STREAM_WORKLET_CODE = `
class AudioStreamProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.audioQueue = []; this.currentBuffer = null;
        this.bufferIndex = 0; this.samplesProcessed = 0; this.isActive = false;
        this.port.onmessage = (e) => {
            if (e.data.type === 'audioData') {
                this.audioQueue.push(e.data.buffer);
                if (!this.isActive) { this.isActive = true; this.port.postMessage({ type: 'started' }); }
            } else if (e.data.type === 'clear') {
                this.audioQueue = []; this.currentBuffer = null;
                this.bufferIndex = 0; this.isActive = false;
                this.port.postMessage({ type: 'cleared' });
            } else if (e.data.type === 'stop') {
                this.isActive = false; this.port.postMessage({ type: 'stopped' });
            }
        };
    }
    process(inputs, outputs) {
        const out = outputs[0]?.[0];
        if (!out) return true;
        for (let i = 0; i < out.length; i++) {
            if (!this.currentBuffer || this.bufferIndex >= this.currentBuffer.length) {
                if (this.audioQueue.length > 0) { this.currentBuffer = this.audioQueue.shift(); this.bufferIndex = 0; }
                else { out[i] = 0; continue; }
            }
            out[i] = this.currentBuffer[this.bufferIndex++];
            this.samplesProcessed++;
        }
        if (this.samplesProcessed % 4800 === 0)
            this.port.postMessage({ type: 'stats', queueLength: this.audioQueue.length });
        return true;
    }
}
registerProcessor('audio-stream-processor', AudioStreamProcessor);
`;

    // ============================================================================
    // INIT
    // ============================================================================

    function init() {
        console.log('[FSW] 🚀 Gemini Fullscreen Widget v3.0.0');

        const ua = navigator.userAgent.toLowerCase();
        STATE.isIOS     = /iphone|ipad|ipod/.test(ua);
        STATE.isAndroid = /android/.test(ua);
        STATE.isMobile  = STATE.isIOS || STATE.isAndroid;

        const tag = findScriptTag();
        if (!tag) return console.error('[FSW] script tag not found');

        CONFIG.assistantId = tag.getAttribute('data-assistantId') || tag.dataset.assistantid;
        CONFIG.serverUrl   = tag.getAttribute('data-server')      || tag.dataset.server;
        CONFIG.model       = tag.getAttribute('data-model')       || tag.dataset.model || '2.5';
        const pos          = tag.getAttribute('data-position')    || tag.dataset.position;
        if (pos) CONFIG.position = pos;

        if (!CONFIG.assistantId || !CONFIG.serverUrl)
            return console.error('[FSW] missing assistantId or server');

        if (!document.getElementById('fsw-fa-css')) {
            const l = document.createElement('link');
            l.id = 'fsw-fa-css'; l.rel = 'stylesheet';
            l.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css';
            document.head.appendChild(l);
        }

        injectStyles();
        buildDOM();
        cacheUI();
        bindEvents();

        document.addEventListener('click',      resumeAudioCtx, { once: true });
        document.addEventListener('touchstart', resumeAudioCtx, { once: true });

        console.log(`[FSW] ✅ Init OK | model:${CONFIG.model} | pos:${CONFIG.position}`);
    }

    function findScriptTag() {
        const bySrc = document.querySelector('script[src*="gemini-widget-fullscreen.js"][data-assistantId]')
                   || document.querySelector('script[src*="gemini-widget-fullscreen.js"]');
        if (bySrc) return bySrc;
        const all = document.querySelectorAll('script[data-assistantId]');
        return all.length ? all[all.length - 1] : document.currentScript;
    }

    // ============================================================================
    // STYLES
    // ============================================================================

    function getPosCSS() {
        const p = CONFIG.position.toLowerCase().split('-');
        return { v: p.includes('top') ? 'top' : 'bottom', h: p.includes('left') ? 'left' : 'right' };
    }

    function injectStyles() {
        document.getElementById('fsw-styles')?.remove();

        const { v, h } = getPosCSS();
        const tx = h === 'right' ? '90px' : '-90px';
        const ty = v === 'bottom' ? '90px' : '-90px';

        const s = document.createElement('style');
        s.id = 'fsw-styles';
        s.textContent = `

/* ═══════════════════════════
   TRIGGER BUTTON
════════════════════════════ */
#fsw-btn {
    position: fixed; ${v}: 20px; ${h}: 20px;
    z-index: 2147483646;
    width: 60px; height: 60px; border-radius: 50%;
    background: linear-gradient(135deg, #4a86e8, #2b59c3);
    box-shadow: 0 8px 32px rgba(74,134,232,.35), 0 0 0 1px rgba(255,255,255,.1);
    display: flex; align-items: center; justify-content: center;
    cursor: pointer; border: none; outline: none;
    transition: transform .45s cubic-bezier(.4,0,.2,1),
                opacity   .45s cubic-bezier(.4,0,.2,1),
                box-shadow .3s ease;
}
#fsw-btn:hover { box-shadow: 0 12px 36px rgba(74,134,232,.5), 0 0 0 1px rgba(255,255,255,.15); }
#fsw-btn.dialog-open { transform: scale(0.9); }
#fsw-btn.fsw-image-mode {
    transform: translate(${tx}, ${ty}) scale(0.6) !important;
    opacity: 0 !important; pointer-events: none !important; animation: none !important;
}
@keyframes fsw-btn-pulse {
    0%   { box-shadow: 0 0 0 0 rgba(74,134,232,.7); }
    70%  { box-shadow: 0 0 0 12px rgba(74,134,232,0); }
    100% { box-shadow: 0 0 0 0 rgba(74,134,232,0); }
}
#fsw-btn.pulse { animation: fsw-btn-pulse 2s infinite; }

.fsw-btn-inner { position: relative; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; }
.fsw-pulse-ring {
    position: absolute; inset: 0; border-radius: 50%;
    background: radial-gradient(rgba(255,255,255,.8) 0%, transparent 70%);
    animation: fsw-pulse-ring 3s ease-out infinite; opacity: 0;
}
@keyframes fsw-pulse-ring { 0%{transform:scale(.5);opacity:0} 25%{opacity:.4} 100%{transform:scale(1.2);opacity:0} }
.fsw-bars-mini { display: flex; align-items: center; height: 26px; gap: 4px; }
.fsw-bar-mini  { width: 3px; border-radius: 1.5px; background: #fff; animation: fsw-eq 1.2s ease-in-out infinite; }
.fsw-bar-mini:nth-child(1){height:7px;animation-delay:0s}
.fsw-bar-mini:nth-child(2){height:12px;animation-delay:.3s}
.fsw-bar-mini:nth-child(3){height:18px;animation-delay:.1s}
.fsw-bar-mini:nth-child(4){height:9px;animation-delay:.5s}
@keyframes fsw-eq { 0%,100%{height:4px} 50%{height:18px} }

/* ═══════════════════════════
   DIALOG PANEL
════════════════════════════ */
#fsw-panel {
    position: fixed; ${v}: 20px; ${h}: 20px;
    z-index: 2147483645;
    width: 320px; height: 0; opacity: 0; pointer-events: none;
    background: rgba(255,255,255,.97);
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-radius: 22px;
    box-shadow: 0 12px 40px rgba(0,0,0,.18), 0 0 0 1px rgba(0,0,0,.05);
    overflow: hidden;
    transition: height  .5s  cubic-bezier(.175,.885,.32,1.275),
                opacity .4s  ease,
                transform .45s cubic-bezier(.4,0,.2,1);
    display: flex; flex-direction: column;
}
#fsw-panel.open { height: 480px; opacity: 1; pointer-events: all; }
#fsw-panel.fsw-image-mode {
    transform: translate(${tx}, ${ty}) scale(0.85) !important;
    opacity: 0 !important; pointer-events: none !important;
}

#fsw-header {
    padding: 16px 20px;
    background: linear-gradient(135deg, #1e3a8a, #2563eb 60%, #3b82f6);
    color: #fff; display: flex; justify-content: space-between; align-items: center;
    border-radius: 22px 22px 0 0; flex-shrink: 0;
}
#fsw-title { font: 600 15px/1 'Segoe UI',Roboto,sans-serif; letter-spacing: .4px; }
#fsw-close-btn {
    width: 28px; height: 28px; border-radius: 50%;
    background: rgba(255,255,255,.15); border: none; color: #fff; font-size: 12px;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    transition: background .2s, transform .2s;
}
#fsw-close-btn:hover { background: rgba(255,255,255,.3); transform: scale(1.1); }

#fsw-body {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    background: linear-gradient(180deg, #f8faff, #f0f4ff);
    position: relative; padding: 28px 20px 14px;
    gap: 0;
}

/* ═══════════════════════════════════════════════════════
   SPHERE WRAP + SONAR RINGS + AMBIENT GLOW
═══════════════════════════════════════════════════════ */

.fsw-sphere-wrap {
    position: relative;
    width: 180px; height: 180px;
    display: flex; align-items: center; justify-content: center;
}

/* Ambient glow-shadow beneath sphere */
.fsw-sphere-wrap::after {
    content: '';
    position: absolute;
    bottom: -20px; left: 50%; transform: translateX(-50%);
    width: 130px; height: 26px;
    border-radius: 50%;
    background: radial-gradient(ellipse, rgba(59,130,246,.22) 0%, transparent 72%);
    filter: blur(8px);
    transition: background .5s ease;
    pointer-events: none;
    z-index: 0;
}
.fsw-sphere-wrap.speaking::after  { background: radial-gradient(ellipse, rgba(5,150,105,.38) 0%, transparent 72%); }
.fsw-sphere-wrap.listening::after { background: radial-gradient(ellipse, rgba(37,99,235,.35) 0%, transparent 72%); }
.fsw-sphere-wrap.interrupted::after { background: radial-gradient(ellipse, rgba(217,119,6,.30) 0%, transparent 72%); }

/* Sonar rings — expand outward ONLY when speaking */
.fsw-ring {
    position: absolute; inset: 0; border-radius: 50%;
    border: 2px solid transparent;
    pointer-events: none; opacity: 0;
}
.fsw-sphere-wrap.speaking .fsw-ring-1 { animation: fsw-sonar 2.4s cubic-bezier(0,.5,.5,1) infinite 0.0s; }
.fsw-sphere-wrap.speaking .fsw-ring-2 { animation: fsw-sonar 2.4s cubic-bezier(0,.5,.5,1) infinite 0.8s; }
.fsw-sphere-wrap.speaking .fsw-ring-3 { animation: fsw-sonar 2.4s cubic-bezier(0,.5,.5,1) infinite 1.6s; }
@keyframes fsw-sonar {
    0%   { transform: scale(1.0); border-color: rgba(5,150,105,.7);  opacity: 1; }
    100% { transform: scale(2.1); border-color: rgba(5,150,105,.0);  opacity: 0; }
}

/* ═══════════════════════════════
   SPHERE CORE
═══════════════════════════════ */
#fsw-sphere {
    position: relative; z-index: 1;
    width: 180px; height: 180px;
    background: linear-gradient(145deg, #f1f5ff, #e2e8f4);
    box-shadow: 0 8px 24px rgba(0,0,0,.1), inset 0 2px 6px rgba(255,255,255,.65);
    border-radius: 50%;
    overflow: hidden;
    display: flex; align-items: center; justify-content: center;
    transition: background .4s ease;
    /* subtle idle organic morph */
    animation: fsw-morph-idle 14s ease-in-out infinite;
}
@keyframes fsw-morph-idle {
    0%,100% { border-radius: 50%; }
    25%     { border-radius: 52% 48% 50% 50% / 50% 52% 48% 50%; }
    50%     { border-radius: 48% 52% 52% 48% / 52% 48% 50% 50%; }
    75%     { border-radius: 50% 50% 48% 52% / 48% 52% 52% 48%; }
}

/* Inner rotating sheen */
#fsw-sphere::before {
    content: ''; position: absolute; width: 140%; height: 140%; border-radius: 40%;
    background: linear-gradient(45deg, rgba(255,255,255,.38), rgba(74,134,232,.12));
    animation: fsw-rotate 10s linear infinite;
    pointer-events: none;
}
@keyframes fsw-rotate { to { transform: rotate(360deg); } }

/* ── LISTENING ── */
#fsw-sphere.listening {
    background: linear-gradient(145deg, #dbeafe, #eff6ff);
    animation: fsw-morph-listen 3.5s ease-in-out infinite;
}
@keyframes fsw-morph-listen {
    0%,100% { border-radius: 50%; box-shadow: 0 0 0 3px rgba(37,99,235,.22), 0 0 24px rgba(37,99,235,.35), inset 0 2px 6px rgba(255,255,255,.65); }
    50%     { border-radius: 54% 46% 48% 52% / 50% 54% 46% 50%; box-shadow: 0 0 0 5px rgba(37,99,235,.28), 0 0 38px rgba(37,99,235,.5), inset 0 2px 6px rgba(255,255,255,.65); }
}
#fsw-sphere.listening::before { animation-duration: 5s; }
#fsw-sphere.listening::after  {
    content: ''; position: absolute; inset: 0; border-radius: 50%;
    border: 2px solid rgba(37,99,235,.4);
    animation: fsw-listen-ring 1.8s ease-out infinite;
    pointer-events: none;
}
@keyframes fsw-listen-ring {
    0%   { transform: scale(.9);  opacity: .8; }
    100% { transform: scale(1.08); opacity: 0; }
}

/* ── SPEAKING — continuous green breathing glow ── */
#fsw-sphere.speaking {
    background: linear-gradient(145deg, #d1fae5, #ecfdf5);
    /* Two simultaneous animations:
       1. fsw-speak-breathe — pulsing green box-shadow (the "always green" fix)
       2. fsw-morph-speak   — subtle border-radius morph  */
    animation: fsw-speak-breathe 1.5s ease-in-out infinite,
               fsw-morph-speak   4s   ease-in-out infinite;
}
@keyframes fsw-speak-breathe {
    0%, 100% {
        box-shadow: 0 0 0 3px rgba(5,150,105,.22),
                    0 0 22px rgba(5,150,105,.42),
                    inset 0 2px 6px rgba(255,255,255,.65);
    }
    50% {
        box-shadow: 0 0 0 6px rgba(5,150,105,.32),
                    0 0 55px rgba(5,150,105,.82),
                    0 0 90px rgba(16,185,129,.28),
                    inset 0 2px 6px rgba(255,255,255,.65);
    }
}
@keyframes fsw-morph-speak {
    0%,100% { border-radius: 50%; }
    33%     { border-radius: 53% 47% 49% 51% / 51% 53% 47% 49%; }
    66%     { border-radius: 47% 53% 51% 49% / 53% 47% 51% 49%; }
}
#fsw-sphere.speaking::before { animation-duration: 3.5s; background: linear-gradient(45deg,rgba(255,255,255,.5),rgba(5,150,105,.18)); }
#fsw-sphere.speaking::after  {
    content: ''; position: absolute; inset: 0; border-radius: 50%;
    background: radial-gradient(circle, transparent 55%, rgba(5,150,105,.1) 100%);
    animation: fsw-speak-inner 1.5s ease-in-out infinite;
    pointer-events: none;
}
@keyframes fsw-speak-inner { 0%,100%{opacity:.5} 50%{opacity:1} }

/* ── INTERRUPTED ── */
#fsw-sphere.interrupted {
    background: linear-gradient(145deg, #fef3c7, #fffbeb);
    box-shadow: 0 0 0 3px rgba(217,119,6,.25), 0 0 26px rgba(217,119,6,.45), inset 0 2px 6px rgba(255,255,255,.65);
    animation: fsw-interrupt-flash .55s ease-out 1, fsw-morph-idle 14s ease-in-out .55s infinite;
}
@keyframes fsw-interrupt-flash {
    0%   { box-shadow: 0 0 0 10px rgba(217,119,6,.7),  inset 0 2px 6px rgba(255,255,255,.65); }
    100% { box-shadow: 0 0 0 3px  rgba(217,119,6,.25), 0 0 26px rgba(217,119,6,.45), inset 0 2px 6px rgba(255,255,255,.65); }
}

/* Mic icon */
.fsw-mic-icon {
    font-size: 32px; z-index: 10; position: relative;
    color: #3b82f6;
    filter: drop-shadow(0 2px 4px rgba(0,0,0,.12));
    transition: color .35s ease, transform .25s ease;
}
#fsw-sphere.listening   .fsw-mic-icon { color: #2563eb; transform: scale(1.06); }
#fsw-sphere.speaking    .fsw-mic-icon { color: #059669; transform: scale(1.10); }
#fsw-sphere.interrupted .fsw-mic-icon { color: #d97706; }

/* Wave bars — color follows sphere state */
.fsw-audio-vis  { position: absolute; width: 100%; max-width: 158px; height: 26px; bottom: 0; pointer-events: none; }
.fsw-audio-bars { display: flex; align-items: flex-end; height: 26px; gap: 2px; width: 100%; justify-content: center; }
.fsw-wave-bar   { width: 3px; height: 2px; border-radius: 1px; transition: height .08s ease, background .35s ease; background: rgba(59,130,246,.55); }
#fsw-sphere.listening   .fsw-wave-bar { background: rgba(37,99,235,.65); }
#fsw-sphere.speaking    .fsw-wave-bar { background: rgba(5,150,105,.65); }
#fsw-sphere.interrupted .fsw-wave-bar { background: rgba(217,119,6,.65); }

/* ── LOADER ── */
#fsw-loader {
    position: absolute; inset: 0; border-radius: 22px;
    background: rgba(248,250,255,.9); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
    display: flex; align-items: center; justify-content: center;
    opacity: 0; visibility: hidden; transition: opacity .3s, visibility .3s;
}
#fsw-loader.show { opacity: 1; visibility: visible; }
.fsw-spinner { width: 38px; height: 38px; border-radius: 50%; border: 3px solid rgba(59,130,246,.15); border-top-color: #3b82f6; animation: fsw-spin 1s linear infinite; }
@keyframes fsw-spin { to { transform: rotate(360deg); } }

/* ── ERROR ── */
#fsw-error {
    display: none; color: #ef4444; background: rgba(254,226,226,.85);
    border: 1px solid rgba(239,68,68,.3); padding: 8px 14px; border-radius: 10px;
    font-size: 13px; font-weight: 500; margin-top: 14px; text-align: center;
}
#fsw-error.show { display: block; }
#fsw-retry { background: #ef4444; color: #fff; border: none; border-radius: 6px; padding: 5px 12px; font-size: 12px; cursor: pointer; margin-top: 8px; display: block; transition: background .2s; }
#fsw-retry:hover { background: #dc2626; }

/* ── STATUS ── */
#fsw-status {
    position: absolute; bottom: 52px; left: 50%; transform: translateX(-50%);
    font-size: 11px; color: #475569; padding: 4px 10px; border-radius: 20px;
    background: rgba(255,255,255,.88); display: flex; align-items: center; gap: 6px;
    opacity: 0; transition: opacity .3s; white-space: nowrap;
    box-shadow: 0 1px 6px rgba(0,0,0,.07);
}
#fsw-status.show { opacity: .9; }
#fsw-dot { width: 7px; height: 7px; border-radius: 50%; background: #10b981; flex-shrink: 0; transition: background .3s; }
#fsw-dot.connecting   { background: #f59e0b; animation: fsw-blink .9s ease-in-out infinite; }
#fsw-dot.disconnected { background: #ef4444; }
#fsw-dot.interrupted  { background: #d97706; }
@keyframes fsw-blink { 0%,100%{opacity:1} 50%{opacity:.25} }

/* ════════════════════════════════════════════════════════════
   IMAGE OVERLAY v3.0.0
   — blurred full-screen backdrop + 82% modal card
════════════════════════════════════════════════════════════ */

/* Full-screen blurred backdrop */
#fsw-overlay {
    position: fixed; inset: 0;
    z-index: 2147483647;      /* HIGHEST: above btn and panel */
    background: rgba(6, 8, 18, 0.7);
    backdrop-filter: blur(24px) saturate(160%);
    -webkit-backdrop-filter: blur(24px) saturate(160%);
    display: flex; align-items: center; justify-content: center;
    opacity: 0; pointer-events: none;
    transition: opacity .4s cubic-bezier(.4,0,.2,1);
}
#fsw-overlay.show { opacity: 1; pointer-events: all; }

/* ── MODAL CARD — 82% centred with spring entrance ── */
#fsw-modal-card {
    position: relative;
    width: min(82vw, 1100px);
    max-height: 83vh;
    border-radius: 24px;
    overflow: hidden;
    background: #0a0a0f;
    /* Spring entrance */
    transform: scale(.88) translateY(20px);
    opacity: 0;
    transition: transform .52s cubic-bezier(.175,.885,.32,1.275),
                opacity    .42s ease;
    /* Multi-layer shadow for depth */
    box-shadow:
        0 0 0 1px rgba(255,255,255,.10),
        0 6px  16px rgba(0,0,0,.4),
        0 24px 70px rgba(0,0,0,.65),
        0 70px 130px rgba(0,0,0,.35);
}
#fsw-overlay.show #fsw-modal-card {
    transform: scale(1) translateY(0);
    opacity: 1;
    transition-delay: .06s;   /* card springs in slightly after backdrop appears */
}

/* Image fills card — contain shows the whole image */
#fsw-img {
    display: block; width: 100%; max-height: 83vh;
    object-fit: contain;
    opacity: 0; transition: opacity .55s ease; transition-delay: .1s;
}
#fsw-img.show { opacity: 1; }

/* Top scrim: ensures pill controls are always readable */
.fsw-card-scrim-top {
    position: absolute; top: 0; left: 0; right: 0; height: 120px;
    background: linear-gradient(to bottom, rgba(0,0,0,.62) 0%, transparent 100%);
    pointer-events: none; z-index: 2;
}
/* Bottom scrim: ensures description is readable */
.fsw-card-scrim-bottom {
    position: absolute; bottom: 0; left: 0; right: 0; height: 140px;
    background: linear-gradient(to top, rgba(0,0,0,.68) 0%, transparent 100%);
    pointer-events: none; z-index: 2;
}

/* ── FROSTED PILL: [⊡ Свернуть | ✕] ── */
#fsw-img-controls {
    position: absolute; top: 16px; right: 16px; z-index: 10;
    display: flex; align-items: center;
    background: rgba(10,10,16,.55);
    backdrop-filter: blur(28px) saturate(180%);
    -webkit-backdrop-filter: blur(28px) saturate(180%);
    border: 1px solid rgba(255,255,255,.13);
    border-radius: 50px; overflow: hidden;
    box-shadow: 0 2px 18px rgba(0,0,0,.5), inset 0 0 0 .5px rgba(255,255,255,.05);
    /* Entrance: slides in from top after card springs */
    opacity: 0; transform: translateY(-10px);
    transition: opacity .35s ease, transform .35s ease;
    transition-delay: 0s;
}
#fsw-overlay.show #fsw-img-controls {
    opacity: 1; transform: translateY(0);
    transition-delay: .34s;
}

#fsw-minimize-img {
    display: flex; align-items: center; gap: 7px;
    background: none; border: none; outline: none; color: rgba(255,255,255,.88);
    font: 500 13px/1 'Segoe UI', Roboto, sans-serif; letter-spacing: .15px;
    padding: 10px 16px 10px 13px; cursor: pointer; white-space: nowrap;
    transition: background .16s, color .16s;
}
#fsw-minimize-img i { font-size: 11px; opacity: .8; }
#fsw-minimize-img:hover { background: rgba(255,255,255,.1); color: #fff; }

.fsw-ctrl-sep { width: 1px; height: 20px; background: rgba(255,255,255,.15); flex-shrink: 0; }

#fsw-img-close {
    display: flex; align-items: center; justify-content: center;
    background: none; border: none; outline: none;
    color: rgba(255,255,255,.75); font-size: 13px;
    padding: 10px 13px; cursor: pointer;
    transition: background .16s, color .16s;
}
#fsw-img-close:hover { background: rgba(239,68,68,.35); color: #fff; }

/* ── IMAGE DESCRIPTION ── */
#fsw-img-desc {
    position: absolute; bottom: 22px; left: 50%;
    transform: translateX(-50%) translateY(8px);
    z-index: 10;
    font: 400 14px/1.6 'Segoe UI', Roboto, sans-serif;
    color: rgba(255,255,255,.92); text-align: center;
    max-width: min(580px, 78%);
    text-shadow: 0 1px 8px rgba(0,0,0,.55);
    opacity: 0; pointer-events: none;
    transition: opacity .5s ease, transform .5s ease;
    transition-delay: 0s;
}
#fsw-img-desc.show {
    opacity: 1; transform: translateX(-50%) translateY(0);
    transition-delay: .46s;
}
        `;
        document.head.appendChild(s);
    }

    // ============================================================================
    // DOM
    // ============================================================================

    function buildDOM() {
        ['fsw-btn','fsw-panel','fsw-overlay'].forEach(id => document.getElementById(id)?.remove());

        document.body.insertAdjacentHTML('beforeend', `
<button id="fsw-btn" class="pulse" aria-label="Голосовой ассистент">
  <div class="fsw-btn-inner">
    <div class="fsw-pulse-ring"></div>
    <div class="fsw-bars-mini">
      <div class="fsw-bar-mini"></div>
      <div class="fsw-bar-mini"></div>
      <div class="fsw-bar-mini"></div>
      <div class="fsw-bar-mini"></div>
    </div>
  </div>
</button>

<div id="fsw-panel">
  <div id="fsw-header">
    <span id="fsw-title">Голосовой Ассистент</span>
    <button id="fsw-close-btn" aria-label="Закрыть"><i class="fas fa-times"></i></button>
  </div>
  <div id="fsw-body">

    <!-- Sphere wrapper: sonar rings sit outside overflow:hidden sphere -->
    <div class="fsw-sphere-wrap" id="fsw-sphere-wrap">
      <div class="fsw-ring fsw-ring-1"></div>
      <div class="fsw-ring fsw-ring-2"></div>
      <div class="fsw-ring fsw-ring-3"></div>
      <div id="fsw-sphere">
        <i class="fas fa-microphone fsw-mic-icon"></i>
        <div class="fsw-audio-vis">
          <div class="fsw-audio-bars" id="fsw-audio-bars"></div>
        </div>
      </div>
    </div>

    <div id="fsw-error">Ошибка соединения<br><button id="fsw-retry">Повторить</button></div>
    <div id="fsw-status"><div id="fsw-dot"></div><span id="fsw-status-text">Подключение...</span></div>
    <div id="fsw-loader" class="show"><div class="fsw-spinner"></div></div>
  </div>
</div>

<!-- IMAGE OVERLAY v3.0.0: blurred backdrop + modal card -->
<div id="fsw-overlay">
  <div id="fsw-modal-card">
    <img id="fsw-img" src="" alt="">
    <div class="fsw-card-scrim-top"></div>
    <div class="fsw-card-scrim-bottom"></div>
    <!-- Frosted pill -->
    <div id="fsw-img-controls">
      <button id="fsw-minimize-img" aria-label="Свернуть">
        <i class="fas fa-compress-alt"></i>Свернуть
      </button>
      <div class="fsw-ctrl-sep"></div>
      <button id="fsw-img-close" aria-label="Закрыть"><i class="fas fa-times"></i></button>
    </div>
    <div id="fsw-img-desc"></div>
  </div>
</div>`);

        const barsEl = document.getElementById('fsw-audio-bars');
        for (let i = 0; i < 20; i++) {
            const b = document.createElement('div');
            b.className = 'fsw-wave-bar';
            barsEl.appendChild(b);
        }
    }

    // ============================================================================
    // CACHE + EVENTS
    // ============================================================================

    function cacheUI() {
        STATE.ui = {
            btn:        document.getElementById('fsw-btn'),
            panel:      document.getElementById('fsw-panel'),
            sphereWrap: document.getElementById('fsw-sphere-wrap'),
            sphere:     document.getElementById('fsw-sphere'),
            waveBars:   document.querySelectorAll('.fsw-wave-bar'),
            loader:     document.getElementById('fsw-loader'),
            dot:        document.getElementById('fsw-dot'),
            statusEl:   document.getElementById('fsw-status'),
            statusTxt:  document.getElementById('fsw-status-text'),
            errorEl:    document.getElementById('fsw-error'),
            retryBtn:   document.getElementById('fsw-retry'),
            closeBtn:   document.getElementById('fsw-close-btn'),
            overlay:    document.getElementById('fsw-overlay'),
            modalCard:  document.getElementById('fsw-modal-card'),
            img:        document.getElementById('fsw-img'),
            minimize:   document.getElementById('fsw-minimize-img'),
            imgClose:   document.getElementById('fsw-img-close'),
            imgDesc:    document.getElementById('fsw-img-desc'),
        };
    }

    function bindEvents() {
        STATE.ui.btn.addEventListener('click', handleOpen);
        STATE.ui.closeBtn.addEventListener('click', handleClose);
        STATE.ui.retryBtn.addEventListener('click', connectWebSocket);
        STATE.ui.minimize.addEventListener('click', handleMinimize);
        STATE.ui.imgClose.addEventListener('click', handleClose);
        // Click on blurred backdrop (outside card) → minimize
        STATE.ui.overlay.addEventListener('click', (e) => {
            if (e.target === STATE.ui.overlay) handleMinimize();
        });
    }

    // ============================================================================
    // MODE TRANSITIONS
    // ============================================================================

    async function handleOpen() {
        if (STATE.mode !== 'button') return;
        toMode('dialog');
        if (!STATE.audioContext) await initAudioContext();
        if (!STATE.isConnected && !STATE.ws) connectWebSocket();
        else if (STATE.isConnected && STATE.readyToRecord && !STATE.isRecording) startRecording();
    }

    function handleClose() {
        if (STATE.mode === 'image') hideImageOverlay();
        toMode('button');
        if (STATE.isRecording) stopRecording();
        stopPlayback();
        if (STATE.ws) { try { STATE.ws.close(); } catch {} STATE.ws = null; }
        STATE.isConnected = false; STATE.isSetupComplete = false; STATE.readyToRecord = false;
        if (STATE.pingInterval) { clearInterval(STATE.pingInterval); STATE.pingInterval = null; }
        if (STATE.setupTimeout) { clearTimeout(STATE.setupTimeout);  STATE.setupTimeout = null; }
        STATE.reconnectAttempts = 0;
    }

    function handleMinimize() {
        STATE.mode = 'dialog';
        hideImageOverlay();
    }

    function toMode(mode) {
        STATE.mode = mode;
        const ui = STATE.ui;
        if (mode === 'button') {
            ui.panel.classList.remove('open');
            ui.btn.classList.remove('dialog-open');
            ui.btn.classList.add('pulse');
        } else if (mode === 'dialog') {
            ui.panel.classList.add('open');
            ui.btn.classList.add('dialog-open');
            ui.btn.classList.remove('pulse');
        }
    }

    // ============================================================================
    // IMAGE OVERLAY v3.0.0
    // ============================================================================

    function showImageOverlay(url, description) {
        const ui = STATE.ui;
        STATE.mode = 'image';

        // Slide btn + panel toward their corner
        ui.btn.classList.add('fsw-image-mode');
        ui.panel.classList.add('fsw-image-mode');

        // Reset desc immediately
        ui.imgDesc.classList.remove('show');
        ui.imgDesc.textContent = '';

        // Show blurred backdrop
        ui.overlay.classList.add('show');

        // ── FIX: same URL → onload won't fire — clear src first ──
        ui.img.classList.remove('show');
        ui.img.onload  = null;
        ui.img.onerror = null;
        ui.img.src = '';

        requestAnimationFrame(() => {
            STATE.currentImageUrl = url;

            ui.img.onload = () => {
                ui.img.classList.add('show');
                if (description?.trim()) {
                    ui.imgDesc.textContent = description;
                    ui.imgDesc.classList.add('show');
                }
            };

            ui.img.onerror = () => {
                console.warn('[FSW] Image failed:', url);
                hideImageOverlay();
                if (STATE.mode !== 'button') toMode('dialog');
            };

            ui.img.src = url;
        });
    }

    function hideImageOverlay() {
        const ui = STATE.ui;

        // Slide btn + panel back in
        ui.btn.classList.remove('fsw-image-mode');
        ui.panel.classList.remove('fsw-image-mode');

        // Start fade out
        ui.img.classList.remove('show');
        ui.imgDesc.classList.remove('show');

        setTimeout(() => {
            ui.overlay.classList.remove('show');
            ui.img.onload  = null;
            ui.img.onerror = null;
            ui.img.src = '';
            ui.imgDesc.textContent = '';
        }, 440);

        STATE.currentImageUrl = null;
    }

    // ============================================================================
    // UI STATE UPDATES
    // ============================================================================

    // Central helper: sets sphere + sphereWrap state classes together
    function setSphereState(stateName) {
        const { sphere, sphereWrap } = STATE.ui;
        if (!sphere) return;
        sphere.classList.remove('listening', 'speaking', 'interrupted');
        sphereWrap?.classList.remove('listening', 'speaking', 'interrupted');
        if (stateName) {
            sphere.classList.add(stateName);
            sphereWrap?.classList.add(stateName);
        }
    }

    function updateUIState(state, message) {
        const ui = STATE.ui;
        if (!ui.sphere) return;

        ui.dot.classList.remove('connecting', 'disconnected', 'interrupted');

        if (message) {
            ui.statusTxt.textContent = message;
            ui.statusEl.classList.add('show');
            setTimeout(() => ui.statusEl.classList.remove('show'), 3000);
        }

        switch (state) {
            case 'connecting':
                setSphereState(null);
                ui.dot.classList.add('connecting');
                ui.loader.classList.add('show');
                break;
            case 'connected':
                ui.loader.classList.remove('show');
                ui.errorEl.classList.remove('show');
                break;
            case 'recording':
                setSphereState('listening');
                break;
            case 'playing':
                setSphereState('speaking');   // ← triggers sonar rings + breathe glow
                break;
            case 'interrupted':
                setSphereState('interrupted');
                ui.dot.classList.add('interrupted');
                break;
            case 'error':
                setSphereState(null);
                ui.dot.classList.add('disconnected');
                ui.errorEl.classList.add('show');
                ui.loader.classList.remove('show');
                break;
            case 'disconnected':
                setSphereState(null);
                ui.dot.classList.add('disconnected');
                break;
        }
    }

    function updateAudioVisualization(audioData) {
        if (!STATE.ui.waveBars?.length) return;
        const bars = STATE.ui.waveBars;
        const step = Math.max(1, Math.floor(audioData.length / bars.length));
        for (let i = 0; i < bars.length; i++) {
            let sum = 0;
            for (let j = 0; j < step; j++) {
                const idx = i * step + j;
                if (idx < audioData.length) sum += Math.abs(audioData[idx]);
            }
            bars[i].style.height = (2 + Math.min(24, Math.floor((sum / step) * 130))) + 'px';
        }
    }

    function resetAudioVisualization() {
        STATE.ui.waveBars?.forEach(b => b.style.height = '2px');
    }

    // ============================================================================
    // AUDIO CONTEXT
    // ============================================================================

    async function initAudioContext() {
        if (STATE.audioContext) {
            if (STATE.audioContext.state === 'suspended') await STATE.audioContext.resume();
            return;
        }
        const Ctx = window.AudioContext || window.webkitAudioContext;
        STATE.audioContext = new Ctx({ sampleRate: CONFIG.audio.playbackSampleRate, latencyHint: 'interactive' });
        CONFIG.audio.actualSampleRate = STATE.audioContext.sampleRate;
        CONFIG.audio.needsResampling  = STATE.audioContext.sampleRate !== CONFIG.audio.outputSampleRate;
        console.log(`[FSW] AudioContext: ${CONFIG.audio.actualSampleRate}Hz`);
        await loadAudioWorklets();
    }

    async function resumeAudioCtx() {
        if (STATE.audioContext?.state === 'suspended') await STATE.audioContext.resume();
    }

    async function loadAudioWorklets() {
        const addModule = async (code) => {
            const blob = new Blob([code], { type: 'application/javascript' });
            const url  = URL.createObjectURL(blob);
            await STATE.audioContext.audioWorklet.addModule(url);
            URL.revokeObjectURL(url);
        };
        try {
            await addModule(RECORDER_WORKLET_CODE);
            STATE.audioWorkletReady = true;
            await addModule(STREAM_WORKLET_CODE);
            STATE.streamWorkletReady = true;
            console.log('[FSW] ✅ AudioWorklets loaded');
        } catch (e) { console.error('[FSW] ❌ AudioWorklet load failed:', e); }
    }

    // ============================================================================
    // WEBSOCKET
    // ============================================================================

    function getWSEndpoint() {
        const base = CONFIG.serverUrl.replace('https://', 'wss://').replace('http://', 'ws://');
        return CONFIG.model === '3.1'
            ? `${base}/ws/gemini-31/${CONFIG.assistantId}`
            : `${base}/ws/gemini/${CONFIG.assistantId}`;
    }

    function connectWebSocket() {
        updateUIState('connecting', 'Подключение...');
        try {
            STATE.ws = new WebSocket(getWSEndpoint());
            STATE.ws.binaryType = 'arraybuffer';

            STATE.ws.onopen = () => {
                console.log('[FSW] ✅ WS connected');
                STATE.isConnected = true; STATE.reconnectAttempts = 0;
                updateUIState('connected', 'Соединение установлено');
                STATE.pingInterval = setInterval(() => {
                    if (STATE.ws?.readyState === WebSocket.OPEN)
                        STATE.ws.send(JSON.stringify({ type: 'ping' }));
                }, CONFIG.ws.pingInterval);
                STATE.setupTimeout = setTimeout(() => {
                    if (!STATE.isSetupComplete) {
                        STATE.isSetupComplete = true; STATE.readyToRecord = true;
                        if (STATE.mode !== 'button') startRecording();
                    }
                }, CONFIG.setup.maxSetupWait);
            };

            STATE.ws.onmessage = handleWSMessage;

            STATE.ws.onerror = (err) => { console.error('[FSW] WS error:', err); updateUIState('error', 'Ошибка сервера'); };

            STATE.ws.onclose = (ev) => {
                console.log('[FSW] WS closed:', ev.code);
                STATE.isConnected = false; STATE.readyToRecord = false;
                stopPlayback(); if (STATE.isRecording) stopRecording();
                if (STATE.pingInterval) { clearInterval(STATE.pingInterval); STATE.pingInterval = null; }
                updateUIState('disconnected', 'Отключено');
                if (STATE.mode !== 'button' && STATE.reconnectAttempts < CONFIG.ws.maxReconnectAttempts) {
                    STATE.reconnectAttempts++;
                    setTimeout(connectWebSocket, CONFIG.ws.reconnectDelay);
                }
            };

        } catch (e) { console.error('[FSW] WS init error:', e); updateUIState('error', 'Ошибка сети'); }
    }

    // ============================================================================
    // MESSAGE HANDLER
    // ============================================================================

    function handleWSMessage(event) {
        if (event.data instanceof ArrayBuffer || event.data instanceof Blob) return;
        let data;
        try { data = JSON.parse(event.data); } catch { return; }

        switch (data.type) {
            case 'gemini.setup.complete':
                STATE.isSetupComplete = true;
                clearTimeout(STATE.setupTimeout);
                updateUIState('connected', 'Настройка завершена');
                setTimeout(() => {
                    STATE.readyToRecord = true;
                    if (STATE.mode !== 'button') startRecording();
                }, CONFIG.setup.waitAfterSetup);
                break;
            case 'response.audio.delta':
                handleAudioDelta(data);
                break;
            case 'assistant.speech.started':
                STATE.isSpeaking = true;
                updateUIState('playing', 'Ассистент говорит');
                if (!STATE.isPlaying) startAudioStream();
                break;
            case 'assistant.speech.ended':
                STATE.isSpeaking = false;
                stopPlayback();
                if (STATE.isRecording) updateUIState('recording', 'Слушаю...');
                if (STATE.ws?.readyState === WebSocket.OPEN)
                    STATE.ws.send(JSON.stringify({ type: 'audio_playback.stopped' }));
                break;
            case 'conversation.interrupted':
                STATE.isSpeaking = false;
                stopPlayback();
                updateUIState('interrupted', 'Прервано');
                setTimeout(() => { if (STATE.isRecording) updateUIState('recording', 'Слушаю...'); }, 800);
                break;
            case 'function_call.completed':
                if (data.function === 'show_image' && data.result?.url) {
                    if (STATE.mode === 'button') toMode('dialog');
                    showImageOverlay(data.result.url, data.result.description || '');
                }
                break;
            case 'pong': break;
            case 'connection_status': console.log('[FSW] Server status:', data.message); break;
            case 'error':
                console.error('[FSW] Server error:', data.error);
                updateUIState('error', data.error?.message || 'Ошибка');
                break;
        }
    }

    // ============================================================================
    // RECORDING
    // ============================================================================

    async function startRecording() {
        if (STATE.isRecording) return;
        if (STATE.audioContext?.state === 'suspended') await STATE.audioContext.resume();
        if (!STATE.audioWorkletReady) await initAudioContext();
        try {
            STATE.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
            });
            const source      = STATE.audioContext.createMediaStreamSource(STATE.mediaStream);
            const workletNode = new AudioWorkletNode(STATE.audioContext, 'recorder-worklet');

            workletNode.port.onmessage = (event) => {
                if (!STATE.isRecording) return;
                const audioData = event.data.data;
                const pcmData   = float32ToPCM16(audioData);
                updateAudioVisualization(audioData);

                const rms = calculateRMS(audioData);
                const db  = 20 * Math.log10(rms + 1e-10);
                if (db > CONFIG.vad.speechThreshold && !STATE.isSpeaking) {
                    setSphereState('listening');
                    const now = Date.now();
                    if (STATE.ws?.readyState === WebSocket.OPEN && now - STATE.lastSpeechNotifyTime > 500) {
                        STATE.lastSpeechNotifyTime = now;
                        STATE.ws.send(JSON.stringify({ type: 'speech.user_started' }));
                    }
                }

                if (STATE.ws?.readyState === WebSocket.OPEN)
                    STATE.ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: arrayBufferToBase64(pcmData.buffer) }));
            };

            source.connect(workletNode);
            // NOTE: do NOT connect workletNode to destination — would echo mic
            STATE.audioWorkletNode = { source, workletNode };
            STATE.isRecording = true;
            updateUIState('recording', 'Слушаю...');
            console.log('[FSW] ✅ Recording started');

        } catch (err) {
            console.error('[FSW] Mic error:', err);
            updateUIState('error', 'Нет доступа к микрофону');
        }
    }

    function stopRecording() {
        if (!STATE.isRecording) return;
        STATE.isRecording = false;
        if (STATE.ws?.readyState === WebSocket.OPEN)
            STATE.ws.send(JSON.stringify({ type: 'speech.user_stopped' }));
        if (STATE.mediaStream) { STATE.mediaStream.getTracks().forEach(t => t.stop()); STATE.mediaStream = null; }
        if (STATE.audioWorkletNode) {
            try { STATE.audioWorkletNode.source.disconnect(); STATE.audioWorkletNode.workletNode?.disconnect(); } catch {}
            STATE.audioWorkletNode = null;
        }
        resetAudioVisualization();
    }

    // ============================================================================
    // PLAYBACK
    // ============================================================================

    function handleAudioDelta(data) {
        if (!data.delta) return;
        try {
            const binaryString = atob(data.delta);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
            const pcm16   = new Int16Array(bytes.buffer);
            const float32 = new Float32Array(pcm16.length);
            for (let i = 0; i < pcm16.length; i++) float32[i] = pcm16[i] / 32768.0;
            let audioData = float32;
            if (CONFIG.audio.needsResampling)
                audioData = resampleAudio(float32, CONFIG.audio.outputSampleRate, CONFIG.audio.actualSampleRate);
            STATE.audioStreamNode?.port.postMessage({ type: 'audioData', buffer: audioData });
            if (!STATE.isPlaying) startAudioStream();
        } catch (e) { console.error('[FSW] Audio delta error:', e); }
    }

    function startAudioStream() {
        if (STATE.isPlaying || !STATE.streamWorkletReady) return;
        try {
            if (!STATE.audioStreamNode) {
                STATE.audioStreamNode = new AudioWorkletNode(STATE.audioContext, 'audio-stream-processor');
                STATE.audioStreamNode.connect(STATE.audioContext.destination);
            }
            STATE.isPlaying = true;
        } catch (e) { console.error('[FSW] Stream start error:', e); }
    }

    function stopPlayback() {
        if (!STATE.isPlaying) return;
        STATE.audioStreamNode?.port.postMessage({ type: 'clear' });
        STATE.isPlaying = false;
        resetAudioVisualization();
    }

    // ============================================================================
    // UTILS
    // ============================================================================

    function resampleAudio(inputBuffer, inputRate, outputRate) {
        if (inputRate === outputRate) return inputBuffer;
        const ratio = inputRate / outputRate;
        const out   = new Float32Array(Math.round(inputBuffer.length / ratio));
        for (let i = 0; i < out.length; i++) {
            const s = i * ratio, f = Math.floor(s), c = Math.min(f + 1, inputBuffer.length - 1);
            out[i] = inputBuffer[f] * (1 - (s - f)) + inputBuffer[c] * (s - f);
        }
        return out;
    }

    function float32ToPCM16(float32Array) {
        const pcm16 = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return pcm16;
    }

    function calculateRMS(float32Array) {
        let sum = 0;
        for (let i = 0; i < float32Array.length; i++) sum += float32Array[i] * float32Array[i];
        return Math.sqrt(sum / float32Array.length);
    }

    function arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
        return btoa(binary);
    }

    // ============================================================================
    // STARTUP
    // ============================================================================

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

})();
