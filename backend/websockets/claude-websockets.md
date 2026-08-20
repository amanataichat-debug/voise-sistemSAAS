# backend/websockets — реал-тайм голосовые хендлеры и провайдерские WS-клиенты

## Назначение
Сердце голосового движка Voicyfy. Здесь живут WebSocket-хендлеры (серверная сторона соединения с браузером/телефонией) и WS-клиенты к провайдерам реал-тайм голоса (OpenAI Realtime, Google Gemini Live, xAI Grok). Хендлер принимает аудио от клиента, проксирует его в провайдера, получает аудио/события обратно, обрабатывает function calling и пишет диалог в БД. Отдельно — мост телефонии Voximplant и хендлер синхронного перевода. Роутеры в `backend/api/*_ws.py` лишь принимают соединение и делегируют сюда.

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

### xAI Grok Voice
- `handler_grok.py` — хендлер Grok Voice Agent (v1.1), endpoint провайдера `wss://api.x.ai/v1/realtime`. Роуты `/ws/grok/{assistant_id}`, `/ws/grok/voximplant/{assistant_id}`, `/ws/grok/custom/{assistant_id}`.
- `grok_client.py` — WS-клиент Grok Voice (v1.1).

### Перевод
- `handler_translate.py` — прокси OpenAI Realtime Translation (`gpt-realtime-translate`), упрощённый (без conversation lifecycle). Роут `/ws/translate/{assistant_id}`.

### Телефония Voximplant
- `voximplant_handler.py` — основной телефонный WS-хендлер (PRODUCTION v2.2): сохраняет каждое сообщение диалога отдельной записью в БД, логирует номер звонящего, считает стоимость.
- `voximplant_adapter.py` — адаптер аудио/протокола между Voximplant и провайдером (v2.1, логирование номера телефона).

### Утилиты
- `sentence_detector.py` — `StreamingSentenceDetector`: детектор границ предложений для стриминговой TTS-озвучки.
- `__init__.py` — реэкспорт хендлеров/клиентов для импорта из роутеров.

## Ключевые сущности / точки входа
- **`handle_websocket_connection_new`** (`handler_realtime_new.py`) — точка входа OpenAI-голоса, вызывается из `api/websocket.py` для `/ws/{assistant_id}` и `/ws/demo`.
- **`handle_gemini_websocket_connection`** / `handle_gemini_31_websocket_connection` / `handle_vox_gemini_websocket` — точки входа Gemini (`api/gemini_ws.py`).
- **`handle_grok_websocket_connection`** — точка входа Grok (`api/grok_ws.py`).
- **`handle_translate_connection`** — точка входа перевода (`api/translate_ws.py`).
- **`handle_openai_streaming_websocket`** — текстовый LLM-стрим `/ws/llm-stream`.
- **Провайдерские клиенты** (`*_client*.py`) — устанавливают upstream-WS к провайдеру, конвертируют аудио (PCM16/base64, обычно 24 кГц mono), пробрасывают события и tool calls.
- **Function calling:** хендлеры собирают определения через `backend/functions` (`get_enabled_functions`) и исполняют (`execute_function`) с контекстом разговора.

## Связи с другими частями проекта
- Используется: `backend/api/websocket.py`, `gemini_ws.py`, `grok_ws.py`, `translate_ws.py`, `voximplant.py` (телефонные сценарии вызывают мост).
- Использует: `backend/functions/` (исполнение AI-функций), `backend/services/` (`conversation_service` — запись диалогов, `telegram_notification`/`webhook_notification` — пост-обработка, `credit_service` — списание, `pinecone_service`), `backend/models/` (Conversation, *Conversation по провайдерам, AssistantConfig и аналоги, User для API-ключей), `backend/utils/audio_utils.py` (конвертация аудио), `backend/core/` (config, logging). Внешние провайдеры: OpenAI/Gemini/Grok realtime, Voximplant.

## На что обратить внимание
- **Много версионных дубликатов** — для одного провайдера сосуществуют актуальные, легаси и экспериментальные хендлеры/клиенты. **Источник истины — какой модуль реально импортирует роутер** (`backend/api/*_ws.py`): сейчас это `handler_realtime_new` + `openai_client_new` (OpenAI), `handler_gemini`/`handler_gemini_31`/`browser_handler_gemini`/`handler_vox_gemini` (Gemini), `handler_grok` (Grok), `handler_translate`. `handler.py`/`openai_client.py` и `handler_realtime_streaming.py` напрямую не подключены — не правьте их, думая что это прод.
- **`gemini_client_31.py`/`handler_gemini_31.py` начинаются со старого docstring** (`# backend/websockets/gemini_client.py`) — комментарии скопированы, ориентируйтесь на версию/модель в теле, а не на первую строку.
- **Порядок роутеров важен:** `/ws/llm-stream`, `/ws/gemini/*`, `/ws/translate/*` должны матчиться ДО `/ws/{assistant_id}` — иначе их перехватит OpenAI-хендлер (в `api/websocket.py` есть явная проверка ROUTE COLLISION).
- **Аудиоформат** — PCM16, обычно 24 кГц mono, base64. Конвертация — через `utils/audio_utils.py`; при смене частоты/каналов проверяйте обе стороны (клиент и провайдер).
- **Voximplant** — телефонный звук и протокол отличаются (`voximplant_adapter`), запись диалога идёт по каждому сообщению отдельно (v2.2). PostCall/стоимость считаются здесь и в `services`.
- **Стоимость и кредиты** списываются в ходе/по завершении разговора — следите за вызовами `credit_service`/`conversation_service`, чтобы не задвоить.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../api/claude-api.md` — роутеры, делегирующие сюда (`*_ws.py`, `websocket.py`)
- `../functions/claude-functions.md` — исполнение AI-функций в разговоре
- `../services/claude-services.md` — запись диалогов, уведомления, кредиты
- `../utils/claude-utils.md` — конвертация аудио (`audio_utils`)
- `../models/claude-models.md` — модели диалогов и ассистентов
- `../../claude-index.md` — корневой индекс
