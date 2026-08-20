require(Modules.OpenAI);

/*
 * Voximplant OUTBOUND Fish Script v1.1
 * ====================================================================
 * Архитектура — та же, что в inbound_fish:
 *   - OpenAI Realtime API (output_modalities: text) ведёт диалог и сам
 *     транскрибирует речь абонента (отдельный ASR не нужен).
 *   - Озвучка — Fish Audio через прокси Voicyfy:
 *
 *         сценарий --{event:"text"}--> /ws/fish/tts/{id} --> Fish Audio
 *         звонок   <--- media-фреймы PCM16 --- прокси <--- PCM ---┘
 *
 * Отличия исходящего от входящего:
 *
 *   1. Конфиг берётся по assistant_id из customData, а не по номеру.
 *   2. Сокет к прокси поднимается ДО callPSTN — пока идут гудки. К моменту
 *      ответа абонента синтез готов принимать текст, приветствие не ждёт
 *      рукопожатия (аналог прогрева плеера в cartesia_outbound).
 *   3. Мьют клиентского аудио: первые MUTE_DURATION мс после Connected
 *      аудио абонента в модель не идёт. Шлюзы операторов часто отдают в
 *      начале щелчки и обрывки гудка — без мьюта серверный VAD принимает
 *      их за речь и обрывает приветствие.
 *   4. Шлюз TTS: реплики модели придерживаются, пока звучит приветствие.
 *      Момент окончания приходит от прокси сообщением speech_done —
 *      длину фразы оценивать не нужно (cartesia_outbound считает её по
 *      символам, здесь это лишнее).
 *
 *   5. Контекст звонка из customData (v1.1): имя контакта, задача звонка и
 *      её описание дописываются в instructions, а custom_greeting (первая
 *      фраза от PreCall-оркестратора агента обзвона) переопределяет
 *      приветствие из конфига. В конфиге ассистента этого быть не может:
 *      один голос обзванивает разных людей по разным поводам.
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
var GREETING_GUARD_MS = 30000;       // потолок ожидания конца приветствия
var TTS_REOPEN_MAX   = 2;            // попыток переоткрыть сокет к прокси

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
    Logger.write("🚀 APP STARTED (OUTBOUND Fish v1.1)");
    Logger.write("🔑 Session History ID: " + call_session_history_id);
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

    // ── Состояние сценария ──────────────────────────────────────────────────
    var realtimeAPIClient = null;
    var call              = null;
    var callConnected     = false;
    var sessionConfigured = false;
    var isInterrupted     = false;
    var isHangingUp       = false;

    // ── Сокет к прокси синтеза ──────────────────────────────────────────────
    var ttsSocket = null;
    var ttsOpen = false;
    var ttsAttached = false;
    var ttsQueue = [];
    var ttsFlushQueued = false;
    var ttsReopens = 0;
    var ttsMediaAccepted = false;  // пришёл ли MEDIA_STARTED (StartEvent принят)

    // ── Состояние текущей реплики ассистента ────────────────────────────────
    var turnFullText   = "";
    var turnStarted    = false;
    var firstFlushDone = false;
    var deltaBuffer    = "";     // дельты, ещё не ушедшие в синтез
    var audioConfirmed = false;

    // ── Watchdog / завершение по прощанию ───────────────────────────────────
    var watchdogTimer     = null;
    var watchdogDisabled  = false;
    var hangupAfterSpeech = false;
    var hangupGuardTimer  = null;

    // ── Приветствие и шлюз TTS ──────────────────────────────────────────────
    var greetingPending    = false;  // приветствие ещё звучит
    var greetingGuardTimer = null;
    var greetingSentAt     = 0;
    var ttsGateClosed      = false;  // реплики модели придерживаются
    var gatedText          = "";     // придержанная реплика

    // ── Мьют клиентского аудио ──────────────────────────────────────────────
    var audioLinked = false;
    var muteTimer   = null;

    // ── Метрики задержки ────────────────────────────────────────────────────
    var mVadStop = 0, mRespCreated = 0, mFirstDelta = 0, mFirstText = 0;

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

    // Контекст звонка от бэкенда (агент обзвона / задача из CRM). Приезжает в
    // script_custom_data — см. voximplant_partner.start_outbound_call.
    // custom_greeting — первая фраза, которую сочинил PreCall-оркестратор для
    // конкретного контакта; она приоритетнее приветствия из конфига.
    var CONTACT_NAME     = callData.contact_name || "";
    var TASK_TITLE       = callData.task_title || "";
    var TASK_DESCRIPTION = callData.task_description || "";
    var API_TASK         = callData.task || "";
    var CUSTOM_GREETING  = callData.custom_greeting || "";

    if (!PHONE_NUMBER || !ASSISTANT_ID) {
        Logger.write("❌ Missing required parameters: phone_number or assistant_id");
        VoxEngine.terminate();
        return;
    }

    var caller_number = "OUTBOUND: " + PHONE_NUMBER;
    var chat_id       = 'vox_' + Math.random().toString(36).substring(2, 15);
    var call_id       = null;

    var CONFIG   = null;
    var GREETING = null;
    var functionNameToIdMap = {};

    var CONFIG_URL    = "https://voicyfy.ru/api/telephony/outbound-config?assistant_id=" + ASSISTANT_ID;
    var FUNCTIONS_URL = "https://voicyfy.ru/api/voximplant/functions/execute";
    var LOG_URL       = "https://voicyfy.ru/api/voximplant/log";

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("📞 OUTBOUND CALL (Fish v1.1)");
    Logger.write("   To: " + PHONE_NUMBER);
    Logger.write("   Caller ID: " + CALLER_ID);
    Logger.write("   Assistant: " + ASSISTANT_ID);
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
                caller_number: caller_number,
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

        Logger.write("📴 OUTBOUND CALL ENDED");

        if (event && event.cost !== undefined)     call_cost = event.cost;
        if (event && event.duration !== undefined) call_duration = event.duration;

        disarmWatchdog();
        if (hangupGuardTimer)   { clearTimeout(hangupGuardTimer); hangupGuardTimer = null; }
        if (greetingGuardTimer) { clearTimeout(greetingGuardTimer); greetingGuardTimer = null; }
        if (muteTimer)          { clearTimeout(muteTimer); muteTimer = null; }

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

    GREETING = (CUSTOM_GREETING || FIRST_PHRASE_OVERRIDE || CONFIG.first_phrase || "Здравствуйте!").trim();

    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    Logger.write("✅ CONFIG LOADED:");
    Logger.write("   📋 Assistant: " + CONFIG.assistant_name);
    Logger.write("   🌐 Language: " + CONFIG.language);
    Logger.write("   🧠 LLM: " + (CONFIG.model || "gpt-realtime-2.1-mini"));
    Logger.write("   🐟 Fish voice: " + CONFIG.fish_voice_id + " / " + CONFIG.fish_model +
                 " (" + CONFIG.fish_latency + ", " + CONFIG.sample_rate + " Hz)");
    Logger.write("   👋 Greeting: \"" + GREETING.substring(0, 60) + "\"");
    Logger.write("   🔧 Functions: " + (CONFIG.functions ? CONFIG.functions.length : 0));
    if (CONTACT_NAME || TASK_TITLE || TASK_DESCRIPTION || API_TASK) {
        Logger.write("   📇 CRM: " + (CONTACT_NAME || "без имени") +
            (TASK_TITLE ? " | " + TASK_TITLE : ""));
    }
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

    // =========================================================================
    // КОНТЕКСТ ЗВОНКА → В ПРОМПТ
    // =========================================================================
    // Задача звонка и карточка контакта известны только бэкенду, в конфиге
    // ассистента их нет: один и тот же голос обзванивает разных людей по разным
    // поводам. Поэтому контекст приезжает в customData и дописывается в
    // instructions — как в outbound_cascade.
    function buildContextBlock() {
        var block = "";
        if (API_TASK) {
            block +=
                "══════════════════════════════════════\n" +
                "ЗАДАЧА НА ЭТОТ ЗВОНОК\n" +
                "══════════════════════════════════════\n" +
                API_TASK + "\n" +
                "Выполни эту задачу — это главная цель звонка.\n" +
                "══════════════════════════════════════\n\n";
        }
        if (CONTACT_NAME || TASK_TITLE || TASK_DESCRIPTION) {
            block +=
                "══════════════════════════════════════\n" +
                "КОНТЕКСТ ЗВОНКА (CRM)\n" +
                "══════════════════════════════════════\n";
            if (CONTACT_NAME)     block += "Клиент: " + CONTACT_NAME + " (обращайся по имени).\n";
            if (TASK_TITLE)       block += "Задача: " + TASK_TITLE + "\n";
            if (TASK_DESCRIPTION) block += "Подробности: " + TASK_DESCRIPTION + "\n";
            block += "══════════════════════════════════════\n\n";
        }
        return block;
    }

    // Реальные номера и текущее время в промпт: без них модель не может
    // корректно вызвать send_sms (некуда отправлять) и путается в датах.
    // Для исходящего caller_number — номер клиента, called_number — наш. МСК.
    function buildCallInfoBlock() {
        var mskTime = new Date(Date.now() + 3 * 3600 * 1000)
            .toISOString().replace("T", " ").slice(0, 16);
        return "\n\nИнформация о звонке:\n" +
            "- Номер клиента (caller_number): " + PHONE_NUMBER + "\n" +
            "- Наш номер (called_number): " + CALLER_ID + "\n" +
            "- Текущее время: " + mskTime + " (МСК)";
    }

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
                var description = tool.function.description;
                if (tool.function.name === "hangup_call") {
                    // Без нажима модель «прощается словами» и держит линию до
                    // таймаута — на исходящем это лишние минуты телефонии.
                    description = "КРИТИЧЕСКИ ВАЖНО: вызови эту функцию НЕМЕДЛЕННО, " +
                        "когда задача звонка выполнена или собеседник хочет закончить " +
                        "разговор («пока», «до свидания», «всё, спасибо»). Не прощайся " +
                        "просто словами — вызови функцию.";
                }
                voximplantTools.push({
                    type: "function",
                    name: tool.function.name,
                    description: description,
                    parameters: tool.function.parameters
                });
            }
        }
    }

    // =========================================================================
    // TTS: ПОДГОТОВКА ТЕКСТА
    // =========================================================================
    function cleanForTTS(text) {
        return text.replace(/\s*\n+\s*/g, " ");
    }

    function isSentenceDot(s, i) {
        var prev = i > 0 ? s.charAt(i - 1) : "";
        var next = i + 1 < s.length ? s.charAt(i + 1) : "";
        if (/\d/.test(prev) && /\d/.test(next)) return false;
        if (/[A-Za-zА-Яа-яЁё]/.test(prev)) {
            var before = i >= 2 ? s.charAt(i - 2) : "";
            if (before === "" || before === " ") return false;
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
    function openTtsSocket() {
        Logger.write("[Fish] Opening TTS socket: " + CONFIG.fish_tts_url);

        ttsSocket = VoxEngine.createWebSocket(CONFIG.fish_tts_url);

        ttsSocket.addEventListener(WebSocketEvents.OPEN, function() {
            ttsOpen = true;
            Logger.write("[Fish] ✅ TTS socket open");

            attachTtsToCall();

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

                if (greetingPending) {
                    // Приветствие договорено — открываем шлюз ровно тогда,
                    // когда абонент его дослушает.
                    setTimeout(function() {
                        onGreetingFinished("speech done");
                    }, remaining + HANGUP_TAIL_MS);
                }
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

    // Медиа привязываем, только когда есть и открытый сокет, и поднятый
    // звонок. Порядок этих событий не гарантирован — зовём из обоих.
    function attachTtsToCall() {
        if (ttsAttached || !ttsOpen || !callConnected || !ttsSocket || !call) return;
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

    function speak(text, final) {
        if (!text || !text.trim() || isHangingUp) return;

        var clean = cleanForTTS(text);

        if (!ttsOpen) {
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
        turnFullText   = "";
        turnStarted    = false;
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

    function confirmAudio() {
        disarmWatchdog();
        if (audioConfirmed) return;
        audioConfirmed = true;

        if (greetingSentAt && greetingPending) {
            Logger.write("⏱ GREETING: connect→audio=" + (Date.now() - greetingSentAt) + "ms");
        }

        if (mVadStop) {
            var now = Date.now();
            var toFirstToken = mFirstDelta ? (mFirstDelta - mVadStop) : -1;
            var toTts = mFirstText ? (mFirstText - mVadStop) : -1;
            Logger.write("⏱ TURN: vad→token=" + toFirstToken + "ms" +
                " vad→tts=" + toTts + "ms" +
                " vad→audio=" + (now - mVadStop) + "ms");
        }
    }

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

    // =========================================================================
    // ПРИВЕТСТВИЕ И ШЛЮЗ TTS
    // =========================================================================
    function armGreetingGuard() {
        greetingGuardTimer = setTimeout(function() {
            greetingGuardTimer = null;
            if (!greetingPending) return;
            Logger.write("⚠️ speech_done по приветствию не пришёл за " + GREETING_GUARD_MS +
                "ms — открываем шлюз принудительно");
            onGreetingFinished("guard timeout");
        }, GREETING_GUARD_MS);
    }

    function onGreetingFinished(reason) {
        if (!greetingPending) return;
        greetingPending = false;
        if (greetingGuardTimer) { clearTimeout(greetingGuardTimer); greetingGuardTimer = null; }
        Logger.write("✅ Приветствие отзвучало (" + reason + ")");
        releaseTtsGate();
    }

    function releaseTtsGate() {
        if (!ttsGateClosed) return;
        ttsGateClosed = false;

        if (isInterrupted || isHangingUp) {
            gatedText = "";
            return;
        }

        if (gatedText && gatedText.trim()) {
            var t = gatedText.trim();
            gatedText = "";
            Logger.write("▶ Отпускаем придержанную реплику (" + t.length + " симв.)");
            resetTurnState();
            turnFullText = t;
            speak(t, true);
        }
    }

    // Мьют: аудио абонента подключается к модели не сразу. Всё сказанное в
    // это окно нигде не буферизуется и в транскрипт не попадёт.
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

    // Сокет к прокси поднимаем заранее — он должен быть готов к моменту,
    // когда абонент снимет трубку.
    openTtsSocket();

    // =========================================================================
    // ПОДКЛЮЧЕНИЕ К OPENAI REALTIME
    // =========================================================================
    Logger.write("🔌 Connecting to OpenAI Realtime API...");

    try {
        realtimeAPIClient = await OpenAI.createRealtimeAPIClient({
            apiKey: CONFIG.api_key,
            model:  CONFIG.model || "gpt-realtime-2.1-mini",
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

            var instructions = buildContextBlock() + (CONFIG.system_prompt || "");
            instructions += "\n\nТы уже поприветствовал абонента фразой: «" +
                GREETING + "». Не здоровайся повторно.";
            instructions += buildCallInfoBlock();

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
            if (!ttsGateClosed) resetTurnState();
            mVadStop = vadStop;
            mRespCreated = Date.now();
            Logger.write("[OpenAI] Response started");
        }
    );

    // Дельты текста → сразу в Fish (он буферизует сам).
    // Пока играет приветствие, шлюз закрыт — копим текст, не озвучивая.
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

            if (ttsGateClosed) {
                gatedText += clean;
                return;
            }

            pushDelta(clean);
            maybeEarlyFlush();
        }
    );

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
            if (assistantMessageBuffer) assistantMessageBuffer += " ";
            assistantMessageBuffer += text.trim();

            if (ttsGateClosed) {
                // Реплика дождётся конца приветствия и уйдёт целиком.
                gatedText = cleanForTTS(text);
                Logger.write("⏸ Реплика придержана шлюзом до конца приветствия");
                return;
            }

            if (!turnStarted && !deltaBuffer) {
                turnFullText = cleanForTTS(text);
                speak(turnFullText, true);
            } else {
                endTurn();
            }
        }
    );

    // Перебивание пользователем
    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.InputAudioBufferSpeechStarted,
        function() {
            if (!callConnected) return;
            // Приветствие не перебиваем: шлюз всё равно держит реплики, а
            // обрыв на первых словах звучит как сорванный звонок.
            if (greetingPending) {
                Logger.write("[OpenAI] SPEECH STARTED во время приветствия — игнорируем");
                return;
            }
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

    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.InputAudioBufferSpeechStopped,
        function() {
            if (!callConnected) return;
            mVadStop = Date.now();
        }
    );

    realtimeAPIClient.addEventListener(
        OpenAI.RealtimeAPIEvents.ConversationItemInputAudioTranscriptionCompleted,
        function(event) {
            try {
                var payload = event.data && event.data.payload;
                var transcript = payload && payload.transcript;
                if (transcript && transcript.trim()) {
                    Logger.write("👤 USER: \"" + transcript + "\"");
                    dialogLog.push({ role: 'user', text: transcript.trim(), ts: Date.now() });
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

        // Прицепляем прогретый сокет к звонку
        attachTtsToCall();

        // Приветствие — напрямую в Fish, без раунда к модели
        Logger.write("🤖 AGENT (greeting): \"" + GREETING.substring(0, 60) + "\"");
        dialogLog.push({ role: 'assistant', text: GREETING, ts: Date.now() });
        if (assistantMessageBuffer) assistantMessageBuffer += " ";
        assistantMessageBuffer += GREETING;

        resetTurnState();
        turnFullText    = GREETING;
        greetingPending = true;
        greetingSentAt  = Date.now();
        ttsGateClosed   = true;     // реплики модели придерживаем
        gatedText       = "";

        speak(GREETING, true);
        armGreetingGuard();

        // Мьют клиентского аудио на фиксированное окно от этого момента
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
    Logger.write("🎉 READY (OUTBOUND Fish v1.1)");
    Logger.write("   🔑 Session: "     + call_session_history_id);
    Logger.write("   🐟 TTS: Fish "    + CONFIG.fish_model + " через прокси");
    Logger.write("   🎧 VAD silence: " + VAD_SILENCE_MS + "ms");
    Logger.write("   🔇 Mute: "        + MUTE_DURATION + "ms (fixed, from Connected)");
    Logger.write("   🛡 Greeting: "    + "шлюз TTS активен до speech_done");
    Logger.write("   📝 Structured dialog: ENABLED");
    Logger.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
});
