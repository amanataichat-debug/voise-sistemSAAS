/**
 * widget-translate.js — Виджет встраивания переводчика Voicyfy
 * OpenAI Realtime Translation API (gpt-realtime-translate)
 *
 * ✅ v1.0: Initial Translate widget
 * ✅ v1.1: data-test-mode (broadcast событий через window),
 *          data-show-subtitles (скрыть встроенные субтитры),
 *          уникальные классы контейнера/кнопки, надёжный динамический инжект
 *
 * Логика:
 *  - Кнопка-микрофон: toggle записи
 *  - Запись через ScriptProcessor → PCM16 24kHz → base64
 *  - Шлёт {"type":"input_audio_buffer.append","audio":base64} в /ws/translate/{id}
 *  - Принимает session.output_audio.delta → проигрывает через AudioContext
 *  - Принимает session.input_transcript.* / session.output_transcript.* → субтитры
 *  - Auto-reconnect при разрыве соединения
 *
 * Подключение:
 *  <script src="https://voicyfy.ru/static/widget-translate.js"
 *          data-assistant-id="{id}" data-server="https://voicyfy.ru"></script>
 */
(function () {
    'use strict';

    // ── Конфиг из data-атрибутов скрипта ──────────────────────────────────
    // ── Конфиг из data-атрибутов скрипта ──────────────────────────────────
    // При обычном встраивании работает document.currentScript.
    // При динамическом инжекте (вкладка «Тестирование») currentScript === null,
    // поэтому ищем скрипт виджета с data-assistant-id вручную.
    var currentScript = document.currentScript
        || document.querySelector('script[data-assistant-id][src*="widget-translate.js"]')
        || (function () {
            var scripts = document.getElementsByTagName('script');
            return scripts[scripts.length - 1];
        })();

    var ASSISTANT_ID = currentScript.getAttribute('data-assistant-id');
    var SERVER = (currentScript.getAttribute('data-server') || window.location.origin).replace(/\/$/, '');
    // 🆕 v1.1: режим тестирования и управление встроенными субтитрами
    var SHOW_SUBTITLES = currentScript.getAttribute('data-show-subtitles') !== 'false';
    var TEST_MODE = currentScript.getAttribute('data-test-mode') === 'true';

    if (!ASSISTANT_ID) {
        console.error('[TRANSLATE-WIDGET] data-assistant-id не указан');
        return;
    }

    var CONFIG = {
        sampleRate: 24000,        // PCM16 24kHz — формат translation API
        bufferSize: 4096,
        reconnectDelay: 2000,
        maxReconnectAttempts: 10
    };

    var STATE = {
        ws: null,
        audioContext: null,        // запись (вход)
        playbackContext: null,     // воспроизведение (выход)
        mediaStream: null,
        processor: null,
        source: null,
        isRecording: false,
        reconnectAttempts: 0,
        manualClose: false,
        playHead: 0,               // время для последовательного проигрывания чанков
        ui: {}
    };

    // ── Утилиты аудио ─────────────────────────────────────────────────────
    function float32ToPCM16(float32) {
        var pcm = new Int16Array(float32.length);
        for (var i = 0; i < float32.length; i++) {
            var s = Math.max(-1, Math.min(1, float32[i]));
            pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return pcm;
    }

    function downsampleTo24k(buffer, inputRate) {
        if (inputRate === CONFIG.sampleRate) return buffer;
        var ratio = inputRate / CONFIG.sampleRate;
        var newLen = Math.round(buffer.length / ratio);
        var result = new Float32Array(newLen);
        var offsetResult = 0, offsetBuffer = 0;
        while (offsetResult < newLen) {
            var nextOffset = Math.round((offsetResult + 1) * ratio);
            var accum = 0, count = 0;
            for (var i = offsetBuffer; i < nextOffset && i < buffer.length; i++) {
                accum += buffer[i];
                count++;
            }
            result[offsetResult] = count > 0 ? accum / count : 0;
            offsetResult++;
            offsetBuffer = nextOffset;
        }
        return result;
    }

    function arrayBufferToBase64(buffer) {
        var bytes = new Uint8Array(buffer);
        var binary = '';
        var chunk = 0x8000;
        for (var i = 0; i < bytes.length; i += chunk) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
        }
        return btoa(binary);
    }

    function base64ToArrayBuffer(b64) {
        var binary = atob(b64);
        var len = binary.length;
        var bytes = new Uint8Array(len);
        for (var i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
        return bytes.buffer;
    }

    // ── Воспроизведение переведённого аудио (PCM16 24kHz) ─────────────────
    function ensurePlaybackContext() {
        if (!STATE.playbackContext) {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            STATE.playbackContext = new Ctx({ sampleRate: CONFIG.sampleRate });
            STATE.playHead = STATE.playbackContext.currentTime;
        }
        if (STATE.playbackContext.state === 'suspended') {
            STATE.playbackContext.resume();
        }
    }

    function playAudioChunk(b64) {
        ensurePlaybackContext();
        var arrayBuffer = base64ToArrayBuffer(b64);
        var pcm16 = new Int16Array(arrayBuffer);
        var float32 = new Float32Array(pcm16.length);
        for (var i = 0; i < pcm16.length; i++) {
            float32[i] = pcm16[i] / 0x8000;
        }
        var audioBuffer = STATE.playbackContext.createBuffer(1, float32.length, CONFIG.sampleRate);
        audioBuffer.getChannelData(0).set(float32);

        var srcNode = STATE.playbackContext.createBufferSource();
        srcNode.buffer = audioBuffer;
        srcNode.connect(STATE.playbackContext.destination);

        var now = STATE.playbackContext.currentTime;
        if (STATE.playHead < now) STATE.playHead = now;
        srcNode.start(STATE.playHead);
        STATE.playHead += audioBuffer.duration;
    }

    // ── WebSocket ─────────────────────────────────────────────────────────
    function wsUrl() {
        var proto = SERVER.indexOf('https') === 0 ? 'wss' : 'ws';
        var host = SERVER.replace(/^https?:\/\//, '');
        return proto + '://' + host + '/ws/translate/' + ASSISTANT_ID;
    }

    function connect() {
        STATE.manualClose = false;
        setStatus('connecting', 'Подключение…');

        try {
            STATE.ws = new WebSocket(wsUrl());
        } catch (e) {
            console.error('[TRANSLATE-WIDGET] WS error:', e);
            scheduleReconnect();
            return;
        }

        STATE.ws.onopen = function () {
            console.log('[TRANSLATE-WIDGET] ✅ WebSocket connected');
            STATE.reconnectAttempts = 0;
            setStatus('ready', 'Готов к переводу');
        };

        STATE.ws.onmessage = function (event) {
            var data;
            try { data = JSON.parse(event.data); } catch (e) { return; }
            handleServerEvent(data);
        };

        STATE.ws.onerror = function (e) {
            console.warn('[TRANSLATE-WIDGET] WS error', e);
        };

        STATE.ws.onclose = function () {
            console.log('[TRANSLATE-WIDGET] WS closed');
            if (!STATE.manualClose) scheduleReconnect();
        };
    }

    function scheduleReconnect() {
        if (STATE.reconnectAttempts >= CONFIG.maxReconnectAttempts) {
            setStatus('error', 'Соединение потеряно');
            return;
        }
        STATE.reconnectAttempts++;
        setStatus('connecting', 'Переподключение…');
        setTimeout(connect, CONFIG.reconnectDelay);
    }

    function handleServerEvent(data) {
        var t = data.type;

        // 🆕 v1.1: в режиме тестирования транслируем все события наружу,
        // чтобы родительская страница могла рисовать субтитры сама.
        if (TEST_MODE) {
            try {
                window.dispatchEvent(new CustomEvent('voicyfy-translate-event', { detail: data }));
            } catch (e) {}
        }

        switch (t) {
            case 'session.output_audio.delta':
                if (data.delta) playAudioChunk(data.delta);
                break;
            case 'session.input_transcript.delta':
                if (SHOW_SUBTITLES) appendSubtitle('source', data.delta || '');
                break;
            case 'session.input_transcript.done':
                // финал исходника — оставляем как есть
                break;
            case 'session.output_transcript.delta':
                if (SHOW_SUBTITLES) appendSubtitle('target', data.delta || '');
                break;
            case 'session.output_transcript.done':
                break;
            case 'translate.greeting':
                if (data.message && SHOW_SUBTITLES) setSubtitle('target', data.message);
                break;
            case 'error':
                console.error('[TRANSLATE-WIDGET] Server error:', data);
                if (data.code === 'openai_key_required') {
                    setStatus('error', 'Нужен OpenAI ключ');
                } else {
                    setStatus('error', data.message || 'Ошибка');
                }
                break;
            case 'pong':
                break;
        }
    }

    // ── Запись микрофона ──────────────────────────────────────────────────
    function startRecording() {
        if (STATE.isRecording) return;

        var Ctx = window.AudioContext || window.webkitAudioContext;
        STATE.audioContext = new Ctx();

        navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        }).then(function (stream) {
            STATE.mediaStream = stream;
            STATE.source = STATE.audioContext.createMediaStreamSource(stream);
            STATE.processor = STATE.audioContext.createScriptProcessor(CONFIG.bufferSize, 1, 1);

            var inputRate = STATE.audioContext.sampleRate;

            STATE.processor.onaudioprocess = function (e) {
                if (!STATE.isRecording) return;
                if (!STATE.ws || STATE.ws.readyState !== WebSocket.OPEN) return;

                var input = e.inputBuffer.getChannelData(0);
                var down = downsampleTo24k(input, inputRate);
                var pcm = float32ToPCM16(down);
                var b64 = arrayBufferToBase64(pcm.buffer);

                STATE.ws.send(JSON.stringify({
                    type: 'input_audio_buffer.append',
                    audio: b64
                }));
            };

            STATE.source.connect(STATE.processor);
            STATE.processor.connect(STATE.audioContext.destination);

            STATE.isRecording = true;
            ensurePlaybackContext();
            updateMicButton(true);
            setStatus('recording', 'Слушаю…');
        }).catch(function (err) {
            console.error('[TRANSLATE-WIDGET] Mic error:', err);
            setStatus('error', 'Нет доступа к микрофону');
        });
    }

    function stopRecording() {
        if (!STATE.isRecording) return;
        STATE.isRecording = false;

        try { if (STATE.processor) STATE.processor.disconnect(); } catch (e) {}
        try { if (STATE.source) STATE.source.disconnect(); } catch (e) {}
        if (STATE.mediaStream) {
            STATE.mediaStream.getTracks().forEach(function (tr) { tr.stop(); });
        }
        if (STATE.audioContext) {
            try { STATE.audioContext.close(); } catch (e) {}
            STATE.audioContext = null;
        }
        updateMicButton(false);
        setStatus('ready', 'Готов к переводу');
    }

    function toggleRecording() {
        if (STATE.isRecording) stopRecording();
        else startRecording();
    }

    // ── UI ────────────────────────────────────────────────────────────────
    function injectStyles() {
        var css = ''
            + '.vfy-tr-widget{position:fixed;bottom:20px;right:20px;width:320px;'
            + 'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;'
            + 'background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.16);'
            + 'padding:18px;z-index:999999;box-sizing:border-box;}'
            + '.vfy-tr-header{display:flex;align-items:center;gap:8px;margin-bottom:12px;}'
            + '.vfy-tr-title{font-size:14px;font-weight:600;color:#1f2937;}'
            + '.vfy-tr-badge{background:#FEF3C7;color:#92400E;font-size:10px;padding:3px 8px;'
            + 'border-radius:4px;text-transform:uppercase;font-weight:700;letter-spacing:.5px;}'
            + '.vfy-tr-subtitles{min-height:64px;margin:12px 0;display:flex;flex-direction:column;gap:6px;}'
            + '.vfy-tr-source{color:#9ca3af;font-size:13px;line-height:1.4;min-height:18px;}'
            + '.vfy-tr-target{color:#111827;font-size:15px;font-weight:500;line-height:1.4;min-height:20px;}'
            + '.vfy-tr-controls{display:flex;align-items:center;gap:12px;}'
            + '.vfy-tr-mic{width:56px;height:56px;border-radius:50%;border:none;cursor:pointer;'
            + 'background:#378ADD;color:#fff;font-size:22px;display:flex;align-items:center;'
            + 'justify-content:center;transition:all .2s;flex-shrink:0;}'
            + '.vfy-tr-mic:hover{background:#2f78c4;}'
            + '.vfy-tr-mic.recording{background:#ef4444;animation:vfy-pulse 1.4s infinite;}'
            + '@keyframes vfy-pulse{0%{box-shadow:0 0 0 0 rgba(239,68,68,.5);}'
            + '70%{box-shadow:0 0 0 12px rgba(239,68,68,0);}100%{box-shadow:0 0 0 0 rgba(239,68,68,0);}}'
            + '.vfy-tr-status{font-size:13px;color:#6b7280;}'
            + '.vfy-tr-status.error{color:#ef4444;}';
        var style = document.createElement('style');
        style.textContent = css;
        document.head.appendChild(style);
    }

    function buildUI() {
        var root = document.createElement('div');
        // 🆕 v1.1: уникальные классы контейнера/кнопки, чтобы родительская
        // страница могла их найти и чтобы не было коллизий с обычным виджетом.
        root.className = 'vfy-tr-widget voicyfy-translate-widget-container';
        root.innerHTML = ''
            + '<div class="vfy-tr-header">'
            + '  <span class="vfy-tr-title">Переводчик</span>'
            + '  <span class="vfy-tr-badge">realtime</span>'
            + '</div>'
            + '<div class="vfy-tr-subtitles">'
            + '  <div class="vfy-tr-source"></div>'
            + '  <div class="vfy-tr-target"></div>'
            + '</div>'
            + '<div class="vfy-tr-controls">'
            + '  <button class="vfy-tr-mic voicyfy-translate-widget-button" title="Микрофон">🎤</button>'
            + '  <span class="vfy-tr-status">Подключение…</span>'
            + '</div>';
        document.body.appendChild(root);

        STATE.ui.root = root;
        STATE.ui.source = root.querySelector('.vfy-tr-source');
        STATE.ui.target = root.querySelector('.vfy-tr-target');
        STATE.ui.mic = root.querySelector('.vfy-tr-mic');
        STATE.ui.status = root.querySelector('.vfy-tr-status');

        // 🆕 v1.1: если субтитры рендерит сама страница — прячем встроенный блок
        if (!SHOW_SUBTITLES) {
            var subs = root.querySelector('.vfy-tr-subtitles');
            if (subs) subs.style.display = 'none';
        }

        STATE.ui.mic.addEventListener('click', toggleRecording);
    }

    function setStatus(kind, text) {
        if (!STATE.ui.status) return;
        STATE.ui.status.textContent = text;
        STATE.ui.status.className = 'vfy-tr-status' + (kind === 'error' ? ' error' : '');
    }

    function updateMicButton(recording) {
        if (!STATE.ui.mic) return;
        STATE.ui.mic.className = 'vfy-tr-mic' + (recording ? ' recording' : '');
    }

    // Субтитры: накапливаем дельты внутри текущей фразы.
    var subtitleBuf = { source: '', target: '' };

    function appendSubtitle(kind, delta) {
        subtitleBuf[kind] += delta;
        if (STATE.ui[kind]) STATE.ui[kind].textContent = subtitleBuf[kind];
    }

    function setSubtitle(kind, text) {
        subtitleBuf[kind] = text;
        if (STATE.ui[kind]) STATE.ui[kind].textContent = text;
    }

    // ── Init ──────────────────────────────────────────────────────────────
    function init() {
        injectStyles();
        buildUI();
        connect();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
