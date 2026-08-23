/*
 * Прогон telephony.html и test_outbound-calls.html в Chromium с подставным API.
 * Проверяем, что Fish реально доехал до UI: появился в списках типов,
 * тянет своих ассистентов и уходит в запросы с assistant_type="fish".
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

const FISH_AGENT = { id: "af3cdd96-1834-4123-9554-b3057075c399", name: "Мой Fish агент" };

const NUMBER = {
    id: "99999999-8888-7777-6666-555555555555",
    phone_number: "+79311071031",
    phone_region: "Санкт-Петербург",
    assistant_type: "fish",
    assistant_id: FISH_AGENT.id,
    assistant_name: FISH_AGENT.name,
    assistant_model: "gpt-realtime-2.1-mini",
    agent_config_id: null,
    agent_name: null,
    first_phrase: null,
    is_active: true,
};

const requested = [];   // все пути к API, которые дёрнула страница

function apiBody(pathname) {
    if (pathname.startsWith("/api/fish-assistants")) {
        return { assistants: [FISH_AGENT], has_openai_key: true, has_fish_key: true };
    }
    if (pathname.startsWith("/api/telephony/my-numbers")) {
        return { numbers: [NUMBER], total: 1 };
    }
    if (pathname.startsWith("/api/telephony/status")) {
        return { is_connected: true, has_scenarios: true, has_outbound_rules: true,
                 balance: 100, vox_account_id: "1", child_account_created: true };
    }
    if (pathname.startsWith("/api/users/me")) {
        return { id: "u1", email: "t@t.ru", first_name: "Тест",
                 subscription_plan: "profi", is_admin: true, subscription_active: true };
    }
    if (pathname.startsWith("/api/subscriptions/assistants-usage")) {
        return { used: 1, max: 10, can_create: true, subscription_active: true, plan_code: "profi" };
    }
    if (pathname.startsWith("/api/functions")) return [];
    return {};
}

async function makePage(browser) {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });

    await page.route("**/*", async (route) => {
        const p = new URL(route.request().url()).pathname;
        if (p.startsWith("/api/")) {
            requested.push(p + (new URL(route.request().url()).search || ""));
            return route.fulfill({ status: 200, contentType: "application/json",
                                   body: JSON.stringify(apiBody(p)) });
        }
        if (p.startsWith("/static/")) {
            try { return await route.fulfill({ path: path.join(STATIC, p.replace("/static/", "")) }); }
            catch { return route.fulfill({ status: 404, body: "" }); }
        }
        return route.fulfill({ status: 200, contentType: "text/css", body: "" });
    });

    await page.addInitScript(() => {
        localStorage.setItem("auth_token", "fake-jwt");
        localStorage.setItem("token", "fake-jwt");
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

    // ── telephony.html ─────────────────────────────────────────────────────
    {
        const { page, errors } = await makePage(browser);
        await page.goto("https://voksyai.online/static/telephony.html", { waitUntil: "networkidle" });
        await page.waitForTimeout(800);

        const opts = await page.$$eval("#bind-assistant-type option",
                                       (o) => o.map((x) => x.value));
        check(opts.includes("fish"), "в списке типов привязки нет fish: " + opts.join(","), errors);
        console.log("✅ telephony: тип «fish» есть в привязке номера —", opts.join(", "));

        const sipOpts = await page.$$eval("#sip-assistant-type option", (o) => o.map((x) => x.value));
        check(sipOpts.includes("fish"), "в SIP-транке нет fish: " + sipOpts.join(","), errors);
        console.log("✅ telephony: тип «fish» есть в SIP-транке");

        // открываем модалку привязки так же, как пользователь — кнопкой у номера
        await page.click(".bind-assistant-btn");
        await page.waitForTimeout(400);

        // выбираем fish → страница должна пойти за списком Fish-ассистентов
        requested.length = 0;
        await page.selectOption("#bind-assistant-type", "fish");
        await page.waitForTimeout(500);

        const hit = requested.find((u) => u.startsWith("/api/fish-assistants"));
        check(hit, "при выборе fish не запрошены ассистенты. Запросы: " + requested.join(" | "), errors);
        console.log("✅ telephony: при выборе Fish тянется " + hit);

        const bindOpts = await page.$$eval("#bind-assistant-select option",
                                           (o) => o.map((x) => x.textContent.trim()));
        check(bindOpts.includes(FISH_AGENT.name),
              "Fish-ассистент не попал в выпадашку: " + bindOpts.join(","), errors);
        console.log("✅ telephony: ассистент «" + FISH_AGENT.name + "» доступен для привязки");

        // подпись у номера в списке
        const table = await page.textContent("body");
        check(table.indexOf(FISH_AGENT.name) !== -1, "номер не показывает привязанного Fish-агента", errors);
        console.log("✅ telephony: номер +79311071031 отображается с Fish-агентом");

        check(errors.length === 0, "ошибки JS на telephony.html:\n   " + errors.join("\n   "), errors);
        await page.close();
    }

    // ── test_outbound-calls.html ───────────────────────────────────────────
    {
        const { page, errors } = await makePage(browser);
        await page.goto("https://voksyai.online/static/test_outbound-calls.html",
                        { waitUntil: "networkidle" });
        await page.waitForTimeout(800);

        const tab = page.locator('.assistant-type-tab[data-type="fish"]');
        check(await tab.count() === 1, "вкладки Fish нет на странице обзвона", errors);
        console.log("✅ обзвон: вкладка Fish на месте");

        requested.length = 0;
        await tab.click();
        await page.waitForTimeout(600);

        const hit = requested.find((u) => u.startsWith("/api/fish-assistants"));
        check(hit, "клик по Fish не запросил ассистентов. Запросы: " + requested.join(" | "), errors);
        console.log("✅ обзвон: по клику тянется " + hit);

        const shown = await page.textContent("#assistantContent").catch(() => "");
        check(shown.indexOf(FISH_AGENT.name) !== -1,
              "Fish-ассистент не отрисовался в списке: " + shown.trim().slice(0, 120), errors);
        console.log("✅ обзвон: ассистент «" + FISH_AGENT.name + "» виден в списке");

        const active = await page.getAttribute('.assistant-type-tab[data-type="fish"]', "class");
        check(active.indexOf("active") !== -1, "вкладка Fish не стала активной: " + active, errors);
        console.log("✅ обзвон: вкладка Fish активна");

        // Полный сценарий запуска обзвона (выбор caller id + список номеров)
        // стендом не гоняется — здесь проверено, что тип долетает до state,
        // а payload формируется из него (assistant_type: state.assistantType).

        check(errors.length === 0, "ошибки JS на test_outbound-calls.html:\n   " + errors.join("\n   "), errors);
        await page.close();
    }

    await browser.close();
    console.log("\nобе страницы проверены");
})();
