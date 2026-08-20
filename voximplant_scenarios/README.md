# Сценарии Voximplant (cascade + VoxTTS)

Код VoxEngine-сценариев для каскадного голосового ассистента:
встроенный ASR (Yandex v2, streaming + interim) → VoxTurnTaking (Silero VAD +
Pipecat Smart Turn) → OpenAI **gpt-realtime-2.1-mini** (Realtime API по WebSocket,
режим «только текст», контекст на стороне OpenAI) → VoxTTS (Anna/Sergey).

## Почему LLM ходит через Realtime, а не Chat Completions

Realtime здесь — **не** голосовой стек, а быстрый текстовый мозг. Аудио в него не
заводится вообще, `audio.input.turn_detection = null`: слушает Yandex ASR, а
момент «пора отвечать» определяет наш `VoxTurnTaking` и явно дёргает
`responseCreate()`. Выигрыш по задержке даёт три вещи:

1. WebSocket открыт с начала звонка — нет установки соединения на каждый ход;
2. история диалога живёт на стороне OpenAI (шлём только новую реплику через
   `conversationItemCreate()`), тогда как Chat Completions пересылал весь массив
   `messages` заново и prefill рос с каждым ходом;
3. модель дистиллирована под низкую задержку.

TTFT падает с ~600мс до ~250мс. Спекулятивная генерация и `warmup()`, которые
существовали только чтобы прятать медленный TTFT, **удалены**. Endpointing
подрезан под новый бюджет: `userSpeechTimeoutMs` 700→500, `transcriptSettleMs`
350→250. Ожидаемая задержка ответа — около 1 секунды.

Перебивание абонентом теперь вызывает `responseCancel()` — модель реально
останавливается на сервере, а не досчитывает ответ, который никто не услышит.

## Пауза перед ответом (пресет на странице агента)

Владелец агента выбирает её на `/static/cascade.html`: **300 мс** (быстрая,
по умолчанию), **650 мс** (сбалансированная), **1000 мс** (терпеливая). Значение
хранится в `grok_assistant_configs.silence_duration_ms`, приезжает в сценарий
полем `silence_duration_ms` конфига и кладётся в `VoxTurnTaking`:

```
vadOptions.minSilenceDurationMs = silence
policy.userSpeechTimeoutMs      = silence + 250
```

Это **не** таймер ответа: вето детектора, стабильность транскрипта и инвариант
«клиент говорит» всё равно могут отложить закрытие хода. Больше сценарий в
настройки turn-taking не лезет — остальные дефолты v2 выверены на проде, и
переопределять их из сценария нельзя (ровно это в своё время вернуло дробление
фраз на сегменты).

Сценарии передают в `VoxTurnTaking.create()` два обязательных коллбэка:

- `isAgentSpeaking` — без него рантайм считает перебиванием **любой** сегмент
  VAD (эхо, кашель, шум). Считается по «озвученному запасу»: каждый отправленный
  в VoxTTS кусок продлевает ожидаемый конец речи.
- `onTurnCorrection` — Yandex досылает полный текст реплики через 2-5 с после
  того, как ход уже ушёл в модель по interim. Сценарий правит историю (`messages`,
  `dialog` для CRM) и отдаёт модели уточнение через `conversationItemCreate()`
  **без** `responseCreate()` — иначе собеседник получает два ответа на один вопрос.

В конце звонка пишется `===TURN_STATS===` (`turnTaking.statsSummary()`) — чтобы
эффект настроек был измерим на проде, а не «на слух».

**Биллинг:** токены берутся из события `ResponseDone` (`response.usage`), причём
`input_token_details.cached_tokens` считаются отдельно — в них уходит вся история
диалога, а стоят они в 10 раз дешевле. В `/api/voximplant/log` уезжают как
`cascade_usage.{prompt_tokens, cached_prompt_tokens, completion_tokens}`.
Ставки — в `backend/services/cascade_credit_service.py`.

**Реконнект:** у Realtime-сессии есть TTL (у Chat Completions его не было). При
обрыве WS сценарий поднимает клиента заново (до 2 попыток) и отдаёт историю
стенограммой внутри `instructions` — для этого локальный массив `messages`
продолжает вестись, хотя в запросы больше не уходит.

Сценарии живут на **родительском аккаунте Voximplant** и копируются на дочерние
аккаунты штатными админ-эндпоинтами. Этот каталог — источник правды для их кода.

## Файлы

| Файл | Имя сценария на Voximplant | Назначение |
|---|---|---|
| `vox-turn-taking.js` | `vox-turn-taking` | **v2.1.** Хелпер turn-taking. Объявляет глобальный `VoxTurnTaking` (Silero VAD + Pipecat Smart Turn + двухскоростной endpointing), сам ничего не запускает. Отдаёт сценарию `onUserTurn`, `onSpeculativeTurn`, `onInterrupt`, `canPlayAgentAudio()`, `currentVersion()`. Должен стоять ПЕРВЫМ в цепочке правила. Рассчитан на Yandex v2 с `interimResults`. |
| `inbound_cascade.js` | `inbound_cascade` | Входящий каскад: конфиг с `/api/telephony/config`, ASR Yandex v2 (interim), LLM gpt-realtime-2.1-mini через **Realtime API** (WS, `output_modalities: ["text"]`, `turn_detection: null`, ключ **серверный** — расход идёт с кредитов каскада), TTS VoxTTS/Anna. Одна активная генерация за раз, гейтинг по `response_id`; перебивание — `responseCancel()`. **Tool-calling**: вызов приходит целиком в `ResponseOutputItemDone`, выполняется через `POST /api/voximplant/functions/execute`, `hangup_call` завершает звонок локально. Ограничение «функции ⊕ спекуляция» снято — спекуляции больше нет. **Запись/стоимость/логирование**: `call.record()` на Connected, `call_session_history_id` из `AppEvents.Started`, стоимость/длительность из события Disconnected, и один `POST /api/voximplant/log` в конце звонка (запись→R2, полная стоимость через GetCallHistory, структурированный `dialog`, токены `cascade_usage`, Telegram). Per-turn `/webhook/transcript` не используется. |
| `outbound_cascade.js` | `outbound_cascade` | **Исходящий** каскад. Точка входа — `AppEvents.Started` + `VoxEngine.customData()` (phone_number, assistant_id, caller_id, contact_name/task_*/custom_greeting). Конфиг с `/api/telephony/outbound-config`. Задача звонка + CRM инжектятся в system-промпт. **Readiness-before-dial**: ASR/TTS и **WS к OpenAI** поднимаются ДО `VoxEngine.callPSTN` (при сбое не звоним = 0₽) — заодно это заменило прогрев LLM. **Mute-окно** `mute_duration_ms` после ответа (мик абонента закрыт, чтобы «Алло» не оборвало приветствие). **Tool-calling** через Realtime `tools` (плоский формат) — в т.ч. `hangup_call`, чтобы ассистент сам завершил звонок. Silence hard-timeout ~180с. Запись/стоимость/`/log` — как в inbound. **Самодостаточен**: `VoxTurnTaking` встроен в сам файл (идемпотентно), поэтому работает и с одиночным правилом, и с цепочкой — `vox-turn-taking` в цепочке НЕ обязателен. |
| `cartesia_inbound.js` | `cartesia_inbound` | Входящий half-cascade на **OpenAI Realtime** (`gpt-realtime-2.1-mini`, output text): STT + turn detection + reasoning на стороне OpenAI (Silero/Pipecat не нужны), TTS — VoxTTS/Anna. Ключ — пользовательский (`CONFIG.api_key`). Одиночный сценарий (без цепочки vox-turn-taking). Несмотря на имя, TTS не Cartesia. |
| `inbound_fish.js` | `inbound_fish` | Входящий half-cascade на **OpenAI Realtime** (output text, ключ пользователя) + озвучка **Fish Audio** через наш прокси: `VoxEngine.createWebSocket(CONFIG.fish_tts_url)` → `/ws/fish/tts/{assistant_id}` → Fish, медиа-фреймы PCM16 обратно в звонок (`sendMediaTo(call)`). Конфиг с `/api/telephony/config`. Отдельный ASR не нужен — транскрибирует сама модель. Реальные номера и время МСК дописываются в `instructions` (нужны для `send_sms`). Одиночный сценарий. |
| `outbound_fish.js` | `outbound_fish` | **v1.1. Исходящий** Fish. Точка входа — `AppEvents.Started` + `VoxEngine.customData()` (phone_number, assistant_id, caller_id, mute_duration_ms, contact_name/task_*/custom_greeting/task). Конфиг с `/api/telephony/outbound-config`. Сокет к прокси синтеза поднимается ДО `callPSTN` (готов к моменту ответа), приветствие уходит в Fish напрямую без раунда к модели, шлюз TTS придерживает реплики модели до `speech_done` по приветствию. **Контекст звонка** (задача + карточка CRM) и реальные номера/время инжектятся в `instructions`; `custom_greeting` от PreCall-оркестратора агента обзвона приоритетнее приветствия из конфига. `hangup_call` завершает звонок локально, остальные функции — через `POST /api/voximplant/functions/execute`. Используется агентом обзвона с `assistant_type='fish'` (rule `outbound_fish`). |

## Раскатка (вручную)

1. На родительском аккаунте Voximplant обновить код сценариев `vox-turn-taking`,
   `inbound_cascade` и `outbound_cascade` содержимым этих файлов (имена — строго
   как в таблице).
2. Раскатать на дочерние аккаунты:
   - `POST /api/telephony/admin/setup-cascade-scenarios` — копирует cascade-сценарии
     (inbound + outbound) и создаёт `outbound_cascade` rule **цепочкой**
     `vox-turn-taking;outbound_cascade`;
   - `POST /api/telephony/admin/deploy-turn-taking` — копирует хелпер и патчит
     inbound-правила cascade-номеров на цепочку `vox-turn-taking;inbound_cascade`,
     а также пересоздаёт `outbound_cascade` rule той же цепочкой.
3. Привязать cascade-ассистента к номеру (`POST /api/telephony/bind-assistant`,
   `assistant_type: "cascade"`) — бекенд сам создаст inbound-правило с цепочкой.
4. Исходящий звонок: `StartScenarios` по правилу `outbound_cascade` со
   `script_custom_data` (см. `voximplant_partner.start_outbound_call`).

Fish-сценарии раскатываются так же, своими эндпоинтами:

1. Обновить код `inbound_fish` и `outbound_fish` на родительском аккаунте.
2. `POST /api/telephony/admin/setup-fish-scenarios` (или
   `…/setup-fish-scenarios-stream` — SSE с прогрессом по аккаунтам): копирует оба
   сценария на дочерние аккаунты и создаёт правило `outbound_fish`.
3. Входящие: `POST /api/telephony/bind-assistant` с `assistant_type: "fish"`
   (или `"agent"` для агента обзвона с fish-голосом) — правило создаётся само.
4. Исходящие агента обзвона идут по правилу `outbound_fish`
   (`task_scheduler._outbound_rule_name`), цепочка сценариев не нужна.

## Как работает цепочка из двух сценариев в одном правиле

`AddRule`/`SetRuleInfo` принимают `scenario_id` списком (`"id1;id2"`).
Оба сценария загружаются в одну JS-сессию по порядку и делят глобальную
область видимости: первый объявляет `VoxTurnTaking`, второй его использует.
Правило при этом остаётся одно — модель «1 номер → 1 правило» не меняется.
Бекенд уже умеет это: `voximplant_partner.add_rule` (список → `;`),
`telephony.bind-assistant` (цепочка для cascade).

## Ограничения текущей версии

- TTS-провайдер поддержан только `voxtts` (Anna/Sergey); для ассистентов со
  старыми провайдерами (`yandex`/`tinkoff`/`sber`) сценарий пишет warning в лог
  и озвучивает VoxTTS/Anna.
- Кастомные функции (`functions`) вызываются и в **исходящем**
  (`outbound_cascade`), и во **входящем** (`inbound_cascade`) каскаде через
  tool-calling — без каких-либо взаимных ограничений.
- Модель `gpt-realtime-2.1-mini` доступна ТОЛЬКО через эндпоинт `/v1/realtime`.
  Для текстовых (не голосовых) применений — например, агент-оркестратор в
  `backend/api/agent.py` — она не подходит, там остаётся прежняя модель.
