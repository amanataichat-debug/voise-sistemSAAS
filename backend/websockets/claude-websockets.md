# backend/websockets — реал-тайм голосовые хендлеры и провайдерские WS-клиенты

## Назначение
Сердце голосового движка Voksy AI. Здесь живут WebSocket-хендлеры (серверная сторона соединения с браузером/телефонией) и WS-клиенты к провайдерам реал-тайм голоса (OpenAI Realtime, Google Gemini Live, Fish Audio, xAI Grok). Хендлер принимает аудио от клиента, проксирует его в провайдера, получает аудио/события обратно, обрабатывает function calling и пишет диалог в БД. Телефония (собственный SIP-шлюз) проходит через те же хендлеры, что и виджет, через `sip_media_adapter.py`. Роутеры в `backend/api/*_ws.py` лишь принимают соединение и делегируют сюда.

**Voximplant не используется** (см. `CLAUDE.md`, раздел «Voximplant is NOT used»): `voximplant_handler.py`, `voximplant_adapter.py`, `handler_vox_gemini.py`, `handler_fish_tts.py` — мёртвый код до общей чистки.

## Состав
### OpenAI GPT-Live (актуальный транспорт OpenAI-ассистентов)
- `handler_live.py` — **актуальный** хендлер OpenAI-ассистентов на `gpt-live-1` (full-duplex). Точка входа `handle_live_websocket_connection`; роуты `/ws/{assistant_id}`, `/ws/demo` (`api/websocket.py`) и телефон через `SIP_HANDLERS["openai"]`. Протокол клиента — виджета. Ход: аудио клиента → `session.input_audio.append` непрерывно (и во время речи ассистента); `session.output_audio.delta` → `response.audio.delta`; `assistant.speech.started/ended` выводятся из потока аудио (пауза `OUTPUT_IDLE_SEC` = 1 с — ассистент договорил, у Live нет события «аудио закончилось»); фрагменты транскрипта → `TranscriptCollector` → в конце сессии пары (user, assistant) в `conversations` (первая пара заполняет пустую запись, созданную на старте для `function_logs`, запись **ожидается**, а не в фоне — SIP-роутер тегирует звонок сразу после хендлера). Приветствие — `session.instructions.append` после `session.started`. Функции приходят от клиента как `live.function_call`, исполняются общим `execute_and_send_function_result` (`function_calls.py`), `hangup_call` ловит SIP-адаптер по `function_call.executing`. Событий VAD/перебивания нет — модель сама слушает во время речи; `response.cancel`/`input_audio_buffer.*` от клиента получают ack и игнорируются. `screen.context` (vision) игнорируется: у `gpt-live-1` нет входа изображений. В `connection_status` уходит `full_duplex: true` — по нему виджет включает непрерывный микрофон и gapless-воспроизведение. Ключ — `user.openai_api_key` владельца (как было у Realtime); бэкенд делегирования работает на нём же.
- `live_client.py` — `OpenAILiveClient`: `wss://api.openai.com/v1/live/sessions`, `session.start` → `session.started`. Конфиг: `model` (`LIVE_MODEL`), `instructions` (промпт ассистента, обрезанный до `LIVE_VOICE_INSTRUCTIONS_MAX_CHARS` + `VOICE_DELEGATION_HINT`, на телефоне + `VOICE_TELEPHONY_HINT`), `audio.format {audio/pcm, 24000}` (один формат на вход и выход), `audio.output.voice` (22 голоса `LIVE_VOICES`: 10 из кабинета + 12 новых; при отказе API один раз повторяет старт с `LIVE_DEFAULT_VOICE`), `delegation.responses` (`LIVE_DELEGATION_MODEL`, `BACKEND_SYSTEM_PROMPT` + полный промпт ассистента, `tools` в плоском формате Responses API). Вызовы функций: `response.event` → `response.output_item.done` (`function_call`) → синтетическое `live.function_call`; `send_function_result()` шлёт `response.item.create` (`function_call_output`) и, когда у делегирования собраны все результаты и пришёл `response.completed`, — `response.create` (у `response.*` нет поля `delegation_id`). Ошибка на `fnres_`/`cont_` → `session.instructions.append` «скажи, что проверить не удалось», иначе бэкенд зависнет. `close()` = `session.close` + ожидание `session.closed` с итоговым `usage.seconds`. Создаёт пустую запись в `conversations` (`conversation_record_id`).
- `function_calls.py` — общий асинхронный исполнитель функций (`execute_and_send_function_result`, `async_save_function_log`, `async_save_to_google_sheets`, `async_save_dialog_to_db`) для `handler_live` и `handler_fish`: функция в фоне, `llm_result` для `query_llm`, логи в `function_logs` с `conversation_id`, результат через `client.send_function_result`; при исключении провайдеру уходит `{"error": ...}`, чтобы он не ждал вечно.

### OpenAI Realtime (легаси, не подключено к роутерам)
- `handler_realtime_new.py` + `openai_client_new.py` — прежний прод (Realtime GA, `gpt-realtime-2`, server VAD). Оставлены для отката; `fish_llm_client.py` берёт отсюда `normalize_functions`/`get_device_vad_settings`, `voximplant_handler.py` (мёртвый код) — `OpenAIRealtimeClientNew`.
- `handler.py` + `openai_client.py` — ещё более старый Realtime Beta.
- `handler_realtime_streaming.py` — **экспериментальный** хендлер с sentence-based TTS-стримингом (LLM → ElevenLabs параллельно). Не в основном проде; см. `sentence_detector`.
- `openai_client_streaming.py` — хендлер `/ws/llm-stream` (текстовый LLM-стрим v3.0), изолированный от голосового канала. Подключён через `api/gemini_ws.py`.

### Google Gemini Live
- `handler_gemini.py` — хендлер Gemini Live (PRODUCTION v1.6.1), чистый Gemini VAD, непрерывный стрим аудио. Роут `/ws/gemini/{assistant_id}`.
- `gemini_client.py` — WS-клиент Gemini Live (v1.6, модель `gemini-2.5-flash-native-audio-preview`).
- `handler_gemini_31.py` / `gemini_client_31.py` — вариант под Gemini 3.1 Flash Live (`gemini-3.1-flash-live-preview`). Роут `/ws/gemini-31/{assistant_id}`.
- `browser_handler_gemini.py` — Gemini-хендлер с DUAL WebSocket (v3.3): голос + отдельный канал для браузерных/визуальных функций и function calling. Роут `/ws/gemini-browser/{assistant_id}`.
- `handler_vox_gemini.py` — мост Voximplant ↔ Gemini Live (v1.0), fallback когда встроенный Gemini-модуль Voximplant недоступен. Роут `/ws/vox-gemini/{assistant_id}`.

### Fish Audio (OpenAI текст + Fish TTS, серверные ключи)
- `handler_fish.py` — хендлер Fish-ассистента (`FishVoiceSession` + `handle_fish_websocket_connection`). Роут `/ws/fish/{assistant_id}` (`api/fish_ws.py`) и телефон через `SIP_HANDLERS["fish"]`. Протокол клиента — виджета. Ход: аудио → OpenAI Realtime (server VAD, транскрипция) → текстовые дельты → `StreamingSentenceDetector` (первое предложение от 25 символов) → `FishTTSClient.say()` → PCM16 24 кГц → `response.audio.delta`. Приветствие идёт в Fish напрямую и кладётся в контекст OpenAI (`add_assistant_message`). Перебивание: `speech_started` при говорящем ассистенте → `response.cancel` + `tts.clear()` + клиенту `speech.started`/`conversation.interrupted`. Функции — общий `execute_and_send_function_result` из `function_calls.py` (клиент Fish реализует его интерфейс), `hangup_call` ловит `HandlerSocket`. Диалог сохраняется в `fish_conversations` через `ConversationService.save_conversation` (ветка `assistant_type == "fish"`) после ожидания стенограммы до 1.5 с. Ключи только `settings.OPENAI_API_KEY` и `settings.FISH_API_KEY`; при их отсутствии клиенту уходит `error` с кодом `openai_not_configured` / `fish_not_configured`.
- `fish_llm_client.py` — `FishLLMClient`: OpenAI Realtime `gpt-realtime-2` (или `gpt-realtime-2.1-mini` из `llm_model`), `output_modalities: ["text"]`, вход PCM16 24 кГц, VAD по устройству (`get_device_vad_settings`) или телефонный профиль `TELEPHONY_VAD` (0.5 / 300 / 500 мс) при `assistant.telephony_mode`. Создаёт пустую запись в `fish_conversations` для привязки `function_logs` (`conversation_record_id`). `send_function_result()` шлёт `function_call_output` и сразу `response.create` (текст).
- `fish_tts_client.py` — `FishTTSClient`: `wss://api.fish.audio/v1/tts/live` (msgpack, заголовки `Authorization` + `model`), StartEvent из `FishAssistantConfig.get_fish_start_request(sample_rate=24000)`, `say(text)` = `text` + `flush`. Конец реплики — по тишине 700 мс после `end_of_response()` (у Fish нет события «реплика доиграна»); начало — первый аудио-кадр. `clear()` при перебивании = смена поколения + переподключение (у Fish нет отмены синтеза); текст, пришедший во время переподключения, досылается. Обрыв со стороны Fish → переподключение при следующем `say()`.

### xAI Grok Voice
- `handler_grok.py` — хендлер Grok Voice Agent (v1.1), endpoint провайдера `wss://api.x.ai/v1/realtime`. Роуты `/ws/grok/{assistant_id}`, `/ws/grok/voximplant/{assistant_id}`, `/ws/grok/custom/{assistant_id}`.
- `grok_client.py` — WS-клиент Grok Voice (v1.1).

### Перевод
- `handler_translate.py` — прокси OpenAI Realtime Translation (`gpt-realtime-translate`), упрощённый (без conversation lifecycle). Роут `/ws/translate/{assistant_id}`.

### Телефония Voximplant (МЁРТВЫЙ КОД, не используется)
- `voximplant_handler.py` — бывший телефонный WS-хендлер Voximplant. Не подключён к рабочей телефонии.
- `voximplant_adapter.py` — адаптер аудио/протокола между Voximplant и провайдером (v2.1, логирование номера телефона).

### Утилиты
- `sentence_detector.py` — `StreamingSentenceDetector`: детектор границ предложений для стриминговой TTS-озвучки.
- `__init__.py` — реэкспорт хендлеров/клиентов для импорта из роутеров.

### Собственная SIP-телефония
- `sip_media_adapter.py` — `HandlerSocket`: псевдо-WebSocket, через который телефонный звонок с VPS-шлюза проходит через **те же** `handler_live` / `handler_gemini` / `handler_fish`, что и виджет. Ресемплинг 8↔24/16 кГц, батчинг входа, barge-in → `clear`, `hangup_call` → прощание → `mark` → `hangup`, замер `reply latency`. Для `openai` (GPT-Live отдаёт аудио в реальном темпе) начало каждой реплики придерживается на `OUTBOUND_CUSHION_MS` (200 мс), чтобы у пейсера моста был запас против джиттера. Подробно: `infra/sip-gateway/claude-sip-gateway.md`.
- `gemini_client.py` — VAD-профиль Gemini задаётся глобально через env `GEMINI_VAD_*` (одинаково для виджета и телефона); `handler_gemini.py` повторяет приветствие один раз, если Gemini оборвал его до первого аудио.

## Ключевые сущности / точки входа
- **`handle_live_websocket_connection`** (`handler_live.py`) — точка входа OpenAI-голоса (GPT-Live), вызывается из `api/websocket.py` для `/ws/{assistant_id}` и `/ws/demo` и из `api/sip_gateway.py` для телефона.
- **`handle_gemini_websocket_connection`** / `handle_gemini_31_websocket_connection` / `handle_vox_gemini_websocket` — точки входа Gemini (`api/gemini_ws.py`).
- **`handle_grok_websocket_connection`** — точка входа Grok (`api/grok_ws.py`).
- **`handle_translate_connection`** — точка входа перевода (`api/translate_ws.py`).
- **`handle_openai_streaming_websocket`** — текстовый LLM-стрим `/ws/llm-stream`.
- **Провайдерские клиенты** (`*_client*.py`) — устанавливают upstream-WS к провайдеру, конвертируют аудио (PCM16/base64, обычно 24 кГц mono), пробрасывают события и tool calls.
- **Function calling:** хендлеры собирают определения через `backend/functions` (`get_enabled_functions`) и исполняют (`execute_function`) с контекстом разговора.

## Связи с другими частями проекта
- Используется: `backend/api/websocket.py`, `gemini_ws.py`, `fish_ws.py`, `grok_ws.py`, `translate_ws.py`, `sip_gateway.py` (телефония через `HandlerSocket`).
- Использует: `backend/functions/` (исполнение AI-функций), `backend/services/` (`conversation_service` — запись диалогов, `telegram_notification`/`webhook_notification` — пост-обработка, `credit_service` — списание, `pinecone_service`), `backend/models/` (Conversation, *Conversation по провайдерам, AssistantConfig и аналоги, User для API-ключей), `backend/utils/audio_utils.py` (конвертация аудио), `backend/core/` (config, logging). Внешние провайдеры: OpenAI/Gemini/Grok realtime, Fish Audio TTS.

## На что обратить внимание
- **Много версионных дубликатов** — для одного провайдера сосуществуют актуальные, легаси и экспериментальные хендлеры/клиенты. **Источник истины — какой модуль реально импортирует роутер** (`backend/api/*_ws.py`): сейчас это `handler_live` + `live_client` (OpenAI, GPT-Live), `handler_gemini`/`handler_gemini_31`/`browser_handler_gemini` (Gemini), `handler_fish` + `fish_llm_client` + `fish_tts_client` (Fish), `handler_grok` (Grok), `handler_translate`. `handler_realtime_new.py`/`openai_client_new.py`, `handler.py`/`openai_client.py` и `handler_realtime_streaming.py` напрямую не подключены — не правьте их, думая что это прод.
- **GPT-Live — full-duplex.** Клиент обязан стримить микрофон непрерывно (и пока ассистент говорит), иначе модель не услышит перебивание; аудио от модели идёт в реальном темпе, поэтому клиенту нужен буфер ~200 мс (виджет — `scheduleLiveAudio`, телефон — `OUTBOUND_CUSHION_MS`). Событий `speech.started`/`conversation.interrupted`/`response.done` в этом транспорте нет — не завязывайте на них новую логику для OpenAI.
- **`gemini_client_31.py`/`handler_gemini_31.py` начинаются со старого docstring** (`# backend/websockets/gemini_client.py`) — комментарии скопированы, ориентируйтесь на версию/модель в теле, а не на первую строку.
- **Порядок роутеров важен:** `/ws/llm-stream`, `/ws/gemini/*`, `/ws/fish/*`, `/ws/sip/*`, `/ws/translate/*` должны матчиться ДО `/ws/{assistant_id}` — иначе их перехватит OpenAI-хендлер (в `api/websocket.py` есть явная проверка ROUTE COLLISION).
- **Аудиоформат** — PCM16, обычно 24 кГц mono, base64. Конвертация — через `utils/audio_utils.py`; при смене частоты/каналов проверяйте обе стороны (клиент и провайдер).
- **Новый провайдер** = хендлер с протоколом виджета + запись в `SIP_HANDLERS` (`api/sip_gateway.py`), `SIP_SUPPORTED_ASSISTANT_TYPES` (`models/sip_gateway.py`) и `HANDLER_IN_RATE`/`INBOUND_BATCH_MS` (`sip_media_adapter.py`). Диалоги провайдера — в своей таблице (`conversations` привязана FK к `assistant_configs`), таблицу надо добавить в union на странице «Диалоги» и в `tag_conversations`.
- **Стоимость и кредиты** списываются в ходе/по завершении разговора — следите за вызовами `credit_service`/`conversation_service`, чтобы не задвоить.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../api/claude-api.md` — роутеры, делегирующие сюда (`*_ws.py`, `websocket.py`)
- `../functions/claude-functions.md` — исполнение AI-функций в разговоре
- `../services/claude-services.md` — запись диалогов, уведомления, кредиты
- `../utils/claude-utils.md` — конвертация аудио (`audio_utils`)
- `../models/claude-models.md` — модели диалогов и ассистентов
- `../../claude-index.md` — корневой индекс
