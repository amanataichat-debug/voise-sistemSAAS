# backend/services/llm_streaming — low-latency стриминг текстовой LLM

## Назначение
Небольшой пакет для потоковой генерации ответов текстовой LLM (OpenAI Chat API) с маршрутизацией токенов в нужное WebSocket-соединение. Используется функцией `query_llm` (`backend/functions/query_llm.py`): пока голосовой ассистент держит паузу, тяжёлый текстовый запрос стримится отдельным каналом и отдаётся на фронтенд по мере генерации, не искажая аудиопоток.

## Состав
- `__init__.py` — экспорт `ChatGPTStreamingClient`, `SessionManager`.
- `streaming_client.py` — `ChatGPTStreamingClient`: async-клиент OpenAI Chat Completions со стримингом токенов. Параметры: `api_key`, `model` (по умолчанию `gpt-4o-mini`), `max_tokens`, `temperature`. Отдаёт токены через `AsyncIterator`, считает latency/метрики, ретраит ошибки.
- `session_manager.py` — `SessionManager`: реестр `session_id → WebSocket` (+ `assistant_id`). Позволяет функции `query_llm` найти WebSocket-соединение, в которое нужно слать события стрима, не имея прямой ссылки на сокет.

## Ключевые сущности / точки входа
- **`ChatGPTStreamingClient`** — создаётся с ключом пользователя/сервера; основной метод выдаёт поток токенов для немедленной отправки на клиент.
- **`SessionManager`** — `register_session(session_id, websocket, assistant_id)`, поиск сокета по `session_id`, снятие регистрации при закрытии. Обычно используется как процессный синглтон, чтобы и WS-хендлер, и функция `query_llm` видели одни и те же сессии.

## Связи с другими частями проекта
- Используется: `backend/functions/query_llm.py` (запуск стрима и отправка событий `llm.stream.*`), `backend/api/llm_streaming.py` (эндпоинты `/api/llm/*`), WebSocket-хендлеры голоса (регистрация сессии при подключении).
- Использует: пакет `openai`, `backend/core/logging`. API-ключ передаётся снаружи (ключ пользователя из `User` или серверный `OPENAI_API_KEY`).

## На что обратить внимание
- **Зачем отдельный канал.** Стрим текста изолирован от голосового WebSocket намеренно — иначе перемешивание сообщений ломает аудио. Не сливайте эти каналы.
- **`SessionManager` хранит состояние в памяти процесса.** При нескольких воркерах Gunicorn регистрация и поиск сессии должны происходить в одном процессе; межпроцессного шеринга нет.
- **Модель по умолчанию `gpt-4o-mini`** — для длинных/сложных ответов вызывающий код может поднять модель и `max_tokens`.
- Не путать с `backend/websockets/openai_client_streaming.py` (`/ws/llm-stream`) — там отдельный WebSocket-хендлер LLM-стрима; этот пакет — сервисная обвязка для функции `query_llm`.

## Связанные файлы документации
- `../claude-services.md` — родительская (сервисный слой)
- `../../functions/claude-functions.md` — `query_llm`, главный потребитель
- `../../api/claude-api.md` — эндпоинты `/api/llm/*`
- `../../websockets/claude-websockets.md` — голосовые хендлеры и `/ws/llm-stream`
- `../../../claude-index.md` — корневой индекс
