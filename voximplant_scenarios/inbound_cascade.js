/**
 * inbound_cascade — входящий full-cascade Voice AI сценарий Voicyfy.
 *
 *   STT:  встроенный ASR (Yandex v2, streaming + interim), язык из конфига
 *   LLM:  OpenAI gpt-realtime-2.1-mini — Realtime API (WebSocket), режим ТОЛЬКО ТЕКСТ
 *   TTS:  VoxTTS (realtime-стриминг, голоса Anna/Sergey)
 *   Turn-taking: Silero VAD + Pipecat Smart Turn через VoxTurnTaking
 *
 * ПОЧЕМУ Realtime, а не Chat Completions (переход v2 -> v3):
 *  Chat Completions stateless: на КАЖДЫЙ ход открывался новый HTTP-стрим и
 *  пересылался весь массив messages — prefill рос с каждой репликой, TTFT
 *  держался на ~500-700мс. Realtime API даёт три вещи разом:
 *   1. WebSocket открыт с начала звонка — нет установки соединения на ход;
 *   2. контекст диалога живёт на стороне OpenAI — шлём только новую реплику
 *      через conversationItemCreate(), старое идёт как cached-токены;
 *   3. gpt-realtime-2.1-mini дистиллирована под низкую задержку.
 *  Итог: TTFT ~200-350мс. Спекулятивная генерация и warmup, которые прятали
 *  медленный TTFT, за ненадобностью УДАЛЕНЫ.
 *
 * ВАЖНО — TURN DETECTION У МОДЕЛИ ВЫКЛЮЧЕН:
 *  Realtime используется как чистый ТЕКСТОВЫЙ мозг. Аудио в него не заводится
 *  вообще (никакого call.sendMediaTo(realtime)), а в сессии стоит
 *  audio.input.turn_detection = null. Слушает Yandex ASR, момент «пора
 *  отвечать» определяет НАШ VoxTurnTaking (Silero + Pipecat) и явно дёргает
 *  responseCreate(). Побочный плюс: не тратятся audio-токены ($10/1M против
 *  $0.60/1M за текст).
 *
 * ГЕЙТИНГ ПОКОЛЕНИЙ:
 *  Одна активная генерация за раз. Всё, что приходит не с текущим response_id
 *  (или когда main.active === false), отбрасывается. Перебивание абонентом —
 *  responseCancel(): модель реально останавливается на сервере, а не досчитывает
 *  в никуда (в Chat Completions хвост доезжал и оплачивался).
 *
 * УЧЁТ ТОКЕНОВ (кредиты каскада):
 *  Из события ResponseDone берём response.usage: input_tokens, output_tokens и
 *  input_token_details.cached_tokens. Cached-токены считаются отдельно — они в
 *  10 раз дешевле ($0.06/1M против $0.60/1M), и именно в них уходит вся история
 *  диалога. Уходят в /log как cascade_usage.{prompt,cached_prompt,completion}_tokens.
 *
 * РЕКОННЕКТ:
 *  У Realtime-сессии есть TTL (у Chat Completions его не было). Локальный массив
 *  `messages` в запросы НЕ уходит, он ведётся как резервная копия: при обрыве WS
 *  поднимаем клиента заново и отдаём историю стенограммой внутри instructions.
 *
 * ЗАПИСЬ / СТОИМОСТЬ / ЛОГИРОВАНИЕ:
 *  - Запись звонка: call.record() на CallEvents.Connected, url из RecordStarted.
 *  - call_session_history_id: из AppEvents.Started (sessionId) — сервер по нему
 *    берёт ПОЛНУЮ стоимость через GetCallHistory.
 *  - Стоимость/длительность: из события Disconnected/Failed (cost/duration).
 *  - В конце звонка (terminateCall) один POST /api/voximplant/log со всем набором.
 *
 * ТРЕБОВАНИЯ:
 * 1. В правиле роутинга `vox-turn-taking` — ПЕРВЫМ, этот сценарий — ВТОРЫМ.
 * 2. Конфиг ассистента: GET /api/telephony/config.
 */

require(Modules.ASR);
require(Modules.OpenAI);
require(Modules.VoxTTS);
require(Modules.Recorder);

const BACKEND_URL = "https://voicyfy.ru";
const LOG_URL = BACKEND_URL + "/api/voximplant/log";
const FUNCTIONS_URL = BACKEND_URL + "/api/voximplant/functions/execute";
const LLM_MODEL = "gpt-realtime-2.1-mini";
// У 2.1-mini есть reasoning-токены (тарифицируются как output). "low" —
// компромисс качество/задержка, проверенный в cartesia_inbound.
const LLM_REASONING_EFFORT = "low";
// Сколько ждём SessionCreated после подключения WS, прежде чем считать
// подключение неудачным.
const SESSION_READY_TIMEOUT_MS = 6000;
// Сколько раз пробуем поднять WS заново, если он оборвался посреди звонка.
const MAX_RECONNECTS = 2;

// Пресеты паузы перед ответом (настройка агента, поле silence_duration_ms).
// Значение = сколько тишины ждёт Silero, прежде чем счесть, что человек
// замолчал. Это НЕ таймер ответа: вето детектора, стабильность транскрипта и
// инвариант «клиент говорит» всё равно могут отложить закрытие хода.
// 300 — быстрый ответ, риск вступить в паузу посреди фразы;
// 650 — сбалансированный; 1000 — терпеливый.
// Оценка длительности синтезированной речи: используется, чтобы понимать,
// звучит ли агент прямо сейчас (для фильтрации ложных перебиваний).
const MS_PER_CHAR = 95;
const SILENCE_MS_DEFAULT = 300;
const SILENCE_MS_MIN = 200;
const SILENCE_MS_MAX = 1500;
// Страховочный таймаут держим на фиксированный запас выше тишины: иначе он
// либо срабатывает раньше VAD, либо висит без нужды.
const SILENCE_TO_TIMEOUT_MS = 250;

function resolveSilenceMs(raw) {
    const v = Number(raw);
    if (!Number.isFinite(v) || v <= 0) return SILENCE_MS_DEFAULT;
    return Math.min(SILENCE_MS_MAX, Math.max(SILENCE_MS_MIN, Math.round(v)));
}

const RT = OpenAI.RealtimeAPIEvents;

// --- Извлечение данных из событий Realtime (провайдерский payload в data) ---
function rtPayload(event) {
    return event?.data?.payload ?? event?.data ?? {};
}
// Дельта текста (response.output_text.delta).
function rtDelta(event) {
    const d = event?.data;
    if (typeof d?.delta === "string" && d.delta) return d.delta;
    const p = d?.payload;
    if (typeof p?.delta === "string" && p.delta) return p.delta;
    return "";
}
// Полный текст ответа (response.output_text.done).
function rtDoneText(event) {
    const d = event?.data;
    if (typeof d?.text === "string" && d.text) return d.text;
    const p = d?.payload;
    if (typeof p?.text === "string" && p.text) return p.text;
    return "";
}
// id генерации: в дельтах лежит плоско (response_id), в response.* — вложенно.
function rtResponseId(event) {
    const p = rtPayload(event);
    return p?.response_id ?? p?.response?.id ?? "";
}
// usage приходит в response.done.
function rtUsage(event) {
    const p = rtPayload(event);
    return p?.response?.usage ?? p?.usage ?? null;
}

const TELEPHONY_STYLE_RULES = `

Правила голосового ответа (телефония):
- Отвечай коротко, обычно 1-2 предложения, без списков, markdown и эмодзи.
- Числа, даты и время произноси словами.
- Если реплика оборвана или неясна — вежливо переспроси одним вопросом.`;

function asrProfileForLang(lang) {
    const map = {
        ru: ASRProfileList.Yandex.ru_RU,
        en: ASRProfileList.Yandex.en_US,
        de: ASRProfileList.Yandex.de_DE,
        es: ASRProfileList.Yandex.es_ES,
        fr: ASRProfileList.Yandex.fr_FR,
    };
    return map[(lang || "ru").toLowerCase()] || ASRProfileList.Yandex.ru_RU;
}
function asrModelForLang() {
    return ASRModelList.Yandex.general;
}
function voxttsVoice(voiceId) {
    return VoxTTS.VoiceList[voiceId] || VoxTTS.VoiceList.Anna;
}
// Модель иногда пишет ответ в несколько строк — "  \n" ломает потоковый синтез.
function cleanForTTS(text) {
    return (text || "").replace(/\s*\n+\s*/g, " ");
}

async function fetchConfig(phone, caller) {
    const url =
        `${BACKEND_URL}/api/telephony/config` +
        `?phone=${encodeURIComponent(phone)}` +
        (caller ? `&caller=${encodeURIComponent(caller)}` : "");
    const response = await Net.httpRequestAsync(url, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
    });
    if (response.code !== 200 || !response.text) {
        Logger.write(`[CASCADE] Config HTTP error: ${response.code}`);
        return null;
    }
    try {
        const config = JSON.parse(response.text);
        return config && config.success ? config : null;
    } catch (e) {
        Logger.write(`[CASCADE] Config parse error: ${e}`);
        return null;
    }
}

// --- Session ID для расчёта полной стоимости на сервере (GetCallHistory) ---
let call_session_history_id = null;
VoxEngine.addEventListener(AppEvents.Started, (e) => {
    call_session_history_id = e.sessionId;
    Logger.write(`[CASCADE] Session History ID: ${call_session_history_id}`);
});

VoxEngine.addEventListener(AppEvents.CallAlerting, async ({ call }) => {
    let stt;
    let realtime; // OpenAI.RealtimeAPIClient — текстовый мозг каскада
    let ttsPlayer;
    let turnTaking;

    // Локальная копия диалога. В запросы НЕ уходит (контекст живёт на стороне
    // OpenAI), нужна только для восстановления после обрыва WS.
    const messages = []; // [0]=system, далее user/assistant

    // Состояние текущей генерации. Одна активная за раз.
    const main = { active: false, responseId: null, text: "", version: -1 };

    // Функции ассистента (нативный tool-calling Realtime).
    let realtimeTools = [];
    const functionNameToIdMap = {};

    // Замер TTFB голоса.
    let firstDeltaLoggedForTurn = false;
    let turnStartedAt = 0;

    // Звучит ли агент прямо сейчас. Нужно VoxTurnTaking, чтобы отличать
    // настоящее перебивание от любого сегмента VAD: без этого флага рантайм
    // считает перебиванием каждый шорох (в проде было 9 ложных на 5 настоящих).
    //
    // Считаем по «озвученному запасу»: каждый отправленный в VoxTTS кусок
    // продлевает ожидаемый конец речи. Так флаг не зависит от имён событий
    // плеера и не залипает, если событие не придёт.
    let agentSpeakingUntil = 0;
    const noteAgentAudio = (text) => {
        const ms = Math.max(300, (text || "").length * MS_PER_CHAR);
        agentSpeakingUntil = Math.max(agentSpeakingUntil, Date.now()) + ms;
    };
    const stopAgentAudio = () => { agentSpeakingUntil = 0; };
    const isAgentSpeaking = () => Date.now() < agentSpeakingUntil;

    // Разовый лог формы события.
    const loggedShapes = new Set();

    let systemPrompt;
    let assistantId;
    let callId;
    let apiKey;
    let reconnectAttempts = 0;
    let silenceMs = SILENCE_MS_DEFAULT;

    // --- Данные для /log (запись, стоимость, диалог) ---
    const chat_id = "vox_" + Math.random().toString(36).substring(2, 15);
    const caller_number = call.callerid() || "unknown";
    const called_number = call.number() || "unknown";
    let record_url = null;
    // Стоимость по позициям, которые вообще отдают cost в события сценария.
    // Это НЕ полный счёт: TTS, WebSocket-потоки (VAD + turn-detector) и детекция
    // конца фразы тарифицируются, но cost в сценарий не присылают — их видно
    // только в GetCallHistory (other_resource_usage).
    let telephony_cost = 0; // Call.Disconnected / Call.Failed
    let asr_cost = 0;       // ASR.Stopped
    let record_cost = 0;    // Call.RecordStopped
    let call_duration = 0;
    const knownCost = () =>
        Math.round((telephony_cost + asr_cost + record_cost) * 1e6) / 1e6;
    // Учёт токенов LLM для списания кредитов каскада. Cached считаем отдельно:
    // ставка за них на порядок ниже, и именно в них уходит история диалога.
    let totalPromptTokens = 0;
    let totalCachedPromptTokens = 0;
    let totalCompletionTokens = 0;
    let usageEventsSeen = 0;
    const dialogLog = []; // [{ role, text, ts }]
    let userMessageBuffer = "";
    let assistantMessageBuffer = "";
    let terminating = false;

    const logDialog = (role, text) => {
        if (!text) return;
        dialogLog.push({ role, text, ts: Date.now() });
        if (role === "user") {
            userMessageBuffer += (userMessageBuffer ? "\n" : "") + text;
        } else {
            assistantMessageBuffer += (assistantMessageBuffer ? "\n" : "") + text;
        }
    };

    const sendConversationLog = async () => {
        if (!assistantId) return;
        if (!userMessageBuffer && !assistantMessageBuffer && dialogLog.length === 0) return;
        const payload = {
            assistant_id: assistantId,
            chat_id: chat_id,
            call_id: callId,
            caller_number: "INBOUND:" + caller_number,
            type: "conversation",
            // Fallback-стоимость: только то, что видно сценарию.
            // Полный счёт бэкенд берёт из GetCallHistory.
            call_cost: knownCost(),
            call_cost_parts: {
                telephony: telephony_cost,
                asr: asr_cost,
                record: record_cost,
            },
            call_duration: call_duration,
            cascade_usage: {
                prompt_tokens: totalPromptTokens,
                cached_prompt_tokens: totalCachedPromptTokens,
                completion_tokens: totalCompletionTokens,
                model: LLM_MODEL,
            },
            data: {
                user_message: userMessageBuffer,
                assistant_message: assistantMessageBuffer,
                dialog: dialogLog,
            },
        };
        if (record_url) payload.record_url = record_url;
        if (call_session_history_id) payload.call_session_history_id = String(call_session_history_id);
        try {
            const resp = await Net.httpRequestAsync(LOG_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                postData: JSON.stringify(payload),
            });
            Logger.write(`[CASCADE] /log HTTP ${resp.code} (turns=${dialogLog.length})`);
        } catch (e) {
            Logger.write(`[CASCADE] /log error: ${e}`);
        }
    };

    // Завершение звонка: финализация -> пауза (запись/событие оседают) ->
    // POST /log -> hangup -> VoxEngine.terminate. Guard от двойного вызова.
    let terminatePromise = null;
    const terminateCall = () => {
        if (terminatePromise) return terminatePromise;
        terminating = true;
        terminatePromise = (async () => {
            try { stt?.stop(); } catch (e) { /* noop */ }
            try { turnTaking?.close(); } catch (e) { /* noop */ }
            try { realtime?.close(); } catch (e) { /* noop */ }

            // Дописать незавершённый ответ ассистента (звонок мог оборваться
            // посреди генерации).
            if (main.active && main.text) {
                logDialog("assistant", main.text);
            }

            // Дать записи/финальным событиям осесть.
            await new Promise((r) => setTimeout(r, 400));

            Logger.write(
                `[CASCADE] ===BILLING=== known_cost=${knownCost()} ` +
                `(telephony=${telephony_cost} asr=${asr_cost} record=${record_cost}) ` +
                `dur=${call_duration}s ` +
                `turns=${dialogLog.length} rec=${record_url ? "yes" : "no"} ` +
                `session=${call_session_history_id || "none"} ` +
                `tokens(in=${totalPromptTokens} cached=${totalCachedPromptTokens} out=${totalCompletionTokens} events=${usageEventsSeen})`
            );
            Logger.write(
                `[CASCADE] ===BILLING_NOTE=== known_cost не включает TTS/WebSocket/turn-detection; ` +
                `итог смотреть в GetCallHistory(other_resource_usage)`
            );
            // Эффект настроек turn-taking должен быть измерим на проде, а не «на слух».
            try {
                if (turnTaking && turnTaking.statsSummary) {
                    Logger.write(`[CASCADE] ===TURN_STATS=== silence=${silenceMs}ms | ${turnTaking.statsSummary()}`);
                }
            } catch (e) { /* noop */ }
            await sendConversationLog();

            try { call.hangup(); } catch (e) { /* noop */ }
            setTimeout(() => VoxEngine.terminate(), 400);
        })();
        return terminatePromise;
    };

    // --- События записи и жизненного цикла звонка ---
    call.addEventListener(CallEvents.RecordStarted, (event) => {
        record_url = event.url;
        Logger.write(`[CASCADE] Recording started: ${event.url}`);
    });
    call.addEventListener(CallEvents.RecordStopped, (event) => {
        if (event && event.url) record_url = event.url;
        if (event && event.cost !== undefined) record_cost = Number(event.cost) || 0;
    });
    call.addEventListener(CallEvents.Connected, () => {
        try {
            call.record({ stereo: false, lossless: false, hd_audio: true });
        } catch (e) {
            Logger.write(`[CASCADE] record() error: ${e}`);
        }
    });
    call.addEventListener(CallEvents.Disconnected, (event) => {
        if (event && event.cost !== undefined) telephony_cost = Number(event.cost) || 0;
        if (event && event.duration !== undefined) call_duration = event.duration;
        Logger.write(`[CASCADE] ===DISCONNECTED=== telephony=${telephony_cost} dur=${call_duration}s`);
        terminateCall();
    });
    call.addEventListener(CallEvents.Failed, (event) => {
        if (event && event.cost !== undefined) telephony_cost = Number(event.cost) || 0;
        if (event && event.duration !== undefined) call_duration = event.duration;
        Logger.write(`[CASCADE] ===CALL_FAILED=== code=${event?.code} ${event?.reason || ""}`);
        terminateCall();
    });

    const logShapeOnce = (tag, event) => {
        if (loggedShapes.has(tag)) return;
        loggedShapes.add(tag);
        try {
            Logger.write(`[CASCADE] ===SHAPE ${tag}=== ${JSON.stringify(event?.data)}`);
        } catch (e) {
            Logger.write(`[CASCADE] SHAPE ${tag} stringify error`);
        }
    };

    const logFirstDelta = (tag) => {
        if (firstDeltaLoggedForTurn) return;
        firstDeltaLoggedForTurn = true;
        const dt = turnStartedAt ? Date.now() - turnStartedAt : -1;
        Logger.write(`[CASCADE] ===FIRST_DELTA=== +${dt}ms${tag ? " " + tag : ""}`);
    };

    // turnTaking создаётся ПОСЛЕ навешивания обработчиков Realtime, поэтому
    // проверка через guard — иначе гонка на первом же ответе.
    const canPlay = () => !!turnTaking && turnTaking.canPlayAgentAudio();
    const ttsSendText = (text) => {
        if (!text) return;
        noteAgentAudio(text);
        ttsPlayer.send({ send_text: { text: cleanForTTS(text) } });
    };
    const ttsFlush = () => {
        ttsPlayer.send({ send_text: { text: " ", flush_context: {} } });
    };

    // --- Учёт токенов из response.done ---
    const accountUsage = (usage) => {
        usageEventsSeen++;
        const input = Number(usage.input_tokens) || 0;
        const output = Number(usage.output_tokens) || 0;
        const cachedRaw = Number(usage?.input_token_details?.cached_tokens) || 0;
        const cached = Math.min(Math.max(0, cachedRaw), input);
        totalCachedPromptTokens += cached;
        totalPromptTokens += input - cached;
        totalCompletionTokens += output;
    };

    // --- Гейтинг: событие относится к текущей активной генерации? ---
    const isCurrent = (event) => {
        if (!main.active) return false;
        const id = rtResponseId(event);
        if (!id) return true;
        if (!main.responseId) {
            main.responseId = id;
            return true;
        }
        return id === main.responseId;
    };

    // --- Отдать реплику абонента модели и запросить ответ ---
    const sendUserText = (text) => {
        realtime.conversationItemCreate({
            item: {
                type: "message",
                role: "user",
                content: [{ type: "input_text", text: text }],
            },
        });
    };
    const requestResponse = (version) => {
        main.active = true;
        main.responseId = null;
        main.text = "";
        main.version = version;
        realtime.responseCreate({});
    };

    const finalizeTurn = (text) => {
        const clean = (text || "").trim();
        if (clean) {
            messages.push({ role: "assistant", content: clean });
            Logger.write(`[CASCADE] ===AGENT=== ${clean}`);
            logDialog("assistant", clean);
        }
        if (canPlay()) ttsFlush();
        main.active = false;
        main.responseId = null;
        main.text = "";
    };

    // --- Tool-calling: выполнение функций ассистента через бекенд ---
    const executeFunction = async (name, args) => {
        const function_id = args.function_id || functionNameToIdMap[name];
        const clean = Object.assign({}, args);
        delete clean.function_id;
        if (!function_id) return `Error: function_id not found for ${name}`;
        try {
            const resp = await Net.httpRequestAsync(FUNCTIONS_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                postData: JSON.stringify({
                    function_id: function_id,
                    arguments: Object.assign({}, clean, { assistant_id: assistantId }),
                    call_data: {
                        call_id: callId || "unknown",
                        chat_id: chat_id,
                        assistant_id: assistantId,
                        caller_number: caller_number,
                        called_number: called_number,
                    },
                }),
            });
            if (resp.code === 200) return resp.text || "{}";
            return `Error: HTTP ${resp.code}`;
        } catch (e) {
            return `Error: ${e}`;
        }
    };

    // Вызов функции пришёл целиком в ResponseOutputItemDone (в отличие от
    // Chat Completions, где tool_calls собирались по дельтам).
    const handleFunctionCall = async (item) => {
        const name = item.name;
        const callIdRef = item.call_id;
        let args = {};
        try { args = JSON.parse(item.arguments || "{}"); } catch (e) { /* noop */ }
        Logger.write(`[CASCADE] ===FUNCTION=== ${name} ${item.arguments || ""}`);

        // Текущая генерация закончилась вызовом функции — снимаем гейт, чтобы
        // хвостовые события старого response не влияли на следующий.
        const pendingText = main.text;
        main.active = false;
        main.responseId = null;
        main.text = "";
        if (pendingText.trim()) {
            messages.push({ role: "assistant", content: pendingText.trim() });
            logDialog("assistant", pendingText.trim());
        }

        // hangup_call — ассистент сам завершает звонок.
        if (name === "hangup_call") {
            const farewell = args.farewell_message || "";
            Logger.write(`[CASCADE] ===HANGUP=== ${farewell}`);
            if (farewell) {
                logDialog("assistant", farewell);
                if (canPlay()) { ttsSendText(farewell); ttsFlush(); }
            }
            setTimeout(() => terminateCall(), farewell ? 3500 : 300);
            return;
        }

        const result = await executeFunction(name, args);
        if (terminating) return;

        realtime.conversationItemCreate({
            item: {
                type: "function_call_output",
                call_id: callIdRef,
                output: String(result),
            },
        });
        // Модель озвучит ответ по результату функции.
        requestResponse(main.version);
    };

    // --- Конфигурация Realtime-сессии ---
    // withHistory=true только при реконнекте: контекст на стороне OpenAI умер
    // вместе с сессией, отдаём его стенограммой прямо в instructions. Это
    // надёжнее, чем пересоздавать items (формат assistant-item зависит от версии API).
    const buildSessionConfig = (withHistory) => {
        let instructions = systemPrompt;
        if (withHistory && messages.length > 1) {
            const transcript = messages
                .filter((m) => m.role !== "system" && m.content)
                .map((m) => (m.role === "user" ? "Собеседник: " : "Ты: ") + m.content)
                .join("\n");
            if (transcript) {
                instructions +=
                    `\n\nСтенограмма уже состоявшейся части разговора ` +
                    `(соединение обрывалось, продолжай с этого места):\n${transcript}`;
            }
        }
        return {
            type: "realtime",
            output_modalities: ["text"], // голос синтезирует VoxTTS, не модель
            instructions: instructions,
            reasoning: { effort: LLM_REASONING_EFFORT },
            tools: realtimeTools,
            tool_choice: realtimeTools.length > 0 ? "auto" : "none",
            // Ход определяем МЫ (VoxTurnTaking), серверный VAD не нужен.
            // Аудио в клиент не заводится вовсе — это вторая линия защиты.
            audio: { input: { turn_detection: null } },
        };
    };

    const attachRealtimeHandlers = (client) => {
        client.addEventListener(RT.ResponseCreated, (event) => {
            if (terminating) return;
            if (main.active && !main.responseId) {
                main.responseId = rtResponseId(event) || "";
            }
        });

        client.addEventListener(RT.ResponseOutputTextDelta, (event) => {
            if (terminating) return;
            logShapeOnce("ResponseOutputTextDelta", event);
            if (!isCurrent(event)) return;
            const delta = rtDelta(event);
            if (!delta) return;
            main.text += delta;
            if (canPlay()) {
                logFirstDelta("");
                ttsSendText(delta);
            }
        });

        client.addEventListener(RT.ResponseOutputTextDone, (event) => {
            if (terminating) return;
            if (!isCurrent(event)) return;
            finalizeTurn(rtDoneText(event) || main.text);
        });

        client.addEventListener(RT.ResponseOutputItemDone, async (event) => {
            if (terminating) return;
            try {
                const payload = rtPayload(event);
                const item = payload && payload.item;
                if (!item || item.type !== "function_call") return;
                logShapeOnce("ResponseOutputItemDone.function_call", event);
                await handleFunctionCall(item);
            } catch (e) {
                Logger.write(`[CASCADE] function handler error: ${e}`);
            }
        });

        // Финал генерации: токены для биллинга + страховка на случай, если
        // ResponseOutputTextDone не пришёл, а текст накоплен.
        client.addEventListener(RT.ResponseDone, (event) => {
            logShapeOnce("ResponseDone", event);
            const usage = rtUsage(event);
            if (usage) {
                accountUsage(usage);
            } else {
                Logger.write("[CASCADE] ⚠️ ResponseDone без usage — токены этого хода не учтены");
            }
            if (terminating) return;
            if (main.active && isCurrent(event) && main.text) {
                finalizeTurn(main.text);
            }
        });

        client.addEventListener(RT.Error, (event) => {
            Logger.write("[CASCADE] ===LLM_ERROR===");
            try { Logger.write(JSON.stringify(event?.data)); } catch (e) { /* noop */ }
        });
    };

    // Поднять WS и дождаться готовности сессии (SessionCreated -> sessionUpdate).
    const connectRealtime = async (withHistory) => {
        const client = await OpenAI.createRealtimeAPIClient({
            apiKey: apiKey,
            model: LLM_MODEL,
            type: OpenAI.RealtimeAPIClientType.REALTIME,
            onWebSocketClose: () => {
                Logger.write("[CASCADE] [OpenAI] WS closed");
                handleRealtimeClose();
            },
            onWebSocketError: (err) => {
                Logger.write(`[CASCADE] [OpenAI] WS error: ${JSON.stringify(err)}`);
            },
        });

        await new Promise((resolve, reject) => {
            let settled = false;
            const timer = setTimeout(() => {
                if (settled) return;
                settled = true;
                reject(new Error("SessionCreated timeout"));
            }, SESSION_READY_TIMEOUT_MS);

            client.addEventListener(RT.SessionCreated, () => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                try {
                    client.sessionUpdate({ session: buildSessionConfig(withHistory) });
                    Logger.write("[CASCADE] [OpenAI] Session configured (text-only, turn_detection=null)");
                    resolve();
                } catch (e) {
                    reject(e);
                }
            });
        });

        attachRealtimeHandlers(client);
        return client;
    };

    // Обрыв WS посреди звонка: TTL сессии, сеть, рестарт на стороне OpenAI.
    // Поднимаем заново и отдаём историю стенограммой.
    const handleRealtimeClose = async () => {
        if (terminating) return;
        if (reconnectAttempts >= MAX_RECONNECTS) {
            Logger.write("[CASCADE] ===RT_RECONNECT_GIVEUP=== завершаем звонок");
            terminateCall();
            return;
        }
        reconnectAttempts++;
        Logger.write(`[CASCADE] ===RT_RECONNECT=== попытка ${reconnectAttempts}/${MAX_RECONNECTS}`);
        main.active = false;
        main.responseId = null;
        main.text = "";
        try {
            realtime = await connectRealtime(true);
            Logger.write("[CASCADE] ===RT_RECONNECT_OK===");
        } catch (e) {
            Logger.write(`[CASCADE] ===RT_RECONNECT_FAILED=== ${e}`);
            terminateCall();
        }
    };

    try {
        call.answer();

        const config = await fetchConfig(call.number(), call.callerid());
        if (!config) {
            Logger.write("[CASCADE] No config for this number, hanging up");
            call.hangup();
            return;
        }
        if (!config.api_key) {
            // Гейт по кредитам каскада: сервер не отдал ключ (баланс исчерпан).
            Logger.write("[CASCADE] No api_key (cascade credits depleted), hanging up");
            call.hangup();
            return;
        }

        apiKey = config.api_key;
        assistantId = config.assistant_id;
        callId = call.id();
        systemPrompt = (config.system_prompt || "Ты — голосовой ассистент.") + TELEPHONY_STYLE_RULES;
        // Анти-повтор приветствия. БЕЗ дословной фразы в инструкции — иначе модель
        // echo'ит её в каждом ответе (эффект «не думай о розовом слоне»).
        if (config.first_phrase) {
            systemPrompt +=
                `\n\nРазговор уже начат: приветствие собеседнику уже произнесено. ` +
                `Не здоровайся и не представляйся заново — сразу отвечай по сути вопроса.`;
        }
        // Информация о звонке в промпт: реальные номера и текущее время, чтобы
        // модель могла корректно вызывать функции (например, send_sms) и
        // ориентироваться во времени. caller_number — номер клиента (звонящего),
        // called_number — наш номер (на который позвонили). Время в МСК (UTC+3).
        const mskTime = new Date(Date.now() + 3 * 3600 * 1000).toISOString().replace("T", " ").slice(0, 16);
        systemPrompt +=
            `\n\nИнформация о звонке:\n` +
            `- Номер клиента (caller_number): ${caller_number}\n` +
            `- Наш номер (called_number): ${called_number}\n` +
            `- Текущее время: ${mskTime} (МСК)`;
        messages.push({ role: "system", content: systemPrompt });

        // Функции ассистента -> tools Realtime (ПЛОСКИЙ формат: name/description/
        // parameters на верхнем уровне, в отличие от вложенного у Chat Completions).
        if (config.functions && config.functions.length > 0) {
            const decls = [];
            for (let i = 0; i < config.functions.length; i++) {
                const t = config.functions[i];
                const fn = (t.type === "function" && t.function) ? t.function : (t.name ? t : null);
                if (!fn) continue;
                functionNameToIdMap[fn.name] = String(i + 1);
                let desc = fn.description;
                if (fn.name === "hangup_call") {
                    desc = "КРИТИЧЕСКИ ВАЖНО: вызови эту функцию НЕМЕДЛЕННО, когда задача звонка выполнена или собеседник хочет завершить разговор («пока», «до свидания», «всё, спасибо»). Не прощайся просто словами — вызови функцию.";
                }
                decls.push({
                    type: "function",
                    name: fn.name,
                    description: desc,
                    parameters: fn.parameters,
                });
            }
            if (decls.length > 0) {
                realtimeTools = decls;
                Logger.write(`[CASCADE] Functions: ${JSON.stringify(functionNameToIdMap)}`);
            }
        }

        // Пауза перед ответом: пресет из настроек агента (300/650/1000).
        silenceMs = resolveSilenceMs(config.silence_duration_ms);

        Logger.write(`[CASCADE] Assistant: ${config.assistant_name} (${assistantId})`);
        Logger.write(`[CASCADE] Call: ${caller_number} -> ${called_number} | id=${callId}`);
        Logger.write(`[CASCADE] LLM: ${LLM_MODEL} via Realtime WS (text-only, наш turn-taking)`);
        Logger.write(
            `[CASCADE] Silence preset: ${silenceMs}ms ` +
            `(fallback timeout ${silenceMs + SILENCE_TO_TIMEOUT_MS}ms)`
        );

        stt = VoxEngine.createASR({
            profile: asrProfileForLang(config.asr_lang),
            model: asrModelForLang(config.asr_lang),
            interimResults: true,
        });
        // ASR тарифицируется отдельной строкой и в cost звонка НЕ входит.
        stt.addEventListener(ASREvents.Stopped, (event) => {
            if (event && event.cost !== undefined) asr_cost = Number(event.cost) || 0;
        });

        realtime = await connectRealtime(false);

        if (config.tts_provider && config.tts_provider !== "voxtts") {
            Logger.write(`[CASCADE] ⚠️ tts_provider='${config.tts_provider}' не поддержан, fallback VoxTTS/Anna`);
        }
        ttsPlayer = VoxTTS.createRealtimeTTSPlayer({
            createContextParameters: {
                create: {
                    modelId: VoxTTS.ModelList.VoxTTS,
                    voiceId: voxttsVoice(config.tts_voice),
                },
            },
        });

        turnTaking = await VoxTurnTaking.create({
            call,
            stt,
            // ВАЖНО: НЕ переопределяем vadOptions целиком и не трогаем таймеры
            // закрытия хода — дефолты v2 (speechPadMs 200, transcriptSettleMs
            // 350, вето детектора, стабильность транскрипта) выверены на проде,
            // и любое их «улучшение» отсюда возвращает дробление фраз.
            // Единственное, чем управляет сценарий, — пауза перед ответом,
            // которую владелец агента выбирает пресетом на странице агента.
            vadOptions: { minSilenceDurationMs: silenceMs },
            turnDetectorOptions: { threshold: 0.7 },
            policy: {
                confidentEouProbability: 0.8,
                userSpeechTimeoutMs: silenceMs + SILENCE_TO_TIMEOUT_MS,
                // Языковые списки — то, что v2 намеренно оставляет пустыми и
                // ждёт от сценария.
                continuationTokens: [
                    "и", "а", "но", "ну", "вот", "так", "значит",
                    "короче", "эм", "ээ", "мм", "это",
                ],
                trailingContinuationTokens: [
                    "чтобы", "потому", "который", "которая", "которое", "которые",
                    "про", "либо", "если", "когда", "пока", "хотя",
                ],
                completeShortAnswers: [
                    "да", "нет", "ок", "окей", "хорошо", "ага", "угу", "не",
                    "стоп", "верно", "точно", "конечно", "давай", "давайте",
                    "спасибо", "понятно", "нет спасибо", "да давайте",
                ],
            },
            enableLogging: true,

            // Без этого коллбэка VoxTurnTaking считает перебиванием ЛЮБОЙ
            // сегмент VAD — эхо, кашель, шум линии.
            isAgentSpeaking: isAgentSpeaking,

            onUserTurn: (input, version) => {
                if (terminating) return;
                firstDeltaLoggedForTurn = false;
                turnStartedAt = Date.now();
                Logger.write(`[CASCADE] ===USER=== ${input}`);
                logDialog("user", input);
                messages.push({ role: "user", content: input });
                sendUserText(input);
                requestResponse(version);
            },

            // Yandex досылает полный текст реплики через 2-5с после того, как
            // ход уже ушёл в модель по interim. Отвечать второй раз нельзя —
            // собеседник задал один вопрос. Поэтому: правим историю и молча
            // отдаём модели уточнение, БЕЗ responseCreate.
            onTurnCorrection: (fullText, correctedTurnId, meta) => {
                if (terminating || !fullText) return;
                const sent = (meta && meta.sentText) || "";
                Logger.write(
                    `[CASCADE] ===USER_CORRECTED=== (+${meta && meta.lagMs}ms) ` +
                    `"${sent}" -> "${fullText}"`
                );

                // Локальная история и то, что уйдёт в CRM: заменяем последнюю
                // реплику собеседника на полную версию.
                for (let i = messages.length - 1; i >= 0; i--) {
                    if (messages[i].role === "user") {
                        messages[i].content = fullText;
                        break;
                    }
                }
                for (let i = dialogLog.length - 1; i >= 0; i--) {
                    if (dialogLog[i].role === "user") {
                        const old = dialogLog[i].text;
                        dialogLog[i].text = fullText;
                        if (userMessageBuffer.endsWith(old)) {
                            userMessageBuffer =
                                userMessageBuffer.slice(0, userMessageBuffer.length - old.length) + fullText;
                        }
                        break;
                    }
                }

                // Модели — служебной репликой, чтобы дальше в диалоге она
                // опиралась на верный текст, но вслух не отвечала повторно.
                try {
                    realtime.conversationItemCreate({
                        item: {
                            type: "message",
                            role: "user",
                            content: [{
                                type: "input_text",
                                text:
                                    "[Уточнение распознавания: предыдущая реплика собеседника " +
                                    `полностью звучала так: «${fullText}». Не отвечай на неё повторно, ` +
                                    "просто учитывай верный текст дальше.]",
                            }],
                        },
                    });
                } catch (e) {
                    Logger.write(`[CASCADE] correction item error: ${e}`);
                }
            },

            onInterrupt: () => {
                if (terminating) return;
                ttsPlayer?.clearBuffer();
                stopAgentAudio();
                if (main.active) {
                    // Останавливаем генерацию НА СЕРВЕРЕ — иначе модель
                    // досчитывает ответ, который никто не услышит, и мы за него платим.
                    try { realtime.responseCancel(); } catch (e) { /* noop */ }
                }
                main.active = false;
                main.responseId = null;
                main.text = "";
            },
        });

        call.sendMediaTo(stt);
        ttsPlayer.sendMediaTo(call);
        // ВНИМАНИЕ: call.sendMediaTo(realtime) НЕ вызываем — Realtime работает
        // как текстовый мозг, аудио он не слышит и turn detection не делает.

        // --- Приветствие ---
        if (config.first_phrase) {
            // Фиксированная фраза мимо LLM — мгновенно, без раунда к модели.
            messages.push({ role: "assistant", content: config.first_phrase });
            logDialog("assistant", config.first_phrase);
            noteAgentAudio(config.first_phrase);
            ttsPlayer.send({ send_text: { text: cleanForTTS(config.first_phrase), flush_context: {} } });
        } else {
            sendUserText("Поприветствуй звонящего одной короткой фразой и спроси, чем можешь помочь.");
            requestResponse(turnTaking.currentVersion());
        }
    } catch (error) {
        Logger.write("[CASCADE] ===UNHANDLED_ERROR===");
        Logger.write(String(error));
        terminateCall();
    }
});
