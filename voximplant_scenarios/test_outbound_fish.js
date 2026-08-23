/*
 * Прогон outbound_fish.js на заглушках VoxEngine.
 * Отдельно проверяем то, чего нет во входящем: прогрев сокета до набора
 * номера, мьют клиентского аудио и шлюз приветствия.
 */
const fs = require("fs");
const vm = require("vm");

const SCENARIO = require("path").join(__dirname, "outbound_fish.js");

const CONFIG = {
    success: true,
    assistant_type: "fish",
    assistant_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    assistant_name: "Тестовый Fish",
    api_key: "sk-test",
    model: "gpt-realtime-2.1-mini",
    system_prompt: "Ты ассистент.",
    first_phrase: "Здравствуйте, это Войсифай.",
    language: "ru",
    fish_voice_id: "voice123",
    fish_model: "s2.1-pro",
    fish_latency: "balanced",
    sample_rate: 8000,
    fish_tts_url: "wss://voksyai.online/ws/fish/tts/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    functions: null,
};

const CUSTOM_DATA = JSON.stringify({
    phone_number: "+79990000000",
    assistant_id: CONFIG.assistant_id,
    caller_id: "+74951234567",
    mute_duration_ms: 100,          // укорочено, чтобы тест не ждал
});

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
        this.url = url; this.sent = []; this.mediaTo = null;
        this.cleared = 0; this.closed = false;
    }
    send(d) { this.sent.push(JSON.parse(d)); }
    sendMediaTo(u) { this.mediaTo = u; }
    clearMediaBuffer() { this.cleared++; }
    close() { this.closed = true; }
}

class FakeCall extends Emitter {
    constructor() { super(); this.recording = false; this.hungup = false; this.mediaTo = null; }
    id() { return "call-out-1"; }
    record() { this.recording = true; }
    sendMediaTo(u) { this.mediaTo = u; }
    hangup() { this.hungup = true; }
}

let createdSocket = null;
let realtimeClient = null;
let outCall = null;
let dialed = null;

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
        customData: () => CUSTOM_DATA,
        callPSTN: (num, cid) => { dialed = { num, cid }; outCall = new FakeCall(); return outCall; },
        terminate: () => { sandbox.__terminated = true; },
    },
    Net: {
        httpRequestAsync: async (url) => {
            if (url.indexOf("/telephony/outbound-config") !== -1) {
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
vm.runInContext(fs.readFileSync(SCENARIO, "utf8"), sandbox, { filename: "outbound_fish.js" });

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
    const done = appHandlers.Started({ sessionId: "sess-out" });
    await tick(10);

    // ── сокет прогрет ДО набора номера ─────────────────────────────────────
    assert(createdSocket, "сокет к прокси не создан");
    assert(dialed, "номер не набран");
    const socketIdx = logs.findIndex((l) => l.indexOf("Opening TTS socket") !== -1);
    const dialIdx   = logs.findIndex((l) => l.indexOf("Calling:") !== -1);
    assert(socketIdx !== -1 && dialIdx !== -1 && socketIdx < dialIdx,
           "сокет поднимается не раньше набора номера");
    assert(dialed.num === "+79990000000" && dialed.cid === "+74951234567",
           "неверные параметры набора: " + JSON.stringify(dialed));
    console.log("✅ прогрев: сокет открыт до callPSTN");

    createdSocket.fire("WebSocket.Open");
    await tick();

    // до Connected медиа привязывать некуда
    assert(createdSocket.mediaTo === null, "sendMediaTo вызван до ответа абонента");

    // ── абонент снял трубку ────────────────────────────────────────────────
    outCall.fire("Connected");
    await tick();

    assert(createdSocket.mediaTo === outCall, "аудио прокси не направлено в звонок");
    assert(outCall.recording, "запись не запущена");
    assert(outCall.mediaTo === null, "аудио абонента подключено к модели без мьюта");

    let msgs = createdSocket.sent;
    assert(msgs[0] && msgs[0].event === "text" && msgs[0].text === CONFIG.first_phrase,
           "приветствие не ушло в синтез: " + JSON.stringify(msgs[0]));
    assert(msgs[1] && msgs[1].event === "flush", "приветствие не закрыто flush");
    console.log("✅ Connected: приветствие в синтез, аудио абонента ещё замьючено");

    await tick(160);   // ждём окончания окна мьюта (100 мс)
    assert(outCall.mediaTo === realtimeClient, "аудио абонента не подключилось после мьюта");
    console.log("✅ мьют: аудио абонента подключено по истечении окна");

    // ── шлюз: реплика модели придерживается, пока звучит приветствие ───────
    createdSocket.sent = [];
    realtimeClient.fire("ResponseCreated");
    const reply = "Я звоню по поводу вашей заявки.";
    for (const ch of reply.match(/.{1,6}/g)) {
        realtimeClient.fire("TextDelta", { data: { delta: ch } });
    }
    realtimeClient.fire("TextDone", { data: { text: reply } });
    await tick();

    assert(createdSocket.sent.length === 0,
           "реплика прорвалась сквозь шлюз во время приветствия: " + JSON.stringify(createdSocket.sent));
    console.log("✅ шлюз: реплика придержана, пока звучит приветствие");

    // ── приветствие договорено → шлюз открывается ─────────────────────────
    createdSocket.fire("WebSocket.Message", {
        text: JSON.stringify({ event: "speech_done", remaining_ms: 20 }),
    });
    await tick(350);

    msgs = createdSocket.sent;
    const texts = msgs.filter((m) => m.event === "text");
    assert(texts.length === 1, "придержанная реплика ушла не одним куском: " + JSON.stringify(msgs));
    assert(texts[0].text === reply, "придержанная реплика искажена: " + texts[0].text);
    assert(msgs.some((m) => m.event === "flush"), "придержанная реплика не закрыта flush");
    console.log("✅ шлюз открылся по speech_done, реплика озвучена целиком");

    // ── перебивание во время приветствия игнорируется ─────────────────────
    // (шлюз уже открыт, поэтому проверяем обычное перебивание)
    createdSocket.sent = [];
    const before = createdSocket.cleared;
    realtimeClient.fire("SpeechStarted");
    await tick();
    assert(createdSocket.cleared === before + 1, "буфер не сброшен при перебивании");
    assert(createdSocket.sent.some((m) => m.event === "clear"), "прокси не получил clear");
    console.log("✅ перебивание после приветствия обрывает озвучку");

    console.log("\nвсе проверки outbound_fish пройдены");
})();
