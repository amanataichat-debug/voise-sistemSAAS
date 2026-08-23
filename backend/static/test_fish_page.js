/*
 * Прогон страницы fish-agents.html в настоящем браузере с подставным API.
 * Ловит ровно тот класс ошибок, что вылез в проде: обращения к элементам и
 * полям, которых не осталось после переноса страницы с Yandex.
 */
const path = require("path");
// playwright ставится отдельно (npm i playwright) — ищем его и рядом со
// скриптом, и в корне репозитория, и глобально.
function loadPlaywright() {
    const candidates = [
        "playwright",
        path.join(__dirname, "node_modules", "playwright"),
        path.join(__dirname, "..", "..", "node_modules", "playwright"),
    ];
    for (const c of candidates) {
        try { return require(c); } catch (e) { /* пробуем следующий */ }
    }
    console.error("playwright не найден. Установите: npm i playwright");
    process.exit(1);
}
const { chromium } = loadPlaywright();

const STATIC = "/home/user/new_struktura_VoiceAI/backend/static";

const AGENT = {
    id: "11111111-2222-3333-4444-555555555555",
    name: "Тестовый Fish",
    description: "описание",
    system_prompt: "Ты ассистент.",
    fish_voice_id: "voice123",
    fish_model: "s2.1-pro",
    fish_latency: "balanced",
    sample_rate: 8000,
    voice_speed: 1.0,
    llm_model: "gpt-realtime-2.1-mini",
    language: "ru",
    temperature: 0.7,
    greeting_message: "Здравствуйте!",
    google_sheet_id: null,
    functions: null,
    is_active: true,
    created_at: new Date().toISOString(),
};

const API = {
    "/api/users/me": {
        id: "u1", email: "test@test.ru", first_name: "Тест",
        subscription_plan: "profi", is_admin: false, is_trial: false,
        subscription_active: true,
    },
    "/api/subscriptions/assistants-usage": {
        used: 1, max: 10, can_create: true, unlimited: false,
        subscription_active: true, plan_code: "profi", breakdown: { fish: 1 },
    },
    "/api/fish-assistants/options": {
        models: ["s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"],
        default_model: "s2.1-pro",
        latency_modes: ["low", "balanced", "normal"],
        default_latency: "balanced",
        llm_models: ["gpt-realtime-2.1-mini", "gpt-realtime-1.5"],
        default_llm_model: "gpt-realtime-2.1-mini",
        sample_rate: 8000,
        speed: { min: 0.5, max: 2.0, default: 1.0 },
        temperature: { min: 0.0, max: 1.0, default: 0.7 },
    },
    "/api/fish-assistants/api-keys": {
        has_openai_key: true, has_fish_key: true,
        openai_key_preview: "sk-...abc", fish_key_preview: "fis...xyz",
    },
    "/api/fish-assistants": { assistants: [AGENT], has_openai_key: true, has_fish_key: true },
    // /functions/ отдаёт массив, а не объект
    "/api/functions": [
        { name: "send_webhook", description: "Отправить вебхук", parameters: {} },
        { name: "hangup_call", description: "Завершить звонок", parameters: {} },
    ],
};

(async () => {
    const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
    const ctx = await browser.newContext();
    const page = await ctx.newPage();

    const errors = [];
    page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });

    // файлы страницы — с диска, всё остальное — заглушки
    await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        const p = url.pathname;

        if (p.startsWith("/api/")) {
            // /api/fish-assistants/<uuid> — один агент, а не список
            if (/^\/api\/fish-assistants\/[0-9a-f-]{36}$/.test(p)) {
                return route.fulfill({ status: 200, contentType: "application/json",
                                       body: JSON.stringify(AGENT) });
            }
            const key = Object.keys(API)
                .sort((a, b) => b.length - a.length)
                .find((k) => p === k || p.startsWith(k + "/") || p.startsWith(k + "?"));
            const body = key ? API[key] : {};
            return route.fulfill({ status: 200, contentType: "application/json",
                                   body: JSON.stringify(body) });
        }
        if (p.startsWith("/static/")) {
            const fp = path.join(STATIC, p.replace("/static/", ""));
            try { return await route.fulfill({ path: fp }); }
            catch { return route.fulfill({ status: 404, body: "" }); }
        }
        // внешние CDN (шрифты, иконки) — глушим
        return route.fulfill({ status: 200, contentType: "text/css", body: "" });
    });

    await page.addInitScript(() => {
        localStorage.setItem("auth_token", "fake-jwt");
    });

    function check(cond, msg) {
        if (!cond) {
            console.error("❌ " + msg);
            if (errors.length) { console.error("--- ошибки страницы ---"); errors.forEach((e) => console.error("   " + e)); }
            process.exit(1);
        }
    }

    // ── 1. Режим создания ──────────────────────────────────────────────────
    await page.goto("https://voksyai.online/static/fish-agents.html?mode=create",
                    { waitUntil: "networkidle" });
    await page.waitForTimeout(600);

    check(errors.length === 0, "ошибки JS при открытии режима создания:\n   " + errors.join("\n   "));
    check(page.url().indexOf("fish-agents") !== -1, "страница ушла на " + page.url());
    console.log("✅ режим создания открывается без ошибок JS");

    const title = await page.textContent("#edit-title");
    check(title && title.indexOf("Новый агент") !== -1, "заголовок не переключился: " + title);

    const model = await page.inputValue("#agent-model");
    const latency = await page.inputValue("#agent-latency");
    const llm = await page.inputValue("#agent-llm-model");
    check(model === "s2.1-pro", "модель синтеза не подставилась: " + model);
    check(latency === "balanced", "латентность не подставилась: " + latency);
    check(llm === "gpt-realtime-2.1-mini", "диалоговая модель не подставилась: " + llm);
    console.log("✅ селекты заполнены: " + model + " / " + latency + " / " + llm);

    const voice = await page.inputValue("#agent-voice");
    check(voice === "", "поле голоса не очищено: " + voice);

    const speed = await page.inputValue("#agent-speed");
    const temp = await page.inputValue("#agent-temperature");
    check(speed === "1", "скорость по умолчанию не 1: " + speed);
    check(temp === "0.7", "живость по умолчанию не 0.7: " + temp);
    check(await page.getAttribute("#agent-temperature", "max") === "1",
          "у живости интонации остался старый предел 2 (это диапазон Fish 0–1)");
    console.log("✅ ползунки: скорость " + speed + ", живость " + temp + " (предел 1)");

    // low должен быть доступен в латентности
    const latOpts = await page.$$eval("#agent-latency option", (o) => o.map((x) => x.value));
    check(latOpts.includes("low"), "в латентности нет low: " + latOpts.join(","));
    const latLabels = await page.$$eval("#agent-latency option", (o) => o.map((x) => x.textContent));
    check(latLabels.some((t) => t.indexOf("самый быстрый") !== -1),
          "у режимов латентности нет понятных подписей: " + latLabels.join(" | "));
    console.log("✅ латентность: " + latOpts.join(", ") + " с человеческими подписями");

    // ── 2. Данные, уходящие на сервер ──────────────────────────────────────
    await page.fill("#agent-name", "Мой Fish");
    await page.fill("#agent-voice", "abc123voice");
    await page.fill("#agent-system-prompt", "Промпт");
    await page.locator("#agent-speed").fill("1.25");
    await page.locator("#agent-temperature").fill("0.35");

    let posted = null;
    await page.route("**/api/fish-assistants", async (route) => {
        if (route.request().method() === "POST") {
            posted = JSON.parse(route.request().postData() || "{}");
            return route.fulfill({ status: 201, contentType: "application/json",
                                   body: JSON.stringify(AGENT) });
        }
        return route.fulfill({ status: 200, contentType: "application/json",
                               body: JSON.stringify(API["/api/fish-assistants"]) });
    });

    await page.click("#save-agent-btn");
    await page.waitForTimeout(500);

    check(posted, "POST на создание агента не ушёл");
    check(posted.name === "Мой Fish", "имя не передано: " + JSON.stringify(posted));
    check(posted.fish_voice_id === "abc123voice", "fish_voice_id не передан: " + JSON.stringify(posted));
    check(posted.fish_model === "s2.1-pro", "fish_model не передан: " + JSON.stringify(posted));
    check(posted.fish_latency === "balanced", "fish_latency не передан: " + JSON.stringify(posted));
    check(posted.llm_model === "gpt-realtime-2.1-mini", "llm_model не передан: " + JSON.stringify(posted));
    check(posted.voice_speed === 1.25, "скорость не передана: " + JSON.stringify(posted));
    check(posted.temperature === 0.35, "живость не передана: " + JSON.stringify(posted));
    check(!("voice_role" in posted) && !("max_tokens" in posted),
          "в запросе остались поля Yandex: " + JSON.stringify(posted));
    console.log("✅ POST уходит с полями Fish: " + Object.keys(posted).sort().join(", "));

    // ── 3. Список агентов и режим редактирования ───────────────────────────
    errors.length = 0;
    await page.goto("https://voksyai.online/static/fish-agents.html", { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    check(errors.length === 0, "ошибки JS в списке агентов:\n   " + errors.join("\n   "));

    const card = await page.textContent(".agent-item");
    check(card.indexOf("s2.1-pro") !== -1, "в карточке нет модели синтеза: " + card.trim());
    check(card.indexOf("voice123") !== -1, "в карточке нет голоса: " + card.trim());
    check(card.indexOf("undefined") === -1, "в карточке undefined: " + card.trim());
    console.log("✅ карточка агента рендерится без undefined");

    errors.length = 0;
    await page.goto("https://voksyai.online/static/fish-agents.html?mode=edit&id=" + AGENT.id,
                    { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    check(errors.length === 0, "ошибки JS в режиме редактирования:\n   " + errors.join("\n   "));

    // Форма заполняется асинхронно (loadAgentData) — дожидаемся признака
    // загрузки, а не полагаемся на случайную задержку.
    await page.waitForFunction(
        () => (document.getElementById("edit-title") || {}).textContent
                  ?.indexOf("Редактирование") === 0,
        null, { timeout: 5000 });

    const editVoice = await page.inputValue("#agent-voice");
    const editModel = await page.inputValue("#agent-model");
    check(editVoice === "voice123", "голос не подтянулся в редактирование: " + editVoice);
    check(await page.inputValue("#agent-speed") === "1", "скорость не подтянулась в редактирование");
    check(editModel === "s2.1-pro", "модель не подтянулась: " + editModel);
    console.log("✅ режим редактирования заполняет форму из ответа API");

    await browser.close();
    console.log("\nвсе проверки страницы пройдены");
})();
