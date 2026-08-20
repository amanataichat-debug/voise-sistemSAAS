# ТЗ: SSE-стриминг ответов ИИ в чате агента (Вариант 1 — полный стриминг)

## 1. Цель

Сделать чат владельца с оркестратором (`agent.html` → `/api/agent/chat`)
стримящим, как в ChatGPT/Claude: пользователь видит **прогресс вызова
инструментов вживую** и **печатающийся токен за токеном финальный ответ**,
а не спиннер на 15–18 секунд.

Решаем главную боль: бóльшая часть латентности — это вызовы инструментов
(`get_agent_contacts`, `update_contact_info` и т.п.), а не генерация текста.
Поэтому стримим и то, и другое.

## 2. Что НЕ меняем

- Старый эндпоинт `POST /api/agent/chat` остаётся **как есть** (fallback +
  Telegram и прочие потребители на него не завязаны — это веб-чат).
- Логику списания кредитов (`CreditService.precheck` / `CreditService.charge`),
  сохранение `agent_config.chat_history`, формат `debug_log`.
- Рендер markdown в пузыре (уже реализован: `renderMarkdown`, `.msg-bubble.md`).
- Telegram-ветки оркестратора (`_run_telegram_v3`, `_run_telegram_v2`).
- Legacy v2 (OpenAI Responses API) — не стримим, для него фронт использует
  fallback на обычный `/chat`.

## 3. Транспорт и протокол

**Транспорт:** `StreamingResponse` (FastAPI) + чтение на фронте через
`fetch` + `ReadableStream`. **НЕ `EventSource`** — он не умеет слать заголовок
`Authorization: Bearer`, а у нас JWT в header (`apiFetch`, `agent.html:890`).

**Формат:** NDJSON — по одному JSON-объекту на строку, разделитель `\n`.
`media_type="application/x-ndjson"`. Обязательно заголовок
`X-Accel-Buffering: no` и `Cache-Control: no-cache`, чтобы прокси Render не
буферизировал поток.

**События (поле `type`):**

| type | поля | когда |
|------|------|-------|
| `start` | — | сразу после старта потока |
| `tool_call` | `tool` (str), `args` (obj) | перед выполнением инструмента |
| `tool_result` | `tool` (str), `result` (obj/str) | успешное выполнение |
| `tool_error` | `tool` (str), `error` (str) | ошибка инструмента |
| `token` | `text` (str) | дельта текста финального ответа |
| `done` | `reply` (str), `debug_log` (array), `timestamp` (str ISO) | конец |
| `error` | `detail` (str/obj), `code` (int, опц.) | ошибка в процессе |

Пример строки: `{"type":"token","text":"Обнов"}\n`

## 4. Бэкенд

### 4.1. `backend/services/openrouter_client.py` — добавить стрим-метод

Новый метод `chat_completion_stream(...)` — async-генератор, который дёргает
OpenRouter с `"stream": true` и отдаёт распарсенные дельты.

Сигнатура — как у `chat_completion`, но это генератор:

```python
async def chat_completion_stream(
    self,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict]] = None,
    tool_choice: Optional[str] = "auto",
    temperature: float = 0.7,
    max_tokens: int = 4000,
    timeout: float = 90.0,
) -> AsyncIterator[Dict[str, Any]]:
    ...
```

Реализация:
- payload как в `chat_completion`, плюс `"stream": True` и
  `"usage": {"include": True}` (OpenRouter возвращает usage в финальном чанке
  при стриминге — проверить, что приходит; если нет — см. п. 4.4 про учёт токенов).
- `async with httpx.AsyncClient(timeout=timeout) as client:`
  `async with client.stream("POST", OPENROUTER_API_URL, json=payload, headers=headers) as resp:`
- `resp.raise_for_status()`; `async for line in resp.aiter_lines():`
  - пропускать пустые строки и строки-комментарии (`": OPENROUTER PROCESSING"`).
  - строки вида `data: {...}`; снять префикс `data: `.
  - `data: [DONE]` → завершить генератор.
  - `json.loads` чанка; **yield** распарсенный chunk-объект
    (`{"choices":[{"delta":{...},"finish_reason":...}], "usage":...}`).
- Ошибки сети/JSON логировать и пробрасывать (поймает оркестратор).

> Важно: реконструкция `tool_calls` из дельт — НЕ здесь, а в оркестраторе
> (метод отдаёт сырые чанки). Так клиент остаётся тонким.

### 4.2. `backend/services/agent_orchestrator.py` — генераторная версия цикла

Добавить в класс `ChatOrchestrator` новый метод
`run_stream(message, agent_config, user, db) -> AsyncIterator[Dict]`
(async-генератор событий из п.3). По сути — генераторная копия
`_run_v3_openrouter` (строки ~1232–1354). Старый `_run_v3_openrouter` НЕ трогаем.

Алгоритм `run_stream`:

1. `CreditService.precheck(db, user)` — **до** первого `yield`. Если кидает
   `SubscriptionExpiredError` / `SubscriptionRequiredError` /
   `InsufficientCreditsError` — пробросить наружу (эндпоинт превратит в 402
   ДО старта стрима, см. 4.3). Это критично: после старта стрима статус уже 200.
2. Собрать `system_prompt = build_orchestrator_prompt(agent_config)`,
   `messages` из `agent_config.chat_history[-20:]` + текущее сообщение,
   `tools = to_chat_completions_tools(AGENT_CHAT_TOOLS)`, `context` — как в
   оригинале. Завести `debug_log` и заполнять его теми же событиями
   (`user_message`, `gpt_thinking`, `tool_call`, `tool_result`, `tool_error`,
   `gpt_response`) — он нужен для финального `done` и для сохранения совместимого
   формата отладки.
3. `yield {"type": "start"}`.
4. Цикл `while iteration < max_iterations` (10):
   - Вызвать `client.chat_completion_stream(...)`.
   - Аккумулировать по ходу чанков:
     - `delta.content` → копить в `content_buf`; **сразу**
       `yield {"type":"token","text": delta.content}`.
     - `delta.tool_calls` (массив с `index`, `id`, `function.name`,
       `function.arguments`-фрагменты) → реконструировать в словарь
       `tool_calls_acc` по `index` (склеивать `arguments` строкой,
       запоминать `id` и `name`).
     - финальный чанк может содержать `usage` → сохранить `p_tok`, `c_tok`.
   - После завершения стрима итерации:
     - **Если `tool_calls_acc` пуст** → это финальный ответ.
       `final_text = content_buf`; `debug_log += gpt_response`; `break`.
     - **Если есть tool_calls**:
       - Если в `content_buf` что-то было застримлено токенами (редкий случай:
         модель写 текст перед вызовом инструмента) → отправить
         `{"type":"clear_partial"}` (см. 5, фронт сотрёт частичный текст этого
         хода). Обычно `content_buf` пуст — глитча нет.
       - Добавить assistant-сообщение с `tool_calls` в `messages`.
       - Для каждого tool_call: `yield {"type":"tool_call","tool":name,"args":args}`
         (+ в `debug_log`), выполнить `await execute_tool(...)`,
         `yield {"type":"tool_result"|"tool_error", ...}` (+ в `debug_log`),
         добавить tool-сообщение в `messages`. Логика 1:1 с оригиналом
         (строки 1303–1328).
   - Суммировать токены: `total_prompt += p_tok; total_completion += c_tok`.
5. После цикла: `if not final_text: final_text = "Готово."`;
   `debug_log += gpt_response` (если ещё не добавлен).
6. `CreditService.charge(...)` — те же аргументы, что в оригинале
   (`ref_type="chat"`, `notes=f"chat iterations: {iteration}"`).
7. Сохранить историю: `new_history += user/assistant`, обрезать `[-20:]`,
   `agent_config.chat_history = ...`, `db.commit()`.
8. `yield {"type":"done","reply":final_text,"debug_log":debug_log,`
   `"timestamp": datetime.utcnow().isoformat()}`.

**Обработка ошибок внутри стрима** (после `start`): обернуть тело в try/except;
при исключении — `yield {"type":"error","detail":str(e)}` и завершить.
Не давать исключению «порвать» соединение без события.

> Анти-дублирование (опционально, для аккуратности): можно вынести общую сборку
> `messages`/`context`/`tools` из `_run_v3_openrouter` и `run_stream` в приватный
> хелпер, чтобы не копировать. Но при риске задеть рабочий путь — допустимо
> просто скопировать тело; главное, не менять старый метод.

### 4.3. `backend/api/agent.py` — новый эндпоинт

`POST /api/agent/chat/stream` рядом с `/chat` (строка 590). Тело и зависимости
те же (`AgentChatRequest`, `agent_id` query, `get_current_user`, `get_db`).

```python
@router.post("/chat/stream")
async def agent_chat_stream(body, agent_id=Query(None), current_user=..., db=...):
    agent = _resolve_agent(db, current_user, agent_id)
    if not agent: raise HTTPException(404, "not_found")
    if not agent.uses_hardcoded_prompt and not current_user.openai_api_key:
        raise HTTPException(400, "openai_key_required")
    # Только v3 стримим. v2 (legacy) → 409, фронт уйдёт на /chat fallback.
    # (определить признак v3 так же, как это делает ChatOrchestrator.run)

    from backend.services.agent_orchestrator import ChatOrchestrator
    orchestrator = ChatOrchestrator()

    async def event_gen():
        try:
            async for ev in orchestrator.run_stream(body.message, agent, current_user, db):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except (SubscriptionExpiredError, SubscriptionRequiredError, InsufficientCreditsError):
            raise  # см. ниже — должно ловиться ДО стрима
        except Exception as e:
            logger.error(f"[AGENT] stream error: {e}", exc_info=True)
            yield json.dumps({"type":"error","detail":f"chat_error: {e}"}) + "\n"

    return StreamingResponse(
        event_gen(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
```

**Проблема 402 до стрима.** `precheck` сейчас внутри `run_stream`, а ошибка
402 должна вернуться HTTP-статусом, а не внутри потока. Решение: вызвать
`CreditService.precheck(db, current_user)` **в самом эндпоинте до создания
StreamingResponse**, в try/except, маппя на 402 точно как в `/chat`
(строки 616–625). Тогда `run_stream` повторный precheck может опустить
(или оставить — он идемпотентен).

**DB-сессия.** Генератор обращается к `db` весь стрим. FastAPI держит
зависимость `get_db` живой до конца StreamingResponse, поэтому
`db.commit()` в конце генератора корректен. Проверить, что в `get_db` сессия
закрывается в `finally` после исчерпания генератора (стандартный паттерн —
ок). Не открывать новую сессию вручную.

### 4.4. Учёт токенов при стриминге

OpenRouter при `stream:true` отдаёт `usage` в последнем чанке, если передать
`"usage":{"include":true}`. Проверить, что `_extract_usage` умеет это достать
из чанка (или адаптировать — взять `usage` из накопленного финального чанка).
Если usage недоступен — НЕ блокировать фичу: списать по приблизительной оценке
(как минимум не падать). Зафиксировать поведение в коде комментарием.

## 5. Фронтенд (`backend/static/agent.html`)

### 5.1. Переписать `sendMessage` (строки ~1457–1480) на стрим

- Добавить пузырь пользователя (как сейчас, `addBubble(text,'user')`).
- Вместо `showTyping()` + ожидания JSON — создать **пустой пузырь ассистента**
  заранее (`createStreamingBubble()`), показать в нём индикатор «думаю» и
  под-строку статуса инструментов.
- `fetch(API + '/chat/stream', {method:'POST', headers:{Authorization, Content-Type},
  body: JSON.stringify({message})})`.
- Если ответ не `ok` (404/400/402/409) — распарсить JSON-ошибку и:
  - 402 → показать то же сообщение про кредиты/подписку, что и сейчас;
  - **409 (legacy v2) или сетевой сбой стрима → fallback** на старый
    `apiFetch(API+'/chat')` и обычный `addAgentBubble`. Так ничего не ломается.
- Иначе читать поток:
  ```js
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while(true){
    const {value, done} = await reader.read();
    if(done) break;
    buf += decoder.decode(value, {stream:true});
    let nl;
    while((nl = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl+1);
      if(line) handleStreamEvent(JSON.parse(line));
    }
  }
  ```
- После `done`: дёрнуть `loadStats(); loadRecentCalls(); loadTasks();` (как сейчас).
- На любом исключении — `removeTyping`/убрать пустой пузырь + fallback или
  сообщение «Ошибка сети».
- В `finally` вернуть `chat-send.disabled = false`.

### 5.2. `handleStreamEvent(ev)` — диспетчер

Состояние на время одного ответа: `acc` (накопленный текст), `toolEvents`
(массив для debug_log), ссылки на DOM-узлы пузыря/статуса.

- `start` → ничего (или скрыть лишнее).
- `tool_call` → показать в строке статуса под пузырём «`TOOL_LABELS[tool]` …»
  (использовать существующий `TOOL_LABELS`), пушнуть в `toolEvents`.
- `tool_result` / `tool_error` → обновить статус («✓ найдено: N» / «ошибка»),
  пушнуть в `toolEvents`.
- `token` → `acc += ev.text`; **инкрементально** перерисовать тело пузыря:
  `bubble.innerHTML = renderMarkdown(acc)`. Перерисовку **троттлить**
  (`requestAnimationFrame` или таймер ~50мс), чтобы markdown не парсился на
  каждый токен. Скроллить вниз. Когда пошли токены — убрать индикатор «думаю».
- `clear_partial` → `acc = ''`, очистить тело пузыря (редкий случай).
- `done` → финальный `bubble.innerHTML = renderMarkdown(ev.reply)`; построить
  блок debug-лога из `ev.debug_log` тем же кодом, что в `addAgentBubble`
  (вынести генерацию `entriesHtml` + тег `N действий · Xс` в общую функцию,
  чтобы не дублировать). Проставить время.
- `error` → показать текст ошибки в пузыре (или fallback).

### 5.3. Рефактор для переиспользования

Вынести из `addAgentBubble` (строки 1461–1483) построение debug-блока
(`entriesHtml`, `tag`, разметка `.debug-tag`/`.debug-log`) в отдельную функцию
`renderDebugBlock(debugLog, elapsed)`, чтобы и стрим-путь, и старый `addAgentBubble`
(остаётся для fallback) использовали её. `toggleLog` не трогаем.

## 6. Edge cases / чек-лист приёмки

- [ ] Обычный ответ без инструментов — печатается токенами, markdown корректен
      (жирный/таблицы/списки рендерятся в финале).
- [ ] Ответ с 1–3 инструментами — видно живой прогресс («Загружаю контакты…
      ✓ найдено: 1»), затем печатается текст, в конце доступен debug-лог
      (тег «N действий · Xс»), идентичный текущему.
- [ ] Markdown-таблица в стриме не «прыгает» уродливо (троттлинг перерисовки).
- [ ] Недостаточно кредитов / истёкшая подписка → HTTP 402 ДО старта стрима,
      то же сообщение в UI, что и сейчас.
- [ ] Legacy v2-агент → fallback на `/chat`, всё работает по-старому.
- [ ] Обрыв сети посреди стрима → не виснет, показывает ошибку/fallback,
      кнопка отправки разблокируется.
- [ ] Кредиты списываются один раз в конце; `chat_history` сохранён корректно
      (проверить, что после рефреша страницы история на месте).
- [ ] На Render (прод) поток не буферизируется (заголовок `X-Accel-Buffering`).
- [ ] Старый `POST /api/agent/chat` продолжает работать (Telegram/прочее).

## 7. Объём правок (ориентир)

- `openrouter_client.py` — +1 метод (~35 строк).
- `agent_orchestrator.py` — +1 метод `run_stream` (~120 строк, копия цикла
  с `yield`).
- `agent.py` — +1 эндпоинт (~40 строк) + импорт `StreamingResponse`, `json`.
- `agent.html` — переписать `sendMessage`, добавить `handleStreamEvent`,
  `createStreamingBubble`, `renderDebugBlock`; CSS для строки статуса
  инструментов под пузырём.

## 8. Ветка

Разработка в существующей ветке `08062026v2`. Коммитить осмысленными шагами:
(1) бэкенд-клиент+оркестратор, (2) эндпоинт, (3) фронт. Не создавать PR без
явной просьбы.
