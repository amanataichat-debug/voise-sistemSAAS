# GPT-Live (gpt-live-1): выжимка из документации OpenAI, 17.10.2026

Снято владельцем через Claude в Chrome (сервер этой сессии до developers.openai.com не ходит). Проза — пересказ машинного перевода, блоки кода — как на сайте. Страницы: `guides/live`, `live-prompting`, `live-delegation`, `live-conversations`, `live-migration`, `voice-websockets`, `custom-voices`, `voice-agents`, `voice-latency-cost`, `models/gpt-live-1`, `reference/resources/live/*`.

## Чего в документации НЕТ (не придумывать)
- Списка языков речи, упоминания русского и кыргызского для Live, таблицы качества по языкам. Встроенные голоса: английские (США, Австралия, Британия, Ирландия, Филиппины, Юг США), португальские (Бразилия), немецкие, французские, хинди, японские, корейский. Русских и кыргызских голосов нет; кастомные prompt-голоса — «with English accents». Совет страницы: проверять голос на нужных языках самим и отдельно оценивать «разборчивость вывода и выбор языка».
- Параметров speed / pitch / style / emotion; настроек порога тишины, чувствительности перебивания, noise reduction, модели распознавания. Темп и тембр — только словами в `instructions` или кастомным голосом.
- Описаний 10 базовых голосов (alloy, ash, ballad, cedar, coral, echo, marin, sage, shimmer, verse) и «самого выразительного».
- Времени до первого звука и его факторов; темпа отдачи аудио; размера чанков и джиттер-буфера; максимальной длительности сессии; таймаутов бэкенда.

## Что используем (и где в коде)
| Факт из документации | Что сделано |
|---|---|
| `instructions` до **16 384 токенов** | `LIVE_VOICE_INSTRUCTIONS_MAX_CHARS` 6000 → 16000, `VOICE_KNOWLEDGE_BUDGET` 4000 → 9000 (`agent_voice_context.py`): больше знаний в промпте — меньше походов к бэкенду (≈4 с) |
| Шаблон промпта: Personality / Backchannel policy / Interruption policy / Delegation policy (Backend tools, Delegate to the backend when, Do not delegate when); заголовки рекомендовано сохранять | `VOICE_DELEGATION_HINT` переписан по шаблону (`live_client.py`) |
| «Delegate before giving an answer that depends on backend work. Do not guess the result while waiting» | в том же блоке |
| `session.commentary.append` — «модель произносит вслух»; для Responses-делегирования `delegation_id` только `null`; ≤ 500 токенов | заполнитель: `LiveVoiceSession._delegation_watch` (`handler_live.py`) |
| Отдельного события «бэкенд упал» нет, голосовая модель сама не узнает | `_backend_failed`: `response.failed/incomplete`, `error` при активном делегировании или таймаут `LIVE_DELEGATION_TIMEOUT_SEC` → `session.instructions.append` «скажи, что не получилось» |
| `delegation.responses`: `reasoning.effort`, `service_tier` (`priority` = fast mode), `text.verbosity`, `max_output_tokens` (≥ 16) | env `LIVE_DELEGATION_REASONING_EFFORT` / `_SERVICE_TIER` / `_VERBOSITY` / `_MAX_OUTPUT_TOKENS` (пусто = не передаём) |
| Рекомендованный бэкенд: «Start with gpt-6-luna, or gpt-6-sol»; в примерах также gpt-5.6-luna | у нас `LIVE_DELEGATION_MODEL=gpt-5.6-terra`; сравнить на замерах (план п. 4) |
| Измерять по этапам, медиану и p95; отдельно «подтверждение» и ответ | замер задержки + разбивка с функцией / без (`call_log`) |

## Не сделано, но возможно
- **Приветствие**: документация советует developer-сообщение в `session.input` внутри `session.start`, а не `session.instructions.append` после `session.started` (instructions-append может прерывать речь). Нужна точная схема элемента `input` (по одной текстовой части) — не гадаем, чтобы не сломать старт сессии.
- **G.711 8 кГц** (`audio/pcmu`, `audio/pcma`) на WebSocket: для телефона можно убрать ресемплинг 8↔24 кГц (поток моста уже 8 кГц). Требует отдельной проверки качества речи.
- **Prompt-голос** (`POST /v1/audio/voices`, бесплатно, до 100): описание голоса словами (акцент, возраст, тембр, темп); официально только английские акценты — для русского/кыргызского не обещано.
- **Native Decisions** (триггеры по фрагментам транскрипта) и спекулятивный поиск до события делегирования — убирают круг через приложение.
- `session.update`: менять можно только `delegation` и `triggers`; `instructions`, `audio`, `input` неизменяемы.
- Лимиты: 128 000 токенов контекста, 90% → замена «voice engine» в той же сессии; параллельных сессий Build 50 / Launch 300 / Grow 500; $0.05/мин, бэкенд платно отдельно.
