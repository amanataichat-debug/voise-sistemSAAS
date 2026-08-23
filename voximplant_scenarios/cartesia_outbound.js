require(Modules.OpenAI);
require(Modules.Cartesia);

/*
 * Voximplant OUTBOUND Cartesia Script v3.1
 * ====================================================================
 * Отличия от v3.0:
 *
 *   1. МЬЮТ ФИКСИРОВАННЫЙ. Аудио абонента подключается к OpenAI ровно
 *      через MUTE_DURATION мс от CallEvents.Connected. Составное условие
 *      "нижняя граница И конец приветствия ИЛИ потолок" убрано вместе с
 *      muteMinDone / muteCeilTimer / tryLinkAfterMute.
 *
 *   2. ПРИВЕТСТВИЕ ЗАЩИЩЕНО ОТ ОБРЫВА. Раз мьют больше не растягивается
 *      до конца приветствия, абонент может влезть с "Алло" на середине.
 *      InputAudioBufferSpeechStarted во время приветствия больше не рвёт
 *      Cartesia. Реплика модели, которую она на это "Алло" сгенерирует,
 *      придерживается шлюзом (ttsGateClosed) и уходит в TTS только после
 *      того, как приветствие доиграло, — иначе два контекста Cartesia
 *      писали бы в один плеер одновременно.
 *
 *   3. ШЛЮЗ СТРАХУЕТСЯ ТАЙМЕРОМ. Если событие конца воспроизведения от
 *      плеера не придёт, шлюз откроется принудительно по расчётной длине
 *      приветствия (armGreetingGuard). Без этого один пропущенный ивент
 *      оставил бы ассистента немым до конца звонка.
 *
 *   4. ПРОГРЕВ ПЛЕЕРА. Плеер Cartesia создаётся ДО callPSTN, пока идут
 *      гудки: WS-хендшейк, TLS, авторизация и прогрев модели съедаются
 *      бесплатным временем. sendMediaTo(call) отложен до Connected.
 *      Прогревочный контекст держится открытым keep-alive'ом (" " раз в
 *      WARMUP_KEEPALIVE_MS) на случай долгих гудков и закрывается в
 *      finishWarmup(). Любой сбой прогрева не роняет звонок — сценарий
 *      молча падает на ленивое создание плеера, как в v3.0.
 *
 *   5. События плеера, прилетевшие во время прогрева, не доходят до
 *      confirmAudio / onSpeechFinished / onPlayerError (флаг isWarmingUp),
 *      иначе они бы сломали логику greetingPending и hangupAfterSpeech.
 *
 *   6. Метрика приветствия: connect→audio в лог. В v3.0 приветствие
 *      вообще не мерилось (confirmAudio писал тайминги только при
 *      заполненном mVadStop, которого у приветствия нет).
 *
 *   7. estimateSpeechMs / SPEECH_CPS сохранены, но переехали с потолка
 *      мьюта на страховку шлюза приветствия.
 * ====================================================================
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
var SPEECH_CPS          = 14;           // символов/сек при speed 1.0 — оценка длительности
var GREETING_TAIL_MS    = 2000;         // запас к расчётной длине приветствия для страховки шлюза

// ── Прогрев плеера ──────────────────────────────────────────────────────────
var WARMUP_ENABLED      = true;         // создавать плеер до набора номера
var WARMUP_KEEPALIVE_MS = 10000;        // досылать " " в прогревочный контекст, пока идут гудки
var WARMUP_TEXT         = " ";          // текст, на котором поднимается сокет (тишина)

// ============================================================================
// ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ БИЛЛИНГА
// ============================================================================
var call_session_history_id = null;
var record_url = null;
var call_cost = 0;
var call_duration = 0;

VoxEngine.addEventListener(AppEvents.Started, async function(e) {
    call_session_history_id = e.sessionId;

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("🚀 APP STARTED (OUTBOUND Cartesia v3.1)");
    Logger.write("🔑 Session History ID: " + call_session_history_id);
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

    // ── Состояние сценария ──────────────────────────────────────────────────
    var realtimeAPIClient = null;
    var ttsPlayer         = null;
    var call              = null;
    var callConnected     = false;
    var sessionConfigured = false;
    var isInterrupted     = false;
    var isHangingUp       = false;

    // ── Состояние текущей реплики ассистента ────────────────────────────────
    var pending          = "";     // накопитель дельт, ещё не ушедших в TTS
    var turnFullText     = "";     // весь текст реплики (для переотправки при сбое)
    var turnCtx          = null;   // context_id текущей реплики
    var ctxOpen          = false;  // контекст ещё не закрыт (continue:false не уходил)
    var playerDead       = false;  // Cartesia вернула ошибку — плеер непригоден
    var firstChunkOfTurn = true;
    var audioConfirmed   = false;  // Cartesia отдала аудио по текущей реплике
    var recoveryStage    = 0;      // 0 — норма, 1 — заглушка, 2 — пересоздан плеер
    var ctxCounter       = 0;

    // ── Watchdog / завершение по прощанию ───────────────────────────────────
    var watchdogTimer     = null;
    var watchdogDisabled  = false;
    var sawPlayerError    = false; // была явная ошибка плеера на текущей реплике
    var hangupAfterSpeech = false;
    var hangupGuardTimer  = null;

    // ── Приветствие и шлюз TTS ──────────────────────────────────────────────
    var greetingText       = "";     // отдельно от turnFullText: тот перетирается новой репликой
    var greetingPending    = false;  // приветствие ещё звучит
    var greetingDone       = false;  // приветствие отзвучало
    var greetingGuardTimer = null;   // страховка на случай пропавшего события конца озвучки
    var greetingSentAt     = 0;      // метрика connect→audio
    var greetingLogged     = false;
    var ttsGateClosed      = false;  // реплики ассистента придерживаются (играет приветствие)
    var gatedTurnDone      = false;  // придержанная реплика получена целиком, ждёт открытия шлюза

    // ── Мьют клиентского аудио ──────────────────────────────────────────────
    var audioLinked = false;   // call.sendMediaTo(realtimeAPIClient) уже вызван
    var muteTimer   = null;

    // ── Прогрев плеера ──────────────────────────────────────────────────────
    var isWarmingUp    = false;
    var warmupCtx      = null;
    var warmupTimer    = null;
    var warmupStartedAt = 0;

    // ── Метрики задержки ────────────────────────────────────────────────────
    var mVadStop = 0, mRespCreated = 0, mFirstDelta = 0, mFirstChunk = 0;

    // ── Структурированный диалог ────────────────────────────────────────────
    var userMessageBuffer      = "";
    var assistantMessageBuffer = "";
    var dialogLog              = [];
    var lastFunctionResult     = null;
    var logCounter             = 0;

    // =========================================================================
    // ПАРСИНГ customData
    // =========================================================================
    var callData;
    try {
        callData = JSON.parse(VoxEngine.customData());
    } catch (err) {
        Logger.write("❌ Failed to parse custom data: " + err);
        VoxEngine.terminate();
        return;
    }

    var PHONE_NUMBER          = callData.phone_number;
    var ASSISTANT_ID          = callData.assistant_id;
    var CALLER_ID             = callData.caller_id || "+1234567890";
    var MUTE_DURATION         = callData.mute_duration_ms !== undefined ? callData.mute_duration_ms : 3000;
    var FIRST_PHRASE_OVERRIDE = callData.first_phrase || null;

    if (!PHONE_NUMBER || !ASSISTANT_ID) {
        Logger.write("❌ Missing required parameters: phone_number or assistant_id");
        VoxEngine.terminate();
        return;
    }

    var caller_number = "OUTBOUND: " + PHONE_NUMBER;
    var chat_id       = 'vox_' + Math.random().toString(36).substring(2, 15);
    var call_id       = null;

    var CONFIG        = null;
    var GREETING      = null;
    var functionNameToIdMap = {};

    var CONFIG_URL    = "https://voksyai.online/api/telephony/outbound-config?assistant_id=" +
                        ASSISTANT_ID + "&assistant_type=cartesia";
    var FUNCTIONS_URL = "https://voksyai.online/api/voximplant/functions/execute";
    var LOG_URL       = "https://voksyai.online/api/voximplant/log";

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("📞 OUTBOUND CALL (Cartesia v3.1)");
    Logger.write("   Target: "     + PHONE_NUMBER);
    Logger.write("   Caller ID: "  + CALLER_ID);
    Logger.write("   Assistant: "  + ASSISTANT_ID);
    Logger.write("   Mute (fixed): " + MUTE_DURATION + "ms");
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
                assistant_id:  ASSISTANT_ID,
                chat_id:       chat_id,
                call_id:       call_id || 'unknown',
                caller_number: caller_number,
                type:          "conversation",
                data: {
                    user_message:      userMessageBuffer,
                    assistant_message: assistantMessageBuffer,
                    function_result:   lastFunctionResult,
                    dialog:            dialogLog
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
                headers:  ["Content-Type: application/json"],
                method:   'POST',
                postData: JSON.stringify(payload)
            });

            Logger.write("📡 Log #" + logCounter + " → HTTP " + logResponse.code);

            if (logResponse.code == 200 && isFinal) {
                userMessageBuffer      = "";
                assistantMessageBuffer = "";
                dialogLog              = [];
                lastFunctionResult     = null;
            }
        } catch (error) {
            Logger.write("❌ Error sending log: " + error);
        }
    }

    // =========================================================================
    // ЗАВЕРШЕНИЕ ЗВОНКА
    // =========================================================================
    var callEndHandler = async function(event) {
        if (isHangingUp) return;
        isHangingUp = true;

        Logger.write("📴 OUTBOUND CALL ENDED | Target: " + caller_number);

        if (event && event.cost     !== undefined) call_cost     = event.cost;
        if (event && event.duration !== undefined) call_duration = event.duration;

        disarmWatchdog();
        disarmWarmupKeepalive();
        if (hangupGuardTimer)   { clearTimeout(hangupGuardTimer);   hangupGuardTimer   = null; }
        if (muteTimer)          { clearTimeout(muteTimer);          muteTimer          = null; }
        if (greetingGuardTimer) { clearTimeout(greetingGuardTimer); greetingGuardTimer = null; }

        if (realtimeAPIClient) { try { realtimeAPIClient.close(); } catch (err) {} }
        if (ttsPlayer)         { try { ttsPlayer.stop(); } catch (err) {} ttsPlayer = null; }
        if (call)              { try { call.stopRecord(); } catch (err) {} }

        // Ждём RecordStopped
        await new Promise(function(resolve) { setTimeout(resolve, 500); });

        // Fallback метрики, если события не принесли cost/duration
        try {
            if (call) {
                if (typeof call.cost === "function") {
                    var c = call.cost();
                    if (c && call_cost === 0) call_cost = c;
                }
                if (typeof call.duration === "function") {
                    var d = call.duration();
                    if (d && call_duration === 0) call_duration = d;
                }
            }
        } catch (err) {}

        Logger.write("💰 BILLING: session=" + (call_session_history_id || "NONE") +
            ", cost=" + call_cost + ", duration=" + call_duration + "s" +
            ", record=" + (record_url ? "YES" : "NONE"));

        if (userMessageBuffer || assistantMessageBuffer || call_session_history_id || dialogLog.length > 0) {
            try { await sendConversationLog(true); } catch (err) { Logger.write("❌ final log: " + err); }
        }

        if (call) { try { call.hangup(); } catch (err) {} }

        setTimeout(function() {
            Logger.write("✅ Terminated. Total logs: " + logCounter);
            VoxEngine.terminate();
        }, 500);
    };

    // =========================================================================
    // ЗАГРУЗКА КОНФИГА
    // =========================================================================
    Logger.write("🔄 Loading config: " + CONFIG_URL);

    var configResponse;
    try {
        configResponse = await Net.httpRequestAsync(CONFIG_URL);
    } catch (err) {
        Logger.write("❌ Config request failed: " + err);
        VoxEngine.terminate();
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
    if (!CONFIG.api_key) {
        Logger.write("❌ No OpenAI API key in config");
        VoxEngine.terminate();
        return;
    }
    if (!CONFIG.cartesia_api_key) {
        Logger.write("❌ No Cartesia API key in config");
        VoxEngine.terminate();
        return;
    }
    if (!CONFIG.cartesia_voice_id) {
        Logger.write("❌ No Cartesia voice_id in config");
        VoxEngine.terminate();
        return;
    }
    if (CONFIG.assistant_type && CONFIG.assistant_type !== "cartesia") {
        Logger.write("❌ Wrong assistant type: " + CONFIG.assistant_type + " (expected: cartesia)");
        VoxEngine.terminate();
        return;
    }

    GREETING = cleanForTTS((FIRST_PHRASE_OVERRIDE || CONFIG.first_phrase || "Здравствуйте!").trim());

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("✅ CONFIG LOADED:");
    Logger.write("   📋 Assistant: "   + CONFIG.assistant_name);
    Logger.write("   🆔 ID: "          + ASSISTANT_ID);
    Logger.write("   🌐 Language: "    + CONFIG.language);
    Logger.write("   🎤 Voice: "       + CONFIG.cartesia_voice_id + " / " + TTS_MODEL_ID);
    Logger.write("   ⚡ Speed: "        + (CONFIG.voice_speed || DEFAULT_VOICE_SPEED));
    Logger.write("   🔧 Functions: "   + (CONFIG.functions ? CONFIG.functions.length : 0));
    Logger.write("   👋 Greeting: \""  + GREETING.substring(0, 60) + "\"");
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

    // =========================================================================
    // ПОДГОТОВКА ФУНКЦИЙ (поддержка вложенного и плоского формата)
    // =========================================================================
    var voximplantTools = [];

    if (CONFIG.functions && Array.isArray(CONFIG.functions)) {
        for (var i = 0; i < CONFIG.functions.length; i++) {
            var tool = CONFIG.functions[i];
            var funcDef;

            if (tool.type === "function" && tool.function) {
                funcDef = tool.function;
            } else if (tool.name) {
                funcDef = tool;
            } else {
                continue;
            }

            var functionId = (i + 1).toString();
            functionNameToIdMap[funcDef.name] = functionId;
            Logger.write("   🔧 Function: " + funcDef.name + " → ID: " + functionId);

            voximplantTools.push({
                type:        "function",
                name:        funcDef.name,
                description: funcDef.description,
                parameters:  funcDef.parameters
            });
        }
        Logger.write("✅ Transformed " + voximplantTools.length + " functions");
    } else {
        Logger.write("ℹ️ No functions configured");
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

        // Шлюз закрыт — играет приветствие. Дельты копим, в TTS не отдаём:
        // два контекста Cartesia, пишущих в один плеер, дают кашу в трубке.
        if (ttsGateClosed) return;

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
            context_id:          ctx,
            continue:            !!more,
            model_id:            TTS_MODEL_ID,
            language:            CONFIG.language || "ru",
            voice:               { mode: "id", id: CONFIG.cartesia_voice_id },
            generation_config:   { speed: CONFIG.voice_speed || DEFAULT_VOICE_SPEED },
            max_buffer_delay_ms: MAX_BUFFER_DELAY_MS
        };
        if (transcript !== null) params.transcript = transcript;
        return params;
    }

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
    // Плеер создаётся один раз — при прогреве до набора номера либо, если
    // прогрев не удался, на первой фразе, которую надо озвучить, — и живёт
    // до конца звонка.
    function speak(text, more) {
        if (!text || !text.trim() || isHangingUp || !call) return;
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
        pending          = "";
        turnFullText     = "";
        turnCtx          = null;
        ctxOpen          = false;
        firstChunkOfTurn = true;
        audioConfirmed   = false;
        recoveryStage    = 0;
        sawPlayerError   = false;
        mVadStop = 0; mRespCreated = 0; mFirstDelta = 0; mFirstChunk = 0;
    }

    // Оценка длительности озвучки — нужна как страховка шлюза приветствия,
    // если событие окончания воспроизведения от плеера так и не придёт.
    function estimateSpeechMs(text) {
        var speed = CONFIG.voice_speed || DEFAULT_VOICE_SPEED;
        var cps   = SPEECH_CPS * speed;
        return Math.round(400 + (text.length / cps) * 1000);
    }

    // =========================================================================
    // ПРОГРЕВ ПЛЕЕРА (пока идут гудки)
    // =========================================================================
    // Холодный старт Cartesia (WS + TLS + auth + прогрев модели) стоил
    // 0.87–1.24 с и в v3.0 приходился ровно на момент, когда абонент снял
    // трубку. Здесь плеер поднимается ДО callPSTN на выброшенном контексте
    // с тишиной, а к звонку прицепляется в Connected.
    //
    // Прогрев принципиально не имеет права уронить звонок: любая ошибка →
    // ttsPlayer = null и ленивое создание плеера, как раньше.

    function warmupPlayer() {
        if (!WARMUP_ENABLED) return;
        try {
            warmupCtx       = newContextId();
            isWarmingUp     = true;
            warmupStartedAt = Date.now();

            // continue:true — контекст остаётся открытым, его добивает keep-alive
            ttsPlayer = Cartesia.createRealtimeTTSPlayer(WARMUP_TEXT, {
                apiKey: CONFIG.cartesia_api_key,
                generationRequestParameters: buildGenParams(warmupCtx, null, true)
            });
            playerDead = false;
            attachPlayerListeners();
            // sendMediaTo(call) НЕ вызываем — звонка ещё нет

            armWarmupKeepalive();
            Logger.write("🔥 Warmup: плеер создан до набора номера (ctx " + warmupCtx + ")");
        } catch (err) {
            Logger.write("⚠️ Warmup failed: " + err + " — работаем по ленивой схеме");
            isWarmingUp = false;
            warmupCtx   = null;
            ttsPlayer   = null;
        }
    }

    // Долгие гудки: Cartesia может закрыть простаивающий контекст.
    function armWarmupKeepalive() {
        disarmWarmupKeepalive();
        warmupTimer = setTimeout(function() {
            warmupTimer = null;
            if (!isWarmingUp || !ttsPlayer || playerDead) return;
            try {
                ttsPlayer.generationRequest(buildGenParams(warmupCtx, " ", true));
                Logger.write("🔥 Warmup keep-alive");
            } catch (err) {
                Logger.write("⚠️ Warmup keep-alive failed: " + err);
                return;
            }
            armWarmupKeepalive();
        }, WARMUP_KEEPALIVE_MS);
    }

    function disarmWarmupKeepalive() {
        if (warmupTimer) { clearTimeout(warmupTimer); warmupTimer = null; }
    }

    // Абонент снял трубку: закрываем прогревочный контекст, чистим буфер от
    // накопленной тишины и прицепляем уже тёплый плеер к звонку.
    function finishWarmup() {
        if (!isWarmingUp) return;
        isWarmingUp = false;
        disarmWarmupKeepalive();

        if (!ttsPlayer) { warmupCtx = null; return; }

        if (playerDead) {
            Logger.write("⚠️ Warmup: плеер умер до Connected — пересоздадим лениво");
            try { ttsPlayer.stop(); } catch (err) {}
            ttsPlayer  = null;
            playerDead = false;
            warmupCtx  = null;
            return;
        }

        try {
            if (warmupCtx) ttsPlayer.generationRequest(buildGenParams(warmupCtx, " ", false));
        } catch (err) {
            Logger.write("⚠️ Warmup: не удалось закрыть контекст: " + err);
        }
        warmupCtx = null;

        try { ttsPlayer.clearBuffer(); } catch (err) {}

        try {
            ttsPlayer.sendMediaTo(call);
            Logger.write("🔥 Warmup: плеер прогрет за " + (Date.now() - warmupStartedAt) +
                "ms и подключён к звонку");
        } catch (err) {
            Logger.write("⚠️ Warmup: sendMediaTo failed: " + err + " — пересоздадим лениво");
            try { ttsPlayer.stop(); } catch (e2) {}
            ttsPlayer = null;
        }
    }

    // Ошибка Cartesia во время прогрева — не повод для recovery-логики
    // текущей реплики (реплики ещё нет). Просто откатываемся к ленивой схеме.
    function onWarmupError(what) {
        Logger.write("⚠️ [Cartesia] warmup error: " + what + " — падаем на ленивое создание плеера");
        disarmWarmupKeepalive();
        isWarmingUp = false;
        warmupCtx   = null;
        try { if (ttsPlayer) ttsPlayer.stop(); } catch (err) {}
        ttsPlayer  = null;
        playerDead = false;
    }

    // =========================================================================
    // ШЛЮЗ TTS НА ВРЕМЯ ПРИВЕТСТВИЯ
    // =========================================================================
    // Мьют теперь фиксированный, поэтому абонент может влезть с "Алло" ещё до
    // конца приветствия. Приветствие мы не рвём, но и ответ модели поверх него
    // пустить нельзя. Шлюз придерживает реплику до конца озвучки приветствия.

    function armGreetingGuard() {
        var ms = estimateSpeechMs(GREETING) + GREETING_TAIL_MS;
        Logger.write("🛡 Greeting guard: " + ms + "ms");
        greetingGuardTimer = setTimeout(function() {
            greetingGuardTimer = null;
            if (!greetingPending) return;
            Logger.write("⚠️ Событие конца приветствия не пришло за " + ms +
                "ms — открываем шлюз принудительно");
            onSpeechFinished("greeting guard timeout");
        }, ms);
    }

    function releaseTtsGate() {
        if (!ttsGateClosed) return;
        ttsGateClosed = false;

        // Контекст приветствия уже закрыт — придержанная реплика должна уйти
        // в свежий, иначе Cartesia ответит "Invalid context ID".
        turnCtx          = null;
        ctxOpen          = false;
        firstChunkOfTurn = true;

        if (isInterrupted || isHangingUp) {
            gatedTurnDone = false;
            pending       = "";
            return;
        }

        if (gatedTurnDone) {
            gatedTurnDone = false;
            var t = (turnFullText || "").trim();
            pending = "";
            if (t) {
                Logger.write("▶ Отпускаем придержанную реплику (" + t.length + " симв.)");
                speak(t, false);
            }
        } else if (pending) {
            // Реплика ещё генерируется — догоняем обычным стримом
            Logger.write("▶ Шлюз открыт, догоняем стрим");
            drainPending();
        }
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
    // Cartesia: текст может уйти в мёртвый плеер, и "total≈271ms" при
    // фактических 5272 мс никого ни о чём не предупредит.
    function confirmAudio(source, ttfb) {
        disarmWatchdog();
        if (audioConfirmed) return;
        audioConfirmed = true;

        // Приветствие: у него нет mVadStop, поэтому меряем от Connected.
        // Именно эта цифра показывает, что дал прогрев плеера.
        if (!mVadStop && greetingSentAt && !greetingLogged) {
            greetingLogged = true;
            Logger.write("⏱ GREETING: connect→audio=" + (Date.now() - greetingSentAt) + "ms" +
                (typeof ttfb === "number" ? " ttfb=" + ttfb + "ms" : "") +
                (WARMUP_ENABLED ? " [warm]" : " [cold]") +
                " [" + source + "]");
            return;
        }

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
    // подтверждения. В этом случае молча снимаем watchdog до конца звонка.
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

        var textToRepeat = (greetingPending ? greetingText : turnFullText || "").trim();

        if (recoveryStage === 0 && ttsPlayer && !playerDead) {
            recoveryStage = 1;
            Logger.write("⚠️ [Cartesia] no audio in " + TTS_WATCHDOG_MS + "ms after error — filler + resend");
            stopSpeaking();
            turnCtx = newContextId();
            try {
                ttsPlayer.generationRequest(buildGenParams(turnCtx, FILLER_TEXT + " ", !!textToRepeat));
                if (textToRepeat) {
                    ttsPlayer.generationRequest(buildGenParams(turnCtx, textToRepeat, false));
                }
                ctxOpen = false;
            } catch (err) {
                Logger.write("[Cartesia] resend failed: " + err);
            }
            armWatchdog(TTS_RETRY_MS);
            return;
        }

        if (recoveryStage <= 1) {
            recoveryStage    = 2;
            Logger.write("⚠️ [Cartesia] still silent — recreating player");
            if (ttsPlayer) { try { ttsPlayer.stop(); } catch (err) {} }
            ttsPlayer        = null;
            playerDead       = false;
            turnCtx          = null;
            ctxOpen          = false;
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
    // Во время прогрева все события глушатся: они относятся к выброшенному
    // контексту и, дойдя до onSpeechFinished, сломали бы greetingPending
    // и hangupAfterSpeech.
    function attachPlayerListeners() {
        ttsPlayer.addEventListener(PlayerEvents.Started, function() {
            if (isWarmingUp) { Logger.write("[Cartesia] ▶ Started (warmup, игнор)"); return; }
            Logger.write("[Cartesia] ▶ Started");
            confirmAudio("Started");
        });

        ttsPlayer.addEventListener(PlayerEvents.AudioChunksPlaybackFinished, function(ev) {
            if (isWarmingUp) { Logger.write("[Cartesia] chunks finished (warmup, игнор)"); return; }
            var ttfb = (ev && typeof ev.timeToFirstByte === "number") ? ev.timeToFirstByte : undefined;
            Logger.write("[Cartesia] ✅ chunks finished (ttfb=" + (ttfb === undefined ? "n/a" : ttfb) + "ms)");
            confirmAudio("AudioChunks", ttfb);
            onSpeechFinished("AudioChunksPlaybackFinished");
        });

        ttsPlayer.addEventListener(PlayerEvents.PlaybackFinished, function(ev) {
            if (isWarmingUp) {
                if (ev && ev.error) { onWarmupError("PlaybackFinished: " + ev.error); return; }
                Logger.write("[Cartesia] PlaybackFinished (warmup, игнор)");
                return;
            }
            if (ev && ev.error) { onPlayerError("PlaybackFinished: " + ev.error); return; }
            Logger.write("[Cartesia] ✅ PlaybackFinished");
            confirmAudio("PlaybackFinished");
            onSpeechFinished("PlaybackFinished");
        });

        ttsPlayer.addEventListener(PlayerEvents.Error, function(ev) {
            if (isWarmingUp) { onWarmupError("Player.Error: " + (ev && ev.error)); return; }
            onPlayerError("Player.Error: " + (ev && ev.error));
        });
    }

    // Единая точка "озвучка закончилась": открывает шлюз после приветствия
    // и вешает трубку после прощания.
    function onSpeechFinished(reason) {
        if (greetingPending) {
            greetingPending = false;
            greetingDone    = true;
            if (greetingGuardTimer) { clearTimeout(greetingGuardTimer); greetingGuardTimer = null; }
            Logger.write("👋 Приветствие доиграно (" + reason + ")");
            releaseTtsGate();
        }
        if (hangupAfterSpeech) finishHangup(reason);
    }

    // Ошибка от Cartesia уносит плеер с собой: дальнейшие generationRequest
    // уходят в никуда молча. Помечаем плеер мёртвым сразу и, если текущая
    // реплика ещё не зазвучала, чиним не дожидаясь watchdog'а.
    function onPlayerError(what) {
        Logger.write("[Cartesia] ❌ " + what);
        playerDead     = true;
        sawPlayerError = true;
        ctxOpen        = false;

        if (audioConfirmed || isInterrupted || isHangingUp) return;

        // Пока играет приветствие, turnFullText может уже принадлежать
        // придержанной реплике — восстанавливаем именно приветствие.
        var recoveringGreeting = greetingPending;
        var textToRepeat = ((recoveringGreeting ? greetingText : turnFullText) || "").trim();
        if (!textToRepeat) return;   // озвучивать нечего

        disarmWatchdog();
        Logger.write("[Cartesia] recovering " + (recoveringGreeting ? "greeting" : "current turn") +
            " on a fresh player");

        recoveryStage    = 2;
        try { ttsPlayer.stop(); } catch (err) {}
        ttsPlayer        = null;
        playerDead       = false;
        turnCtx          = null;
        ctxOpen          = false;
        firstChunkOfTurn = true;

        // pending — это суффикс turnFullText, а не отдельный кусок: обработчик
        // дельт пишет в оба. Складывать их нельзя, хвост задвоится.
        // Но при восстановлении приветствия pending принадлежит придержанной
        // реплике и должен пережить восстановление.
        if (!recoveringGreeting) pending = "";

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
    // МЬЮТ КЛИЕНТСКОГО АУДИО — ФИКСИРОВАННОЕ ОКНО
    // =========================================================================
    // Аудио абонента не маршрутизируется в OpenAI ровно MUTE_DURATION мс от
    // момента ответа на звонок. Это не "мьют микрофона" в привычном смысле:
    // сказанное в это окно нигде не буферизуется и в транскрипт не попадёт.
    //
    // От длины приветствия окно больше не зависит — за приветствие теперь
    // отвечает шлюз TTS (releaseTtsGate), а не мьют.
    function linkClientAudio(reason) {
        if (audioLinked || isHangingUp) return;
        if (!call || !realtimeAPIClient) return;
        audioLinked = true;
        if (muteTimer) { clearTimeout(muteTimer); muteTimer = null; }
        call.sendMediaTo(realtimeAPIClient);
        Logger.write("🎙️ Client audio → OpenAI connected (" + reason + ")");
    }

    function startMuteWindow() {
        if (MUTE_DURATION <= 0) {
            linkClientAudio("mute disabled");
            return;
        }

        Logger.write("🔇 Mute: фиксированные " + MUTE_DURATION + "ms от Connected");

        muteTimer = setTimeout(function() {
            muteTimer = null;
            linkClientAudio("fixed mute " + MUTE_DURATION + "ms");
        }, MUTE_DURATION);
    }

    // =========================================================================
    // ПОДКЛЮЧЕНИЕ К OPENAI REALTIME (STT + turn detection + reasoning)
    // =========================================================================
    Logger.write("🔌 Connecting to OpenAI Realtime API...");

    try {
        realtimeAPIClient = await OpenAI.createRealtimeAPIClient({
            apiKey: CONFIG.api_key,
            model:  CONFIG.model || "gpt-realtime",
            type:   OpenAI.RealtimeAPIClientType.REALTIME,
            onWebSocketClose: function() {
                Logger.write("[OpenAI] WS closed");
                if (!isHangingUp) callEndHandler(null);
            },
            onWebSocketError: function(err) {
                Logger.write("[OpenAI] WS error: " + JSON.stringify(err));
            }
        });
    } catch (err) {
        Logger.write("❌ Failed to connect to OpenAI: " + err);
        VoxEngine.terminate();
        return;
    }

    Logger.write("✅ OpenAI connected");

    // =========================================================================
    // SESSION CREATED — конфигурируем сессию
    // =========================================================================
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.SessionCreated,
        function() {
            if (sessionConfigured) return;
            sessionConfigured = true;
            Logger.write("[OpenAI] Session created — configuring");

            // Модель должна знать, что приветствие уже прозвучало из TTS
            // напрямую, иначе первым же ответом поздоровается второй раз.
            var instructions = CONFIG.system_prompt || "Ты голосовой ассистент.";
            instructions += "\n\nЭто ИСХОДЯЩИЙ звонок: ты позвонил сам. " +
                "Ты уже поприветствовал абонента фразой: «" + GREETING + "». " +
                "Не здоровайся повторно и не представляйся заново.";

            // turn_detection живёт ВНУТРИ audio.input. В корне session он молча
            // игнорируется, и остаются дефолты OpenAI (silence_duration_ms 200).
            realtimeAPIClient.sessionUpdate({
                session: {
                    type:              "realtime",
                    output_modalities: ["text"],
                    instructions:      instructions,
                    audio: {
                        input: {
                            transcription: {
                                model:    "gpt-4o-transcribe",
                                language: CONFIG.language || "ru"
                            },
                            turn_detection: {
                                type:                "server_vad",
                                threshold:           VAD_THRESHOLD,
                                prefix_padding_ms:   VAD_PREFIX_MS,
                                silence_duration_ms: VAD_SILENCE_MS,
                                create_response:     true,
                                interrupt_response:  true
                            }
                        }
                    },
                    tools:       voximplantTools,
                    tool_choice: voximplantTools.length > 0 ? "auto" : "none"
                }
            });

            Logger.write("[OpenAI] Session configured");
        }
    );

    // Проверяем, что VAD реально применился (в v1.1 он молча игнорировался).
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.SessionUpdated,
        function(event) {
            try {
                var s  = event && event.data && event.data.payload && event.data.payload.session;
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
            if (!callConnected) return;
            isInterrupted = false;
            var vadStop = mVadStop;
            resetTurnState();          // turnFullText приветствия здесь стирается,
                                       // но само приветствие живёт в greetingText
            gatedTurnDone = false;
            mVadStop      = vadStop;   // метрику текущего хода сохраняем
            mRespCreated  = Date.now();
            Logger.write("[OpenAI] Response started" + (ttsGateClosed ? " (шлюз закрыт, придержим)" : ""));
        }
    );

    // Дельты текста → чанкер → Cartesia
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ResponseOutputTextDelta,
        function(event) {
            if (!callConnected || isInterrupted || isHangingUp) return;
            var delta =
                (event && event.data && event.data.delta) ||
                (event && event.data && event.data.payload && event.data.payload.delta) || "";
            if (!delta) return;

            if (!mFirstDelta) mFirstDelta = Date.now();

            var clean = cleanForTTS(delta);
            pending      += clean;
            turnFullText += clean;
            drainPending();            // при закрытом шлюзе просто копит
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

            // Подстраховка: если дельты не пришли (ответ отдан одним куском),
            // turnFullText пуст — берём полный текст.
            if (!turnFullText) turnFullText = cleanForTTS(text);

            // Шлюз закрыт — приветствие ещё звучит. Реплика целиком уйдёт в TTS
            // из releaseTtsGate, когда приветствие доиграет.
            if (ttsGateClosed) {
                pending       = "";
                gatedTurnDone = true;
                Logger.write("⏸ Реплика придержана до конца приветствия");
                return;
            }

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

    // Перебивание абонентом (серверный VAD)
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.InputAudioBufferSpeechStarted,
        function() {
            if (!callConnected) return;

            // Приветствие защищено: не рвём озвучку и не выставляем
            // isInterrupted, иначе ответ модели на это "Алло" будет отброшен.
            // Ответ придержит шлюз до конца приветствия.
            if (greetingPending) {
                Logger.write("[OpenAI] SPEECH STARTED во время приветствия — озвучку НЕ рвём (защита)");
                return;
            }

            Logger.write("[OpenAI] SPEECH STARTED — stop Cartesia" +
                (ctxOpen ? " (context open → cancel)" : " (context closed → buffer only)"));
            isInterrupted = true;
            disarmWatchdog();
            try { realtimeAPIClient.clearMediaBuffer(); } catch (err) {}
            stopSpeaking();
            pending          = "";
            turnCtx          = null;
            ctxOpen          = false;
            firstChunkOfTurn = true;
            gatedTurnDone    = false;
        }
    );

    // Конец реплики абонента — точка отсчёта метрик
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.InputAudioBufferSpeechStopped,
        function() {
            if (!callConnected) return;
            mVadStop = Date.now();
        }
    );

    // Транскрипция абонента
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ConversationItemInputAudioTranscriptionCompleted,
        function(event) {
            try {
                var payload    = event.data && event.data.payload;
                var transcript = payload && payload.transcript;
                if (transcript && transcript.trim()) {
                    Logger.write("👤 USER (" + PHONE_NUMBER + "): \"" + transcript + "\"");
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
                var item    = payload && payload.item;
                if (!item || item.type !== "function_call") return;

                var functionName = item.name;
                var callId       = item.call_id;
                var args         = JSON.parse(item.arguments);
                Logger.write("🔧 FUNCTION CALL: " + functionName);

                if (functionName === "hangup_call") {
                    Logger.write("📴 HANGUP CALL requested");
                    lastFunctionResult = { action: "call_terminated", reason: args.reason || "user request" };

                    if (args.farewell_message && args.farewell_message.trim()) {
                        var farewell = cleanForTTS(args.farewell_message.trim());

                        // Если приветствие ещё звучит — дожидаемся его, иначе
                        // прощание наложится поверх. Крайне редкий, но реальный
                        // случай при очень длинном приветствии.
                        if (ttsGateClosed) {
                            Logger.write("⏸ Прощание придержано до конца приветствия");
                            turnFullText      = farewell;
                            gatedTurnDone     = true;
                            hangupAfterSpeech = true;
                        } else {
                            resetTurnState();
                            turnFullText      = farewell;
                            hangupAfterSpeech = true;
                            speak(farewell, false);
                        }

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
                    headers:  ["Content-Type: application/json"],
                    method:   'POST',
                    postData: JSON.stringify({
                        function_id: function_id,
                        arguments:   args,
                        call_data: {
                            call_id:       call_id,
                            chat_id:       chat_id,
                            assistant_id:  ASSISTANT_ID,
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
    // ПРОГРЕВ ПЛЕЕРА ПЕРЕД НАБОРОМ
    // =========================================================================
    warmupPlayer();

    // =========================================================================
    // СОВЕРШЕНИЕ ИСХОДЯЩЕГО ЗВОНКА
    // =========================================================================
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("📞 Calling: " + PHONE_NUMBER + " from " + CALLER_ID);
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

    call = VoxEngine.callPSTN(PHONE_NUMBER, CALLER_ID);

    // ─── CONNECTED ───────────────────────────────────────────────────────────
    call.addEventListener(CallEvents.Connected, function() {
        callConnected = true;
        call_id = call.id();
        Logger.write("✅ OUTBOUND CALL CONNECTED | Call ID: " + call_id);

        try {
            call.record({ stereo: false, lossless: false, hd_audio: true });
            Logger.write("🎙️ Recording started");
        } catch (recordErr) {
            Logger.write("⚠️ Recording failed: " + recordErr);
        }

        // ── Прицепляем прогретый плеер к звонку ─────────────────────────────
        finishWarmup();

        // ── Приветствие: напрямую в Cartesia, без раунда к модели ───────────
        Logger.write("🤖 AGENT (greeting): \"" + GREETING.substring(0, 60) + "\"");
        dialogLog.push({ role: 'assistant', text: GREETING, ts: Date.now() });
        if (assistantMessageBuffer) assistantMessageBuffer += " ";
        assistantMessageBuffer += GREETING;

        resetTurnState();
        greetingText    = GREETING;
        turnFullText    = GREETING;   // чтобы приветствие можно было переозвучить
        greetingPending = true;
        greetingDone    = false;
        greetingLogged  = false;
        greetingSentAt  = Date.now();
        ttsGateClosed   = true;       // реплики модели придерживаем до конца приветствия
        gatedTurnDone   = false;

        speak(GREETING, false);
        armGreetingGuard();

        // ── Мьют: фиксированное окно от этого момента ───────────────────────
        startMuteWindow();
    });

    // ─── RECORD EVENTS ───────────────────────────────────────────────────────
    call.addEventListener(CallEvents.RecordStarted, function(event) {
        if (event.url) { record_url = event.url; Logger.write("🎙️ RecordStarted: " + record_url); }
    });

    call.addEventListener(CallEvents.RecordStopped, function(event) {
        if (event.url) record_url = event.url;
        if (event.cost !== undefined) Logger.write("🎙️ RecordStopped, cost: " + event.cost);
    });

    // ─── FAILED / DISCONNECTED ───────────────────────────────────────────────
    call.addEventListener(CallEvents.Failed, function(event) {
        Logger.write("❌ OUTBOUND CALL FAILED | Code: " + event.code + " | Reason: " + event.reason);
        callEndHandler(event);
    });

    call.addEventListener(CallEvents.Disconnected, function(event) {
        callEndHandler(event);
    });

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("🎉 READY (OUTBOUND Cartesia v3.1)");
    Logger.write("   🔑 Session: "     + call_session_history_id);
    Logger.write("   🎤 TTS: "         + TTS_MODEL_ID + ", buffer delay " + MAX_BUFFER_DELAY_MS + "ms");
    Logger.write("   🔥 Warmup: "      + (WARMUP_ENABLED ? "ON, keep-alive " + WARMUP_KEEPALIVE_MS + "ms" : "OFF"));
    Logger.write("   🎧 VAD silence: " + VAD_SILENCE_MS + "ms");
    Logger.write("   🔇 Mute: "        + MUTE_DURATION + "ms (fixed, from Connected)");
    Logger.write("   🛡 Greeting: "    + "защищено от обрыва, шлюз TTS активен");
    Logger.write("   📝 Structured dialog: ENABLED");
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
});
