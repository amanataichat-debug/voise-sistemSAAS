# backend/functions — модульная система AI-функций (function calling)

## Назначение
Реестр функций, которые голосовой/чат-ассистент может вызывать во время разговора (OpenAI/Gemini/Grok function calling). Каждая функция — отдельный модуль с классом-наследником `FunctionBase`. При старте приложения `discover_functions()` сканирует папку, импортирует все модули и регистрирует найденные классы. Роутеры и WebSocket-хендлеры получают определения функций (`get_enabled_functions`) и исполняют их по имени (`execute_function`) с передачей контекста разговора.

## Состав
### Инфраструктура реестра
- `__init__.py` — публичный API пакета; вызывает `discover_functions()` при импорте; экспортирует `register_function`, `get_function_definitions`, `get_enabled_functions`, `execute_function`, `normalize_function_name`. Алиасы `get_all_definitions` / `get_all_openai_definitions` для обратной совместимости.
- `base.py` — абстрактный `FunctionBase`: `get_name`, `get_description`, `get_parameters` (обязательные), `get_display_name`/`get_example_prompt` (метаданные для UI), `get_definition` (полное определение для LLM + UI) и `async execute(arguments, context)`.
- `registry.py` — `FunctionRegistry` (синглтон): `register`, `get_function` (с нормализацией имени), `get_definitions`, `get_enabled_functions(enabled_names)`, `execute_function`, `discover_functions` (авто-импорт по именам файлов), `normalize_function_name` (camelCase→snake_case).

### Функции (каждый файл — один класс `FunctionBase`)
- `add_google_sheet_row.py` — добавляет строку в Google Таблицу (сервисный аккаунт).
- `api_request.py` — произвольный HTTP-запрос (GET/POST/PUT/DELETE/PATCH) к внешнему API.
- `create_crm_voicyfy_task.py` — создаёт задачу на обратный звонок в CRM Voksy AI; находит/создаёт контакт по телефону.
- `get_current_time.py` — текущие дата/время в заданном часовом поясе.
- `hangup_call.py` — завершает звонок (только в контексте Voximplant-телефонии).
- `query_llm.py` — запрос к текстовой LLM (ChatGPT) для развёрнутых ответов; использует `services/llm_streaming` для стриминга в WebSocket.
- `query_orchestrator.py` — обращение к оркестратору Voksy AI Agent (`services/agent_orchestrator`); мост между голосовым агентом и «мозгом».
- `read_google_doc.py` — читает текст из публичного Google Документа по ссылке.
- `search_contact_by_phone.py` — поиск контакта в CRM по номеру телефона.
- `search_pinecone.py` — векторный поиск по базе знаний (`services/pinecone_service`).
- `send_sms.py` — отправка SMS (через Voximplant).
- `send_telegram_notification.py` — уведомление через Telegram-бота в чат/группу/канал.
- `send_webhook.py` — отправка данных на внешний вебхук (n8n, Make, Zapier, любой HTTP endpoint).
- `show_image.py` — показывает изображение по URL на экране пользователя во время разговора.
- `start_browser_task.py` — запускает задачу браузерной автоматизации (`services/browser_agent_service` / `models/browser_task`).
- `write_google_doc.py` — дописывает текст в конец Google Документа (нужны права Редактора у сервисного аккаунта).

## Ключевые сущности / точки входа
- **`FunctionBase`** (`base.py`) — контракт функции. Минимум для новой функции: переопределить `get_name`, `get_description`, `get_parameters` (JSON Schema параметров) и `async execute(arguments, context)`. `get_display_name`/`get_example_prompt` — опциональные метаданные для UI настройки ассистента.
- **`discover_functions()`** — авто-дискавери: проходит по `*.py` (кроме `base.py`/`registry.py`/`__init__.py`), импортирует модуль, регистрирует все подклассы `FunctionBase`. Чтобы добавить функцию — достаточно положить файл в папку; ручной регистрации не требуется.
- **`get_enabled_functions(names)`** — возвращает определения только для функций, включённых в конфиге ассистента (`functions`/`agent_functions` в моделях). Поддерживает оба написания имени.
- **`execute_function(name, arguments, context)`** — единая точка вызова; ловит исключения и возвращает `{"error": ...}` вместо падения. `context` — словарь с данными разговора (id ассистента/пользователя, телефон звонящего, WebSocket session_id и т.п.); конкретный состав зависит от вызывающего хендлера.
- **`normalize_function_name`** — приводит имена к snake_case, чтобы совпадали имена из конфига и из LLM-ответа.

## Связи с другими частями проекта
- Используется: `backend/websockets/*` (голосовые хендлеры собирают определения и исполняют вызовы LLM), `backend/api/functions.py` (отдаёт список доступных функций для UI), `backend/api/voximplant.py` (`/functions/execute` для телефонии), `app.py` (`discover_functions()` при старте).
- Использует: `backend/services/*` — `pinecone_service`, `google_sheets_service`, `telegram_notification`, `llm_streaming`, `agent_orchestrator`, `browser_agent_service`; `backend/models/*` (Contact, Task, BrowserTask, SmsMessage, FunctionLog); `backend/core/logging`; внешние API (Google, Telegram, Pinecone, OpenAI/OpenRouter, Voximplant).

## На что обратить внимание
- **Авто-дискавери по имени файла.** Имя файла не обязано совпадать с `get_name()` — регистрация идёт по классу, а не по файлу. Но «битый» модуль (ошибка импорта) молча логируется и пропускается — функция тихо исчезнет из реестра.
- **Контекст исполнения непостоянен.** Разные вызывающие (web-виджет, Voximplant-телефония, агент) кладут в `context` разный набор полей. Функция должна аккуратно проверять наличие нужных ключей; `hangup_call`/`send_sms` имеют смысл только в телефонном контексте.
- **`execute` всегда async** и не должен бросать наружу — `execute_function` оборачивает в try/except, но лучше возвращать структурированный результат с понятным сообщением для LLM.
- **Логирование вызовов** — фактический результат пишется в `FunctionLog` (см. `services/function_log_service`), а не здесь.
- `query_orchestrator` и `start_browser_task` тянут тяжёлые подсистемы (оркестратор агента, браузерная автоматизация) — их доступность зависит от конфигурации и внешних сервисов.

## Связанные файлы документации
- `../claude-backend.md` — родительская
- `../services/claude-services.md` — сервисы, на которые опираются функции
- `../services/llm_streaming/claude-llm-streaming.md` — стриминг для `query_llm`
- `../websockets/claude-websockets.md` — основной потребитель (исполнение вызовов)
- `../api/claude-api.md` — `functions.py`, `voximplant.py`
- `../models/claude-models.md` — `FunctionLog`, `BrowserTask`, `SmsMessage`
- `../../claude-index.md` — корневой индекс
