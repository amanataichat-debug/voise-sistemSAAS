"""
Список топовых моделей для оркестратора Voicyfy Agent.
Все модели должны поддерживать function calling (tool calling).
Слаги — формат OpenRouter (provider/model-name).

✅ Кредитная модель: ставки списания НЕ задаются вручную, а выводятся из
   реальной цены модели на OpenRouter (`_rates`). Единственный источник правды
   для каждой модели — её себестоимость в долларах за 1000 токенов; всё
   остальное считает формула. Подробности — в docs/credits_system.md.

Как добавить модель:
    1. Проверить, что слаг есть на https://openrouter.ai/api/v1/models
       и что в `supported_parameters` присутствует `tools` (без function
       calling оркестратор с моделью работать не может).
    2. Взять `pricing.prompt` / `pricing.completion` (это цена за 1 токен),
       умножить на 1000 и передать в `_rates(...)`.
    3. Поставить запись рядом с её семейством — порядок массива определяет
       порядок опций в селекте на фронте.
"""

# ── Экономика кредитов оркестратора ─────────────────────────────────────────
# Цена кредита взята по САМОМУ ВЫГОДНОМУ для пользователя пакету
# (Enterprise: 500 000 кредитов за 29 990 ₽, см. seed в app.py). Считая маржу
# по нему, мы гарантируем заявленные +20% на всех пакетах — на более мелких
# кредит дороже, значит и маржа выше.
CREDIT_PRICE_RUB = 0.05998

# Курс закладывается с запасом к споту (ЦБ на 01.08.2026 — 79.46 ₽/$). Это
# подушка: себестоимость у нас в долларах, а кредит продаётся за рубли, и при
# марже 1.2 движение курса на 20% съело бы её целиком.
USD_RUB = 95.0

# Наценка к себестоимости OpenRouter.
ORCHESTRATOR_MARGIN = 1.2

# Сколько кредитов списать за $1 себестоимости: 1.2 × 95 / 0.05998 ≈ 1900.63.
_CREDITS_PER_USD = ORCHESTRATOR_MARGIN * USD_RUB / CREDIT_PRICE_RUB

# Эталонный вызов оркестратора для оценки «≈N кредитов за звонок», которую
# видит пользователь в селекте модели (`credits_per_call`). Цветовой градации
# у моделей сознательно нет — только число: красные маркеры отталкивали от
# вполне рабочих моделей. Подсказку «что выбрать» даёт флаг `is_recommended`.
# Это ОЦЕНКА, а не замер: реальный расход
# зависит от длины инструкций и истории диалога. Уточнять по факту —
#   SELECT ROUND(AVG(prompt_tokens)), ROUND(AVG(completion_tokens))
#   FROM credit_transactions
#   WHERE product = 'orchestrator' AND type = 'spend'
#     AND prompt_tokens IS NOT NULL AND created_at > NOW() - INTERVAL '30 days';
REFERENCE_CALL_TOKENS = (4000, 500)


def _rates(usd_per_1k_in: float, usd_per_1k_out: float) -> dict:
    """
    Себестоимость OpenRouter ($ за 1000 токенов) → поля модели для API.

    round(..., 4) обязателен: без него 0.0015 * 1900.63... даёт хвост вида
    2.8509503167722577, и этот мусор уезжает в JSON-ответ фронту.
    """
    rate_in = round(usd_per_1k_in * _CREDITS_PER_USD, 4)
    rate_out = round(usd_per_1k_out * _CREDITS_PER_USD, 4)

    ref_in, ref_out = REFERENCE_CALL_TOKENS
    per_call = round((ref_in / 1000.0) * rate_in + (ref_out / 1000.0) * rate_out, 2)

    return {
        "usd_per_1k_in": usd_per_1k_in,
        "usd_per_1k_out": usd_per_1k_out,
        "input_credits_per_1k": rate_in,
        "output_credits_per_1k": rate_out,
        "credits_per_call": per_call,
    }


ORCHESTRATOR_MODELS = [
    {
        "slug": "deepseek/deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "description": "Лучшее соотношение цена/качество. 1M контекст. Топ для агентов.",
        "is_default": True,
        **_rates(0.000435, 0.00087),
    },
    {
        "slug": "deepseek/deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "description": "Самая дешёвая из топов. Быстрая. Подходит для простых сценариев.",
        **_rates(0.00014, 0.00028),
    },
    {
        "slug": "deepseek/deepseek-v4-flash-0731",
        "name": "DeepSeek V4 Flash 0731",
        "description": "Свежая ревизия V4 Flash: то же MoE 284B/13B и та же цена, "
                       "дообучена под код, рассуждения и агентные сценарии.",
        **_rates(0.00014, 0.00028),
    },
    {
        "slug": "anthropic/claude-opus-4.8",
        "name": "Claude Opus 4.8",
        "description": "Самая мощная модель Anthropic. Лучшие рассуждения. Дорогая.",
        **_rates(0.005, 0.025),
    },
    {
        "slug": "anthropic/claude-sonnet-4.6",
        "name": "Claude Sonnet 4.6",
        "description": "Баланс цены и качества от Anthropic. Стабильный tool calling.",
        **_rates(0.003, 0.015),
    },
    {
        "slug": "openai/gpt-5.5",
        "name": "GPT-5.5",
        "description": "Флагман OpenAI. Сильный в reasoning. Тяжёлый по токенам.",
        **_rates(0.005, 0.03),
    },
    {
        "slug": "openai/gpt-5.5-pro",
        "name": "GPT-5.5 Pro",
        "description": "Премиум OpenAI для сложных задач. 1M+ контекст.",
        **_rates(0.03, 0.18),
    },
    {
        "slug": "openai/gpt-5.6-luna",
        "name": "GPT-5.6 Luna",
        "description": "Самая дешёвая модель OpenAI. Быстрая, 1M контекст. "
                       "Для больших объёмов звонков и лёгких агентных задач.",
        # Пометка «рекомендуем» в селекте. На модель по умолчанию не влияет —
        # новым агентам по-прежнему подставляется is_default (DeepSeek V4 Pro).
        "is_recommended": True,
        **_rates(0.0001, 0.0006),
    },
    {
        "slug": "google/gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro",
        "description": "1M контекст. Сильный в multistep workflows. Хороший tool calling.",
        **_rates(0.002, 0.012),
    },
    {
        "slug": "google/gemini-3.6-flash",
        "name": "Gemini 3.6 Flash",
        "description": "Новое поколение Flash от Google. Аккуратнее в длинных цепочках "
                       "tool calls, ответы дешевле, чем у 3.5 Flash.",
        **_rates(0.0015, 0.0075),
    },
    {
        "slug": "google/gemini-3.5-flash",
        "name": "Gemini 3.5 Flash",
        "description": "Дешёвый Gemini с приличным качеством. Топ tool calling.",
        **_rates(0.0015, 0.009),
    },
    {
        "slug": "google/gemini-3.1-flash-lite",
        "name": "Gemini 3.1 Flash Lite",
        "description": "Самый дешёвый Gemini. Для простых сценариев и высокого объёма звонков.",
        **_rates(0.00025, 0.0015),
    },
    {
        "slug": "moonshotai/kimi-k2.6",
        "name": "Kimi K2.6",
        "description": "Открытая модель, заточена под агентов. 128K контекст.",
        **_rates(0.0006, 0.00341),
    },
    {
        "slug": "xiaomi/mimo-v2.5-pro",
        "name": "MiMo V2.5 Pro",
        "description": "Xiaomi MoE с 1M контекстом. Сильный в длинных цепочках tool calls.",
        **_rates(0.000435, 0.00087),
    },
    {
        "slug": "z-ai/glm-5.2",
        "name": "GLM 5.2",
        "description": "Флагман Zhipu AI. Сильный агентный tool calling, хорошая цена.",
        **_rates(0.00076006, 0.00238876),
    },
    {
        "slug": "qwen/qwen3.7-plus",
        "name": "Qwen 3.7 Plus",
        "description": "Флагман Alibaba. Уверенный tool calling, широкий контекст.",
        **_rates(0.00032, 0.00128),
    },
    {
        "slug": "minimax/minimax-m3",
        "name": "MiniMax M3",
        "description": "Компактная агентная модель. Быстрая и недорогая для рутинных звонков.",
        **_rates(0.0003, 0.0012),
    },
    {
        "slug": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "name": "Nemotron 3 Ultra (free)",
        "description": "Огромный MoE от NVIDIA на бесплатном тарифе OpenRouter. "
                       "Лимиты и очередь — на стороне провайдера.",
        **_rates(0.0, 0.0),
    },
]

# Слаги, которые были в списке раньше, но на OpenRouter отсутствуют или
# переименовались. Нужны, чтобы у уже существующих агентов не ломалось
# сохранение настроек до того, как отработает миграция в app.py
# (ensure_agent_orchestrator_model_migration).
LEGACY_MODEL_ALIASES = {
    "google/gemini-3.1-pro": "google/gemini-3.1-pro-preview",
}


def resolve_slug(slug: str) -> str:
    """Привести устаревший слаг к актуальному. Неизвестный — вернуть как есть."""
    return LEGACY_MODEL_ALIASES.get(slug, slug)


def get_default_model() -> str:
    return next(m["slug"] for m in ORCHESTRATOR_MODELS if m.get("is_default"))


def is_valid_model(slug: str) -> bool:
    slug = resolve_slug(slug)
    return any(m["slug"] == slug for m in ORCHESTRATOR_MODELS)


def get_model_rates(slug: str) -> dict | None:
    """Вернуть ставки списания кредитов для модели или None если slug неизвестен."""
    slug = resolve_slug(slug)
    for m in ORCHESTRATOR_MODELS:
        if m["slug"] == slug:
            return {
                "input_credits_per_1k": m["input_credits_per_1k"],
                "output_credits_per_1k": m["output_credits_per_1k"],
            }
    return None
