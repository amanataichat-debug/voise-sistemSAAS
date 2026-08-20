/*
 * Прогон inbound_fish.js на заглушках VoxEngine.
 * Проверяем реальный код сценария, а не его копию: подменяем платформенные
 * глобалы, прокручиваем звонок и смотрим, что ушло в сокет синтеза.
 */
const fs = require("fs");
const vm = require("vm");

const SCENARIO = require("path").join(__dirname, "inbound_fish.js");

const CONFIG = {
    success: true,
    assistant_type: "fish",
    assistant_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    assistant_name: "Тестовый Fish",
    api_key: "sk-test",
    model: "gpt-realtime-2.1-mini",
    system_prompt: "Ты ассистент.",
    first_phrase: "Здравствуйте! Чем помочь?",
    language: "ru",
    fish_voice_id: "voice123",
    fish_model: "s2.1-pro",
    fish_latency: "balanced",
    sample_rate: 8000,
    fish_tts_url: "wss://voicyfy.ru/ws/fish/tts/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    functions: null,
};

// ── заглушки платформы ──────────────────────────────────────────────────────
const appHandlers = {};
const logs = [];

function Emitter() {
    this._h = {};
    this.addEventListener = (ev, cb) => { (this._h[ev] = this._h[ev] || []).push(cb); };
    this.fire = (ev, payload) => (this._h[ev] || []).forEach((cb) => cb(payload));
}

class FakeSocket extends Emitter {
    constructor(url) {
        super();
        this.url = url;
        this.sent = [];
        this.mediaTo = null;
        this.cleared = 0;
        this.closed = false;
    }
    send(data) { this.sent.push(JSON.parse(data)); }
    sendMediaTo(unit) { this.mediaTo = unit; }
    clearMediaBuffer() { this.cleared++; }
    close() { this.closed = true; }
}

class FakeCall extends Emitter {
    constructor() {
        super();
        this.answered = false;
        this.recording = false;
        this.hungup = false;
        this.mediaTo = null;
    }
    id() { return "call-1"; }
    callerid() { return "+70000000000"; }
    answer() { this.answered = true; }
    record() { this.recording = true; }
    sendMediaTo(u) { this.mediaTo = u; }
    hangup() { this.hungup = true; }
}

let createdSocket = null;
let realtimeClient = null;

const sandbox = {
    console,
    setTimeout, clearTimeout, Promise, JSON, Math, Date, String, Array, Object, RegExp, Error,
    require: () => {},
    Modules: { OpenAI: "OpenAI" },
    Logger: { write: (m) => logs.push(String(m)) },
    AppEvents: { Started: "Started", CallAlerting: "CallAlerting" },
    CallEvents: {
        Connected: "Connected", Disconnected: "Disconnected", Failed: "Failed",
        RecordStarted: "RecordStarted", RecordStopped: "RecordStopped",
    },
    WebSocketEvents: {
        OPEN: "WebSocket.Open", MESSAGE: "WebSocket.Message",
        CLOSE: "WebSocket.Close", ERROR: "WebSocket.Error",
    },
    VoxEngine: {
        addEventListener: (ev, cb) => { appHandlers[ev] = cb; },
        createWebSocket: (url) => { createdSocket = new FakeSocket(url); return createdSocket; },
        terminate: () => { sandbox.__terminated = true; },
    },
    Net: {
        httpRequestAsync: async (url) => {
            if (url.indexOf("/telephony/config") !== -1) {
                return { code: 200, text: JSON.stringify(CONFIG) };
            }
            return { code: 200, text: "{}" };
        },
    },
    OpenAI: {
        RealtimeAPIClientType: { REALTIME: "realtime" },
        RealtimeAPIEvents: {
            SessionCreated: "SessionCreated", SessionUpdated: "SessionUpdated",
            ResponseCreated: "ResponseCreated",
            ResponseOutputTextDelta: "TextDelta", ResponseOutputTextDone: "TextDone",
            InputAudioBufferSpeechStarted: "SpeechStarted",
            InputAudioBufferSpeechStopped: "SpeechStopped",
            ConversationItemInputAudioTranscriptionCompleted: "Transcript",
            ResponseOutputItemDone: "OutputItemDone",
            Error: "Error",
        },
        createRealtimeAPIClient: async () => {
            realtimeClient = new Emitter();
            realtimeClient.sessionUpdate = () => {};
            realtimeClient.responseCreate = () => {};
            realtimeClient.conversationItemCreate = () => {};
            realtimeClient.clearMediaBuffer = () => {};
            realtimeClient.close = () => {};
            return realtimeClient;
        },
    },
};
sandbox.global = sandbox;

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCENARIO, "utf8"), sandbox, { filename: "inbound_fish.js" });

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

function assert(cond, msg) {
    if (!cond) {
        console.error("❌ " + msg);
        console.error("--- лог сценария ---");
        logs.forEach((l) => console.error("   " + l));
        process.exit(1);
    }
}

(async () => {
    appHandlers.Started({ sessionId: "sess-1" });

    const call = new FakeCall();
    const done = appHandlers.CallAlerting({ call, destination: "74951234567" });

    await tick(10);   // конфиг + подключение OpenAI

    // ── сокет к прокси поднят до ответа на звонок ───────────────────────────
    assert(createdSocket, "сокет к прокси не создан");
    assert(createdSocket.url === CONFIG.fish_tts_url, "неверный URL прокси: " + createdSocket.url);
    assert(createdSocket.mediaTo === null, "sendMediaTo вызван до ответа на звонок");

    await done;
    assert(call.answered, "звонок не отвечен");
    assert(call.mediaTo === realtimeClient, "аудио звонящего не подключено к OpenAI");

    // ── приветствие ушло в очередь, пока сокет закрыт ───────────────────────
    assert(createdSocket.sent.length === 0, "текст ушёл в неоткрытый сокет");

    createdSocket.fire("WebSocket.Open");
    await tick();

    assert(createdSocket.mediaTo === call, "аудио прокси не направлено в звонок после OPEN");
    let msgs = createdSocket.sent;
    assert(msgs[0] && msgs[0].event === "text", "первым не ушёл текст приветствия: " + JSON.stringify(msgs[0]));
    assert(msgs[0].text === CONFIG.first_phrase, "приветствие искажено: " + msgs[0].text);
    assert(msgs[1] && msgs[1].event === "flush", "приветствие не закрыто flush");
    console.log("✅ приветствие: накопилось до OPEN, ушло текстом + flush");

    // ── реплика модели дельтами ────────────────────────────────────────────
    createdSocket.sent = [];
    realtimeClient.fire("SpeechStopped");
    realtimeClient.fire("ResponseCreated");

    const reply = "Конечно, помогу вам с этим. ";
    for (const ch of reply.match(/.{1,5}/g)) {
        realtimeClient.fire("TextDelta", { data: { delta: ch } });
    }
    realtimeClient.fire("TextDone", { data: { text: reply } });
    await tick();

    msgs = createdSocket.sent;
    const texts = msgs.filter((m) => m.event === "text");
    const flushes = msgs.filter((m) => m.event === "flush");

    assert(texts.length > 0, "реплика не ушла в синтез");
    // батчинг: кадров заметно меньше, чем дельт
    assert(texts.length < 5, "батчинг не работает: " + texts.length + " кадров на 6 дельт");
    assert(flushes.length >= 1, "реплика не закрыта flush");

    const joined = texts.map((m) => m.text).join("");
    assert(joined === reply, "текст склеился неверно:\n  ожидали: " + JSON.stringify(reply) +
                             "\n  получили: " + JSON.stringify(joined));
    console.log("✅ реплика: " + texts.length + " кадров на 6 дельт, текст склеивается побайтово");

    // ── перебивание ────────────────────────────────────────────────────────
    createdSocket.sent = [];
    const clearedBefore = createdSocket.cleared;
    realtimeClient.fire("SpeechStarted");
    await tick();

    assert(createdSocket.cleared === clearedBefore + 1, "буфер Voximplant не сброшен при перебивании");
    const clears = createdSocket.sent.filter((m) => m.event === "clear");
    assert(clears.length === 1, "прокси не получил clear при перебивании");
    console.log("✅ перебивание: clearMediaBuffer + clear в прокси");

    // ── прощание и hangup по speech_done ───────────────────────────────────
    createdSocket.sent = [];
    realtimeClient.fire("OutputItemDone", {
        data: { payload: { item: {
            type: "function_call", name: "hangup_call", call_id: "fc-1",
            arguments: JSON.stringify({ farewell_message: "Всего доброго!", reason: "done" }),
        } } },
    });
    await tick();

    const farewell = createdSocket.sent.filter((m) => m.event === "text");
    assert(farewell.length === 1 && farewell[0].text === "Всего доброго!",
           "прощание не ушло в синтез: " + JSON.stringify(createdSocket.sent));

    assert(!call.hungup, "трубка положена до окончания прощания");
    createdSocket.fire("WebSocket.Message", {
        text: JSON.stringify({ event: "speech_done", remaining_ms: 30 }),
    });
    await tick(400);
    assert(call.hungup, "трубка не положена после speech_done");
    console.log("✅ прощание: озвучено, трубка положена по speech_done");

    console.log("\nвсе проверки inbound_fish пройдены");
})();
