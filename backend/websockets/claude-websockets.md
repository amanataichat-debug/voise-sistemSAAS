# backend/websockets — реал-тайм голосовые хендлеры и провайдерские WS-клиенты

## Назначение
Сердце голосового движка Voksy AI. Здесь живут WebSocket-хендлеры (серверная сторона соединения с браузером/телефонией) и WS-клиенты к провайдерам реал-тайм голоса (OpenAI Realtime, Google Gemini Live, Fish Audio, xAI Grok). Хендлер принимает аудио от клиента, проксирует его в провайдера, получает аудио/события обратно, обрабатывает function calling и пишет диалог в БД. Телефония (собственный SIP-шлюз) проходит через те же хендлеры, что и виджет, через `sip_media_adapter.py`. Роутеры в `backend/api/*_ws.py` лишь принимают соединение и делегируют сюда.

**Voximplant не используется** (см. `CLAUDE.md`, раздел «Voximplant is NOT used»): `voximplant_handler.py`, `voximplant_adapter.py`, `handler_vox_gemini.py`, `handler_fish_tts.py` — мёртвый код до общей чистки.

## Состав
### OpenAI Realtime
- `handler_realtime_new.py` — **актуальный** хендлер (PRODUCTION v3.0, модель `gpt-realtime-2`). Используется роутером `/ws/{assistant_id}` (`api/websocket.py`).
- `openai_client_new.py` — **актуальный** WS-клиент OpenAI Realtime (v4.0, `gpt-realtime-2`), парный к `handler_realtime_new`.
- `handler.py` — **легаси** хендлер OpenAI Realtime; оставлен как backup для отката (импорт закомментирован в роутере).
- `openai_client.py` — **легаси** WS-клиент OpenAI Realtime (пара к `handler.py`).
- `handler_realtime_streaming.py` — **экспериментальный** хендлер с sentence-based TTS-стримингом (LLM → ElevenLabs параллельно). Не в основном проде; см. `sentence_detector`.
- `openai_client_streaming.py` — хендлер `/ws/llm-stream` (текстовый LLM-стрим v3.0), изолированный от голосового канала. Подключён через `api/gemini_ws.py`.

### Google Gemini Live
- `handler_gemini.py` — хендлер Gemini Live (PRODUCTION v1.6.1), чистый Gemini VAD, непрерывный стрим аудио. Роут `/ws/gemini/{assistant_id}`.
- `gemini_client.py` — WS-клиент Gemini Live (v1.6, модель `gemini-2.5-flash-native-audio-preview`).
- `handler_gemini_31.py` / `gemini_client_31.py` — вариант под Gemini 3.1 Flash Live (`gemini-3.1-flash-live-preview`). Роут `/ws/gemini-31/{assistant_id}`.
- `browser_handler_gemini.py` — Gemini-хендлер с DUAL WebSocket (v3.3): голос + отдельный канал для браузерных/визуальных функций и function calling. Роут `/ws/gemini-browser/{assistant_id}`.
- `handler_vox_gemini.py` — мост Voximplant ↔ Gemini Live (v1.0), fallback когда встроенный Gemini-модуль Voximplant недоступен. Роут `/ws/vox-gemini/{assistant_id}`.

### Fish Audio (OpenAI текст + Fish TTS, серверные ключи)
- `handler_fish.py` — хендлер Fish-ассистента (`FishVoiceSession` + `handle_fish_websocket_connection`). Роут `/ws/fish/{assistant_id}` (`api/fish_ws.py`) и телефон через `SIP_HANDLERS["fish"]`. Протокол клиента — виджета. Ход: аудио → OpenAI Realtime (server VAD, транскрипция) → текстовые дельты → `StreamingSentenceDetector` (первое предложение от 25 символов) → `FishTTSClient.say()` → PCM16 24 кГц → `response.audio.delta`. Приветствие идёт в Fish напрямую и кладётся в контекст OpenAI (`add_assistant_message`). Перебивание: `speech_started` при говорящем ассистенте → `response.cancel` + `tts.clear()` + клиенту `speech.started`/`conversation.interrupted`. Функции — общий `execute_and_send_function_result` из `handler_realtime_new` (клиент Fish реализует его интерфейс), `hangup_call` ловит `HandlerSocket`. Диалог сохраняется в `fish_conversations` через `ConversationService.save_conversation` (ветка `assistant_type == "fish"`) после ожидания стенограммы до 1.5 с. Ключи только `settings.OPENAI_API_KEY` и `settings.FISH_API_KEY`; при их отсутствии клиенту уходит `error` с кодом `openai_not_configured` / `fish_not_configured`.
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
- `sip_media_adapter.py` — `HandlerSocket`: псевдо-WebSocket, через который телефонный звонок с VPS-шлюза проходит через **те же** `handler_realtime_new` / `handler_gemini`, что и виджет. Ресемплинг 8↔24/16 кГц, батчинг входа, barge-in → `clear`, `hangup_call` → прощание → `mark` → `hangup`, замер `reply latency`. Подробно: `infra/sip-gateway/claude-sip-gateway.md`.
- `gemini_client.py` — VAD-профиль Gemini задаётся глобально через env `GEMINI_VAD_*` (одинаково для виджета и телефона); `handler_gemini.py` повторяет приветствие один раз, если Gemini оборвал его до первого аудио.

## Ключевые сущности / точки входа
- **`handle_websocket_connection_new`** (`handler_realtime_new.py`) — точка входа OpenAI-голоса, вызывается из `api/websocket.py` для `/ws/{assistant_id}` и `/ws/demo`.
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
- **Много версионных дубликатов** — для одного провайдера сосуществуют актуальные, легаси и экспериментальные хендлеры/клиенты. **Источник истины — какой модуль реально импортирует роутер** (`backend/api/*_ws.py`): сейчас это `handler_realtime_new` + `openai_client_new` (OpenAI), `handler_gemini`/`handler_gemini_31`/`browser_handler_gemini` (Gemini), `handler_fish` + `fish_llm_client` + `fish_tts_client` (Fish), `handler_grok` (Grok), `handler_translate`. `handler.py`/`openai_client.py` и `handler_realtime_streaming.py` напрямую не подключены — не правьте их, думая что это прод.
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
