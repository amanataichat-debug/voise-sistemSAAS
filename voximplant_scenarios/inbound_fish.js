require(Modules.OpenAI);

/*
 * Voximplant INBOUND Fish Script v1.0
 * ====================================================================
 * Архитектура:
 *   - OpenAI Realtime API (output_modalities: text) — STT + turn detection
 *     + reasoning. Серверный VAD сам определяет конец реплики и перебивание.
 *     Отдельный ASR не нужен: модель транскрибирует речь сама.
 *   - Fish Audio TTS через прокси Voksy AI. У Voximplant нет встроенного
 *     модуля Fish (в отличие от Modules.Cartesia), а медиа-канал VoxEngine
 *     принимает только собственный JSON-протокол, тогда как Fish говорит на
 *     MessagePack. Поэтому озвучка идёт так:
 *
 *         сценарий --{event:"text"}--> /ws/fish/tts/{id} --> Fish Audio
 *         звонок   <--- media-фреймы PCM16 --- прокси <--- PCM ---┘
 *
 *     Ключ Fish в сценарий не попадает: прокси берёт его из профиля
 *     владельца ассистента.
 *   - Динамический конфиг из /api/telephony/config
 *   - Структурированный диалог (dialogLog) для UI
 *   - Запись звонка + биллинг, function calls
 *
 * Чем отличается от cartesia_inbound (откуда взята вся обвязка):
 *
 *   1. Вместо плеера Cartesia — WebSocket к прокси. Сокет один на весь
 *      звонок, поднимается ещё до ответа на звонок (аналог прогрева плеера).
 *   2. Нет context_id и continue: у Fish контекстов нет. Реплика
 *      закрывается событием flush.
 *   3. Темп отдачи аудио держит сам Voximplant: медиа из WebSocket копится
 *      в буфере (до 10 с) и проигрывается в реальном времени. Поэтому
 *      перебивание — это clearMediaBuffer() на сокете: очередь гасится
 *      мгновенно, ждать нечего.
 *   4. Границы реплики приходят от прокси служебными сообщениями
 *      speech_started / speech_done: штатные MEDIA_STARTED / MEDIA_ENDED
 *      для этого не годятся — они относятся к медиапотоку целиком, а он
 *      живёт весь звонок. speech_done несёт remaining_ms — сколько аудио
 *      ещё доигрывает в буфере Voximplant; по нему кладём трубку после
 *      прощания, не гадая о длине фразы.
 *      Сам MEDIA_STARTED при этом слушаем как индикатор: он приходит
 *      только если Voximplant принял StartEvent прокси. Нет его — значит
 *      звука в трубке не будет, сколько бы текста мы ни отправили.
 */

// ============================================================================
// КОНСТАНТЫ (крутить здесь, логику не трогать)
// ============================================================================
var VAD_SILENCE_MS   = 500;          // хвост тишины до конца реплики
var VAD_PREFIX_MS    = 300;
var VAD_THRESHOLD    = 0.5;
var FIRST_FLUSH_MIN  = 25;           // ранний flush первого предложения реплики
var TEXT_BATCH_MIN   = 40;           // копим дельты до этой длины перед отправкой
var TTS_WATCHDOG_MS  = 4000;         // нет звука после отправки текста → тревога
var HANGUP_GUARD_MS  = 15000;        // потолок ожидания конца прощания
var HANGUP_TAIL_MS   = 250;          // запас после remaining_ms перед hangup
var TTS_REOPEN_MAX   = 2;            // попыток переоткрыть сокет к прокси

// ============================================================================
// ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ БИЛЛИНГА
// ============================================================================
var call_session_history_id = null;
var record_url = null;
var call_cost = 0;
var call_duration = 0;

VoxEngine.addEventListener(AppEvents.Started, function(e) {
    call_session_history_id = e.sessionId;
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("🚀 APP STARTED (INBOUND Fish v1.0)");
    Logger.write("🔑 Session History ID: " + call_session_history_id);
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
});

// ============================================================================
// ОСНОВНОЙ ОБРАБОТЧИК ВХОДЯЩЕГО ЗВОНКА
// ============================================================================
VoxEngine.addEventListener(AppEvents.CallAlerting, async function(e) {
    var call = e.call;
    var caller_number = call.callerid() || "unknown";
    var called_number = e.destination || "unknown";
    var call_id = call.id();
    var chat_id = 'vox_' + Math.random().toString(36).substring(2, 15);

    // ── Состояние сценария ──────────────────────────────────────────────────
    var realtimeAPIClient = null;
    var callAnswered = false;
    var sessionConfigured = false;
    var greetingStarted = false;
    var isInterrupted = false;
    var isHangingUp = false;

    // ── Сокет к прокси синтеза ──────────────────────────────────────────────
    var ttsSocket = null;
    var ttsOpen = false;
    var ttsAttached = false;     // sendMediaTo(call) уже вызван
    var ttsQueue = [];           // текст, накопленный до открытия сокета
    var ttsFlushQueued = false;  // в очереди есть незакрытая реплика
    var ttsReopens = 0;
    var ttsMediaAccepted = false;  // пришёл ли MEDIA_STARTED (StartEvent принят)

    // ── Состояние текущей реплики ассистента ────────────────────────────────
    var turnFullText = "";
    var turnStarted = false;     // в TTS по этой реплике что-то уже уходило
    var firstFlushDone = false;  // ранний flush первого предложения сделан
    var deltaBuffer    = "";     // дельты, ещё не ушедшие в синтез
    var audioConfirmed = false;  // прокси подтвердил начало звука

    // ── Watchdog / завершение по прощанию ───────────────────────────────────
    var watchdogTimer = null;
    var watchdogDisabled = false;
    var hangupAfterSpeech = false;
    var hangupGuardTimer = null;

    // ── Метрики задержки ────────────────────────────────────────────────────
    var mVadStop = 0, mRespCreated = 0, mFirstDelta = 0, mFirstText = 0;

    // ── Структурированный диалог ────────────────────────────────────────────
    var userMessageBuffer = "";
    var assistantMessageBuffer = "";
    var dialogLog = [];
    var lastFunctionResult = null;
    var logCounter = 0;

    // ── Конфиг ──────────────────────────────────────────────────────────────
    var CONFIG = null;
    var ASSISTANT_ID = null;
    var functionNameToIdMap = {};

    var CONFIG_URL = "https://voicyfy.ru/api/telephony/config?phone=" + called_number.replace(/\D/g, '');
    var FUNCTIONS_URL = "https://voicyfy.ru/api/voximplant/functions/execute";
    var LOG_URL = "https://voicyfy.ru/api/voximplant/log";

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("📞 INBOUND CALL (Fish v1.0)");
    Logger.write("   From: " + caller_number);
    Logger.write("   To: " + called_number);
    Logger.write("   Call ID: " + call_id);
    Logger.write("   Session ID: " + call_session_history_id);
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

    // =========================================================================
    // ФУНКЦИЯ ЛОГИРОВАНИЯ ДИАЛОГА НА БЕКЕНД
    // =========================================================================
    async function sendConversationLog(isFinal) {
        try {
            logCounter++;
            Logger.write("📤 SENDING LOG #" + logCounter + (isFinal ? " (FINAL)" : "") +
                " — dialog turns: " + dialogLog.length);

            var payload = {
                assistant_id: ASSISTANT_ID,
                chat_id: chat_id,
                call_id: call_id,
                caller_number: "INBOUND: " + caller_number,
                type: "conversation",
                data: {
                    user_message: userMessageBuffer,
                    assistant_message: assistantMessageBuffer,
                    function_result: lastFunctionResult,
                    dialog: dialogLog
                }
            };

            if (isFinal) {
                if (record_url)              payload.record_url = record_url;
                if (call_session_history_id) payload.call_session_history_id = String(call_session_history_id);
                payload.call_cost     = call_cost;
                payload.call_duration = call_duration;

                Logger.write("📊 Billing: record=" + (record_url ? "YES" : "NO") +
                    ", cost=" + call_cost + ", duration=" + call_duration + "s");
            }

            var logResponse = await Net.httpRequestAsync(LOG_URL, {
                headers: ["Content-Type: application/json"],
                method: 'POST',
                postData: JSON.stringify(payload)
            });

            Logger.write("📡 Log #" + logCounter + " → HTTP " + logResponse.code);

            if (logResponse.code == 200 && isFinal) {
                userMessageBuffer = "";
                assistantMessageBuffer = "";
                dialogLog = [];
                lastFunctionResult = null;
            }
        } catch (error) {
            Logger.write("❌ Error sending log: " + error);
        }
    }

    // =========================================================================
    // ОБРАБОТЧИК ЗАВЕРШЕНИЯ ЗВОНКА
    // =========================================================================
    var callEndHandler = async function(event) {
        if (isHangingUp) return;
        isHangingUp = true;

        Logger.write("📴 INBOUND CALL DISCONNECTED");

        if (event && event.cost !== undefined)     call_cost = event.cost;
        if (event && event.duration !== undefined) call_duration = event.duration;

        disarmWatchdog();
        if (hangupGuardTimer) { clearTimeout(hangupGuardTimer); hangupGuardTimer = null; }

        if (realtimeAPIClient) { try { realtimeAPIClient.close(); } catch (err) {} }
        closeTtsSocket();

        // Запись останавливать вручную нечем: метода stopRecord в API нет,
        // она завершается вместе со звонком. Ждём RecordStopped, чтобы
        // забрать record_url до отправки финального лога.
        await new Promise(function(resolve) { setTimeout(resolve, 500); });

        if (userMessageBuffer || assistantMessageBuffer || call_session_history_id || dialogLog.length > 0) {
            try { await sendConversationLog(true); } catch (err) { Logger.write("❌ final log: " + err); }
        }

        Logger.write("✅ Terminated. Total logs: " + logCounter);
        VoxEngine.terminate();
    };

    // =========================================================================
    // ЗАГРУЗКА КОНФИГА
    // =========================================================================
    Logger.write("🔄 Loading config for phone: " + called_number);

    var configResponse;
    try {
        configResponse = await Net.httpRequestAsync(CONFIG_URL);
    } catch (err) {
        Logger.write("❌ Config request failed: " + err);
        call.answer();
        call.addEventListener(CallEvents.Disconnected, callEndHandler);
        call.addEventListener(CallEvents.Failed, callEndHandler);
        return;
    }

    if (configResponse.code != 200) {
        Logger.write("❌ Config HTTP error: " + configResponse.code);
        VoxEngine.terminate();
        return;
    }

    try {
        CONFIG = JSON.parse(configResponse.text);
    } catch (err) {
        Logger.write("❌ Config parse error: " + err);
        VoxEngine.terminate();
        return;
    }

    if (!CONFIG.success) {
        Logger.write("❌ Config returned success=false");
        VoxEngine.terminate();
        return;
    }

    if (CONFIG.assistant_type !== "fish") {
        Logger.write("❌ Wrong assistant type: " + CONFIG.assistant_type + " (expected: fish)");
        VoxEngine.terminate();
        return;
    }

    if (!CONFIG.fish_tts_url) {
        Logger.write("❌ Config has no fish_tts_url — озвучивать нечем");
        VoxEngine.terminate();
        return;
    }

    ASSISTANT_ID = CONFIG.assistant_id;

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("✅ CONFIG LOADED:");
    Logger.write("   📋 Assistant: " + CONFIG.assistant_name);
    Logger.write("   🆔 ID: " + ASSISTANT_ID);
    Logger.write("   🌐 Language: " + CONFIG.language);
    Logger.write("   🧠 LLM: " + (CONFIG.model || "gpt-realtime-2.1-mini"));
    Logger.write("   🐟 Fish voice: " + CONFIG.fish_voice_id + " / " + CONFIG.fish_model +
                 " (" + CONFIG.fish_latency + ", " + CONFIG.sample_rate + " Hz)");
    Logger.write("   🔧 Functions: " + (CONFIG.functions ? CONFIG.functions.length : 0));
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

    // =========================================================================
    // ПОДГОТОВКА ФУНКЦИЙ
    // =========================================================================
    var voximplantTools = [];

    if (CONFIG.functions && Array.isArray(CONFIG.functions)) {
        for (var i = 0; i < CONFIG.functions.length; i++) {
            var tool = CONFIG.functions[i];
            if (tool.type === "function" && tool.function) {
                var functionId = (i + 1).toString();
                functionNameToIdMap[tool.function.name] = functionId;
                Logger.write("   🔧 Function: " + tool.function.name + " → ID: " + functionId);
                voximplantTools.push({
                    type: "function",
                    name: tool.function.name,
                    description: tool.function.description,
                    parameters: tool.function.parameters
                });
            }
        }
    }

    // =========================================================================
    // TTS: ПОДГОТОВКА ТЕКСТА
    // =========================================================================

    // Модель иногда пишет ответ в несколько строк — "  \n" ломает синтез.
    function cleanForTTS(text) {
        return text.replace(/\s*\n+\s*/g, " ");
    }

    // Точка — конец предложения, а не десятичный разделитель и не инициал.
    function isSentenceDot(s, i) {
        var prev = i > 0 ? s.charAt(i - 1) : "";
        var next = i + 1 < s.length ? s.charAt(i + 1) : "";
        if (/\d/.test(prev) && /\d/.test(next)) return false;           // 1.5, 12.30
        if (/[A-Za-zА-Яа-яЁё]/.test(prev)) {
            var before = i >= 2 ? s.charAt(i - 2) : "";
            if (before === "" || before === " ") return false;          // "А.", "т.е."
        }
        return true;
    }

    function hasSentenceEnd(s) {
        var TERM = ".!?…";
        for (var i = 0; i < s.length; i++) {
            var ch = s.charAt(i);
            if (TERM.indexOf(ch) === -1) continue;
            if (ch === "." && !isSentenceDot(s, i)) continue;
            return true;
        }
        return false;
    }

    // =========================================================================
    // TTS: СОКЕТ К ПРОКСИ
    // =========================================================================
    // Разделения на чанки, как у Cartesia, здесь нет: Fish буферизует текст
    // сам (chunk_length / latency) и начинает синтез, не дожидаясь конца
    // реплики. Наша задача — вовремя дать flush: один раз на первом
    // законченном предложении (чтобы звук пошёл раньше) и один раз в конце
    // реплики.

    function openTtsSocket() {
        Logger.write("[Fish] Opening TTS socket: " + CONFIG.fish_tts_url);

        ttsSocket = VoxEngine.createWebSocket(CONFIG.fish_tts_url);

        ttsSocket.addEventListener(WebSocketEvents.OPEN, function() {
            ttsOpen = true;
            Logger.write("[Fish] ✅ TTS socket open");

            // Маршрутизируем аудио из сокета в звонок. Если звонок ещё не
            // отвечен, привяжем позже — в attachTtsToCall().
            attachTtsToCall();

            // Досылаем текст, накопленный пока сокет поднимался.
            var queued = ttsQueue;
            ttsQueue = [];
            for (var i = 0; i < queued.length; i++) {
                sendToTts({ event: "text", text: queued[i] });
            }
            if (ttsFlushQueued) {
                ttsFlushQueued = false;
                sendToTts({ event: "flush" });
            }
        });

        // Voximplant подтверждает, что StartEvent принят и поток привязан.
        // Если этого события нет — аудио в трубку не попадёт вообще, каким бы
        // исправным ни выглядел остальной лог (так и вышло на первом звонке:
        // MEDIA_STARTED не пришёл, потому что StartEvent был отвергнут).
        ttsSocket.addEventListener(WebSocketEvents.MEDIA_STARTED, function(ev) {
            ttsMediaAccepted = true;
            Logger.write("[Fish] ✅ MEDIA_STARTED — поток принят, кодек " +
                (ev && ev.encoding));
        });

        ttsSocket.addEventListener(WebSocketEvents.MEDIA_ENDED, function() {
            Logger.write("[Fish] MEDIA_ENDED — поток закрыт");
        });

        // Прокси присылает служебные сообщения о границах реплики.
        ttsSocket.addEventListener(WebSocketEvents.MESSAGE, function(ev) {
            var msg;
            try {
                msg = JSON.parse(ev && ev.text);
            } catch (err) {
                return;
            }
            if (!msg || !msg.event) return;

            if (msg.event === "speech_started") {
                confirmAudio();
            } else if (msg.event === "speech_done") {
                var remaining = typeof msg.remaining_ms === "number" ? msg.remaining_ms : 0;
                Logger.write("[Fish] ⏹ speech done, ещё " + remaining + "ms в буфере");
                if (hangupAfterSpeech) scheduleHangup(remaining);
            }
        });

        ttsSocket.addEventListener(WebSocketEvents.ERROR, function(ev) {
            Logger.write("[Fish] ❌ TTS socket error: " + JSON.stringify(ev));
        });

        ttsSocket.addEventListener(WebSocketEvents.CLOSE, function(ev) {
            ttsOpen = false;
            ttsAttached = false;
            Logger.write("[Fish] TTS socket closed: " + (ev && ev.reason));

            if (isHangingUp) return;
            if (ttsReopens >= TTS_REOPEN_MAX) {
                Logger.write("[Fish] ❌ Переоткрытия исчерпаны — озвучки не будет");
                return;
            }
            ttsReopens++;
            Logger.write("[Fish] Переоткрываем сокет (попытка " + ttsReopens + ")");
            openTtsSocket();
        });
    }

    // sendMediaTo можно звать только когда есть и сокет, и отвеченный звонок.
    // Порядок этих двух событий не гарантирован, поэтому вызываем из обоих.
    function attachTtsToCall() {
        if (ttsAttached || !ttsOpen || !callAnswered || !ttsSocket) return;
        try {
            ttsSocket.sendMediaTo(call);
            ttsAttached = true;
            Logger.write("[Fish] 🔊 TTS media → call");
        } catch (err) {
            Logger.write("[Fish] ❌ sendMediaTo failed: " + err);
        }
    }

    function sendToTts(msg) {
        if (!ttsSocket || !ttsOpen) return false;
        try {
            ttsSocket.send(JSON.stringify(msg));
            return true;
        } catch (err) {
            Logger.write("[Fish] ❌ send failed: " + err);
            return false;
        }
    }

    function closeTtsSocket() {
        if (!ttsSocket) return;
        try { sendToTts({ event: "stop" }); } catch (err) {}
        try { ttsSocket.close(); } catch (err) {}
        ttsSocket = null;
        ttsOpen = false;
        ttsAttached = false;
    }

    // Единственная точка отправки текста в синтез.
    function speak(text, final) {
        if (!text || !text.trim() || isHangingUp) return;

        var clean = cleanForTTS(text);

        if (!ttsOpen) {
            // Сокет ещё поднимается — копим, дошлём в OPEN.
            ttsQueue.push(clean);
            if (final) ttsFlushQueued = true;
        } else {
            sendToTts({ event: "text", text: clean });
            if (final) sendToTts({ event: "flush" });
        }

        Logger.write("[Fish] → \"" + clean.substring(0, 60) + "\"" + (final ? " (final)" : ""));

        if (!turnStarted) {
            turnStarted = true;
            if (mVadStop && !mFirstText) mFirstText = Date.now();
            armWatchdog(TTS_WATCHDOG_MS);
        }
    }

    // Ранний flush: как только в реплике набралось законченное предложение,
    // просим Fish начать синтез, не дожидаясь конца ответа модели.
    // Дельты модели копим и отдаём пачками. Fish буферизует текст сам
    // (chunk_length / latency), поэтому кадр на каждый токен ничего не
    // ускоряет — только гонит сотню WS-фреймов в секунду впустую.
    function pushDelta(delta) {
        deltaBuffer  += delta;
        turnFullText += delta;

        if (deltaBuffer.length >= TEXT_BATCH_MIN || hasSentenceEnd(deltaBuffer)) {
            var out = deltaBuffer;
            deltaBuffer = "";
            speak(out, false);
        }
    }

    // Конец реплики: дожимаем остаток накопленного и закрываем flush'ем.
    function endTurn() {
        var out = deltaBuffer;
        deltaBuffer = "";

        if (out && out.trim()) {
            speak(out, true);
        } else if (turnStarted) {
            if (ttsOpen) sendToTts({ event: "flush" });
            else ttsFlushQueued = true;
        }
    }

    function maybeEarlyFlush() {
        if (firstFlushDone || isInterrupted) return;
        if (turnFullText.length < FIRST_FLUSH_MIN) return;
        if (!hasSentenceEnd(turnFullText)) return;
        firstFlushDone = true;
        if (ttsOpen) sendToTts({ event: "flush" });
        else ttsFlushQueued = true;
        Logger.write("[Fish] ⚡ early flush первого предложения");
    }

    // Перебивание: гасим очередь Voximplant (мгновенно) и рвём генерацию у
    // прокси. Одного clearMediaBuffer мало — Fish продолжит присылать хвост
    // прерванной фразы, и он доиграет поверх следующей реплики.
    function stopSpeaking() {
        if (ttsSocket && ttsOpen) {
            try { ttsSocket.clearMediaBuffer(); } catch (err) {
                Logger.write("[Fish] clearMediaBuffer failed: " + err);
            }
            sendToTts({ event: "clear" });
        }
        ttsQueue = [];
        ttsFlushQueued = false;
    }

    function resetTurnState() {
        turnFullText = "";
        turnStarted = false;
        firstFlushDone = false;
        deltaBuffer    = "";
        audioConfirmed = false;
        mVadStop = 0; mRespCreated = 0; mFirstDelta = 0; mFirstText = 0;
    }

    // =========================================================================
    // TTS: WATCHDOG И МЕТРИКИ
    // =========================================================================

    function armWatchdog(ms) {
        disarmWatchdog();
        if (watchdogDisabled) return;
        watchdogTimer = setTimeout(onTtsSilent, ms);
    }

    function disarmWatchdog() {
        if (watchdogTimer) { clearTimeout(watchdogTimer); watchdogTimer = null; }
    }

    // Метрика меряется до РЕАЛЬНОГО звука (прокси подтверждает, что media
    // ушло в звонок), а не до отправки текста в Fish.
    function confirmAudio() {
        disarmWatchdog();
        if (audioConfirmed) return;
        audioConfirmed = true;

        if (mVadStop) {
            var now = Date.now();
            var toFirstToken = mFirstDelta ? (mFirstDelta - mVadStop) : -1;
            var toTts = mFirstText ? (mFirstText - mVadStop) : -1;
            Logger.write("⏱ TURN: vad→token=" + toFirstToken + "ms" +
                " vad→tts=" + toTts + "ms" +
                " vad→audio=" + (now - mVadStop) + "ms");
        }
    }

    // Звука нет. В отличие от Cartesia пересоздавать плеер не нужно — но если
    // сокет отвалился, переоткрытие уже идёт из обработчика CLOSE. Здесь
    // остаётся только зафиксировать проблему и не мешать разговору: вставлять
    // заглушку вслепую хуже, чем промолчать.
    function onTtsSilent() {
        watchdogTimer = null;
        if (isInterrupted || isHangingUp || audioConfirmed) return;

        Logger.write("⚠️ [Fish] нет подтверждения звука за " + TTS_WATCHDOG_MS + "ms" +
            " (socket " + (ttsOpen ? "open" : "closed") + ")");

        if (!ttsOpen) {
            Logger.write("⚠️ [Fish] сокет закрыт — ждём переоткрытия");
            return;
        }

        if (!ttsMediaAccepted) {
            Logger.write("❌ [Fish] MEDIA_STARTED так и не пришёл — Voximplant " +
                "не принял StartEvent прокси. Аудио в трубку не пойдёт: " +
                "проверьте mediaFormat.encoding (для 8 кГц это PCM8) и что " +
                "в StartEvent нет tag.");
            return;
        }

        // Сокет жив, а подтверждения нет — вероятнее всего, служебное
        // сообщение просто не дошло. Снимаем watchdog до конца звонка, чтобы
        // не сыпать предупреждениями на каждой реплике.
        watchdogDisabled = true;
        Logger.write("⚠️ [Fish] WATCHDOG DISABLED до конца звонка");
    }

    function scheduleHangup(remainingMs) {
        if (!hangupAfterSpeech || isHangingUp) return;
        var delay = Math.max(0, remainingMs) + HANGUP_TAIL_MS;
        Logger.write("📴 Прощание отзвучит через " + delay + "ms — вешаем трубку");
        setTimeout(function() { finishHangup("speech done"); }, delay);
    }

    function finishHangup(reason) {
        if (!hangupAfterSpeech || isHangingUp) return;
        hangupAfterSpeech = false;
        if (hangupGuardTimer) { clearTimeout(hangupGuardTimer); hangupGuardTimer = null; }
        Logger.write("📴 Hangup after farewell (" + reason + ")");
        try { call.hangup(); } catch (err) {}
    }

    // Сокет поднимаем заранее — пока идут гудки и конфигурируется сессия.
    openTtsSocket();

    // =========================================================================
    // ПОДКЛЮЧЕНИЕ К OPENAI REALTIME (STT + turn detection + reasoning)
    // =========================================================================
    Logger.write("🔌 Connecting to OpenAI Realtime API...");

    try {
        realtimeAPIClient = await OpenAI.createRealtimeAPIClient({
            apiKey: CONFIG.api_key,
            model: CONFIG.model || "gpt-realtime-2.1-mini",
            type: OpenAI.RealtimeAPIClientType.REALTIME,
            onWebSocketClose: function() {
                Logger.write("[OpenAI] WS closed");
                if (!isHangingUp) callEndHandler({ cost: 0, duration: 0 });
            },
            onWebSocketError: function(err) {
                Logger.write("[OpenAI] WS error: " + JSON.stringify(err));
            }
        });
    } catch (err) {
        Logger.write("❌ Failed to connect to OpenAI: " + err);
        call.answer();
        call.addEventListener(CallEvents.Disconnected, callEndHandler);
        call.addEventListener(CallEvents.Failed, callEndHandler);
        return;
    }

    Logger.write("✅ OpenAI connected");

    // Приветствие.
    //  - Есть first_phrase: озвучиваем напрямую, без раунда к модели. Модель
    //    узнаёт об этом из инструкций, её первый ответ — уже на реплику абонента.
    //  - Нет first_phrase: просим поздороваться саму модель (нужна готовая сессия).
    function maybeStartGreeting() {
        if (greetingStarted || !callAnswered) return;

        if (CONFIG.first_phrase) {
            greetingStarted = true;
            var phrase = cleanForTTS(CONFIG.first_phrase.trim());
            Logger.write("🤖 AGENT (greeting): \"" + phrase + "\"");
            dialogLog.push({ role: 'assistant', text: phrase, ts: Date.now() });
            if (assistantMessageBuffer) assistantMessageBuffer += " ";
            assistantMessageBuffer += phrase;
            turnFullText = phrase;
            speak(phrase, true);
            return;
        }

        if (!sessionConfigured) return;
        greetingStarted = true;
        realtimeAPIClient.conversationItemCreate({
            item: { type: "message", role: "user", content: [{ type: "input_text",
                text: "Поприветствуй звонящего одной короткой фразой и спроси, чем можешь помочь." }] }
        });
        realtimeAPIClient.responseCreate({});
        Logger.write("[OpenAI] Greeting requested from model");
    }

    // =========================================================================
    // SESSION CREATED — конфигурируем сессию
    // =========================================================================
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.SessionCreated,
        function() {
            if (sessionConfigured) return;
            sessionConfigured = true;
            Logger.write("[OpenAI] Session created — configuring");

            var instructions = CONFIG.system_prompt || "";
            if (CONFIG.first_phrase) {
                instructions += "\n\nТы уже поприветствовал абонента фразой: «" +
                    CONFIG.first_phrase.trim() + "». Не здоровайся повторно.";
            }
            // Реальные номера и время: без них модель не может корректно вызвать
            // send_sms (некуда отправлять) и путается в датах. МСК (UTC+3).
            var mskTime = new Date(Date.now() + 3 * 3600 * 1000)
                .toISOString().replace("T", " ").slice(0, 16);
            instructions += "\n\nИнформация о звонке:\n" +
                "- Номер клиента (caller_number): " + caller_number + "\n" +
                "- Наш номер (called_number): " + called_number + "\n" +
                "- Текущее время: " + mskTime + " (МСК)";

            // turn_detection живёт ВНУТРИ audio.input. В корне session он молча
            // игнорируется, и остаются дефолты OpenAI (silence_duration_ms 200).
            realtimeAPIClient.sessionUpdate({
                session: {
                    type: "realtime",
                    output_modalities: ["text"],
                    instructions: instructions,
                    audio: {
                        input: {
                            transcription: {
                                model: "gpt-4o-transcribe",
                                language: CONFIG.language || "ru"
                            },
                            turn_detection: {
                                type: "server_vad",
                                threshold: VAD_THRESHOLD,
                                prefix_padding_ms: VAD_PREFIX_MS,
                                silence_duration_ms: VAD_SILENCE_MS,
                                create_response: true,
                                interrupt_response: true
                            }
                        }
                    },
                    tools: voximplantTools,
                    tool_choice: voximplantTools.length > 0 ? "auto" : "none"
                }
            });

            maybeStartGreeting();
        }
    );

    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.SessionUpdated,
        function(event) {
            try {
                var s = event && event.data && event.data.payload && event.data.payload.session;
                var td = s && s.audio && s.audio.input && s.audio.input.turn_detection;
                if (td) {
                    Logger.write("[OpenAI] VAD applied: silence=" + td.silence_duration_ms +
                        "ms prefix=" + td.prefix_padding_ms + "ms threshold=" + td.threshold);
                    if (td.silence_duration_ms !== VAD_SILENCE_MS) {
                        Logger.write("⚠️ [OpenAI] VAD mismatch — expected silence=" + VAD_SILENCE_MS + "ms");
                    }
                }
            } catch (err) {}
        }
    );

    // =========================================================================
    // НОВАЯ РЕПЛИКА АССИСТЕНТА
    // =========================================================================
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ResponseCreated,
        function() {
            if (!callAnswered) return;
            isInterrupted = false;
            var vadStop = mVadStop;
            resetTurnState();
            mVadStop = vadStop;          // метрику текущего хода сохраняем
            mRespCreated = Date.now();
            Logger.write("[OpenAI] Response started");
        }
    );

    // Дельты текста → сразу в Fish (он буферизует сам)
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ResponseOutputTextDelta,
        function(event) {
            if (!callAnswered || isInterrupted || isHangingUp) return;
            var delta =
                (event && event.data && event.data.delta) ||
                (event && event.data && event.data.payload && event.data.payload.delta) || "";
            if (!delta) return;

            if (!mFirstDelta) mFirstDelta = Date.now();

            pushDelta(cleanForTTS(delta));
            maybeEarlyFlush();
        }
    );

    // Реплика закончена — flush, чтобы Fish досинтезировал хвост.
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ResponseOutputTextDone,
        function(event) {
            if (isInterrupted || isHangingUp) return;

            var text =
                (event && event.data && event.data.text) ||
                (event && event.data && event.data.payload && event.data.payload.text) || "";
            if (!text || !text.trim()) return;

            Logger.write("🤖 AGENT: \"" + text.substring(0, 80) + "\"");
            dialogLog.push({ role: 'assistant', text: text.trim(), ts: Date.now() });
            Logger.write("   📝 [DIALOG] Added ASSISTANT turn #" + dialogLog.length);
            if (assistantMessageBuffer) assistantMessageBuffer += " ";
            assistantMessageBuffer += text.trim();

            if (!turnStarted && !deltaBuffer) {
                // Дельты не приходили (ответ отдан одним куском) — озвучиваем целиком.
                turnFullText = cleanForTTS(text);
                speak(turnFullText, true);
            } else {
                endTurn();
            }
        }
    );

    // Перебивание пользователем (серверный VAD)
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.InputAudioBufferSpeechStarted,
        function() {
            if (!callAnswered) return;
            Logger.write("[OpenAI] SPEECH STARTED — обрываем озвучку");
            isInterrupted = true;
            disarmWatchdog();
            try { realtimeAPIClient.clearMediaBuffer(); } catch (err) {}
            stopSpeaking();
            turnStarted = false;
            firstFlushDone = false;
            deltaBuffer = "";
        }
    );

    // Конец реплики абонента — точка отсчёта метрик
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.InputAudioBufferSpeechStopped,
        function() {
            if (!callAnswered) return;
            mVadStop = Date.now();
        }
    );

    // Транскрипция пользователя
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ConversationItemInputAudioTranscriptionCompleted,
        function(event) {
            try {
                var payload = event.data && event.data.payload;
                var transcript = payload && payload.transcript;
                if (transcript && transcript.trim()) {
                    Logger.write("👤 USER: \"" + transcript + "\"");
                    dialogLog.push({ role: 'user', text: transcript.trim(), ts: Date.now() });
                    Logger.write("   📝 [DIALOG] Added USER turn #" + dialogLog.length);
                    if (userMessageBuffer) userMessageBuffer += " ";
                    userMessageBuffer += transcript.trim();
                }
            } catch (err) { Logger.write("❌ USER handler: " + err); }
        }
    );

    // =========================================================================
    // FUNCTION CALLS
    // =========================================================================
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ResponseOutputItemDone,
        async function(event) {
            try {
                var payload = event.data && event.data.payload;
                var item = payload && payload.item;
                if (!item || item.type !== "function_call") return;

                var functionName = item.name;
                var callId = item.call_id;
                var args = JSON.parse(item.arguments);
                Logger.write("🔧 FUNCTION CALL: " + functionName);

                if (functionName === "hangup_call") {
                    Logger.write("📴 HANGUP CALL requested");
                    lastFunctionResult = { action: "call_terminated", reason: args.reason || "user request" };

                    if (args.farewell_message && args.farewell_message.trim()) {
                        var farewell = cleanForTTS(args.farewell_message.trim());
                        resetTurnState();
                        turnFullText = farewell;
                        hangupAfterSpeech = true;
                        speak(farewell, true);
                        // Потолок на случай, если speech_done не придёт
                        hangupGuardTimer = setTimeout(function() {
                            finishHangup("guard timeout");
                        }, HANGUP_GUARD_MS);
                    } else {
                        call.hangup();
                    }
                    return;
                }

                var function_id = functionNameToIdMap[functionName];
                if (!function_id) {
                    Logger.write("❌ Unknown function: " + functionName);
                    realtimeAPIClient.conversationItemCreate({
                        item: { type: "function_call_output", call_id: callId,
                                output: JSON.stringify({ error: "Unknown function: " + functionName }) }
                    });
                    realtimeAPIClient.responseCreate({});
                    return;
                }

                args.function_id = function_id;
                var functionResponse = await Net.httpRequestAsync(FUNCTIONS_URL, {
                    headers: ["Content-Type: application/json"],
                    method: 'POST',
                    postData: JSON.stringify({
                        function_id: function_id,
                        arguments: args,
                        call_data: {
                            call_id: call_id,
                            chat_id: chat_id,
                            assistant_id: ASSISTANT_ID,
                            caller_number: caller_number
                        }
                    })
                });

                if (functionResponse.code == 200) {
                    var result = JSON.parse(functionResponse.text);
                    lastFunctionResult = result;
                    realtimeAPIClient.conversationItemCreate({
                        item: { type: "function_call_output", call_id: callId, output: JSON.stringify(result) }
                    });
                    realtimeAPIClient.responseCreate({});
                    Logger.write("✅ Function executed: " + functionName);
                } else {
                    Logger.write("❌ Function failed: HTTP " + functionResponse.code);
                    realtimeAPIClient.conversationItemCreate({
                        item: { type: "function_call_output", call_id: callId,
                                output: JSON.stringify({ error: "Function execution failed" }) }
                    });
                    realtimeAPIClient.responseCreate({});
                }
            } catch (err) { Logger.write("❌ function handler: " + err); }
        }
    );

    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.Error,
        function(event) { Logger.write("[OpenAI] Error: " + JSON.stringify(event && event.data)); }
    );

    // =========================================================================
    // ОТВЕТ НА ЗВОНОК + ЗАПИСЬ
    // =========================================================================
    callAnswered = true;
    call.answer();
    call.addEventListener(CallEvents.Disconnected, callEndHandler);
    call.addEventListener(CallEvents.Failed, callEndHandler);
    Logger.write("[Call] Answered");

    // Сокет мог открыться раньше ответа на звонок — привязываем медиа теперь.
    attachTtsToCall();

    try {
        call.record({ stereo: false, lossless: false, hd_audio: true });
        Logger.write("🎙️ Recording started");
    } catch (recordError) {
        Logger.write("⚠️ Recording failed to start: " + recordError);
    }

    call.addEventListener(CallEvents.RecordStarted, function(event) {
        if (event.url) { record_url = event.url; Logger.write("🎙️ RecordStarted: " + record_url); }
    });
    call.addEventListener(CallEvents.RecordStopped, function(event) {
        if (event.url) record_url = event.url;
    });

    // Аудио звонящего → OpenAI (STT + серверный VAD)
    call.sendMediaTo(realtimeAPIClient);
    Logger.write("[OpenAI] call → OpenAI audio connected");

    // Приветствие (сессия могла сконфигурироваться и до ответа на звонок)
    maybeStartGreeting();

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("🎉 READY FOR CONVERSATION (Fish v1.0)");
    Logger.write("   🔑 Session: " + call_session_history_id);
    Logger.write("   🐟 TTS: Fish " + CONFIG.fish_model + " через прокси");
    Logger.write("   🎧 VAD silence: " + VAD_SILENCE_MS + "ms");
    Logger.write("   📝 Structured dialog: ENABLED");
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
});
