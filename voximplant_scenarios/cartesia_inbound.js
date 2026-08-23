require(Modules.OpenAI);
require(Modules.Cartesia);

/*
 * Voximplant INBOUND Cartesia Script v3.0
 * ====================================================================
 * Архитектура:
 *   - OpenAI Realtime API (output_modalities: text) — STT + turn detection
 *     + reasoning. Серверный VAD сам определяет конец реплики и перебивание.
 *   - Cartesia Realtime TTS — ОДИН долгоживущий плеер на весь звонок.
 *     Текст досылается через generationRequest(), перебивание —
 *     cancelContextRequest(). Плеер не пересоздаётся между репликами,
 *     WebSocket к Cartesia остаётся тёплым.
 *   - Динамический конфиг из /api/telephony/config
 *   - Структурированный диалог (dialogLog) для UI
 *   - Запись звонка + биллинг, function calls
 *
 * Отличия от v1.0 (по итогам разбора лога тестового звонка 4914868630,
 * где пауза после реплики клиента составляла 1.25–1.98 с, а один раз
 * озвучка не началась вовсе):
 *
 *   1. Один плеер на звонок вместо CreatePlayer/DestroyPlayer на каждую
 *      реплику. В v1.0 холодный старт плеера стоил 0.87–1.24 с (67% паузы).
 *   2. Текст уходит в TTS по границам предложений прямо во время генерации,
 *      а не целиком после ResponseOutputTextDone (экономия ~265 мс).
 *   3. max_buffer_delay_ms: 200 вместо дефолтных 3000 у Cartesia — кусок без
 *      завершающей пунктуации не подвешивает синтез на 3 секунды.
 *   4. Свежий context_id на каждую реплику. В v1.0 один context_id
 *      переиспользовался весь звонок с continue:false, что совпало с
 *      зависанием синтеза на 9.4 с.
 *   5. turn_detection перенесён в session.audio.input.turn_detection.
 *      В v1.0 он лежал в корне session и молча игнорировался — реально
 *      работали дефолтные 200 мс вместо заданных 500.
 *   6. Убран параметр progressive — его нет в Cartesia.RealtimeTTSPlayerParameters
 *      (там только generationRequestParameters, apiKey, statistics, trace, privacy).
 *   7. Звонок отвечается сразу после коннекта к OpenAI, а не после
 *      буферизации приветствия (было 3.29 с гудков). Приветствие из конфига
 *      уходит прямо в TTS, без лишнего раунда к модели.
 *   8. Watchdog на молчащий TTS: заглушка + переотправка, при повторе —
 *      пересоздание плеера. В v1.0 зависший плеер не детектился никак.
 *   9. Метрики задержки в лог, TTFB берётся из штатного
 *      PlayerEvents.AudioChunksPlaybackFinished.
 */

// ============================================================================
// КОНСТАНТЫ (крутить здесь, логику не трогать)
// ============================================================================
var TTS_MODEL_ID        = "sonic-3.5";  // sonic-3.5 | sonic-3 | sonic-latest (latest = beta, не для прода)
var MAX_BUFFER_DELAY_MS = 200;          // Cartesia: дефолт 3000, диапазон [0, 5000]
var VAD_SILENCE_MS      = 500;          // хвост тишины до конца реплики
var VAD_PREFIX_MS       = 300;
var VAD_THRESHOLD       = 0.5;
var CHUNK_SOFT_LIMIT    = 140;          // рез по последней запятой, если нет терминатора
var FIRST_CHUNK_MIN     = 25;           // ранний рез первого чанка реплики
var CHUNK_MIN_LEN       = 12;           // не резать на огрызки (кроме первого чанка)
var TTS_WATCHDOG_MS     = 3000;         // нет аудио после первого чанка → восстановление
var TTS_RETRY_MS        = 1200;         // окно на попытку восстановления
var FILLER_TEXT         = "Секунду.";   // заглушка при зависшем синтезе
var HANGUP_GUARD_MS     = 15000;        // потолок ожидания конца прощания
var DEFAULT_VOICE_SPEED = 0.9;          // Cartesia: диапазон 0.6–1.5

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
    Logger.write("🚀 APP STARTED (INBOUND Cartesia v3.0)");
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
    var ttsPlayer = null;
    var callAnswered = false;
    var sessionConfigured = false;
    var greetingStarted = false;
    var isInterrupted = false;
    var isHangingUp = false;

    // ── Состояние текущей реплики ассистента ────────────────────────────────
    var pending = "";              // накопитель дельт, ещё не ушедших в TTS
    var turnFullText = "";         // весь текст реплики (для переотправки при сбое)
    var turnCtx = null;            // context_id текущей реплики
    var ctxOpen = false;           // контекст ещё не закрыт (continue:false не уходил)
    var playerDead = false;        // Cartesia вернула ошибку — плеер непригоден
    var firstChunkOfTurn = true;
    var audioConfirmed = false;    // Cartesia отдала аудио по текущей реплике
    var recoveryStage = 0;         // 0 — норма, 1 — заглушка, 2 — пересоздан плеер
    var ctxCounter = 0;

    // ── Watchdog / завершение по прощанию ───────────────────────────────────
    var watchdogTimer = null;
    var watchdogDisabled = false;
    var sawPlayerError = false;    // была явная ошибка плеера на текущей реплике
    var hangupAfterSpeech = false;
    var hangupGuardTimer = null;

    // ── Метрики задержки ────────────────────────────────────────────────────
    var mVadStop = 0, mRespCreated = 0, mFirstDelta = 0, mFirstChunk = 0;

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

    var CONFIG_URL = "https://voksyai.online/api/telephony/config?phone=" + called_number.replace(/\D/g, '');
    var FUNCTIONS_URL = "https://voksyai.online/api/voximplant/functions/execute";
    var LOG_URL = "https://voksyai.online/api/voximplant/log";

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("📞 INBOUND CALL (Cartesia v3.0)");
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
        if (ttsPlayer)         { try { ttsPlayer.stop(); } catch (err) {} ttsPlayer = null; }
        if (call)              { try { call.stopRecord(); } catch (err) {} }

        // Ждём RecordStopped
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

    if (CONFIG.assistant_type !== "cartesia") {
        Logger.write("❌ Wrong assistant type: " + CONFIG.assistant_type + " (expected: cartesia)");
        VoxEngine.terminate();
        return;
    }

    ASSISTANT_ID = CONFIG.assistant_id;

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("✅ CONFIG LOADED:");
    Logger.write("   📋 Assistant: " + CONFIG.assistant_name);
    Logger.write("   🆔 ID: " + ASSISTANT_ID);
    Logger.write("   🌐 Language: " + CONFIG.language);
    Logger.write("   🎤 Cartesia Voice: " + CONFIG.cartesia_voice_id + " / " + TTS_MODEL_ID);
    Logger.write("   ⚡ Voice Speed: " + (CONFIG.voice_speed || DEFAULT_VOICE_SPEED));
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
    // TTS: ЧАНКИНГ ТЕКСТА
    // =========================================================================

    // Модель иногда пишет ответ в несколько строк — "  \n" ломает синтез.
    function cleanForTTS(text) {
        return text.replace(/\s*\n+\s*/g, " ");
    }

    // Точка — конец предложения, а не десятичный разделитель и не инициал/сокращение.
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

    // Индекс символа, ПОСЛЕ которого можно резать; -1 если резать негде.
    function findTerminator(s, from) {
        var TERM = ".!?…:;";
        for (var i = from; i < s.length; i++) {
            var ch = s.charAt(i);
            if (TERM.indexOf(ch) === -1) continue;
            if (ch === "." && !isSentenceDot(s, i)) continue;
            return i;
        }
        return -1;
    }

    // Первая граница предложения, дающая кусок не короче CHUNK_MIN_LEN.
    // Слишком ранние границы (сокращения вроде "т.е.") перескакиваем, иначе
    // огрызок в начале заблокировал бы все последующие точки реза.
    function findCutIndex(s) {
        var from = 0;
        while (true) {
            var cut = findTerminator(s, from);
            if (cut === -1) return -1;
            if (firstChunkOfTurn || (cut + 1) >= CHUNK_MIN_LEN) return cut;
            from = cut + 1;
        }
    }

    // Режем накопитель и отправляем готовые куски в Cartesia.
    // Рез всегда ПОСЛЕ знака препинания, остаток сохраняется как есть (вместе
    // с ведущим пробелом) — склейка всех чанков даёт исходный текст побайтово,
    // как того требует Cartesia для корректной просодии.
    function drainPending() {
        if (isInterrupted || !pending) return;

        var guard = 0;
        while (pending && guard++ < 32) {
            var cut = findCutIndex(pending);

            if (cut === -1 && firstChunkOfTurn && pending.length >= FIRST_CHUNK_MIN) {
                // Первый кусок реплики выталкиваем раньше: важнее всего отдать
                // первые несколько слов, дальше стрим догоняет сам.
                var early = pending.indexOf(",", FIRST_CHUNK_MIN - 1);
                if (early !== -1) cut = early;
            }

            if (cut === -1 && pending.length >= CHUNK_SOFT_LIMIT) {
                var lastComma = pending.lastIndexOf(",");
                cut = (lastComma !== -1) ? lastComma : pending.length - 1;
            }

            if (cut === -1) return;

            var chunk = pending.substring(0, cut + 1);
            pending = pending.substring(cut + 1);
            speak(chunk, true);
        }
    }

    // =========================================================================
    // TTS: ОТПРАВКА В CARTESIA
    // =========================================================================

    function newContextId() {
        ctxCounter++;
        return "vox_" + ctxCounter + "_" + Math.random().toString(36).substring(2, 10);
    }

    function buildGenParams(ctx, transcript, more) {
        var params = {
            context_id: ctx,
            continue: !!more,
            model_id: TTS_MODEL_ID,
            language: CONFIG.language || "ru",
            voice: { mode: "id", id: CONFIG.cartesia_voice_id },
            generation_config: { speed: CONFIG.voice_speed || DEFAULT_VOICE_SPEED },
            max_buffer_delay_ms: MAX_BUFFER_DELAY_MS
        };
        if (transcript !== null) params.transcript = transcript;
        return params;
    }

    // Плеер умер (ошибка от Cartesia) — пересоздаём на ближайшей отправке,
    // не дожидаясь watchdog'а. Мёртвый плеер молча глотает generationRequest,
    // поэтому чинить его надо детерминированно, а не по таймеру.
    function createPlayer(text, more) {
        ttsPlayer = Cartesia.createRealtimeTTSPlayer(text, {
            apiKey: CONFIG.cartesia_api_key,
            // transcript передаётся первым аргументом createRealtimeTTSPlayer
            generationRequestParameters: buildGenParams(turnCtx, null, more)
        });
        playerDead = false;
        attachPlayerListeners();
        ttsPlayer.sendMediaTo(call);
        Logger.write("[Cartesia] Player created (" + TTS_MODEL_ID + ", ctx " + turnCtx + ")");
    }

    // Единственная точка отправки текста в TTS.
    // Плеер создаётся один раз — на первой фразе, которую надо озвучить, —
    // и живёт до конца звонка.
    function speak(text, more) {
        if (!text || !text.trim() || isHangingUp) return;
        if (!turnCtx) turnCtx = newContextId();

        if (ttsPlayer && playerDead) {
            Logger.write("[Cartesia] player is dead — recreating");
            try { ttsPlayer.stop(); } catch (err) {}
            ttsPlayer = null;
        }

        try {
            if (!ttsPlayer) {
                createPlayer(text, more);
            } else {
                ttsPlayer.generationRequest(buildGenParams(turnCtx, text, more));
            }
        } catch (err) {
            Logger.write("[Cartesia] ❌ send failed: " + err);
            return;
        }

        // Контекст остаётся открытым, пока не ушёл кусок с continue:false.
        // Отменять уже закрытый контекст нельзя — Cartesia отвечает
        // "Invalid context ID", и Voximplant уничтожает плеер.
        ctxOpen = !!more;

        Logger.write("[Cartesia] → \"" + text.substring(0, 60) + "\"" + (more ? "" : " (final)"));

        if (firstChunkOfTurn) {
            firstChunkOfTurn = false;
            if (mVadStop && !mFirstChunk) mFirstChunk = Date.now();
            armWatchdog(TTS_WATCHDOG_MS);
        }
    }

    // Закрыть контекст, когда хвоста текста не осталось: Cartesia ждёт
    // финальный запрос с continue:false, иначе контекст висит открытым.
    function closeTurnContext() {
        if (!ttsPlayer || !turnCtx || playerDead) return;
        try {
            ttsPlayer.generationRequest(buildGenParams(turnCtx, " ", false));
            ctxOpen = false;
        } catch (err) {
            Logger.write("[Cartesia] close ctx failed: " + err);
        }
    }

    // Оборвать озвучку, не убивая плеер: WebSocket к Cartesia остаётся тёплым
    // для следующей реплики.
    //
    // cancelContextRequest допустим ТОЛЬКО пока контекст открыт. На закрытом
    // (уже ушёл continue:false) Cartesia возвращает "Invalid context ID", что
    // приходит как PlaybackFinished с ошибкой и уносит плеер вместе с собой.
    // А закрытый контекст — это как раз обычная смена хода: агент договорил,
    // абонент начал отвечать. То есть без этой проверки плеер умирает на
    // каждой реплике, а не только при настоящем перебивании.
    function stopSpeaking() {
        if (!ttsPlayer || playerDead) return;
        try {
            if (ctxOpen && turnCtx) {
                ttsPlayer.cancelContextRequest({ context_id: turnCtx, cancel: true });
                ctxOpen = false;
            }
            ttsPlayer.clearBuffer();
        } catch (err) {
            Logger.write("[Cartesia] stopSpeaking failed: " + err);
        }
    }

    function resetTurnState() {
        pending = "";
        turnFullText = "";
        turnCtx = null;
        ctxOpen = false;
        firstChunkOfTurn = true;
        audioConfirmed = false;
        recoveryStage = 0;
        sawPlayerError = false;
        mVadStop = 0; mRespCreated = 0; mFirstDelta = 0; mFirstChunk = 0;
    }

    // =========================================================================
    // TTS: WATCHDOG
    // =========================================================================

    function armWatchdog(ms) {
        disarmWatchdog();
        if (watchdogDisabled) return;
        watchdogTimer = setTimeout(onTtsSilent, ms);
    }

    function disarmWatchdog() {
        if (watchdogTimer) { clearTimeout(watchdogTimer); watchdogTimer = null; }
    }

    // Метрика меряется до РЕАЛЬНОГО звука в трубке, а не до отправки текста в
    // Cartesia: в прошлом звонке текст уходил в мёртвый плеер, и "total≈271ms"
    // при фактических 5272 мс никого ни о чём не предупредил.
    function confirmAudio(source, ttfb) {
        disarmWatchdog();
        if (audioConfirmed) return;
        audioConfirmed = true;

        if (mVadStop) {
            var now = Date.now();
            var toFirstToken = mFirstDelta ? (mFirstDelta - mVadStop) : -1;
            var toTts = mFirstChunk ? (mFirstChunk - mVadStop) : -1;
            Logger.write("⏱ TURN: vad→token=" + toFirstToken + "ms" +
                " vad→tts=" + toTts + "ms" +
                " vad→audio=" + (now - mVadStop) + "ms" +
                (typeof ttfb === "number" ? " ttfb=" + ttfb + "ms" : "") +
                (recoveryStage > 0 ? " (recovery stage " + recoveryStage + ")" : "") +
                " [" + source + "]");
        }
    }

    // Cartesia не отдала аудио.
    //
    // Заглушку озвучиваем только при ЯВНОЙ ошибке плеера — тогда мы точно
    // знаем, что абонент сидит в тишине. Если ошибки не было, у нас нет
    // доказательства, что аудио не идёт: возможно, просто не приходит событие
    // подтверждения. В этом случае молча снимаем watchdog до конца звонка —
    // остаться без страховки лучше, чем вклинивать "Секунду." в каждую реплику.
    function onTtsSilent() {
        watchdogTimer = null;
        if (isInterrupted || isHangingUp || audioConfirmed) return;

        if (!sawPlayerError) {
            watchdogDisabled = true;
            Logger.write("⚠️ [Cartesia] watchdog: нет подтверждения аудио за " + TTS_WATCHDOG_MS +
                "ms, но и ошибок плеера не было — считаем, что событие просто не приходит.");
            Logger.write("⚠️ [Cartesia] WATCHDOG DISABLED до конца звонка (заглушка не проигрывается)");
            return;
        }

        var textToRepeat = (turnFullText || "").trim();

        if (recoveryStage === 0 && ttsPlayer && !playerDead) {
            recoveryStage = 1;
            Logger.write("⚠️ [Cartesia] no audio in " + TTS_WATCHDOG_MS + "ms after error — filler + resend");
            stopSpeaking();
            turnCtx = newContextId();
            try {
                ttsPlayer.generationRequest(buildGenParams(turnCtx, FILLER_TEXT + " ", !!textToRepeat));
                if (textToRepeat) {
                    ttsPlayer.generationRequest(buildGenParams(turnCtx, textToRepeat, false));
                    ctxOpen = false;
                } else {
                    ctxOpen = false;
                }
            } catch (err) {
                Logger.write("[Cartesia] resend failed: " + err);
            }
            armWatchdog(TTS_RETRY_MS);
            return;
        }

        if (recoveryStage <= 1) {
            recoveryStage = 2;
            Logger.write("⚠️ [Cartesia] still silent — recreating player");
            if (ttsPlayer) { try { ttsPlayer.stop(); } catch (err) {} }
            ttsPlayer = null;
            playerDead = false;
            turnCtx = null;
            ctxOpen = false;
            firstChunkOfTurn = true;
            speak(FILLER_TEXT + " ", !!textToRepeat);
            if (textToRepeat) speak(textToRepeat, false);
            armWatchdog(TTS_RETRY_MS);
            return;
        }

        Logger.write("❌ [Cartesia] TTS unrecoverable for this turn — giving up");
    }

    // =========================================================================
    // СОБЫТИЯ ПЛЕЕРА
    // =========================================================================
    // Вешаются ДО sendMediaTo, иначе Started может проскочить мимо подписки.
    //
    // Наблюдение с тестового звонка: AudioChunksPlaybackFinished не пришёл ни
    // разу за весь звонок, так что TTFB от платформы получить не удаётся, а
    // единственный реальный признак начала озвучки — Started. Слушатель на
    // AudioChunks оставлен: если событие всё-таки появится, оно принесёт TTFB.
    // Срабатывает ли Started на каждую генерацию у живого плеера, всё ещё
    // неизвестно — в прошлом звонке плеер не пережил ни одной смены хода.
    function attachPlayerListeners() {
        ttsPlayer.addEventListener(PlayerEvents.Started, function() {
            Logger.write("[Cartesia] ▶ Started");
            confirmAudio("Started");
        });

        ttsPlayer.addEventListener(PlayerEvents.AudioChunksPlaybackFinished, function(ev) {
            var ttfb = (ev && typeof ev.timeToFirstByte === "number") ? ev.timeToFirstByte : undefined;
            Logger.write("[Cartesia] ✅ chunks finished (ttfb=" + (ttfb === undefined ? "n/a" : ttfb) + "ms)");
            confirmAudio("AudioChunks", ttfb);
            if (hangupAfterSpeech) finishHangup("playback finished");
        });

        ttsPlayer.addEventListener(PlayerEvents.PlaybackFinished, function(ev) {
            if (ev && ev.error) { onPlayerError("PlaybackFinished: " + ev.error); return; }
            Logger.write("[Cartesia] ✅ PlaybackFinished");
            confirmAudio("PlaybackFinished");
            if (hangupAfterSpeech) finishHangup("playback finished");
        });

        ttsPlayer.addEventListener(PlayerEvents.Error, function(ev) {
            onPlayerError("Player.Error: " + (ev && ev.error));
        });
    }

    // Ошибка от Cartesia уносит плеер с собой: дальнейшие generationRequest
    // уходят в никуда молча. Поэтому помечаем плеер мёртвым сразу и, если
    // текущая реплика ещё не зазвучала, чиним не дожидаясь watchdog'а.
    function onPlayerError(what) {
        Logger.write("[Cartesia] ❌ " + what);
        playerDead = true;
        sawPlayerError = true;
        ctxOpen = false;

        if (audioConfirmed || isInterrupted || isHangingUp) return;
        if (!turnFullText) return;   // озвучивать нечего

        disarmWatchdog();
        Logger.write("[Cartesia] recovering current turn on a fresh player");
        // pending — это суффикс turnFullText, а не отдельный кусок: обработчик
        // дельт пишет в оба. Складывать их нельзя, хвост задвоится.
        var textToRepeat = turnFullText.trim();
        recoveryStage = 2;
        try { ttsPlayer.stop(); } catch (err) {}
        ttsPlayer = null;
        playerDead = false;
        turnCtx = null;
        ctxOpen = false;
        firstChunkOfTurn = true;
        pending = "";
        speak(textToRepeat, false);
    }

    function finishHangup(reason) {
        if (!hangupAfterSpeech || isHangingUp) return;
        hangupAfterSpeech = false;
        if (hangupGuardTimer) { clearTimeout(hangupGuardTimer); hangupGuardTimer = null; }
        Logger.write("📴 Hangup after farewell (" + reason + ")");
        try { call.hangup(); } catch (err) {}
    }

    // =========================================================================
    // ПОДКЛЮЧЕНИЕ К OPENAI REALTIME (STT + turn detection + reasoning)
    // =========================================================================
    Logger.write("🔌 Connecting to OpenAI Realtime API...");

    try {
        realtimeAPIClient = await OpenAI.createRealtimeAPIClient({
            apiKey: CONFIG.api_key,
            model: CONFIG.model || "gpt-realtime",
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
    //  - Есть first_phrase: озвучиваем напрямую через Cartesia, без раунда к
    //    модели. Модель узнаёт об этом из инструкций, её первый ответ — уже на
    //    реплику абонента.
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
            turnFullText = phrase;   // чтобы приветствие тоже можно было переозвучить
            speak(phrase, false);
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

    // Проверяем, что VAD реально применился (в v1.0 он молча игнорировался).
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

    // Дельты текста → чанкер → Cartesia
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ResponseOutputTextDelta,
        function(event) {
            if (!callAnswered || isInterrupted || isHangingUp) return;
            var delta =
                (event && event.data && event.data.delta) ||
                (event && event.data && event.data.payload && event.data.payload.delta) || "";
            if (!delta) return;

            if (!mFirstDelta) mFirstDelta = Date.now();

            var clean = cleanForTTS(delta);
            pending += clean;
            turnFullText += clean;
            drainPending();
        }
    );

    // Реплика закончена — досылаем хвост, закрывая контекст (continue: false).
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

            // Подстраховка: если дельты не пришли (например, ответ отдан одним
            // куском), turnFullText пуст — берём полный текст.
            if (!turnFullText) turnFullText = cleanForTTS(text);

            var tail = pending;
            pending = "";

            if (!turnCtx) {
                // В TTS ещё ничего не уходило — озвучиваем ответ целиком.
                speak(cleanForTTS(text), false);
            } else if (tail && tail.trim()) {
                speak(tail, false);
            } else {
                closeTurnContext();
            }
        }
    );

    // Перебивание пользователем (серверный VAD)
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.InputAudioBufferSpeechStarted,
        function() {
            if (!callAnswered) return;
            Logger.write("[OpenAI] SPEECH STARTED — stop Cartesia" +
                (ctxOpen ? " (context open → cancel)" : " (context closed → buffer only)"));
            isInterrupted = true;
            disarmWatchdog();
            try { realtimeAPIClient.clearMediaBuffer(); } catch (err) {}
            stopSpeaking();
            pending = "";
            turnCtx = null;
            ctxOpen = false;
            firstChunkOfTurn = true;
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
                        speak(farewell, false);
                        // Потолок на случай, если событие окончания озвучки не придёт
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
    // Отвечаем сразу, не дожидаясь синтеза приветствия: конфиг и коннект к
    // OpenAI уже прошли под гудки, а первый звук приходит через TTFB Cartesia.
    callAnswered = true;
    call.answer();
    call.addEventListener(CallEvents.Disconnected, callEndHandler);
    call.addEventListener(CallEvents.Failed, callEndHandler);
    Logger.write("[Call] Answered");

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
    Logger.write("🎉 READY FOR CONVERSATION (Cartesia v3.0)");
    Logger.write("   🔑 Session: " + call_session_history_id);
    Logger.write("   🎤 TTS: " + TTS_MODEL_ID + ", buffer delay " + MAX_BUFFER_DELAY_MS + "ms");
    Logger.write("   🎧 VAD silence: " + VAD_SILENCE_MS + "ms");
    Logger.write("   📝 Structured dialog: ENABLED");
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
});
