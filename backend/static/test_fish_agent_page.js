/*
 * Прогон agent.html (агент обзвона) в Chromium с подставным API.
 * Проверяем, что Fish доехал до мастера создания агента: карточка типа,
 * пилюли ключей (OpenAI + Fish), поле голоса fish_voice_id вместо селекта
 * голосов, и что в /api/agent/create уходит assistant_type="fish" со
 * настройками голоса. Плюс тип есть в edit-модалке существующего агента.
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

const FISH_VOICE_ID = "e58b0d7efca34eb38d5c4985e378abcb";

// Агент с fish-голосом — для проверки шапки и edit-модалки.
const FISH_AGENT = {
    id: "11111111-2222-3333-4444-555555555555",
    name: "Fish агент",
    assistant_type: "fish",
    fish_assistant_id: "99999999-8888-7777-6666-555555555555",
    fish_voice_id: FISH_VOICE_ID,
    fish_model: "s2.1-pro-free",
    fish_latency: "balanced",
    voice_speed: 1.1,
    is_active: true,
    uses_hardcoded_prompt: true,
    orchestrator_model: "deepseek/deepseek-v4-pro",
    voice_assistant_name: "Fish агент Voice",
    working_hours_start: 9,
    working_hours_end: 21,
    chat_history: [],
};

const MODELS = {
    models: [{ slug: "deepseek/deepseek-v4-pro", name: "DeepSeek V4 Pro", is_default: true,
               credits_per_call: 10, description: "Тестовая" }],
    default: "deepseek/deepseek-v4-pro",
};

let agentsExist = false;        // /list отдаёт агента или пустой список
const requested = [];           // пути к API, которые дёрнула страница
const createBodies = [];        // тела POST /api/agent/create

function apiBody(pathname) {
    if (pathname === "/api/agent/list") {
        return {
            total: agentsExist ? 1 : 0,
            max_agents: 3,
            can_create_more: true,
            has_agent_access: true,
            agents: agentsExist ? [FISH_AGENT] : [],
        };
    }
    if (pathname === "/api/agent/" || pathname === "/api/agent") return FISH_AGENT;
    if (pathname.startsWith("/api/agent/orchestrator-models")) return MODELS;
    if (pathname.startsWith("/api/agent/phone-numbers")) return { phone_numbers: [] };
    if (pathname.startsWith("/api/agent/stats")) return { stats: {} };
    if (pathname.startsWith("/api/agent/calls")) return { calls: [] };
    if (pathname.startsWith("/api/agent/tasks")) return { tasks: [] };
    if (pathname.startsWith("/api/agent/contacts")) return { contacts: [], total: 0 };
    if (pathname.startsWith("/api/agent/knowledge-base")) return { has_kb: false };
    if (pathname.startsWith("/api/agent/connectors")) return { connectors: [] };
    if (pathname.startsWith("/api/agent/create")) return { ...FISH_AGENT, trial_activated: true };
    if (pathname.startsWith("/api/telephony/status")) return { is_verified: true, is_connected: true };
    if (pathname.startsWith("/api/users/me")) {
        // Ключей нет: мастер обязан показать поля ввода обоих ключей Fish.
        return { id: "u1", email: "t@t.ru", first_name: "Тест",
                 has_api_key: false, has_gemini_api_key: true, has_fish_api_key: false,
                 subscription_plan: "profi", subscription_active: true };
    }
    if (pathname.startsWith("/api/credits")) {
        return { balance: 1500, subscription: { active: true, plan_code: "profi" }, packages: [] };
    }
    return {};
}

async function makePage(browser) {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });

    await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        const p = url.pathname;
        if (p.startsWith("/api/")) {
            requested.push(p + (url.search || ""));
            if (p.startsWith("/api/agent/create")) {
                try { createBodies.push(JSON.parse(route.request().postData() || "{}")); }
                catch { createBodies.push({}); }
            }
            return route.fulfill({ status: 200, contentType: "application/json",
                                   body: JSON.stringify(apiBody(p)) });
        }
        if (p.startsWith("/static/")) {
            try { return await route.fulfill({ path: path.join(STATIC, p.replace("/static/", "")) }); }
            catch { return route.fulfill({ status: 404, body: "" }); }
        }
        // Внешние CDN (marked, dompurify, шрифты) стенду не нужны.
        return route.fulfill({ status: 200, contentType: "text/css", body: "" });
    });

    await page.addInitScript(() => {
        localStorage.setItem("auth_token", "fake-jwt");
        localStorage.setItem("token", "fake-jwt");
        localStorage.removeItem("agent_wizard_v3");
    });

    return { page, errors };
}

function check(cond, msg, errors) {
    if (!cond) {
        console.error("❌ " + msg);
        if (errors && errors.length) {
            console.error("--- ошибки страницы ---");
            errors.forEach((e) => console.error("   " + e));
        }
        process.exit(1);
    }
}

(async () => {
    const browser = await chromium.launch({
        executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    });

    // ── Мастер создания: выбор Fish ────────────────────────────────────────
    {
        agentsExist = false;
        const { page, errors } = await makePage(browser);
        await page.goto("https://voicyfy.ru/static/agent.html", { waitUntil: "networkidle" });
        await page.waitForTimeout(600);

        // Обучающая карусель показывается всегда — пропускаем её.
        await page.evaluate(() => obSkip());
        await page.waitForTimeout(400);

        const types = await page.$$eval(".type-card .type-name", (n) => n.map((x) => x.textContent.trim()));
        check(types.some((t) => t.indexOf("Fish") !== -1),
              "в мастере нет карточки Fish: " + types.join(", "), errors);
        console.log("✅ мастер: типы —", types.join(", "));

        // Выбираем Fish: ключей нет → должны появиться поля обоих ключей,
        // а кнопка «Далее» остаться заблокированной.
        await page.evaluate(() => selectType("fish"));
        await page.waitForTimeout(300);

        const pills = await page.$$eval(".key-pill", (n) => n.map((x) => x.textContent.trim()));
        check(pills.some((p) => p.indexOf("OpenAI") !== -1) && pills.some((p) => p.indexOf("Fish") !== -1),
              "нет пилюль обоих ключей Fish: " + pills.join(" | "), errors);
        console.log("✅ мастер: пилюли ключей —", pills.join(" | "));

        check(await page.locator("#wk-openai_api_key").count() === 1,
              "нет поля ввода ключа OpenAI", errors);
        check(await page.locator("#wk-fish_api_key").count() === 1,
              "нет поля ввода ключа Fish", errors);
        console.log("✅ мастер: просит ввести оба ключа");

        const nextDisabled = await page.locator(".wizard-actions .btn-primary").isDisabled();
        check(nextDisabled, "«Далее» доступна без ключей Fish", errors);
        console.log("✅ мастер: без ключей дальше не пускает");

        // Шаг выбора голоса: у Fish — текстовое поле reference_id, не селект.
        await page.evaluate(async () => {
            wizardData.assistant_type = "fish";
            wizardStep = 7;
            await renderWizard();
        });
        await page.waitForTimeout(400);

        check(await page.locator("#w-fish-voice-id").count() === 1,
              "нет поля Fish Voice ID на шаге голоса", errors);
        check(await page.locator("#w-fish-latency").count() === 1,
              "нет селекта режима синтеза Fish", errors);
        check(await page.locator("#w-voice").count() === 0,
              "у Fish показан селект голосов вместо поля reference_id", errors);
        console.log("✅ мастер: голос Fish задаётся reference_id + скорость + режим");

        // Заполняем голос и жмём «Создать агента» — смотрим, что уходит на бэк.
        await page.fill("#w-fish-voice-id", FISH_VOICE_ID);
        await page.selectOption("#w-fish-latency", "low");
        await page.evaluate(() => {
            wizardData.name = "Fish агент";
            ["doc_who_am_i", "doc_who_we_call", "doc_how_we_talk",
             "doc_what_we_offer", "doc_rules_and_goals"].forEach((f) => { wizardData[f] = "текст"; });
        });
        createBodies.length = 0;
        await page.evaluate(() => submitCreate());
        await page.waitForTimeout(1200);

        check(createBodies.length === 1,
              "создание агента не ушло на бэк (запросов: " + createBodies.length + ")", errors);
        const body = createBodies[0];
        check(body.assistant_type === "fish",
              "в /create ушёл тип " + body.assistant_type, errors);
        check(body.fish_voice_id === FISH_VOICE_ID,
              "в /create не доехал fish_voice_id: " + JSON.stringify(body.fish_voice_id), errors);
        check(body.fish_latency === "low",
              "в /create не доехал fish_latency: " + JSON.stringify(body.fish_latency), errors);
        console.log("✅ мастер: /api/agent/create ← assistant_type=fish, voice=" +
                    body.fish_voice_id + ", latency=" + body.fish_latency);

        check(errors.length === 0, "ошибки JS в мастере:\n   " + errors.join("\n   "), errors);
        await page.close();
    }

    // ── Существующий fish-агент: шапка и модалки ───────────────────────────
    {
        agentsExist = true;
        const { page, errors } = await makePage(browser);
        await page.goto("https://voicyfy.ru/static/agent.html", { waitUntil: "networkidle" });
        await page.waitForTimeout(900);

        const head = await page.textContent("body");
        check(head.indexOf("Fish") !== -1, "тип Fish не показан в интерфейсе агента", errors);
        console.log("✅ дашборд: агент отображается с типом Fish");

        const editOpts = await page.$$eval("#e-assistant_type option", (o) => o.map((x) => x.value));
        check(editOpts.indexOf("fish") !== -1,
              "в edit-модалке нет типа fish: " + editOpts.join(","), errors);
        console.log("✅ настройки: тип fish доступен для смены —", editOpts.join(", "));

        // Модалка инструкций рисует контрол голоса по типу агента.
        await page.evaluate(() => openInstructionsModal());
        await page.waitForTimeout(400);
        const vid = await page.inputValue("#i-fish-voice-id");
        check(vid === FISH_VOICE_ID, "поле голоса не заполнено текущим значением: " + vid, errors);
        const lat = await page.inputValue("#i-fish-latency");
        check(lat === "balanced", "режим синтеза не заполнен текущим значением: " + lat, errors);
        console.log("✅ настройки: голос Fish подставлен в модалку (" + vid + ", " + lat + ")");

        check(errors.length === 0, "ошибки JS на дашборде:\n   " + errors.join("\n   "), errors);
        await page.close();
    }

    await browser.close();
    console.log("\nagent.html проверен: Fish доступен в мастере и настройках");
})();
